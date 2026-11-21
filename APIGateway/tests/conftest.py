import asyncio
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import Base
from app.main import create_app

WEB = {"X-Channel": "web"}
DISCORD = {"X-Channel": "discord"}
ADMIN = {"X-API-Key": "admin-key"}

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _reset_postgres(url: str) -> None:
    async def _drop() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_drop())


def make_settings(tmp_path, **overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        admin_api_key="admin-key",
        allowed_channels="web,discord",
        database_url=TEST_DATABASE_URL or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        rate_limit_per_minute=100,
        llm_service_url="",
        page_scrapper_url="",
        image_recognition_url="",
        mock_mode=False,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def make_client(tmp_path) -> Callable[..., Any]:
    @contextmanager
    def _make(handler: Callable[[httpx.Request], httpx.Response] | None = None, **overrides: Any) -> Iterator[TestClient]:
        if TEST_DATABASE_URL:
            _reset_postgres(TEST_DATABASE_URL)
        transport = httpx.MockTransport(handler) if handler else None
        app = create_app(make_settings(tmp_path, **overrides), transport=transport)
        with TestClient(app) as client:
            yield client

    return _make
