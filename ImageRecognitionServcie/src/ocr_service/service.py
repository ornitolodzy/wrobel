"""Transport-agnostic OCR service.

``OcrService.recognize(bytes, ...)`` contains everything except HTTP: validation,
preprocessing, engine pool / concurrency limits, reading order, metrics. An async queue worker
(e.g. Redis + arq/RQ) can call exactly the same method, so adding an async mode does not
require touching the engines.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from enum import StrEnum

import anyio
import anyio.to_thread

from ocr_service.config import Settings, parse_lang_list
from ocr_service.engines.base import OcrEngine, TextBlock
from ocr_service.errors import ApiError
from ocr_service.imaging import PreprocessedImage, PreprocessOptions, decode_image, preprocess
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
    def __init__(self, settings: Settings, engine_factory: Callable[[], OcrEngine]) -> None:
        self.settings = settings
        self._engine_factory = engine_factory
        self._engines: list[OcrEngine] = []
        self._pool: asyncio.Queue[OcrEngine] | None = None
        self._engine_limiter = anyio.CapacityLimiter(settings.workers)
        # Decoding/preprocessing is also CPU-bound; allow a little more parallelism than OCR.
        self._prep_limiter = anyio.CapacityLimiter(settings.workers * 2)
        self._admitted = 0
        self.state = ServiceState.STARTING
        self.error: str | None = None
        self.engine_name = "unknown"
        self.supported_langs: frozenset[str] = frozenset(settings.langs)
        self._preprocess_options = PreprocessOptions(
            upscale_target=settings.preprocess_upscale_target,
            max_upscale=settings.preprocess_max_upscale,
            dark_mode=settings.preprocess_dark_mode,
            dark_threshold=settings.preprocess_dark_threshold,
        )

    # ------------------------------------------------------------------ lifecycle
    @property
    def ready(self) -> bool:
        return self.state is ServiceState.READY

    async def start(self) -> None:
        """Load ``workers`` engine instances (once per process) in a worker thread."""
        started = time.perf_counter()
        pool: asyncio.Queue[OcrEngine] = asyncio.Queue()
        try:
            for _ in range(self.settings.workers):
                engine = await anyio.to_thread.run_sync(self._create_engine)
                self._engines.append(engine)
                pool.put_nowait(engine)
        except Exception as exc:
            self.state = ServiceState.FAILED
            self.error = f"{type(exc).__name__}: {exc}"
            ENGINE_READY.set(0)
            logger.exception("engine failed to load")
            return
        self._pool = pool
        self.engine_name = self._engines[0].name
        self.supported_langs = self._engines[0].supported_langs
        self.state = ServiceState.READY
        ENGINE_READY.set(1)
        logger.info(
            "engine ready",
            extra={
                "engine": self.engine_name,
                "workers": self.settings.workers,
                "load_s": round(time.perf_counter() - started, 2),
            },
        )

    def _create_engine(self) -> OcrEngine:
        engine = self._engine_factory()
        engine.load()
        return engine

    async def stop(self) -> None:
        self.state = ServiceState.STOPPED
        ENGINE_READY.set(0)
        for engine in self._engines:
            try:
                engine.close()
            except Exception:
                logger.exception("error while closing engine")
        self._engines.clear()
        self._pool = None

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
        if not self.ready or self._pool is None:
            message = (
                f"OCR engine failed to load: {self.error}"
                if self.state is ServiceState.FAILED
                else "OCR engine is still loading, retry later."
            )
            raise ApiError(503, "not_ready", message, headers={"Retry-After": "5"})

        lang_list = self.resolve_langs(langs)

        capacity = self.settings.workers + self.settings.max_queue
        if self._admitted >= capacity:
            raise ApiError(
                503, "busy", "Too many concurrent OCR requests, retry later.", {"Retry-After": "2"}
            )

        self._admitted += 1
        INFLIGHT.inc()
        started = time.perf_counter()
        try:
            prepared = await anyio.to_thread.run_sync(
                self._prepare, data, limiter=self._prep_limiter
            )
            blocks = await self._run_engine(prepared, lang_list)
        finally:
            self._admitted -= 1
            INFLIGHT.dec()

        blocks = _to_original_coordinates(blocks, prepared)
        lines = group_lines(blocks)
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
                "width": prepared.original_width,
                "height": prepared.original_height,
                "scale": round(prepared.scale, 3),
                "inverted": prepared.inverted,
                "blocks": len(ordered),
                "duration_ms": response.duration_ms,
            },
        )
        return response

    def _prepare(self, data: bytes) -> PreprocessedImage:
        image = decode_image(data, self.settings.max_pixels)
        try:
            return preprocess(image, self._preprocess_options)
        finally:
            image.close()

    async def _run_engine(self, prepared: PreprocessedImage, langs: list[str]) -> list[TextBlock]:
        if self._pool is None:
            raise ApiError(503, "not_ready", "OCR engine is not available.")
        pool = self._pool
        wait_started = time.perf_counter()
        try:
            engine = await asyncio.wait_for(pool.get(), self.settings.queue_timeout_s)
        except TimeoutError:
            raise ApiError(
                503,
                "queue_timeout",
                "Timed out waiting for a free OCR worker, retry later.",
                {"Retry-After": "5"},
            ) from None
        OCR_QUEUE_WAIT.observe(time.perf_counter() - wait_started)
        try:
            # Not abandoned on cancellation: the engine goes back to the pool only once the
            # thread has really finished with it.
            return await anyio.to_thread.run_sync(
                engine.recognize, prepared.image, langs, limiter=self._engine_limiter
            )
        except ApiError:
            raise
        except Exception as exc:
            logger.exception("engine error")
            raise ApiError(500, "ocr_failed", "OCR engine failed to process the image.") from exc
        finally:
            pool.put_nowait(engine)


def _to_original_coordinates(
    blocks: list[TextBlock], prepared: PreprocessedImage
) -> list[TextBlock]:
    """Map boxes from the (possibly upscaled) processed image back to the uploaded image."""
    w, h, s = prepared.original_width, prepared.original_height, prepared.scale

    def clamp(value: float, upper: int) -> int:
        return max(0, min(round(value), upper))

    return [
        TextBlock(
            text=b.text,
            bbox=(
                clamp(b.bbox[0] / s, w),
                clamp(b.bbox[1] / s, h),
                clamp(b.bbox[2] / s, w),
                clamp(b.bbox[3] / s, h),
            ),
            confidence=b.confidence,
        )
        for b in blocks
    ]
