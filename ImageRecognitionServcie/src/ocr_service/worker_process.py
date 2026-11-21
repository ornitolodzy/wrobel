"""Entry point of an OCR worker process (``OCR_EXECUTOR=process``).

Deliberately imports nothing web-related: a worker only loads the engine and then serves
tasks from a shared queue until it receives ``None`` or its parent disappears.

Protocol (all messages go to the shared result queue as ``(kind, worker_id, payload)``):
  ready        payload = (engine_name, [langs])
  load_failed  payload = "error description"
  started      payload = task_id
  done         payload = (task_id, ok, PipelineResult | (status, code, message))
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import signal
import time
from typing import Any

from ocr_service.config import Settings

logger = logging.getLogger("ocr_service.worker")

# Task tuple: (task_id, image bytes, langs, request_id, deadline as time.time())
Task = tuple[int, bytes, list[str], str | None, float]


def _limit_native_threads(threads: int) -> None:
    """Keep every worker within its CPU budget (no oversubscription across workers)."""
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[var] = str(threads)


def worker_main(worker_id: int, settings: Settings, tasks: Any, results: Any) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent coordinates shutdown
    _limit_native_threads(settings.paddle_cpu_threads)

    # Imported only after the thread limits are in place.
    import cv2

    from ocr_service.engines import create_engine
    from ocr_service.exceptions import ApiError
    from ocr_service.logging_setup import configure_logging, request_id_var
    from ocr_service.pipeline import preprocess_options, run_pipeline

    cv2.setNumThreads(1)
    configure_logging(settings.log_level, settings.log_json)

    try:
        engine = create_engine(settings)
        engine.load()
    except BaseException as exc:
        logger.exception("worker failed to load engine", extra={"worker": worker_id})
        results.put(("load_failed", worker_id, f"{type(exc).__name__}: {exc}"))
        return

    results.put(("ready", worker_id, (engine.name, sorted(engine.supported_langs))))
    options = preprocess_options(settings)
    parent = multiprocessing.parent_process()

    while True:
        try:
            task: Task | None = tasks.get(timeout=1.0)
        except queue.Empty:
            if parent is not None and not parent.is_alive():
                break  # orphaned - the API process is gone
            continue
        if task is None:
            break

        task_id, data, langs, request_id, deadline = task
        results.put(("started", worker_id, task_id))
        token = request_id_var.set(request_id)
        try:
            if time.time() > deadline:
                raise ApiError(504, "ocr_timeout", "Task expired before processing started.")
            result = run_pipeline(engine, data, langs, settings.max_pixels, options)
            results.put(("done", worker_id, (task_id, True, result)))
        except ApiError as exc:
            results.put(
                ("done", worker_id, (task_id, False, (exc.status_code, exc.code, exc.message)))
            )
        except Exception:
            logger.exception("engine error", extra={"worker": worker_id})
            results.put(
                (
                    "done",
                    worker_id,
                    (
                        task_id,
                        False,
                        (500, "ocr_failed", "OCR engine failed to process the image."),
                    ),
                )
            )
        finally:
            request_id_var.reset(token)

    engine.close()
