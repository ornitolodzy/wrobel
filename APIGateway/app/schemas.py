from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Verdict(str, Enum):

    confirmed = "confirmed"
    false = "false"
    misleading = "misleading"
    insufficient_evidence = "insufficient_evidence"
    not_verifiable = "not_verifiable"


class CheckStatus(str, Enum):

    completed = "completed"
    needs_clarification = "needs_clarification"
    error = "error"
    api_limit = "api_limit"


class InputType(str, Enum):
    claim = "claim"
    url = "url"
    image = "image"


class ResolvedOptions(BaseModel):

    language: str
    max_claims: int
    max_sources: int


class RuntimeConfig(BaseModel):

    llm_provider: str
    llm_model: str
    default_language: str
    default_max_claims: int
    default_max_sources: int
    mock_mode: bool


class RuntimeConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_provider: str | None = Field(default=None, min_length=1, max_length=64)
    llm_model: str | None = Field(default=None, min_length=1, max_length=128)
    default_language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    default_max_claims: int | None = Field(default=None, ge=1, le=10)
    default_max_sources: int | None = Field(default=None, ge=1, le=20)
    mock_mode: bool | None = None


class ClaimVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=3)


class UrlVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8)


class Source(BaseModel):
    title: str
    publisher: str
    url: str
    published_at: str | None = None
    retrieved_at: str | None = None
    fragment: str | None = Field(default=None)
    source_type: str | None = Field(default=None)


class ClaimResult(BaseModel):
    claim: str
    verdict: Verdict
    explanation: str
    evidence_strength: Literal["none", "weak", "moderate", "strong"] | None = None
    strength_rationale: str | None = Field(default=None)
    sources: list[Source] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ErrorInfo(BaseModel):
    code: str
    message: str


class ModelInfo(BaseModel):
    provider: str
    name: str


class CheckResponse(BaseModel):
    check_id: str
    status: CheckStatus
    input_type: InputType
    channel: str
    created_at: datetime
    duration_ms: int
    claims: list[ClaimResult] = Field(default_factory=list)
    summary: str | None = None
    limitations: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    ocr_text: str | None = None
    model: ModelInfo | None = None
    mock: bool = False
    pii_masked: bool = False
    error: ErrorInfo | None = None


class OcrResponse(BaseModel):
    text: str
    confidence: float | None = None
    language: str | None = None
    limitations: list[str] = Field(default_factory=list)
    mock: bool = False


class ServiceHealth(BaseModel):
    name: str
    configured: bool
    status: Literal["ok", "down", "mock"]
    detail: str | None = None


class DeleteResponse(BaseModel):
    check_id: str
    deleted: bool


class LlmVerifyResponse(BaseModel):
    status: Literal["completed", "needs_clarification"] = "completed"
    claims: list[ClaimResult] = Field(default_factory=list)
    summary: str | None = None
    limitations: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


JsonDict = dict[str, Any]
