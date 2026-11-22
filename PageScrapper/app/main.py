import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.catalog import load_catalog
from app.config import Settings, get_settings
from app.db import Database
from app.fetch import FetchError, Fetcher, Resolver, system_resolver
from app.ingest import Ingestor, RagClient, schedule_loop
from app.render import PlaywrightRenderer, Renderer
from app.routers import catalog, ingest, live, scrape

logger = logging.getLogger("scraper")


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    resolve: Resolver = system_resolver,
    renderer: Renderer | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    source_catalog = load_catalog(settings.sources_file)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url)
        await db.init()
        fetcher = Fetcher(settings, transport=transport, resolve=resolve)
        active_renderer = renderer or PlaywrightRenderer(settings, resolve)
        rag = RagClient(settings.rag_ingest_url, transport=transport) if settings.rag_ingest_url else None
        app.state.db = db
        app.state.fetcher = fetcher
        app.state.renderer = active_renderer
        app.state.ingestor = Ingestor(db, fetcher, active_renderer, settings, source_catalog, rag)
        logger.info("Loaded %d sources from %s", len(source_catalog.sources), settings.sources_file)
        if not settings.google_factcheck_api_key:
            logger.warning("GOOGLE_FACTCHECK_API_KEY is empty - google-factcheck source will be skipped")
        scheduler = None
        if settings.scrape_interval_minutes > 0:
            scheduler = asyncio.create_task(
                schedule_loop(app.state.ingestor, settings.scrape_interval_minutes, settings.scrape_on_startup)
            )
            logger.info("Automatic scraping every %s minutes", settings.scrape_interval_minutes)
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.cancel()
            task = app.state.ingestor.task
            if task is not None and not task.done():
                task.cancel()
            await fetcher.close()
            await active_renderer.close()
            if rag is not None:
                await rag.close()
            await db.close()

    docs = settings.docs_enabled
    app = FastAPI(
        title="PRAWDOWID PageScrapper",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.catalog = source_catalog

    @app.exception_handler(FetchError)
    async def fetch_error_handler(request: Request, exc: FetchError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"detail": {"code": exc.code, "message": exc.message}})

    @app.get("/health", summary="Sprawdza, czy scraper działa", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(scrape.router)
    app.include_router(ingest.router)
    app.include_router(catalog.router)
    app.include_router(live.router)
    return app


app = create_app()
