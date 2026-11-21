"""Entry point: ``python -m ocr_service`` (or the ``ocr-service`` script)."""

from __future__ import annotations

import uvicorn

from ocr_service.config import get_settings
from ocr_service.logging_setup import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    # One process: models are loaded once per process, scale with OCR_WORKERS or more replicas.
    uvicorn.run(
        "ocr_service.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
