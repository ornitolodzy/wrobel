"""Process-based execution: parallelism across processes and worker supervision."""

from __future__ import annotations

import asyncio
import time

import pytest

from ocr_service.app import create_backend
from ocr_service.exceptions import ApiError
from ocr_service.service import OcrService
from tests.conftest import image_bytes, make_settings
from tests.fake_engine import CRASH_WIDTH, HANG_WIDTH

FAKE = "tests.fake_engine:ProcessFakeEngine"


def make_service(**overrides: object) -> OcrService:
    settings = make_settings(
        executor="process", engine=FAKE, preprocess_upscale_target=0, **overrides
    )
    return OcrService(settings, create_backend(settings))


async def wait_for_workers(service: OcrService, count: int, limit_s: float = 30) -> None:
    deadline = time.monotonic() + limit_s
    while service.backend.ready_workers < count:
        assert time.monotonic() < deadline, "workers did not come back"
        await asyncio.sleep(0.1)


def test_parallel_workers_are_separate_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_OCR_DELAY_S", "0.6")

    async def scenario() -> None:
        service = make_service(workers=3)
        await service.start()
        assert service.ready
        assert service.engine_name == "fake-process"
        try:
            started = time.perf_counter()
            results = await asyncio.gather(*(service.recognize(image_bytes()) for _ in range(3)))
            elapsed = time.perf_counter() - started
            pids = {r.text for r in results}
            assert len(pids) == 3, pids  # each image handled by a different process
            assert elapsed < 1.5, elapsed  # 3 x 0.6 s ran in parallel
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_crashed_worker_fails_one_request_and_is_restarted() -> None:
    async def scenario() -> None:
        service = make_service(workers=1)
        await service.start()
        try:
            with pytest.raises(ApiError) as info:
                await service.recognize(image_bytes(size=(CRASH_WIDTH, 40)))
            assert info.value.status_code == 500
            assert info.value.code == "worker_crashed"
            await wait_for_workers(service, 1)
            result = await service.recognize(image_bytes())
            assert result.text.startswith("pid ")
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_hung_worker_is_killed_after_task_timeout() -> None:
    async def scenario() -> None:
        service = make_service(workers=1, task_timeout_s=1.0)
        await service.start()
        try:
            with pytest.raises(ApiError) as info:
                await service.recognize(image_bytes(size=(HANG_WIDTH, 40)))
            assert info.value.status_code == 504
            assert info.value.code == "ocr_timeout"
            await wait_for_workers(service, 1)
            assert (await service.recognize(image_bytes())).text.startswith("pid ")
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_invalid_image_error_crosses_process_boundary() -> None:
    async def scenario() -> None:
        service = make_service(workers=1)
        await service.start()
        try:
            with pytest.raises(ApiError) as info:
                await service.recognize(b"not an image at all")
            assert info.value.status_code == 415
            assert info.value.code == "unsupported_media_type"
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_load_failure_marks_service_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_OCR_FAIL_LOAD", "1")

    async def scenario() -> None:
        service = make_service(workers=2)
        await service.start()
        assert not service.ready
        assert service.state == "failed"
        assert "weights not found" in (service.error or "")
        with pytest.raises(ApiError) as info:
            await service.recognize(image_bytes())
        assert info.value.code == "not_ready"

    asyncio.run(scenario())


def test_auto_workers_uses_cpu_and_memory_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from ocr_service import config

    monkeypatch.setattr(config, "available_cpus", lambda: 16)
    monkeypatch.setattr(config, "available_memory_mb", lambda: None)
    assert make_settings(workers=0, paddle_cpu_threads=1).effective_workers == 16
    assert make_settings(workers=0, paddle_cpu_threads=4).effective_workers == 4
    # 4 GB container: (4096 - 512) // 700 = 5 workers despite 16 CPUs
    monkeypatch.setattr(config, "available_memory_mb", lambda: 4096)
    assert make_settings(workers=0, paddle_cpu_threads=1).effective_workers == 5
    monkeypatch.setattr(config, "available_memory_mb", lambda: 600)
    assert make_settings(workers=0).effective_workers == 1
    assert make_settings(workers=5).effective_workers == 5  # explicit value wins
