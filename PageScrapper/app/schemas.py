from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2048)
    render: Literal["auto", "never", "always"] = "auto"


class Page(BaseModel):
    url: str
    final_url: str
    title: str | None
    publisher: str | None
    published_at: date | None
    retrieved_at: datetime
    language: str | None
    text: str
    rendered: bool


class ScrapAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] | None = None
    max_items_per_source: int | None = Field(default=None, ge=1, le=500)


class RunOut(BaseModel):
    run_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    request: dict[str, Any]
    results: dict[str, Any]


class SourceOut(BaseModel):
    id: str
    name: str
    publisher: str
    source_type: str
    adapter: str
    url: str
    active: bool
    max_items: int
    license: str
    description: str
    configured: bool
    documents: int
    last_retrieved_at: datetime | None


class DocumentOut(BaseModel):
    id: int
    url: str
    source_id: str
    source_type: str
    title: str | None
    publisher: str | None
    published_at: date | None
    retrieved_at: datetime
    language: str | None
    content_hash: str
    extra: dict[str, Any] | None
    text: str | None = None
    preview: str | None = None


class CorpusStats(BaseModel):
    documents: int
    domain_count: int
    domains: dict[str, int]
    by_source_type: dict[str, int]
    by_source: dict[str, int]
    oldest_published_at: date | None
    newest_published_at: date | None
    documents_without_date: int


class DocumentList(BaseModel):
    total: int
    items: list[DocumentOut]


class LiveSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=3, max_length=2000)
    max_documents: int = Field(default=5, ge=1, le=10)


class LiveSearchSource(BaseModel):
    source_id: str
    name: str
    status: Literal["ok", "no_results", "skipped", "error"]
    found: int
    message: str | None = None


class LiveSearchDocument(BaseModel):
    url: str
    title: str | None
    publisher: str | None
    published_at: date | None
    source_id: str
    source_name: str
    source_type: str
    new: bool
    ingested: bool


class LiveSearchOut(BaseModel):
    query: str
    terms: list[str]
    sources: list[LiveSearchSource]
    documents: list[LiveSearchDocument]
    duration_ms: int
