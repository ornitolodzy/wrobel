"""Engine factories keyed by ``OCR_ENGINE``. Heavy imports happen lazily inside factories."""

from __future__ import annotations

import importlib
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


def _from_import_path(path: str) -> EngineFactory:
    """``package.module:ClassOrFactory`` - a callable taking Settings and returning an engine.

    Works across process boundaries (worker processes re-import it), which makes it suitable
    for out-of-tree plugins and test doubles.
    """
    module_name, _, attr = path.partition(":")
    try:
        factory: EngineFactory = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"Cannot import OCR engine {path!r}: {exc}") from exc
    return factory


def create_engine(settings: Settings) -> OcrEngine:
    name = settings.engine
    if ":" in name:
        return _from_import_path(name)(settings)
    try:
        factory = ENGINES[name]
    except KeyError:
        raise ValueError(
            f"Unknown OCR engine {name!r}. Available: {sorted(ENGINES)} "
            "or an import path 'package.module:Class'."
        ) from None
    return factory(settings)
