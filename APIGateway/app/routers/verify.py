import asyncio
import json
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import Settings
from app.db import Database
from app.deps import Client, get_db, get_downstream, get_runtime, get_settings, rate_limit
from app.runtime_config import RuntimeConfigStore
from app.schemas import CheckResponse, ClaimVerifyRequest, LiveVerifyRequest, UrlVerifyRequest
from app.services import Downstream
from app.validation import validate_image, validate_public_url, validate_text
from app.verification import CheckRunner, LiveSearchNotApplicable

router = APIRouter(prefix="/api/v1/verify", tags=["verify"])

_RESPONSES = {
    502: {"model": CheckResponse},
    503: {"model": CheckResponse},
    429: {},
}


def get_runner(
    client: Client = Depends(rate_limit),
    db: Database = Depends(get_db),
    downstream: Downstream = Depends(get_downstream),
    runtime: RuntimeConfigStore = Depends(get_runtime),
) -> CheckRunner:
    return CheckRunner(db=db, downstream=downstream, runtime=runtime, client=client)


@router.post("/claim", response_model=CheckResponse, responses=_RESPONSES, summary="Weryfikuje twierdzenie podane jako tekst")
async def verify_claim(
    body: ClaimVerifyRequest,
    runner: CheckRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    text = validate_text(body.text, settings.max_text_length)
    return await runner.verify_claim(text)


@router.post("/url", response_model=CheckResponse, responses=_RESPONSES, summary="Weryfikuje treść strony spod podanego adresu URL")
async def verify_url(
    body: UrlVerifyRequest,
    runner: CheckRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    url = validate_public_url(body.url, settings.max_url_length)
    return await runner.verify_url(url)


@router.post("/image", response_model=CheckResponse, responses=_RESPONSES, summary="Odczytuje tekst z obrazu i weryfikuje zawarte w nim twierdzenia")
async def verify_image(
    file: UploadFile = File(...),
    runner: CheckRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    data = await file.read(settings.max_image_size_bytes + 1)
    mime = validate_image(data, settings.max_image_size_bytes)
    filename = (file.filename or "image").replace("\\", "/").rsplit("/", 1)[-1][:128]
    return await runner.verify_image(filename, data, mime)


@router.post(
    "/live",
    response_model=CheckResponse,
    responses=_RESPONSES,
    summary="Brak potwierdzonych danych: szuka na żywo w zaufanych witrynach i weryfikuje ponownie",
)
async def verify_live(
    body: LiveVerifyRequest,
    runner: CheckRunner = Depends(get_runner),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    if not settings.live_search_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wyszukiwanie na żywo jest wyłączone.")
    check = await db.get_check(body.check_id)
    if check is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono sprawdzenia.")
    try:
        return await runner.verify_live(
            check, settings.live_search_max_documents, settings.live_search_timeout_seconds
        )
    except LiveSearchNotApplicable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from None


# ---------- Wersje strumieniowe (NDJSON): postęp sprawdzania, a na końcu ten sam wynik ----------

_STREAM_DOC = (
    "Strumień NDJSON: linie {\"type\": \"progress\", \"stage\": ...} w trakcie pracy (np. odczyt zdjęcia, "
    "lista wątków, kolejne sprawdzone wątki), na końcu {\"type\": \"result\", \"status\": <HTTP>, \"body\": "
    "<CheckResponse>} - to samo co zwykły endpoint."
)


def _stream(runner: CheckRunner, run: Callable[[], Awaitable[JSONResponse]]) -> StreamingResponse:
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_progress(event: dict) -> None:
        await queue.put({"type": "progress", **event})

    runner.progress = on_progress

    def line(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str) + "\n"

    async def lines():
        yield line({"type": "progress", "stage": "received"})
        task = asyncio.ensure_future(run())
        task.add_done_callback(lambda _: queue.put_nowait(None))
        while (event := await queue.get()) is not None:
            yield line(event)
        try:
            response = task.result()
            status_code, body = response.status_code, json.loads(response.body)
        except HTTPException as exc:
            status_code, body = exc.status_code, {"detail": exc.detail}
        except Exception:
            status_code, body = 502, {"detail": "Sprawdzenie przerwane z powodu błędu technicznego."}
        yield line({"type": "result", "status": status_code, "body": body})

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/claim/stream", summary="Jak /claim, ale z postępem (NDJSON)", description=_STREAM_DOC)
async def verify_claim_stream(
    body: ClaimVerifyRequest,
    runner: CheckRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    text = validate_text(body.text, settings.max_text_length)
    return _stream(runner, lambda: runner.verify_claim(text))


@router.post("/url/stream", summary="Jak /url, ale z postępem (NDJSON)", description=_STREAM_DOC)
async def verify_url_stream(
    body: UrlVerifyRequest,
    runner: CheckRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    url = validate_public_url(body.url, settings.max_url_length)
    return _stream(runner, lambda: runner.verify_url(url))


@router.post("/image/stream", summary="Jak /image, ale z postępem (NDJSON)", description=_STREAM_DOC)
async def verify_image_stream(
    file: UploadFile = File(...),
    runner: CheckRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    data = await file.read(settings.max_image_size_bytes + 1)
    mime = validate_image(data, settings.max_image_size_bytes)
    filename = (file.filename or "image").replace("\\", "/").rsplit("/", 1)[-1][:128]
    return _stream(runner, lambda: runner.verify_image(filename, data, mime))
