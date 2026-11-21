import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import Settings, get_settings
from app.db import Database
from app.deps import RateLimiter
from app.routers import admin, checks, health, knowledge, services, verify
from app.runtime_config import RuntimeConfigStore
from app.services import Downstream

logger = logging.getLogger("gateway")

_PURGE_INTERVAL_SECONDS = 3600


async def _retention_loop(db: Database, days: int) -> None:
    while True:
        try:
            removed = await db.purge_older_than(days)
            if removed:
                logger.info("Retencja: usunięto %d rekordów starszych niż %d dni", removed, days)
        except Exception:
            logger.exception("Błąd podczas czyszczenia retencji")
        await asyncio.sleep(_PURGE_INTERVAL_SECONDS)


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url)
        await db.init()
        runtime = RuntimeConfigStore(settings)
        app.state.settings = settings
        app.state.db = db
        app.state.runtime = runtime
        app.state.downstream = Downstream(settings, runtime, transport=transport)
        app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)

        if not settings.admin_api_key:
            logger.warning("Brak ADMIN_API_KEY – endpointy administracyjne są wyłączone.")
        for name, url in app.state.downstream.services.items():
            logger.info("Serwis %s: %s", name, url or "nieskonfigurowany -> MOCK")

        retention_task = asyncio.create_task(_retention_loop(db, settings.retention_days))
        try:
            yield
        finally:
            retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retention_task
            await app.state.downstream.close()
            await db.close()

    app = FastAPI(
        title="PRAWDOWID API Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["X-API-Key", "X-Channel", "Content-Type"],
        expose_headers=["X-Check-Id", "X-Response-Time-Ms", "Retry-After"],
    )

    max_body = settings.max_image_size_bytes + 1024 * 1024

    @app.middleware("http")
    async def limits_and_timing(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_body:
            return JSONResponse(status_code=413, content={"detail": "Żądanie jest za duże."})
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - started) * 1000))
        return response

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxy_list)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        db: Database | None = getattr(request.app.state, "db", None)
        if db is not None:
            await db.log_event(
                "validation_error",
                detail={"path": request.url.path, "errors": [e.get("type") for e in exc.errors()][:10]},
            )
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "Niepoprawne dane wejściowe.", "errors": errors})

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Nieobsłużony błąd dla %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Wewnętrzny błąd gatewaya. To nie jest wynik sprawdzenia."},
        )

    app.include_router(health.router)
    app.include_router(verify.router)
    app.include_router(services.router)
    app.include_router(knowledge.router)
    app.include_router(checks.router)
    app.include_router(admin.router)

    def openapi_without_captions() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                operation.pop("description", None)
                for response in operation.get("responses", {}).values():
                    response["description"] = ""
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_without_captions
    return app


app = create_app()
