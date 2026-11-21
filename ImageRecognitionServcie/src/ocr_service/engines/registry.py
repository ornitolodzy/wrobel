"""Engine factories keyed by ``OCR_ENGINE``. Heavy imports happen lazily inside factories."""

from __future__ import annotations

from collections.abc import Callable

from ocr_service.config import Settings
from ocr_service.engines.base import OcrEngine

EngineFactory = Callable[[Settings], OcrEngine]


def _paddleocr(settings: Settings) -> OcrEngine:
    from ocr_service.engines.paddle import PaddleOcrEngine

    return PaddleOcrEngine(settings)


# To add an engine: implement OcrEngine in engines/<name>.py and register it here,
# e.g. "tesseract": _tesseract, "qwen-vl": _qwen_vl.
ENGINES: dict[str, EngineFactory] = {
    "paddleocr": _paddleocr,
}


def create_engine(settings: Settings) -> OcrEngine:
    try:
        factory = ENGINES[settings.engine]
    except KeyError:
        raise ValueError(
            f"Unknown OCR engine {settings.engine!r}. Available: {sorted(ENGINES)}"
        ) from None
    return factory(settings)
