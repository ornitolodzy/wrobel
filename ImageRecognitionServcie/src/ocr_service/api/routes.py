"""HTTP endpoints."""

from __future__ import annotations

import base64
import binascii
import sys
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from ocr_service.callbacks import CallbackDispatcher
from ocr_service.config import Settings
from ocr_service.errors import ApiError, error_response
from ocr_service.imaging import ensure_supported_format
from ocr_service.schemas import (
    AcceptedResponse,
    ErrorResponse,
    HealthResponse,
    OcrJsonRequest,
    OcrResponse,
    OutputMode,
    ReadyResponse,
)
from ocr_service.security import require_api_key
from ocr_service.service import OcrService, ServiceState

router = APIRouter()
v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


class InMemoryMultiPartParser(MultiPartParser):
    """Starlette spools uploads > 1 MB to a temp file; keep them in RAM (body size is capped)."""

    spool_max_size = sys.maxsize


def _error(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


def get_service(request: Request) -> OcrService:
    service: OcrService = request.app.state.service
    return service


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


# ---------------------------------------------------------------- probes & metrics
@router.get("/healthz", response_model=HealthResponse, tags=["ops"], summary="Liveness probe")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadyResponse,
    tags=["ops"],
    summary="Readiness probe (200 only after the model is loaded)",
    responses={503: _error("Model still loading or failed to load.")},
)
async def readyz(service: Annotated[OcrService, Depends(get_service)]) -> Any:
    if not service.ready:
        if service.state is ServiceState.FAILED:
            return error_response(503, "engine_failed", f"Engine failed to load: {service.error}")
        return error_response(503, "not_ready", f"Engine state: {service.state.value}.")
    return ReadyResponse(
        status="ready",
        engine=service.engine_name,
        executor=type(service.backend).__name__.removesuffix("Backend").lower(),
        workers=service.capacity,
        workers_ready=service.backend.ready_workers,
        langs=sorted(service.supported_langs),
    )


@router.get("/metrics", tags=["ops"], summary="Prometheus metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------- OCR
_OCR_REQUEST_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                    "langs": {"type": "string", "example": "pl,en"},
                    "output": {"type": "string", "enum": ["text", "blocks"]},
                },
            }
        },
        "application/json": {"schema": {"$ref": "#/components/schemas/OcrJsonRequest"}},
    },
}


@v1.post(
    "/ocr",
    response_model=OcrResponse,
    tags=["ocr"],
    summary="Recognise text in an image",
    description=(
        "Send the image either as `multipart/form-data` (field `file`) or as JSON "
        '`{"image": "<base64>"}`. `langs`/`output` can be passed as query parameters '
        "or inside the body (body wins). Accepted formats: PNG, JPEG, WebP (detected by content)."
        "\n\n**Callback mode** (`OCR_CALLBACK_URL` set): the endpoint answers `202` with a "
        "`request_id` and the result is POSTed to the configured URL as `CallbackPayload`. "
        "Format/size/language errors are still returned synchronously."
    ),
    openapi_extra={"requestBody": _OCR_REQUEST_BODY},
    responses={
        202: {"model": AcceptedResponse, "description": "Callback mode: job accepted."},
        400: _error("Corrupted image, invalid base64 or malformed multipart body."),
        401: _error("Missing or invalid X-API-Key."),
        413: _error("File, body or pixel count exceeds the configured limits."),
        415: _error("Unsupported content type or image format."),
        422: _error("Invalid parameters (e.g. unsupported language, missing file)."),
        500: _error("Unexpected engine failure."),
        503: _error("Engine not ready or overloaded (see Retry-After)."),
    },
)
async def ocr(
    request: Request,
    service: Annotated[OcrService, Depends(get_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    langs: Annotated[
        str | None,
        Query(
            description="Comma-separated languages, default: all configured (pl,en).",
            examples=["pl,en"],
        ),
    ] = None,
    output: Annotated[OutputMode | None, Query(description="`blocks` (default) or `text`.")] = None,
) -> JSONResponse:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "multipart/form-data":
        data, body_langs, body_output = await _read_multipart(request, settings)
    elif content_type == "application/json":
        data, body_langs, body_output = await _read_json(request, settings)
    else:
        raise ApiError(
            415,
            "unsupported_media_type",
            "Content-Type must be multipart/form-data or application/json.",
        )

    mode = body_output or output or OutputMode.BLOCKS
    dispatcher: CallbackDispatcher | None = request.app.state.dispatcher
    if dispatcher is not None:
        ensure_supported_format(data)
        request_id = dispatcher.submit(data, body_langs or langs, mode)
        accepted = AcceptedResponse(request_id=request_id)
        return JSONResponse(accepted.model_dump(), status_code=202)

    result = await service.recognize(data, langs=body_langs or langs, output=mode)
    exclude = {"blocks"} if mode is OutputMode.TEXT else set()
    return JSONResponse(result.model_dump(exclude=exclude))


async def _read_multipart(
    request: Request, settings: Settings
) -> tuple[bytes, str | None, OutputMode | None]:
    parser = InMemoryMultiPartParser(
        request.headers, request.stream(), max_files=1, max_fields=8, max_part_size=4096
    )
    try:
        form = await parser.parse()
    except MultiPartException as exc:
        raise ApiError(400, "invalid_multipart", str(exc)) from exc
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(422, "missing_file", "Multipart field 'file' with an image is required.")
        data = await upload.read()
        langs = form.get("langs")
        output = form.get("output")
    finally:
        await form.close()

    _check_size(len(data), settings)
    return data, _str_or_none(langs), _parse_output(output)


async def _read_json(
    request: Request, settings: Settings
) -> tuple[bytes, str | None, OutputMode | None]:
    body = await request.body()
    # JSON parsing + base64 decoding of a multi-MB body is CPU work: keep it off the event loop.
    return await anyio.to_thread.run_sync(_decode_json_body, body, settings)


def _decode_json_body(
    body: bytes, settings: Settings
) -> tuple[bytes, str | None, OutputMode | None]:
    try:
        payload = OcrJsonRequest.model_validate_json(body)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in exc.errors()
        )
        raise ApiError(422, "validation_error", details) from exc

    encoded = payload.image.strip()
    if encoded.startswith("data:"):
        _, _, encoded = encoded.partition(",")
    encoded = "".join(encoded.split())
    # Reject oversized payloads before decoding (exact decoded length of padded base64).
    _check_size(len(encoded) * 3 // 4 - encoded[-2:].count("="), settings)
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(400, "invalid_base64", "Field 'image' is not valid base64.") from exc

    _check_size(len(data), settings)
    return data, payload.langs, payload.output


def _check_size(size: int, settings: Settings) -> None:
    if size > settings.max_file_bytes:
        raise ApiError(
            413,
            "file_too_large",
            f"Image is {size} bytes, the limit is {settings.max_file_bytes} bytes.",
        )


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _parse_output(value: object) -> OutputMode | None:
    text = _str_or_none(value)
    if text is None:
        return None
    try:
        return OutputMode(text.strip().lower())
    except ValueError:
        raise ApiError(
            422, "validation_error", "Field 'output' must be 'text' or 'blocks'."
        ) from None
