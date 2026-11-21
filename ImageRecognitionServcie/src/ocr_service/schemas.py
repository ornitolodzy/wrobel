"""Public API models (also drive the OpenAPI schema at /docs)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OutputMode(StrEnum):
    TEXT = "text"
    BLOCKS = "blocks"


class Block(BaseModel):
    text: str = Field(examples=["Zażółć gęślą jaźń"])
    bbox: list[int] = Field(
        min_length=4,
        max_length=4,
        description="[x1, y1, x2, y2] in pixels of the original image.",
        examples=[[12, 40, 230, 68]],
    )
    confidence: float = Field(ge=0.0, le=1.0, examples=[0.98])


class OcrResponse(BaseModel):
    text: str = Field(description="Full text in reading order (lines separated by \\n).")
    lang: str | None = Field(
        description="Detected dominant language among the requested ones (null if no text).",
        examples=["pl"],
    )
    blocks: list[Block] | None = Field(default=None, description="Present only when output=blocks.")
    engine: str = Field(examples=["paddleocr"])
    duration_ms: int = Field(description="Server-side processing time.", examples=[412])


class AcceptedResponse(BaseModel):
    status: str = Field(default="accepted", examples=["accepted"])
    request_id: str = Field(
        description="Correlates this request with the callback; set it via X-Request-ID."
    )


class CallbackError(BaseModel):
    code: str
    message: str


class CallbackPayload(BaseModel):
    """JSON body POSTed to OCR_CALLBACK_URL."""

    status: str = Field(description="`ok` or `error`.", examples=["ok"])
    request_id: str
    text: str | None = None
    lang: str | None = None
    blocks: list[Block] | None = None
    engine: str | None = None
    duration_ms: int | None = None
    error: CallbackError | None = None


class OcrJsonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(
        min_length=1,
        description="Base64-encoded PNG/JPEG/WebP; data URLs (data:image/png;base64,...) work too.",
    )
    langs: str | None = Field(default=None, examples=["pl,en"])
    output: OutputMode | None = None


class ErrorDetail(BaseModel):
    code: str = Field(examples=["unsupported_media_type"])
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])


class ReadyResponse(BaseModel):
    status: str = Field(examples=["ready"])
    engine: str
    executor: str
    workers: int
    workers_ready: int
    langs: list[str]
    delivery: str = Field(
        description="`response` (result in the HTTP response) or `callback` (POSTed to a URL).",
        examples=["response"],
    )
