"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ocr_service import __version__
from ocr_service.api import router, v1
from ocr_service.backends import Backend, ProcessBackend, ThreadBackend
from ocr_service.callbacks import CallbackDispatcher
from ocr_service.config import Settings, get_settings
from ocr_service.engines import OcrEngine, create_engine
from ocr_service.errors import install_error_handlers
from ocr_service.logging_setup import configure_logging
from ocr_service.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from ocr_service.schemas import CallbackPayload
from ocr_service.service import OcrService

logger = logging.getLogger(__name__)

DESCRIPTION = """
Local OCR microservice for screenshots (Polish + English). Runs fully offline -
no external APIs. Engine: PaddleOCR (pluggable via `OCR_ENGINE`).
"""


def create_backend(
    settings: Settings, engine_factory: Callable[[], OcrEngine] | None = None
) -> Backend:
    """An explicit in-process ``engine_factory`` (tests, embedding) implies the thread backend."""
    if engine_factory is not None:
        return ThreadBackend(settings, engine_factory)
    if settings.executor == "process":
        return ProcessBackend(settings)
    return ThreadBackend(settings, lambda: create_engine(settings))


def create_app(
    settings: Settings | None = None,
    engine_factory: Callable[[], OcrEngine] | None = None,
    *,
    setup_logging: bool = True,
    callback_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. ``callback_transport`` lets tests intercept callback HTTP calls."""
    settings = settings or get_settings()
    if setup_logging:
        configure_logging(settings.log_level, settings.log_json)

    service = OcrService(settings, create_backend(settings, engine_factory))
    dispatcher = (
        CallbackDispatcher(settings, service, transport=callback_transport)
        if settings.callback_url is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Load the model in the background: /healthz answers immediately,
        # /readyz turns 200 once the engine is loaded.
        load_task = asyncio.create_task(service.start(), name="engine-load")
        app.state.load_task = load_task
        if dispatcher is not None:
            await dispatcher.start()
            logger.info("callback mode", extra={"callback_url": dispatcher.url})
        try:
            yield
        finally:
            if dispatcher is not None:
                await dispatcher.stop()
            if not load_task.done():
                load_task.cancel()
                with suppress(asyncio.CancelledError):
                    await load_task
            await service.stop()
            logger.info("service stopped")

    app = FastAPI(
        title="ImageRecognitionServcie OCR",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.service = service
    app.state.dispatcher = dispatcher

    install_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:  # outermost, so error responses carry CORS headers too
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "Retry-After"],
        )

    app.include_router(router)
    app.include_router(v1)
    _document_callback_schema(app)
    return app


def _document_callback_schema(app: FastAPI) -> None:
    """Add CallbackPayload to the OpenAPI components so receivers can see its shape."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = original()
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            components.update(
                CallbackPayload.model_json_schema(ref_template="#/components/schemas/{model}").pop(
                    "$defs", {}
                )
            )
            components["CallbackPayload"] = CallbackPayload.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            components["CallbackPayload"].pop("$defs", None)
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
