from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

from app.config import Settings
from app.db import Database
from app.deps import Client, get_db, get_downstream, get_runtime, get_settings, rate_limit
from app.runtime_config import RuntimeConfigStore
from app.schemas import CheckResponse, ClaimVerifyRequest, UrlVerifyRequest
from app.services import Downstream
from app.validation import validate_image, validate_public_url, validate_text
from app.verification import CheckRunner

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
