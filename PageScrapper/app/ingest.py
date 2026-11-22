import asyncio
import logging
import uuid
from typing import Any

import httpx

from app.catalog import Catalog, SourceConfig
from app.config import Settings
from app.db import Database, Document, ScrapeRun, utcnow
from app.fetch import FetchError, Fetcher
from app.render import Renderer
from app.sources import ADAPTERS
from app.sources.base import AdapterContext, SourceSkipped

logger = logging.getLogger("scraper.ingest")

_MIN_DOCUMENT_CHARS = 50
_MIN_RECORD_CHARS = 10


class RunAlreadyActive(Exception):
    pass


class UnknownSource(Exception):
    pass


async def schedule_loop(ingestor: "Ingestor", interval_minutes: float, run_on_startup: bool) -> None:
    if not run_on_startup:
        await asyncio.sleep(interval_minutes * 60)
    while True:
        try:
            run = await ingestor.start(None, None, trigger="schedule")
            logger.info("Scheduled scrape started: %s", run.id)
        except RunAlreadyActive:
            logger.info("Scheduled scrape skipped - another run is active")
        except Exception:
            logger.exception("Scheduled scrape failed to start")
        await asyncio.sleep(interval_minutes * 60)


class RagClient:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = url
        self.client = httpx.AsyncClient(timeout=30, transport=transport)

    async def send(self, doc: Document) -> bool:
        payload = {
            "url": doc.url,
            "source_id": doc.source_id,
            "source_type": doc.source_type,
            "title": doc.title,
            "publisher": doc.publisher,
            "published_at": doc.published_at.isoformat() if doc.published_at else None,
            "retrieved_at": doc.retrieved_at.isoformat(),
            "language": doc.language,
            "text": doc.text,
            "content_hash": doc.content_hash,
            # Fact-checker's own ClaimReview (app/claimreview.py) -> ContextGraph CheckedClaim.
            # Receivers without the field ignore it (VerificationPipeline /ingest).
            "claims": (doc.extra or {}).get("claim_reviews") or [],
        }
        try:
            response = await self.client.post(self.url, json=payload)
            return response.status_code < 400
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self.client.aclose()


class Ingestor:
    def __init__(
        self,
        db: Database,
        fetcher: Fetcher,
        renderer: Renderer,
        settings: Settings,
        catalog: Catalog,
        rag: RagClient | None = None,
    ) -> None:
        self.db = db
        self.fetcher = fetcher
        self.renderer = renderer
        self.settings = settings
        self.catalog = catalog
        self.rag = rag
        self._running = False
        self.task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._running

    def select_sources(self, source_ids: list[str] | None) -> list[SourceConfig]:
        if not source_ids:
            return [s for s in self.catalog.sources if s.active]
        selected = []
        for source_id in source_ids:
            source = self.catalog.get(source_id)
            if source is None:
                raise UnknownSource(source_id)
            selected.append(source)
        return selected

    async def start(self, source_ids: list[str] | None, max_items: int | None, trigger: str = "api") -> ScrapeRun:
        if self._running:
            raise RunAlreadyActive()
        sources = self.select_sources(source_ids)
        self._running = True
        run = ScrapeRun(
            id=str(uuid.uuid4()),
            started_at=utcnow(),
            status="running",
            request={"source_ids": [s.id for s in sources], "max_items_per_source": max_items, "trigger": trigger},
            results={s.id: {"status": "pending"} for s in sources},
        )
        try:
            await self.db.save_run(run)
        except Exception:
            self._running = False
            raise
        self.task = asyncio.create_task(self._execute(run, sources, max_items))
        return run

    async def _execute(self, run: ScrapeRun, sources: list[SourceConfig], max_items: int | None) -> None:
        try:
            for source in sources:
                result = await self._run_source(source, max_items)
                run.results = {**run.results, source.id: result}
                await self.db.save_run(run)
            failed = any(r.get("status") == "error" for r in run.results.values())
            run.status = "finished_with_errors" if failed else "finished"
        except Exception:
            logger.exception("Scrape run %s crashed", run.id)
            run.status = "crashed"
        finally:
            run.finished_at = utcnow()
            await self.db.save_run(run)
            self._running = False

    async def _run_source(self, source: SourceConfig, max_items: int | None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "running", "new": 0, "updated": 0, "unchanged": 0, "rejected": 0,
            "rag_sent": 0, "rag_errors": 0, "started_at": utcnow().isoformat(),
        }
        ctx = AdapterContext(
            source=source,
            fetcher=self.fetcher,
            renderer=self.renderer,
            settings=self.settings,
            known_urls=await self.db.known_urls(source.id),
            max_items=max_items or source.max_items,
        )
        default_min = _MIN_RECORD_CHARS if source.adapter == "file_import" else _MIN_DOCUMENT_CHARS
        min_chars = int(source.options.get("min_text_chars", default_min))
        try:
            async for doc in ADAPTERS[source.adapter](ctx):
                if len(doc.text or "") < min_chars:
                    result["rejected"] += 1
                    ctx.add_error(doc.url, "za mało treści")
                    continue
                outcome, stored = await self.db.upsert(doc)
                result[outcome] += 1
                if outcome != "unchanged" and self.rag is not None:
                    key = "rag_sent" if await self.rag.send(stored) else "rag_errors"
                    result[key] += 1
            result["status"] = "ok"
        except SourceSkipped as exc:
            result["status"] = "skipped"
            result["message"] = str(exc)
        except FetchError as exc:
            logger.warning("Source %s failed: %s", source.id, exc.message)
            result["status"] = "error"
            result["message"] = exc.message
        except Exception as exc:
            logger.exception("Source %s failed", source.id)
            result["status"] = "error"
            result["message"] = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
        result["skipped_known"] = ctx.skipped_known
        result["errors"] = ctx.errors
        result["finished_at"] = utcnow().isoformat()
        logger.info("Source %s: %s", source.id, {k: v for k, v in result.items() if k != "errors"})
        return result
