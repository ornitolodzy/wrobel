"""Reconciliation with the source of truth: PageScrapper `documents` -> the graph.

Push (PageScrapper RAG_INGEST_URLS -> /ingest/document) is the fast path and can be lost (graph
down, Ollama tunnel down, network). This job guarantees convergence: it compares every scraper
document's `revision` with `Document.scraper_revision` in the graph, re-ingests what is missing or
older, embeds nodes without a current-model vector, and reports graph documents the scraper does not
have (never deletes them). Idempotent: running it twice changes nothing the second time.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

import httpx

from context_graph.config import Settings
from context_graph.db import Graph
from context_graph.embeddings import Embedder
from context_graph.extract import EntityExtractor
from context_graph.ingest import (
    CORPUS_VERSION_ID,
    canonical_url,
    embed_pending,
    mixed_vectors,
    remaining_embeddings,
    update_corpus_consistency,
    upsert_document,
)
from context_graph.models import DocumentIn, ReconcileResult

logger = logging.getLogger("context_graph.sync")

# PageScrapper DocumentOut -> DocumentIn (the same shape its RagClient pushes).
FIELDS = (
    "url",
    "source_id",
    "source_type",
    "title",
    "publisher",
    "published_at",
    "retrieved_at",
    "language",
    "text",
    "content_hash",
    "revision",
)
PAGE = 5000
LIST_LIMIT = 20  # urls kept in the report
EMBED_BATCH = 2000

_lock = threading.Lock()


def scraper_payload(doc: dict) -> dict:
    payload = {k: doc.get(k) for k in FIELDS}
    payload["claims"] = (doc.get("extra") or {}).get("claim_reviews") or []
    return payload


def scraper_revisions(client: httpx.Client) -> list[dict]:
    items: list[dict] = []
    while True:
        page = client.get("/documents/revisions", params={"limit": PAGE, "offset": len(items)}).raise_for_status()
        body = page.json()
        items += body["items"]
        if not body["items"] or len(items) >= body["total"]:
            return items


def graph_revisions(graph: Graph) -> dict[str, int | None]:
    rows = graph.read(
        "MATCH (d:Document) WHERE NOT coalesce(d.stub, false) RETURN d.url AS url, d.scraper_revision AS rev"
    )
    return {r["url"]: r["rev"] for r in rows}


def diff(scraper: list[dict], graph: dict[str, int | None]) -> tuple[list[dict], list[dict], list[str]]:
    """-> (missing, stale, orphan urls). Urls compared in the graph's canonical form."""
    latest: dict[str, dict] = {}
    for item in scraper:
        key = canonical_url(item["url"])
        if key not in latest or item["revision"] > latest[key]["revision"]:
            latest[key] = item
    missing = [i for k, i in latest.items() if k not in graph]
    stale = [i for k, i in latest.items() if k in graph and (graph[k] is None or graph[k] < i["revision"])]
    orphans = sorted(k for k in graph if k not in latest)
    return missing, stale, orphans


def _embed_all(graph: Graph, embedder: Embedder) -> None:
    """Embed every pending node; stops when nothing moves (e.g. the embedder is down)."""
    if embedder.model is None:
        return
    while True:
        res = embed_pending(graph, embedder, limit=EMBED_BATCH)
        if res.error or not (res.embedded + res.claims_embedded) or not (res.remaining + res.claims_remaining):
            return


def reconcile(
    graph: Graph,
    extractor: EntityExtractor,
    settings: Settings,
    embedder: Embedder,
    *,
    dry_run: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> ReconcileResult:
    if not settings.scraper_url:
        return ReconcileResult(dry_run=dry_run, error="SCRAPER_URL is not set", finished_at=datetime.now(UTC))
    if not _lock.acquire(blocking=False):
        return ReconcileResult(dry_run=dry_run, error="reconcile already running", finished_at=datetime.now(UTC))
    try:
        return _reconcile(graph, extractor, settings, embedder, dry_run, transport)
    finally:
        _lock.release()


def _reconcile(graph, extractor, settings, embedder, dry_run, transport) -> ReconcileResult:
    result = ReconcileResult(dry_run=dry_run)
    with httpx.Client(
        base_url=settings.scraper_url.rstrip("/"), timeout=settings.reconcile_timeout_s, transport=transport
    ) as client:
        try:
            scraper = scraper_revisions(client)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            result.error = f"scraper unavailable: {type(exc).__name__}: {exc}"[:300]
            result.finished_at = datetime.now(UTC)
            return result
        graph_revs = graph_revisions(graph)
        missing, stale, orphans = diff(scraper, graph_revs)
        result.scraper_documents = len(scraper)
        result.graph_documents = len(graph_revs)
        result.missing, result.stale = len(missing), len(stale)
        result.orphans, result.orphan_urls = len(orphans), orphans[:LIST_LIMIT]
        stamps = [i["updated_at"] for i in scraper if i.get("updated_at")]
        result.watermark = max(datetime.fromisoformat(s) for s in stamps) if stamps else None

        if not dry_run:
            for item in missing + stale:
                try:
                    body = client.get(f"/documents/{item['id']}").raise_for_status().json()
                    upsert_document(
                        graph, DocumentIn.model_validate(scraper_payload(body)), extractor, settings, embedder
                    )
                    result.ingested += 1
                except Exception as exc:  # one bad document must not stop the rest
                    logger.warning("Reconcile: %s failed: %s", item["url"], exc)
                    result.failed += 1
                    if len(result.failed_urls) < LIST_LIMIT:
                        result.failed_urls.append(item["url"])
            _embed_all(graph, embedder)

    model = embedder.model
    if model is not None:
        result.unembedded_chunks, result.unembedded_claims = remaining_embeddings(graph, model)
        result.consistent = mixed_vectors(graph, model) == 0 if dry_run else update_corpus_consistency(graph, model)
    pending = result.missing + result.stale - result.ingested if not dry_run else result.missing + result.stale
    result.lag = max(pending, 0) + result.failed + result.unembedded_chunks + result.unembedded_claims
    if not dry_run and result.lag == 0:
        # Watermark for seeds / audits: the graph matched the scraper up to this point.
        graph.write(
            """
            MATCH (v:CorpusVersion {id: $id})
            SET v.scraper_synced_at = datetime(), v.scraper_updated_at_max = $wm, v.scraper_documents = $n
            """,
            id=CORPUS_VERSION_ID,
            wm=result.watermark,
            n=result.scraper_documents,
        )
    result.finished_at = datetime.now(UTC)
    logger.info(
        "Reconcile: missing=%d stale=%d ingested=%d failed=%d orphans=%d unembedded=%d lag=%d",
        result.missing,
        result.stale,
        result.ingested,
        result.failed,
        result.orphans,
        result.unembedded_chunks + result.unembedded_claims,
        result.lag,
    )
    return result
