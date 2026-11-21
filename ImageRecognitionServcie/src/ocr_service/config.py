"""Service configuration. Everything comes from environment variables (prefix ``OCR_``)."""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
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

    # Number of engine instances loaded at start-up == max number of images OCR-ed in parallel.
    workers: int = Field(default=1, ge=1, le=64)
    # How many requests may wait for a free engine before new ones are rejected with 503.
    max_queue: int = Field(default=16, ge=0)
    # How long (seconds) a request may wait for a free engine.
    queue_timeout_s: float = Field(default=30.0, gt=0)

    # --- Input limits ------------------------------------------------------
    max_file_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    max_pixels: int = Field(default=40_000_000, ge=10_000)

    # --- Security ----------------------------------------------------------
    api_key: SecretStr | None = None

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
    paddle_cpu_threads: int = Field(default=4, ge=1)
    paddle_enable_mkldnn: MkldnnMode = "auto"
    paddle_min_score: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("langs", mode="before")
    @classmethod
    def _split_langs(cls, value: object) -> list[str]:
        langs = parse_lang_list(value)
        if not langs:
            raise ValueError("at least one language is required")
        return langs

    @field_validator("api_key", "model_dir", mode="before")
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
    def max_request_bytes(self) -> int:
        """Upper bound for the raw HTTP body: base64 overhead (4/3) + room for multipart headers."""
        return self.max_file_bytes * 4 // 3 + 64 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
