"""Data contracts shared by all pipeline steps.

Every node takes and returns these types, so steps can be developed, tested
and swapped independently (see wiki: prawdowid-verification-pipeline).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ClaimType = Literal["factual", "opinion", "prediction", "unclear"]
StanceLabel = Literal["supports", "refutes", "missing_context", "irrelevant"]
VerdictLabel = Literal[
    "potwierdzone", "falszywe", "wprowadzajace_w_blad", "brak_dowodow", "nie_podlega_weryfikacji"
]
SourceType = Literal["primary", "factcheck", "secondary"]
Strength = Literal["wysoka", "średnia", "niska"]
# Technical status is NOT a verdict (scenario J).
Status = Literal["ok", "error", "api_limit", "needs_clarification"]


class NormalizedInput(BaseModel):
    text: str
    channel: str = "web"
    language: str = "pl"
    pii_masked: bool = False
    injection_suspected: bool = False


class Claim(BaseModel):
    id: str
    text: str  # self-contained, unambiguous statement
    type: ClaimType
    entities: list[str] = Field(default_factory=list)
    time_ref: str | None = None


class Query(BaseModel):
    claim_id: str
    dense: str  # for vector search
    sparse: str  # entities + keywords + numbers for full-text


class Evidence(BaseModel):
    chunk_id: str
    doc_id: str
    source_type: SourceType
    publisher: str
    title: str
    url: str
    published_at: datetime | None = None
    fetched_at: datetime
    dup_group: str  # reprint group -> counting independent confirmations
    text: str
    rerank_score: float


class ClaimEvidence(BaseModel):
    claim_id: str
    evidence: list[Evidence] = Field(default_factory=list)
    early_stop: bool = False  # best rerank score below threshold -> skip LLM


class Stance(BaseModel):
    claim_id: str
    chunk_id: str
    label: StanceLabel
    quote: str  # VERBATIM fragment of Evidence.text
    rationale: str


class Verdict(BaseModel):
    claim_id: str
    label: VerdictLabel
    strength: Strength
    reasons: list[str]  # explicit premises from rules, not from the LLM
    evidence: list[Stance] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CheckResult(BaseModel):
    check_id: str
    status: Status
    claims: list[Claim] = Field(default_factory=list)
    verdicts: list[Verdict] = Field(default_factory=list)
    answer: str = ""
    limitations: list[str] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
