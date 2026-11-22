"""Data contracts shared by all pipeline steps.

Every node takes and returns these types, so steps can be developed, tested
and swapped independently (see wiki: prawdowid-verification-pipeline).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ClaimType = Literal["factual", "opinion", "prediction", "unclear"]
# announced = the source only announces / plans / schedules the event, it does not show it happened
StanceLabel = Literal["supports", "refutes", "missing_context", "announced", "irrelevant"]
VerdictLabel = Literal[
    "potwierdzone", "falszywe", "wprowadzajace_w_blad", "brak_dowodow", "nie_podlega_weryfikacji"
]
SourceType = Literal["primary", "factcheck", "secondary", "web"]  # web = publisher outside the catalog
Strength = Literal["wysoka", "średnia", "niska"]
# What a verified quote entitles us to say (citation validator, level 2):
#   ocena   - a fact-checker's rating label ("Fałsz")     -> "X ocenił ... jako fałsz"
#   tresc   - a sentence of the source text               -> attributed content
#   naglowek- the page title / headline                   -> exact wording of the headline
QuoteKind = Literal["ocena", "tresc", "naglowek"]
# Short answer shown first (derived from verdict + whether the user asked a question).
Lead = Literal[
    "Tak.", "Nie.", "Prawda.", "Fałsz.", "Wprowadzające w błąd.", "Nie znaleziono informacji.",
    "Nie da się tego jednoznacznie potwierdzić.", "Niemożliwe do weryfikacji.",
]
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
    # The user asked a question ("Czy PAP usunęła...?"). `text` keeps the question's
    # proposition as asked, negation included, so "Tak/Nie" answers the question directly.
    is_question: bool = False
    # What kind of authoritative document would settle the claim (said when nothing is found).
    needed_source: str | None = None


class Query(BaseModel):
    claim_id: str
    dense: str  # for vector search
    sparse: str  # entities + keywords + numbers for full-text


class RetrievalTrace(BaseModel):
    """Why this chunk was retrieved: every ranking stage leaves its numbers (explainability)."""

    found_by: list[str] = Field(default_factory=list)  # dense | sparse | graph
    dense_rank: int | None = None
    dense_score: float | None = None
    sparse_rank: int | None = None
    sparse_score: float | None = None
    matched: str | None = None  # full-text match with <b>highlights</b>
    rrf: float | None = None
    rerank_score: float | None = None  # cross-encoder, if enabled
    final_rank: int | None = None
    corpus_version: int | str | None = None   # Postgres: serial id; Neo4j graph: e.g. "current"
    neighbours: list[int] = Field(default_factory=list)  # positions of chunks glued as context


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
    section: QuoteKind = "tresc"  # which part of the page this chunk is
    http_status: int | None = None  # observed when we fetched the page (availability)
    via_url: str | None = None  # page that linked here (fact-checker -> primary source)
    retrieval: RetrievalTrace | None = None


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


# podlinkowane = primary material a fact-checker links to; shown for provenance, not judged
SourceRole = Literal["potwierdza", "zaprzecza", "brak_kontekstu", "zapowiedz", "podlinkowane"]
ROLE_OF_STANCE: dict[str, SourceRole] = {
    "supports": "potwierdza", "refutes": "zaprzecza", "missing_context": "brak_kontekstu",
    "announced": "zapowiedz",
}


class CitedQuote(BaseModel):
    kind: QuoteKind
    text: str  # passed the citation validator
    rationale: str = ""


class SourceRef(BaseModel):
    """A source the verdict rests on: what it says (verified verbatim quote) and where it is."""

    n: int  # number used in the answer: [1], [2]...
    role: SourceRole
    publisher: str
    title: str
    url: str
    source_type: SourceType
    published_at: datetime | None = None
    fetched_at: datetime
    doc_id: str
    chunk_id: str
    quote: str  # passed the citation validator
    rationale: str
    kind: QuoteKind = "tresc"
    quotes: list[CitedQuote] = Field(default_factory=list)  # all verified quotes from this URL
    http_status: int | None = None
    via: int | None = None  # n of the source that links to this one (chain of sources)
    via_url: str | None = None


class Verdict(BaseModel):
    claim_id: str
    label: VerdictLabel
    lead: Lead = "Nie znaleziono informacji."
    strength: Strength
    reasons: list[str]  # explicit premises from rules, not from the LLM
    evidence: list[Stance] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)  # numbered, rendered in the answer
    warnings: list[str] = Field(default_factory=list)
    # "Why" sentences of the answer (LLM, checked against the sources, or the template), set by compose.
    explanation: str | None = None


class CheckResult(BaseModel):
    check_id: str
    status: Status
    claims: list[Claim] = Field(default_factory=list)
    verdicts: list[Verdict] = Field(default_factory=list)
    answer: str = ""
    limitations: list[str] = Field(default_factory=list)
    # Security flag for the gateway audit log: a manipulation attempt in the input is treated as
    # data, but the event must be recorded (task §6, scenario E).
    injection_suspected: bool = False
    trace: list[dict] = Field(default_factory=list)
