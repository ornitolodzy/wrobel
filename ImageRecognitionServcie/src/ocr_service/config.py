"""Service configuration. Everything comes from environment variables (prefix ``OCR_``)."""

from __future__ import annotations

import math
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
MkldnnMode = Literal["auto", "true", "false"]


def parse_lang_list(value: object) -> list[str]:
    """Parse ``"pl, en"`` / ``["pl", "en"]`` into a normalised, de-duplicated list."""
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)  # type: ignore[call-overload]
    result: list[str] = []
    for item in items:
        lang = str(item).strip().lower()
        if lang and lang not in result:
            result.append(lang)
    return result


def _cgroup_cpu_limit() -> float | None:
    """CPU quota of the container (cgroup v2, then v1); None when unlimited/unknown."""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return int(quota) / int(period)
        return None
    except (OSError, ValueError):
        pass
    try:
        quota_us = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period_us = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        return quota_us / period_us if quota_us > 0 else None
    except (OSError, ValueError):
        return None


def available_cpus() -> int:
    """CPUs this process may actually use: affinity mask, capped by the container CPU quota."""
    try:
        cpus = len(os.sched_getaffinity(0))  # type: ignore[attr-defined,unused-ignore]
    except AttributeError:  # Windows / macOS
        cpus = os.cpu_count() or 1
    limit = _cgroup_cpu_limit()
    if limit is not None:
        cpus = min(cpus, max(1, math.floor(limit)))
    return max(1, cpus)


def available_memory_mb() -> int | None:
    """Memory limit of the container (cgroup v2/v1) or physical RAM on Linux; None if unknown."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 1 << 60:  # cgroup v1 reports "unlimited" as ~2^63
            return int(raw) // (1024 * 1024)
    sysconf = getattr(os, "sysconf", None)  # Unix only
    if sysconf is None:
        return None
    try:
        return int(sysconf("SC_PAGE_SIZE")) * int(sysconf("SC_PHYS_PAGES")) // (1024 * 1024)
    except (ValueError, OSError):
        return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OCR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- HTTP server -------------------------------------------------------
    host: str = "0.0.0.0"  # noqa: S104 - the service is meant to listen in a container
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: LogLevel = "INFO"
    log_json: bool = True

    # --- Engine ------------------------------------------------------------
    engine: str = "paddleocr"
    langs: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["pl", "en"])

    # process = each engine instance lives in its own worker process (scales across cores,
    #           no GIL contention, crashed/hung workers are restarted) - recommended;
    # thread  = engine instances in threads of the API process (debugging / tests).
    executor: Literal["process", "thread"] = "process"
    # Engine instances == max images OCR-ed in parallel. 0 = auto: available CPUs (cgroup-aware)
    # divided by paddle_cpu_threads.
    workers: int = Field(default=0, ge=0, le=256)
    # How many requests may wait for a free worker before new ones are rejected with 503.
    max_queue: int = Field(default=64, ge=0)
    # How long (seconds) a request may wait for a free worker.
    queue_timeout_s: float = Field(default=30.0, gt=0)
    # Max time for one image inside a worker; a hung process worker is killed and restarted.
    task_timeout_s: float = Field(default=120.0, gt=0)
    # Memory budget per worker used to cap OCR_WORKERS=0 (auto) by the container memory limit.
    worker_memory_mb: int = Field(default=700, ge=100)

    # Minimum recognition confidence (0-1) for a text block to be reported at all;
    # anything below is treated as "no text there".
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- Result delivery ---------------------------------------------------
    # When set, POST /v1/ocr answers 202 right away and the result is POSTed (JSON) here.
    # Empty = synchronous mode: the result is returned in the HTTP response.
    callback_url: AnyHttpUrl | None = None
    # Optional token sent to the callback receiver as "Authorization: Bearer <token>".
    callback_token: SecretStr | None = None
    callback_timeout_s: float = Field(default=10.0, gt=0)
    # Retries for network errors, 5xx and 429 (exponential backoff from callback_retry_backoff_s).
    callback_retries: int = Field(default=3, ge=0, le=20)
    callback_retry_backoff_s: float = Field(default=1.0, ge=0)

    # --- Input limits ------------------------------------------------------
    max_file_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    max_pixels: int = Field(default=40_000_000, ge=10_000)

    # --- Security ----------------------------------------------------------
    api_key: SecretStr | None = None
    # Browser origins allowed to call the API (CORS), e.g. "*" for the local test page.
    # Empty = CORS disabled (the default for service-to-service use).
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Preprocessing -----------------------------------------------------
    preprocess_upscale_target: int = Field(default=1280, ge=0)
    preprocess_max_upscale: float = Field(default=3.0, ge=1.0)
    preprocess_dark_mode: bool = True
    preprocess_dark_threshold: int = Field(default=110, ge=0, le=255)

    # --- Models / PaddleOCR -------------------------------------------------
    # Where model weights are stored (exported as PADDLE_PDX_CACHE_HOME). Empty = ~/.paddlex
    model_dir: str | None = None
    # true = never check model hosters at start-up (weights must already be in model_dir).
    offline: bool = False
    paddle_det_model: str = "PP-OCRv5_mobile_det"
    paddle_rec_model: str = "latin_PP-OCRv5_mobile_rec"
    paddle_device: str = "cpu"
    # Threads per worker. 1 = best throughput under load (one worker per core); raise it to
    # cut single-request latency when traffic is low.
    paddle_cpu_threads: int = Field(default=1, ge=1)
    paddle_enable_mkldnn: MkldnnMode = "auto"

    @field_validator("langs", mode="before")
    @classmethod
    def _split_langs(cls, value: object) -> list[str]:
        langs = parse_lang_list(value)
        if not langs:
            raise ValueError("at least one language is required")
        return langs

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return list(value) if value else []  # type: ignore[call-overload]

    @field_validator("api_key", "model_dir", "callback_url", "callback_token", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def mkldnn_enabled(self) -> bool:
        """oneDNN crashes in the Windows build of PaddlePaddle 3.x -> ``auto`` = not on Windows."""
        if self.paddle_enable_mkldnn == "auto":
            return sys.platform != "win32" and self.paddle_device.startswith("cpu")
        return self.paddle_enable_mkldnn == "true"

    @property
    def effective_workers(self) -> int:
        if self.workers > 0:
            return self.workers
        by_cpu = available_cpus() // self.paddle_cpu_threads
        memory = available_memory_mb()
        # ~512 MB stays reserved for the API process itself.
        by_memory = (memory - 512) // self.worker_memory_mb if memory else by_cpu
        return max(1, min(by_cpu, by_memory))

    @property
    def max_request_bytes(self) -> int:
        """Upper bound for the raw HTTP body: base64 overhead (4/3) + room for multipart headers."""
        return self.max_file_bytes * 4 // 3 + 64 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
