"""Concurrency limits of OcrService (no HTTP involved)."""

from __future__ import annotations

import asyncio

import pytest

from ocr_service.errors import ApiError
from ocr_service.schemas import OutputMode
from ocr_service.service import OcrService
from tests.conftest import FakeEngine, image_bytes, make_settings


async def _started(service: OcrService) -> OcrService:
    await service.start()
    assert service.ready
    return service


def test_rejects_when_queue_is_full() -> None:
    async def scenario() -> None:
        service = await _started(
            OcrService(make_settings(workers=1, max_queue=0), lambda: FakeEngine(delay_s=0.3))
        )
        data = image_bytes()
        results = await asyncio.gather(
            service.recognize(data), service.recognize(data), return_exceptions=True
        )
        errors = [r for r in results if isinstance(r, ApiError)]
        assert len(errors) == 1
        assert errors[0].status_code == 503
        assert errors[0].code == "busy"
        await service.stop()

    asyncio.run(scenario())


def test_queue_timeout() -> None:
    async def scenario() -> None:
        service = await _started(
            OcrService(
                make_settings(workers=1, max_queue=4, queue_timeout_s=0.05),
                lambda: FakeEngine(delay_s=0.4),
            )
        )
        data = image_bytes()
        results = await asyncio.gather(
            service.recognize(data), service.recognize(data), return_exceptions=True
        )
        codes = sorted(r.code if isinstance(r, ApiError) else "ok" for r in results)
        assert codes == ["ok", "queue_timeout"]
        await service.stop()

    asyncio.run(scenario())


def test_workers_run_in_parallel() -> None:
    async def scenario() -> None:
        service = await _started(
            OcrService(make_settings(workers=2), lambda: FakeEngine(delay_s=0.3))
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        data = image_bytes()
        await asyncio.gather(service.recognize(data), service.recognize(data))
        assert loop.time() - started < 0.55
        await service.stop()

    asyncio.run(scenario())


def test_service_is_reusable_without_http() -> None:
    """The same call a future queue worker would make."""

    async def scenario() -> None:
        service = await _started(OcrService(make_settings(), FakeEngine))
        result = await service.recognize(image_bytes(), langs=["en"], output=OutputMode.TEXT)
        assert result.text
        assert result.lang == "en"
        assert result.blocks is None
        await service.stop()

    asyncio.run(scenario())


def test_engine_crash_is_reported_as_500() -> None:
    class Broken(FakeEngine):
        def recognize(self, image, langs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    async def scenario() -> None:
        service = await _started(OcrService(make_settings(), Broken))
        with pytest.raises(ApiError) as info:
            await service.recognize(image_bytes())
        assert info.value.status_code == 500
        assert info.value.code == "ocr_failed"
        # the engine went back to the pool
        assert service._pool is not None
        assert service._pool.qsize() == 1
        await service.stop()

    asyncio.run(scenario())
