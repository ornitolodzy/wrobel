from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from app.config import Settings
from app.db import Database
from app.deps import Client, get_db, get_downstream, get_settings, rate_limit, require_admin
from app.schemas import OcrResponse
from app.services import Downstream, DownstreamError
from app.validation import validate_image

router = APIRouter(prefix="/api/v1", tags=["services"])

_ERROR_HTTP = {"api_limit": 503, "timeout": 504}


def _to_http(exc: DownstreamError) -> HTTPException:
    return HTTPException(
        status_code=_ERROR_HTTP.get(exc.code, 502),
        detail={"status": exc.status.value, "code": exc.code, "message": exc.message},
    )


@router.post("/scrap/all", summary="Uruchamia pobranie wszystkich źródeł do bazy wiedzy (admin)")
async def scrap_all(
    body: dict[str, Any] = Body(default_factory=dict),
    client: Client = Depends(require_admin),
    downstream: Downstream = Depends(get_downstream),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await downstream.scrap_all(body)
    except DownstreamError as exc:
        await db.log_event("downstream_error", client=client.name, detail={"service": exc.service, "code": exc.code})
        raise _to_http(exc) from exc
    await db.log_event("scrap_all_triggered", client=client.name, detail={"mock": bool(result.get("mock"))})
    return result


@router.post("/text-recognition-from-img", response_model=OcrResponse, summary="Odczytuje tekst z obrazu bez weryfikacji")
async def text_recognition_from_img(
    file: UploadFile = File(...),
    client: Client = Depends(rate_limit),
    downstream: Downstream = Depends(get_downstream),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OcrResponse:
    data = await file.read(settings.max_image_size_bytes + 1)
    mime = validate_image(data, settings.max_image_size_bytes)
    filename = (file.filename or "image").replace("\\", "/").rsplit("/", 1)[-1][:128]
    try:
        result = await downstream.ocr(filename, data, mime)
    except DownstreamError as exc:
        await db.log_event("downstream_error", client=client.name, detail={"service": exc.service, "code": exc.code})
        raise _to_http(exc) from exc
    await db.log_event("ocr_request", client=client.name, detail={"mime": mime, "bytes": len(data), "mock": result.mock})
    return result
