"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from ocr_service import __version__
from ocr_service.api import router, v1
from ocr_service.config import Settings, get_settings
from ocr_service.engines import OcrEngine, create_engine
from ocr_service.errors import install_error_handlers
from ocr_service.logging_setup import configure_logging
from ocr_service.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from ocr_service.service import OcrService

logger = logging.getLogger(__name__)

DESCRIPTION = """
Local OCR microservice for screenshots (Polish + English). Runs fully offline -
no external APIs. Engine: PaddleOCR (pluggable via `OCR_ENGINE`).
"""


def create_app(
    settings: Settings | None = None,
    engine_factory: Callable[[], OcrEngine] | None = None,
    *,
    setup_logging: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    if setup_logging:
        configure_logging(settings.log_level, settings.log_json)

    factory = engine_factory or (lambda: create_engine(settings))
    service = OcrService(settings, factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Load the model in the background: /healthz answers immediately,
        # /readyz turns 200 once the engine is loaded.
        load_task = asyncio.create_task(service.start(), name="engine-load")
        app.state.load_task = load_task
        try:
            yield
        finally:
            if not load_task.done():
                load_task.cancel()
                with suppress(asyncio.CancelledError):
                    await load_task
            await service.stop()
            logger.info("service stopped")

    app = FastAPI(
        title="ScreenScrapper OCR",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.service = service

    install_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)  # outermost: sees every response

    app.include_router(router)
    app.include_router(v1)
    return app
