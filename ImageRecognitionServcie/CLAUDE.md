# CLAUDE.md — ScreenScrapper OCR

Local OCR microservice: HTTP in, text from screenshots out (Polish + English). FastAPI + PaddleOCR 3.7 (PP-OCRv5), Python 3.12, managed with uv. Fully offline at runtime: no external APIs, model weights are downloaded once (`./models` locally, baked into the Docker image).

## Working agreements (from the user)

- **Commit and push after every finished task** (branch `develop`) so any step can be rolled back. Only commit working states (tests + lint green). Never force-push or rewrite history.
- **Code comments, docstrings and identifiers: English.** README and the user-facing texts of `start-tester.bat` / `tools/ocr-tester.html` are Polish. Talk to the user in Polish.
- **Throughput under many concurrent requests is the priority**; the production server will be much stronger than the dev laptop. Keep CPU work out of the API process and event loop, and measure before/after (`scripts/loadtest.py`).

## Commands

`uv` may not be on PATH on the dev machine — use `python -m uv` there.

```powershell
uv sync                                          # install from uv.lock (Python 3.12)
uv run ocr-service                               # run the API on :8000 (reads .env)
uv run python -m ocr_service.download_models     # fetch/warm model weights into OCR_MODEL_DIR
uv run pytest -m "not ocr"                       # fast suite (~15 s, spawns worker processes)
uv run pytest                                    # + real PaddleOCR e2e tests (needs weights)
uv run ruff check src tests examples scripts
uv run ruff format src tests examples scripts
uv run mypy                                      # strict, src only
uv run python scripts/loadtest.py img.png -c 16 -n 100   # throughput / latency
start-tester.bat                                 # one-click: start service + open HTML tester
```

Set `OCR_MODEL_DIR=models` for the e2e tests to reuse the local weights. CI (`.github/workflows/ci.yml`) runs ruff, mypy and pytest, then builds the Docker image and smoke-tests it with `--network none`. There is no Docker on the dev machine, so the Dockerfiles are only validated in CI.

## Architecture (`src/ocr_service/`)

Request path: `middleware.py` (body size limit, request id, access log, HTTP metrics) → `api/routes.py` (multipart parsed **in memory**, base64 JSON decoded in a thread) → `service.py` `OcrService.recognize()` (admission → `503 busy`, wait for a slot → `503 queue_timeout`) → `backends.py` → worker → `pipeline.py` `run_pipeline()` (decode → preprocess → `engine.recognize()` → map boxes back to original coords) → back in `service.py`: reading order + language detection (`text.py`).

- **`backends.py`**
  - `ProcessBackend` (default, `OCR_EXECUTOR=process`): N `spawn`ed processes running `worker_process.worker_main`. They pull from one shared `multiprocessing.Queue`; results come back via a reader thread into asyncio futures. A monitor restarts dead workers with exponential backoff; a request hung longer than `OCR_TASK_TIMEOUT_S` gets 504 and its worker is killed.
  - `ThreadBackend`: engine instances in threads. Used for tests and debugging, and whenever `create_app(engine_factory=...)` is given.
- **`worker_process.py`** must stay free of FastAPI/web imports. That is why `ApiError` lives in the lightweight `exceptions.py` (picklable via `__reduce__`); `errors.py` only holds the FastAPI handlers.
- **`engines/`**: `OcrEngine` ABC (`supported_langs`, `load()` once, `recognize(rgb_array, langs) -> list[TextBlock]`). Instances are never shared between threads, so engines need not be thread-safe. Register new engines in `registry.py`, or point `OCR_ENGINE=pkg.module:Class` at an import path (works across processes).
- **`config.py`**: every setting comes from `OCR_*` env vars (pydantic-settings, `.env` supported). `effective_workers` = `OCR_WORKERS`, or with `0` (auto) cgroup-aware CPUs / `OCR_PADDLE_CPU_THREADS`, capped by memory (`OCR_WORKER_MEMORY_MB`).
- **Errors** are always `{"error": {"code", "message"}}`; raise `ApiError(status, code, message)` anywhere, including inside workers.
- **Probes**: `/healthz` answers immediately. `/readyz` returns 200 only once all workers have loaded; models load in the background after startup. `/metrics` exposes Prometheus metrics. `/docs` serves the OpenAPI UI.
- **Async queue mode** (Redis + worker) is not built yet. `OcrService.recognize(bytes, langs, output)` is transport-agnostic so it can be reused there.

## Gotchas

- **oneDNN on Windows:** PaddlePaddle 3.2–3.3 on Windows crashes with oneDNN enabled (`ConvertPirAttribute2RuntimeAttribute not support`), so `OCR_PADDLE_ENABLE_MKLDNN=auto` disables it on win32. Windows inference is therefore slow (~6–9 s per 1080p screenshot); Linux uses oneDNN.
- **Env read at import:** PaddleX reads env vars (`PADDLE_PDX_CACHE_HOME`, `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK`) at import time. `engines/paddle.py` sets them before importing `paddleocr`; keep that order.
- **Lock files in the model dir:** PaddleX writes `locks/` and `temp/` next to the weights, so the model dir must be writable by the runtime user (the Dockerfile `chown`s it).
- **Model choice is explicit:** setting `OCR_PADDLE_DET_MODEL` / `OCR_PADDLE_REC_MODEL` bypasses PaddleOCR's lang-based selection; `lang="pl"` alone would pick the heavier PP-OCRv6 medium. Defaults: `PP-OCRv5_mobile_det` + `latin_PP-OCRv5_mobile_rec` (covers pl + en).
- **Use processes, not threads:** threads barely scale because of GIL contention. Measured locally: thread pool 4×2 = 0.17 img/s vs processes 8×1 = 0.37 img/s. Don't move OCR back into the API process.
- **Process-mode tests:** tests using real worker processes must use an importable engine (`tests/fake_engine.py:ProcessFakeEngine`, behaviour driven by image width/env vars), not lambdas. `tests/conftest.py:FakeEngine` + `engine_factory` covers the in-process path.
- **CORS:** off by default; `start-tester.bat` sets `OCR_CORS_ORIGINS=*` so the `file://` tester page can call the API.
- **Line endings:** `.gitattributes` forces LF, except CRLF for `.bat`/`.cmd`. Keep `start-tester.bat` CRLF.
- **Dependencies:** `uv.lock` uses `exclude-newer` (no releases younger than ~2 weeks). PaddleX pulls `opencv-contrib-python`; don't add another OpenCV distribution.
