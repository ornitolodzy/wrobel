"""JSON logging for the service, uvicorn and third-party libraries."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else was passed via ``extra=`` and gets serialised.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "color_message",  # uvicorn's ANSI-coloured duplicate of msg
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")
        }
        return f"{base} {extras}" if extras else base


def configure_logging(level: str = "INFO", json_format: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_format else TextFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Route uvicorn/paddle loggers through the root handler; access log comes from our middleware.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "paddlex", "paddle", "ppocr"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("paddlex", "paddle", "ppocr", "PIL", "multipart"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLevelName(level)))
