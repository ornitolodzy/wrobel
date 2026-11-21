"""Execution back-ends: where engine instances live and how images reach them.

* :class:`ProcessBackend` (default) - N worker processes, each with its own model. True
  parallelism across cores (no GIL), the API process only does I/O, crashed or hung workers
  are restarted without taking the service down.
* :class:`ThreadBackend` - N engine instances in threads of the API process. Simple, used in
  tests and for debugging; limited by the GIL.

The service in front gates concurrency to :attr:`Backend.capacity`, so a backend never gets
more simultaneous calls than it has workers.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import multiprocessing
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from multiprocessing.process import BaseProcess
from typing import Any

import anyio
import anyio.to_thread

from ocr_service.config import Settings
from ocr_service.engines.base import OcrEngine
from ocr_service.exceptions import ApiError
from ocr_service.metrics import WORKER_RESTARTS, WORKERS_READY
from ocr_service.pipeline import PipelineResult, preprocess_options, run_pipeline
from ocr_service.worker_process import worker_main

logger = logging.getLogger(__name__)


def _timeout_error(timeout: float) -> ApiError:
    return ApiError(504, "ocr_timeout", f"OCR did not finish within {timeout:g} s.")


class Backend(ABC):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.capacity = settings.effective_workers
        self.engine_name = settings.engine
        self.supported_langs: frozenset[str] = frozenset(settings.langs)

    @property
    @abstractmethod
    def ready_workers(self) -> int: ...

    @abstractmethod
    async def start(self) -> None:
        """Load all engine instances; raise if the engine cannot be loaded."""

    @abstractmethod
    async def run(
        self, data: bytes, langs: Sequence[str], request_id: str | None
    ) -> PipelineResult: ...

    @abstractmethod
    async def stop(self) -> None: ...


# ===================================================================== threads
class ThreadBackend(Backend):
    def __init__(self, settings: Settings, engine_factory: Callable[[], OcrEngine]) -> None:
        super().__init__(settings)
        self._engine_factory = engine_factory
        self._engines: list[OcrEngine] = []
        self._pool: asyncio.Queue[OcrEngine] = asyncio.Queue()
        self._limiter = anyio.CapacityLimiter(self.capacity)
        self._options = preprocess_options(settings)

    @property
    def ready_workers(self) -> int:
        return len(self._engines)

    async def start(self) -> None:
        for _ in range(self.capacity):
            engine = await anyio.to_thread.run_sync(self._create_engine)
            self._engines.append(engine)
            self._pool.put_nowait(engine)
            WORKERS_READY.set(len(self._engines))
        self.engine_name = self._engines[0].name
        self.supported_langs = self._engines[0].supported_langs

    def _create_engine(self) -> OcrEngine:
        engine = self._engine_factory()
        engine.load()
        return engine

    async def run(
        self, data: bytes, langs: Sequence[str], request_id: str | None
    ) -> PipelineResult:
        engine = await self._pool.get()

        async def work() -> PipelineResult:
            try:
                return await anyio.to_thread.run_sync(
                    run_pipeline,
                    engine,
                    data,
                    langs,
                    self.settings.max_pixels,
                    self._options,
                    limiter=self._limiter,
                )
            finally:
                # Only once the thread is really done with the engine.
                self._pool.put_nowait(engine)

        task = asyncio.ensure_future(work())
        task.add_done_callback(lambda t: t.cancelled() or t.exception())  # avoid stray warnings
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.settings.task_timeout_s)
        except TimeoutError:
            raise _timeout_error(self.settings.task_timeout_s) from None
        except ApiError:
            raise
        except Exception as exc:
            logger.exception("engine error")
            raise ApiError(500, "ocr_failed", "OCR engine failed to process the image.") from exc

    async def stop(self) -> None:
        for engine in self._engines:
            try:
                engine.close()
            except Exception:
                logger.exception("error while closing engine")
        self._engines.clear()
        WORKERS_READY.set(0)


# ===================================================================== processes
class ProcessBackend(Backend):
    MONITOR_INTERVAL_S = 0.5
    MAX_RESTART_BACKOFF_S = 30.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        # "spawn" everywhere: forking a process with native thread pools is unsafe.
        self._ctx = multiprocessing.get_context("spawn")
        self._tasks: Any = self._ctx.Queue()
        self._results: Any = self._ctx.Queue()
        self._procs: dict[int, BaseProcess] = {}
        self._ready: set[int] = set()
        self._running: dict[int, int] = {}  # worker id -> task id
        self._futures: dict[int, asyncio.Future[PipelineResult]] = {}
        self._task_ids = itertools.count()
        self._failures: dict[int, int] = {}  # consecutive deaths without becoming ready
        self._respawn_at: dict[int, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader: threading.Thread | None = None
        self._monitor: asyncio.Task[None] | None = None
        self._startup: asyncio.Future[None] | None = None
        self._stopping = False

    @property
    def ready_workers(self) -> int:
        return len(self._ready)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._startup = self._loop.create_future()
        self._reader = threading.Thread(target=self._read_results, name="ocr-results", daemon=True)
        self._reader.start()
        for worker_id in range(self.capacity):
            self._spawn(worker_id)
        self._monitor = asyncio.create_task(self._watch_workers(), name="ocr-worker-monitor")
        await self._startup

    def _spawn(self, worker_id: int) -> None:
        proc = self._ctx.Process(
            target=worker_main,
            args=(worker_id, self.settings, self._tasks, self._results),
            name=f"ocr-worker-{worker_id}",
            daemon=True,
        )
        proc.start()
        self._procs[worker_id] = proc
        logger.info("worker started", extra={"worker": worker_id, "pid": proc.pid})

    async def stop(self) -> None:
        self._stopping = True
        if self._monitor is not None:
            self._monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor
        for _ in self._procs:
            self._tasks.put(None)
        await anyio.to_thread.run_sync(self._join_workers)
        self._results.put(None)  # stops the reader thread
        if self._reader is not None:
            await anyio.to_thread.run_sync(self._reader.join, 5)
        for future in self._futures.values():
            if not future.done():
                future.set_exception(ApiError(503, "shutting_down", "Service is shutting down."))
        self._futures.clear()
        self._ready.clear()
        WORKERS_READY.set(0)

    def _join_workers(self) -> None:
        deadline = time.monotonic() + 10
        for proc in self._procs.values():
            proc.join(max(0.0, deadline - time.monotonic()))
            if proc.is_alive():
                proc.kill()
                proc.join(2)

    def _require_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("ProcessBackend.start() has not been called")
        return self._loop

    # ------------------------------------------------------------------ messages
    def _read_results(self) -> None:
        """Blocking reader thread: forwards worker messages to the event loop."""
        loop = self._require_loop()
        while True:
            try:
                message = self._results.get()
            except (EOFError, OSError):
                return
            if message is None:
                return
            loop.call_soon_threadsafe(self._on_message, message)

    def _on_message(self, message: tuple[str, int, Any]) -> None:
        kind, worker_id, payload = message
        if kind == "ready":
            self._ready.add(worker_id)
            self._failures.pop(worker_id, None)
            self.engine_name, langs = payload[0], payload[1]
            self.supported_langs = frozenset(langs)
            WORKERS_READY.set(len(self._ready))
            logger.info("worker ready", extra={"worker": worker_id})
            if (
                self._startup is not None
                and not self._startup.done()
                and len(self._ready) == self.capacity
            ):
                self._startup.set_result(None)
        elif kind == "load_failed":
            if self._startup is not None and not self._startup.done():
                self._startup.set_exception(RuntimeError(payload))
            else:
                logger.error("worker failed to reload engine", extra={"worker": worker_id})
        elif kind == "started":
            self._running[worker_id] = payload
        elif kind == "done":
            task_id, ok, value = payload
            if self._running.get(worker_id) == task_id:
                del self._running[worker_id]
            future = self._futures.pop(task_id, None)
            if future is None or future.done():
                return  # caller already gave up (timeout / disconnect)
            if ok:
                future.set_result(value)
            else:
                future.set_exception(ApiError(*value))

    # ------------------------------------------------------------------ supervision
    async def _watch_workers(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.MONITOR_INTERVAL_S)
            for worker_id, proc in list(self._procs.items()):
                if proc.is_alive() or self._stopping:
                    continue
                self._handle_dead_worker(worker_id, proc)

    def _handle_dead_worker(self, worker_id: int, proc: BaseProcess) -> None:
        if worker_id not in self._respawn_at:  # first tick that notices this death
            self._ready.discard(worker_id)
            WORKERS_READY.set(len(self._ready))
            task_id = self._running.pop(worker_id, None)
            if task_id is not None:
                future = self._futures.pop(task_id, None)
                if future is not None and not future.done():
                    future.set_exception(
                        ApiError(
                            500, "worker_crashed", "OCR worker crashed while processing the image."
                        )
                    )
            if self._startup is not None and not self._startup.done():
                self._startup.set_exception(
                    RuntimeError(f"worker {worker_id} exited during start-up ({proc.exitcode})")
                )
                return
            # Exponential backoff for workers that keep dying: 0 s, 1 s, 2 s, 4 s ... 30 s.
            failures = self._failures.get(worker_id, 0)
            self._failures[worker_id] = failures + 1
            delay = 0.0 if failures == 0 else min(self.MAX_RESTART_BACKOFF_S, 2.0 ** (failures - 1))
            self._respawn_at[worker_id] = time.monotonic() + delay
            logger.error(
                "worker died",
                extra={
                    "worker": worker_id,
                    "exitcode": proc.exitcode,
                    "pid": proc.pid,
                    "restart_in_s": delay,
                },
            )
        if time.monotonic() < self._respawn_at[worker_id]:
            return
        del self._respawn_at[worker_id]
        WORKER_RESTARTS.inc()
        with contextlib.suppress(ValueError):
            proc.close()
        self._spawn(worker_id)

    # ------------------------------------------------------------------ requests
    async def run(
        self, data: bytes, langs: Sequence[str], request_id: str | None
    ) -> PipelineResult:
        loop = self._require_loop()
        timeout = self.settings.task_timeout_s
        task_id = next(self._task_ids)
        future: asyncio.Future[PipelineResult] = loop.create_future()
        self._futures[task_id] = future
        self._tasks.put((task_id, data, list(langs), request_id, time.time() + timeout))
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except TimeoutError:
            self._futures.pop(task_id, None)
            self._kill_worker_running(task_id)
            raise _timeout_error(timeout) from None
        except asyncio.CancelledError:
            self._futures.pop(task_id, None)  # client went away; result will be dropped
            raise

    def _kill_worker_running(self, task_id: int) -> None:
        for worker_id, running in list(self._running.items()):
            if running == task_id:
                logger.error("killing hung worker", extra={"worker": worker_id})
                self._ready.discard(worker_id)  # stop counting it before the monitor notices
                WORKERS_READY.set(len(self._ready))
                self._procs[worker_id].kill()  # the monitor restarts it
