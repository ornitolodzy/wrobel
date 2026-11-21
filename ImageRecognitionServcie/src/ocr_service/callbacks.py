"""Callback delivery: accept a request, run OCR in the background, POST the result elsewhere.

Enabled by ``OCR_CALLBACK_URL``. The HTTP request returns ``202`` with a ``request_id``;
the receiver gets a :class:`~ocr_service.schemas.CallbackPayload` with the same id.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

import httpx

from ocr_service.config import Settings
from ocr_service.exceptions import ApiError
from ocr_service.logging_setup import request_id_var
from ocr_service.metrics import CALLBACK_LATENCY, CALLBACKS
from ocr_service.schemas import CallbackError, CallbackPayload, OutputMode
from ocr_service.service import OcrService

logger = logging.getLogger(__name__)


class CallbackDispatcher:
    SHUTDOWN_GRACE_S = 30.0

    def __init__(
        self,
        settings: Settings,
        service: OcrService,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.callback_url is None:
            raise ValueError("OCR_CALLBACK_URL is not configured")
        self.settings = settings
        self.service = service
        self.url = str(settings.callback_url)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> int:
        return len(self._tasks)

    async def start(self) -> None:
        headers = {"User-Agent": "image-recognition-servcie"}
        if self.settings.callback_token is not None:
            headers["Authorization"] = f"Bearer {self.settings.callback_token.get_secret_value()}"
        self._client = httpx.AsyncClient(
            timeout=self.settings.callback_timeout_s, headers=headers, transport=self._transport
        )

    async def stop(self) -> None:
        """Let in-flight jobs finish (bounded), then close the HTTP client."""
        if self._tasks:
            logger.info("waiting for pending callbacks", extra={"pending": len(self._tasks)})
            _, still_running = await asyncio.wait(self._tasks, timeout=self.SHUTDOWN_GRACE_S)
            for task in still_running:
                task.cancel()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def submit(self, data: bytes, langs: Sequence[str] | str | None, output: OutputMode) -> str:
        """Validate cheaply, schedule the job and return its request id (raises ApiError)."""
        self.service.ensure_ready()
        self.service.resolve_langs(langs)
        # Jobs waiting here count against the same limit as synchronous requests.
        if self.pending >= self.service.capacity + self.settings.max_queue:
            raise ApiError(
                503, "busy", "Too many pending OCR jobs, retry later.", {"Retry-After": "2"}
            )
        request_id = request_id_var.get() or "unknown"
        task = asyncio.create_task(self._run(data, langs, output, request_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return request_id

    async def _run(
        self, data: bytes, langs: Sequence[str] | str | None, output: OutputMode, request_id: str
    ) -> None:
        token = request_id_var.set(request_id)
        try:
            try:
                result = await self.service.recognize(data, langs=langs, output=output)
                payload = CallbackPayload(
                    status="ok", request_id=request_id, **result.model_dump(exclude_none=True)
                )
            except ApiError as exc:
                payload = CallbackPayload(
                    status="error",
                    request_id=request_id,
                    error=CallbackError(code=exc.code, message=exc.message),
                )
            except Exception:
                logger.exception("ocr job failed")
                payload = CallbackPayload(
                    status="error",
                    request_id=request_id,
                    error=CallbackError(code="internal_error", message="Internal server error."),
                )
            exclude = {"blocks"} if output is OutputMode.TEXT else set()
            await self._deliver(payload.model_dump(exclude=exclude, exclude_none=True))
        finally:
            request_id_var.reset(token)

    async def _deliver(self, body: dict[str, Any]) -> None:
        if self._client is None:
            raise RuntimeError("CallbackDispatcher.start() has not been called")
        started = time.perf_counter()
        attempts = self.settings.callback_retries + 1
        last_error = ""
        for attempt in range(attempts):
            try:
                response = await self._client.post(
                    self.url, json=body, headers={"X-Request-ID": body["request_id"]}
                )
                if response.is_success:
                    CALLBACKS.labels(result="delivered").inc()
                    CALLBACK_LATENCY.observe(time.perf_counter() - started)
                    logger.info(
                        "callback delivered",
                        extra={"status": response.status_code, "attempt": attempt + 1},
                    )
                    return
                last_error = f"HTTP {response.status_code}"
                if response.status_code < 500 and response.status_code != 429:
                    break  # the receiver rejected the payload - retrying will not help
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                await asyncio.sleep(self.settings.callback_retry_backoff_s * 2**attempt)
        CALLBACKS.labels(result="failed").inc()
        CALLBACK_LATENCY.observe(time.perf_counter() - started)
        logger.error(
            "callback delivery failed",
            extra={"url": self.url, "error": last_error, "attempts": attempt + 1},
        )
