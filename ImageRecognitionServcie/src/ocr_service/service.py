"""Transport-agnostic OCR service.

``OcrService.recognize(bytes, ...)`` contains everything except HTTP: admission control,
queueing with timeouts, dispatch to the execution back-end, reading order, language detection
and metrics. An async queue worker (e.g. Redis + arq/RQ) can call exactly the same method, so
adding an async mode does not require touching the engines.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from enum import StrEnum

from ocr_service.backends import Backend
from ocr_service.config import Settings, parse_lang_list
from ocr_service.exceptions import ApiError
from ocr_service.logging_setup import request_id_var
from ocr_service.metrics import ENGINE_READY, INFLIGHT, OCR_DURATION, OCR_QUEUE_WAIT
from ocr_service.schemas import Block, OcrResponse, OutputMode
from ocr_service.text import detect_language, group_lines, lines_to_text

logger = logging.getLogger(__name__)


class ServiceState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPED = "stopped"


class OcrService:
    def __init__(self, settings: Settings, backend: Backend) -> None:
        self.settings = settings
        self.backend = backend
        self.capacity = backend.capacity
        # At most `capacity` images are handed to the back-end at once; the rest wait here,
        # where timeouts and client disconnects can still be handled cleanly.
        self._slots = asyncio.Semaphore(self.capacity)
        self._admitted = 0
        self.state = ServiceState.STARTING
        self.error: str | None = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def ready(self) -> bool:
        return self.state is ServiceState.READY and self.backend.ready_workers > 0

    @property
    def engine_name(self) -> str:
        return self.backend.engine_name

    @property
    def supported_langs(self) -> frozenset[str]:
        return self.backend.supported_langs

    async def start(self) -> None:
        started = time.perf_counter()
        try:
            await self.backend.start()
        except Exception as exc:
            self.state = ServiceState.FAILED
            self.error = f"{type(exc).__name__}: {exc}"
            ENGINE_READY.set(0)
            logger.exception("engine failed to load")
            with contextlib.suppress(Exception):
                await self.backend.stop()
            return
        self.state = ServiceState.READY
        ENGINE_READY.set(1)
        logger.info(
            "engine ready",
            extra={
                "engine": self.engine_name,
                "executor": self.settings.executor,
                "workers": self.capacity,
                "cpu_threads_per_worker": self.settings.paddle_cpu_threads,
                "load_s": round(time.perf_counter() - started, 2),
            },
        )

    async def stop(self) -> None:
        if self.state is ServiceState.STOPPED:
            return
        self.state = ServiceState.STOPPED
        ENGINE_READY.set(0)
        await self.backend.stop()

    # ------------------------------------------------------------------ requests
    def resolve_langs(self, langs: str | Sequence[str] | None) -> list[str]:
        requested = parse_lang_list(langs) if langs is not None else list(self.settings.langs)
        if not requested:
            raise ApiError(422, "validation_error", "Parameter 'langs' must not be empty.")
        unsupported = [lang for lang in requested if lang not in self.supported_langs]
        if unsupported:
            raise ApiError(
                422,
                "unsupported_language",
                f"Unsupported language(s): {', '.join(unsupported)}. "
                f"Supported: {', '.join(sorted(self.supported_langs))}.",
            )
        return requested

    async def recognize(
        self,
        data: bytes,
        langs: str | Sequence[str] | None = None,
        output: OutputMode = OutputMode.BLOCKS,
    ) -> OcrResponse:
        if not self.ready:
            if self.state is ServiceState.FAILED:
                message = f"OCR engine failed to load: {self.error}"
            elif self.state is ServiceState.READY:
                message = "All OCR workers are restarting, retry later."
            else:
                message = "OCR engine is still loading, retry later."
            raise ApiError(503, "not_ready", message, headers={"Retry-After": "5"})

        lang_list = self.resolve_langs(langs)

        if self._admitted >= self.capacity + self.settings.max_queue:
            raise ApiError(
                503, "busy", "Too many concurrent OCR requests, retry later.", {"Retry-After": "2"}
            )

        self._admitted += 1
        INFLIGHT.inc()
        started = time.perf_counter()
        try:
            try:
                await asyncio.wait_for(self._slots.acquire(), self.settings.queue_timeout_s)
            except TimeoutError:
                raise ApiError(
                    503,
                    "queue_timeout",
                    "Timed out waiting for a free OCR worker, retry later.",
                    {"Retry-After": "5"},
                ) from None
            OCR_QUEUE_WAIT.observe(time.perf_counter() - started)
            try:
                result = await self.backend.run(data, lang_list, request_id_var.get())
            finally:
                self._slots.release()
        finally:
            self._admitted -= 1
            INFLIGHT.dec()

        lines = group_lines(result.blocks)
        ordered = [block for line in lines for block in line]
        text = lines_to_text(lines)
        elapsed = time.perf_counter() - started
        OCR_DURATION.labels(engine=self.engine_name).observe(elapsed)

        response = OcrResponse(
            text=text,
            lang=detect_language(text, lang_list),
            engine=self.engine_name,
            duration_ms=round(elapsed * 1000),
        )
        if output is OutputMode.BLOCKS:
            response.blocks = [
                Block(text=b.text, bbox=list(b.bbox), confidence=round(b.confidence, 4))
                for b in ordered
            ]
        logger.info(
            "ocr done",
            extra={
                "engine": self.engine_name,
                "width": result.width,
                "height": result.height,
                "scale": round(result.scale, 3),
                "inverted": result.inverted,
                "blocks": len(ordered),
                "duration_ms": response.duration_ms,
            },
        )
        return response
