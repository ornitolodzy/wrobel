from collections import Counter
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.catalog import SourceConfig
from app.config import Settings
from app.db import Document
from app.schemas import CorpusStats, DocumentList, DocumentOut, DocumentRevision, RevisionList, SourceOut
from app.sources.file_import import resolve_import_path

router = APIRouter(tags=["catalog"])


def _is_configured(source: SourceConfig, settings: Settings) -> bool:
    if source.adapter == "google_factcheck":
        return bool(settings.google_factcheck_api_key)
    if source.adapter == "euvsdisinfo_mcp":
        return bool(settings.euvsdisinfo_token)
    if source.adapter == "file_import":
        try:
            return resolve_import_path(settings.imports_dir, source.options.get("file", "")).is_file()
        except ValueError:
            return False
    return True


def _doc_out(doc: Document, full: bool) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        url=doc.url,
        source_id=doc.source_id,
        source_type=doc.source_type,
        title=doc.title,
        publisher=doc.publisher,
        published_at=doc.published_at,
        retrieved_at=doc.retrieved_at,
        language=doc.language,
        content_hash=doc.content_hash,
        extra=doc.extra,
        revision=doc.revision or 1,
        updated_at=doc.updated_at,
        text=doc.text if full else None,
        preview=None if full else doc.text[:300],
    )


@router.get("/sources", response_model=list[SourceOut], summary="Zwraca jawny katalog źródeł (do audytu)")
async def list_sources(request: Request) -> list[SourceOut]:
    state = request.app.state
    stats = await state.db.stats_by_source()
    out = []
    for s in state.catalog.sources:
        configured = _is_configured(s, state.settings)
        st = stats.get(s.id, {})
        out.append(
            SourceOut(
                id=s.id,
                name=s.name,
                publisher=s.publisher,
                source_type=s.source_type,
                adapter=s.adapter,
                url=s.url,
                active=s.active,
                max_items=s.max_items,
                license=s.license,
                description=s.description,
                configured=configured,
                documents=st.get("documents", 0),
                last_retrieved_at=st.get("last_retrieved_at"),
            )
        )
    return out


@router.get("/stats", response_model=CorpusStats, summary="Zwraca statystyki korpusu: dokumenty, domeny, typy źródeł")
async def corpus_stats(request: Request) -> CorpusStats:
    rows = await request.app.state.db.corpus_rows()
    domains = Counter(urlsplit(url).netloc for url, _, _, _ in rows)
    dates = [d for _, _, _, d in rows if d is not None]
    return CorpusStats(
        documents=len(rows),
        domain_count=len(domains),
        domains=dict(domains.most_common()),
        by_source_type=dict(Counter(t for _, _, t, _ in rows)),
        by_source=dict(Counter(s for _, s, _, _ in rows)),
        oldest_published_at=min(dates) if dates else None,
        newest_published_at=max(dates) if dates else None,
        documents_without_date=len(rows) - len(dates),
    )


@router.get("/documents", response_model=DocumentList, summary="Zwraca zebrane dokumenty (bez pełnej treści)")
async def list_documents(
    request: Request,
    source_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DocumentList:
    total, docs = await request.app.state.db.list_documents(source_id, limit, offset)
    return DocumentList(total=total, items=[_doc_out(d, full=False) for d in docs])


@router.get(
    "/documents/revisions",
    response_model=RevisionList,
    summary="Rewizje wszystkich dokumentów (do rekoncyliacji odbiorców, np. ContextGraph)",
)
async def document_revisions(
    request: Request,
    limit: int = Query(default=5000, ge=1, le=20000),
    offset: int = Query(default=0, ge=0),
) -> RevisionList:
    total, rows = await request.app.state.db.revisions(limit, offset)
    return RevisionList(
        total=total,
        items=[DocumentRevision(id=i, url=u, revision=r or 1, updated_at=t) for i, u, r, t in rows],
    )


@router.get("/documents/{doc_id}", response_model=DocumentOut, summary="Zwraca jeden dokument z pełną treścią")
async def get_document(doc_id: int, request: Request) -> DocumentOut:
    doc = await request.app.state.db.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono dokumentu.")
    return _doc_out(doc, full=True)
