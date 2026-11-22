from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.cases import CaseDesk
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
    request: Request,
    client: Client = Depends(rate_limit),
    db: Database = Depends(get_db),
    downstream: Downstream = Depends(get_downstream),
    runtime: RuntimeConfigStore = Depends(get_runtime),
    settings: Settings = Depends(get_settings),
) -> CheckRunner:
    transport = getattr(request.app.state, "transport", None)
    return CheckRunner(db=db, downstream=downstream, runtime=runtime, client=client,
                       cases=CaseDesk(db, settings, transport=transport))


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
