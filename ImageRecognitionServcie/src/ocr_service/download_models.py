"""Download (and warm up) model weights for the configured engine.

Used at Docker build time and in CI, so the running service never needs the internet:
``python -m ocr_service.download_models``
"""

from __future__ import annotations

import logging
import sys
import time

from ocr_service.config import get_settings
from ocr_service.engines import create_engine
from ocr_service.logging_setup import configure_logging

logger = logging.getLogger("ocr_service.download_models")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    started = time.perf_counter()
    try:
        engine = create_engine(settings)
        engine.load()
        engine.close()
    except Exception:
        logger.exception("model download failed")
        return 1
    logger.info(
        "models ready",
        extra={
            "engine": settings.engine,
            "model_dir": settings.model_dir or "~/.paddlex",
            "seconds": round(time.perf_counter() - started, 1),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
