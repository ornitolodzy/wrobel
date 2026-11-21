"""Optional API-key authentication (``X-API-Key``), enabled when ``OCR_API_KEY`` is set."""

from __future__ import annotations

import hmac

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from ocr_service.config import Settings
from ocr_service.errors import ApiError

API_KEY_HEADER = "X-API-Key"

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER,
    auto_error=False,
    description="Required only when the server has OCR_API_KEY configured.",
)


async def require_api_key(request: Request, api_key: str | None = Security(api_key_header)) -> None:
    settings: Settings = request.app.state.settings
    if settings.api_key is None:
        return
    if not api_key:
        raise ApiError(401, "missing_api_key", f"Missing {API_KEY_HEADER} header.")
    expected = settings.api_key.get_secret_value().encode()
    if not hmac.compare_digest(api_key.encode(), expected):
        raise ApiError(401, "invalid_api_key", "Invalid API key.")
