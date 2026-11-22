"""API / ingestion contracts.

`ContextItem` deliberately mirrors `Evidence` in Prawdowid (src/prawdowid/models.py):
chunk_id, doc_id, url, title, publisher, source_type, published_at, dup_group, text,
so a `Retriever` adapter there is a field mapping.

`DocumentIn` accepts both the PageScrapper payload (`RAG_INGEST_URL`: source_id, retrieved_at,
language, content_hash, catalog types official / fact_check / disinfo_db) and the original
`/ingest/page` shape (links, corrects, rating, contract types primary / factcheck / ...).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

ContractSourceType = Literal["primary", "factcheck", "secondary", "web"]
Section = Literal["tresc", "naglowek", "ocena", "ocr"]
EntityType = Literal["PERSON", "ORG", "GPE", "LOC", "DATE", "LAW", "MISC"]
ChunkRelation = Literal["SUPPORTS", "CONTRADICTS"]
DocumentRelation = Literal["CORRECTS", "CITES", "REPRINTS"]


ClaimOrigin = Literal["claimreview", "google_factcheck", "llm"]


class ClaimIn(BaseModel):
    """A claim checked by the fact-checker that published the document (PageScrapper `claim_reviews`).

    `claimreview` / `google_factcheck`: declared by the fact-checker in schema.org ClaimReview, so the
    claimant may become an ATTRIBUTED_TO relation. `llm`: extracted by a model, never attributed.
    """

    text: str = Field(min_length=1)
    claimant: str | None = None
    claimant_type: str | None = None  # schema.org @type of the author: Person / Organization
    appearances: list[str] = Field(default_factory=list)  # where the claim circulated (posts, reels, archives)
    rating: str | None = None  # fact-checker label as published ("fałsz", "Przerobione zdjęcie")
    claim_date: date | None = None
    review_date: date | None = None
    origin: ClaimOrigin = "claimreview"

    @field_validator("claim_date", "review_date", mode="before")
    @classmethod
    def _lenient_date(cls, value):
        """A malformed date in third-party markup must not reject the whole document."""
        if value in (None, ""):
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


class DocumentIn(BaseModel):
    """One document from PageScrapper (or the legacy `/ingest/page` shape)."""

    url: str
    text: str
    title: str | None = ""
    publisher: str | None = ""
    source_id: str | None = None  # PageScrapper catalog id (sources.yaml); Source key when present
    domain: str | None = None  # derived from url when missing
    source_type: str = "web"  # catalog (official / fact_check / disinfo_db) or contract type, mapped on ingest
    published_at: datetime | date | None = None
    fetched_at: datetime | None = Field(default=None, validation_alias=AliasChoices("fetched_at", "retrieved_at"))
    language: str | None = None
    content_hash: str | None = None  # PageScrapper hash (title + date + text), kept for audit
    license: str | None = None
    links: list[str] = Field(default_factory=list)  # outgoing links -> CITES
    corrects: list[str] = Field(default_factory=list)  # urls this document corrects -> CORRECTS
    rating: str | None = None  # fact-checker label exactly as on the page ("Fałsz") -> section "ocena"
    claims: list[ClaimIn] = Field(default_factory=list)  # -> (:Document)-[:CHECKS]->(:CheckedClaim)


PageIn = DocumentIn


class ImageIn(BaseModel):
    """OCR result from ImageRecognitionService `/text-recognition-from-img/`."""

    image_id: str
    path: str  # FileStorage path
    ocr_text: str
    url: str | None = None
    page_url: str | None = None  # document the image was found on -> HAS_IMAGE


class RelationIn(BaseModel):
    """Externally derived relation (e.g. LLM in batch). Interpretation, not fact: needs a quote."""

    type: ChunkRelation | DocumentRelation
    src: str  # chunk id (SUPPORTS/CONTRADICTS) or document url/id (CORRECTS/CITES/REPRINTS)
    dst: str
    quote: str  # verbatim from the src chunk / document
    method: str = "manual"
    model: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class IngestResult(BaseModel):
    id: str
    chunks: int
    entities: int
    embedded: int = 0
    relations: dict[str, int] = Field(default_factory=dict)


class EmbeddingBackfillResult(BaseModel):
    embedded: int  # chunks
    remaining: int
    claims_embedded: int = 0  # CheckedClaim.text
    claims_remaining: int = 0
    model: str | None
    error: str | None = None


class ContextQuery(BaseModel):
    text: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)  # extra entity names from the caller (e.g. NER)
    hops: int = Field(default=2, ge=1, le=3)
    limit: int = Field(default=10, ge=1, le=50)
    save: bool = True  # persist Claim -[:CONTEXT]-> Chunk


class EntityRef(BaseModel):
    key: str
    name: str
    type: str
    found: bool  # present in the graph


class ContextItem(BaseModel):
    chunk_id: str
    text: str
    section: str
    doc_id: str | None = None
    url: str | None = None
    title: str | None = None
    publisher: str | None = None
    source_type: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    dup_group: str | None = None  # original document id when this document reprints another
    score: float
    hops: int  # 0 = direct hit
    path: list[str]  # human-readable relation path that led here
    flags: list[str] = Field(default_factory=list)  # corrected / corrects / contradicted / ...


class RelationOut(BaseModel):
    type: str
    src: str
    dst: str
    quote: str | None = None
    method: str | None = None


class EventOut(BaseModel):
    """Layer-2 event evidenced by one of the returned chunks (who did what, when, where + verbatim quote)."""

    id: str
    action: str
    kind: str
    chunk_id: str
    quote: str
    actors: list[dict] = Field(default_factory=list)
    date: str | None = None
    date_inferred: bool = False
    date_text: str | None = None
    place: str | None = None


class ContextResponse(BaseModel):
    claim_id: str
    entities: list[EntityRef]
    items: list[ContextItem]
    relations: list[RelationOut]  # relations between the returned items/documents
    events: list[EventOut] = Field(default_factory=list)
