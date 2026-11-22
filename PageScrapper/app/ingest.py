import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.catalog import Catalog, SourceConfig, normalize_page_url, site_of
from app.claimreview import extract_claim_reviews
from app.config import Settings
from app.db import Database, Document, DocumentIn, ScrapeRun, utcnow
from app.extract import clean_text, parse_date
from app.fetch import FetchError, Fetcher
from app.render import Renderer
from app.scrape import fetch_page
from app.sources import ADAPTERS
from app.sources.base import AdapterContext, SourceSkipped

logger = logging.getLogger("scraper.ingest")

_MIN_DOCUMENT_CHARS = 50
_MIN_RECORD_CHARS = 10


class RunAlreadyActive(Exception):
    pass


class UnknownSource(Exception):
    pass


@dataclass
class PageResult:
    run: ScrapeRun
    source: SourceConfig
    document: Document
    details: dict[str, Any]


class PageNotInCatalog(Exception):
    """The page is not on a site from the catalog (or not on the site of the requested source)."""


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


def _accepted(response: httpx.Response) -> bool:
    """2xx, and a receiver that reports vectors (ContextGraph `unembedded`) has none missing:
    a document stored without vectors is invisible to dense search until the next reconcile."""
    if response.status_code >= 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return True
    if isinstance(body, dict) and "unembedded" in body:
        return body["unembedded"] == 0
    return True


class RagClient:
    """Pushes every stored document to each receiver. Fast path only: a receiver that missed a
    document (down, error, no vectors) catches up from GET /documents/revisions."""

    def __init__(self, urls: str | list[str], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.urls = [urls] if isinstance(urls, str) else list(urls)
        self.client = httpx.AsyncClient(timeout=60, transport=transport)

    async def _post(self, url: str, payload: dict) -> bool:
        try:
            ok = _accepted(await self.client.post(url, json=payload))
        except httpx.HTTPError as exc:
            logger.warning("Ingest push to %s failed: %s", url, type(exc).__name__)
            return False
        if not ok:
            logger.warning("Ingest push to %s not accepted for %s", url, payload["url"])
        return ok

    async def send(self, doc: Document) -> bool:
        """True only if every receiver accepted the document."""
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
            "revision": doc.revision or 1,
        }
        results = await asyncio.gather(*(self._post(url, payload) for url in self.urls))
        return all(results)

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

    # ---------- one page (POST /scrap/page) ----------

    def source_for_page(self, url: str, source_id: str | None) -> SourceConfig:
        if source_id is None:
            source = self.catalog.source_for_page(url)
            if source is None:
                raise PageNotInCatalog("Adres nie należy do żadnej witryny z katalogu źródeł.")
            return source
        source = self.catalog.get(source_id)
        if source is None:
            raise UnknownSource(source_id)
        if site_of(url) not in source.sites():
            raise PageNotInCatalog(f"Adres nie należy do witryny źródła {source_id}.")
        return source

    async def scrape_page(self, url: str, source_id: str | None, render: str) -> PageResult:
        """Fetch one page from a catalog site and run it through the same pipeline as /scrap/all
        (source cleaning rules, ClaimReview, upsert, RAG push). Recorded in /scrap/runs as well."""
        url = normalize_page_url(url)
        source = self.source_for_page(url, source_id)
        run = ScrapeRun(
            id=str(uuid.uuid4()),
            started_at=utcnow(),
            status="running",
            request={"url": url, "source_ids": [source.id], "trigger": "page"},
            results={source.id: {"status": "running"}},
        )
        result: dict[str, Any] = {"status": "running", "url": url, "started_at": run.started_at.isoformat()}
        try:
            stored, details = await self._ingest_page(url, source, render)
            result.update(details)
            result["status"] = "ok"
            run.status = "finished"
            return PageResult(run=run, source=source, document=stored, details=result)
        except (FetchError, PageNotInCatalog) as exc:
            result["status"] = "error"
            result["message"] = getattr(exc, "message", None) or str(exc)
            run.status = "finished_with_errors"
            raise
        except Exception as exc:
            logger.exception("Single page scrape of %s crashed", url)
            result["status"] = "error"
            result["message"] = f"{type(exc).__name__}: {exc}"
            run.status = "crashed"
            raise
        finally:
            result["finished_at"] = utcnow().isoformat()
            run.results = {source.id: result}
            run.finished_at = utcnow()
            await self.db.save_run(run)

    async def _ingest_page(self, url: str, source: SourceConfig, render: str) -> tuple[Document, dict[str, Any]]:
        if not await self.fetcher.allowed_by_robots(url):
            raise FetchError("robots_disallowed", "robots.txt nie pozwala na pobranie tej strony.", 403)
        scraped = await fetch_page(url, render, self.fetcher, self.renderer, self.settings)
        page = scraped.page
        if site_of(page.final_url) not in source.sites():
            raise PageNotInCatalog("Strona przekierowuje poza witrynę z katalogu źródeł.")

        published_at = page.published_at
        date_selector = source.options.get("date_selector")
        extra: dict[str, Any] = {}
        if scraped.html is not None:
            if date_selector:
                element = BeautifulSoup(scraped.html, "lxml").select_one(date_selector)
                published_at = (parse_date(element.get_text(" ", strip=True)) if element else None) or published_at
            if reviews := extract_claim_reviews(scraped.html):
                extra["claim_reviews"] = reviews

        doc = DocumentIn(
            url=normalize_page_url(page.final_url),
            source_id=source.id,
            source_type=source.source_type,
            title=page.title,
            publisher=scraped.site_publisher or source.publisher,
            published_at=published_at,
            text=clean_text(page.text, drop_prefixes=source.options.get("drop_lines_starting_with") or []),
            language=source.options.get("language") or page.language or "pl",
            extra=extra,
        )
        min_chars = int(source.options.get("min_text_chars", _MIN_DOCUMENT_CHARS))
        if len(doc.text) < min_chars:
            raise FetchError("no_text", "Za mało treści na stronie, żeby zapisać ją w bazie.", 422)

        outcome, stored = await self.db.upsert(doc)
        if self.rag is None:
            rag = "disabled"
        elif outcome == "unchanged":
            rag = "skipped"
        else:
            rag = "sent" if await self.rag.send(stored) else "error"
        logger.info("Page %s -> %s: %s, rag %s", url, source.id, outcome, rag)
        return stored, {
            "outcome": outcome,
            "new": int(outcome == "new"),
            "updated": int(outcome == "updated"),
            "unchanged": int(outcome == "unchanged"),
            "rag": rag,
            "rag_sent": int(rag == "sent"),
            "rag_errors": int(rag == "error"),
            "document_id": stored.id,
            "final_url": stored.url,
            "rendered": page.rendered,
            "claim_reviews": len(extra.get("claim_reviews", [])),
        }
