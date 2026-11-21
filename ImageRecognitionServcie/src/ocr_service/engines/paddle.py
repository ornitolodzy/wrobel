"""PaddleOCR 3.x engine (PP-OCR pipeline: text detection + recognition)."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ocr_service.config import Settings
from ocr_service.engines.base import OcrEngine, TextBlock

logger = logging.getLogger(__name__)

# The PP-OCR "latin" recognition models cover Polish (incl. diacritics) and English together,
# so one model serves any combination of these languages.
SUPPORTED_LANGS = frozenset({"pl", "en"})


def configure_paddle_environment(settings: Settings) -> None:
    """Must run before ``paddleocr``/``paddlex`` is imported - they read env vars at import time."""
    if settings.model_dir:
        model_dir = os.path.abspath(settings.model_dir)
        os.makedirs(model_dir, exist_ok=True)
        os.environ["PADDLE_PDX_CACHE_HOME"] = model_dir
    if settings.offline:
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ.setdefault("GLOG_minloglevel", "2")  # silence paddle C++ INFO logs
    os.environ.setdefault("FLAGS_logtostderr", "0")


class PaddleOcrEngine(OcrEngine):
    name = "paddleocr"

    def __init__(self, settings: Settings) -> None:
        unsupported = set(settings.langs) - SUPPORTED_LANGS
        if unsupported:
            raise ValueError(
                f"PaddleOCR engine does not support languages: {sorted(unsupported)}. "
                f"Supported: {sorted(SUPPORTED_LANGS)}"
            )
        self._settings = settings
        self._langs = frozenset(settings.langs)
        self._ocr: Any = None

    @property
    def supported_langs(self) -> frozenset[str]:
        return self._langs

    def load(self) -> None:
        settings = self._settings
        configure_paddle_environment(settings)

        from paddleocr import PaddleOCR

        # paddlex attaches its own colourful stdout handler on import; use ours instead.
        for name in ("paddlex", "paddleocr", "ppocr"):
            lib_logger = logging.getLogger(name)
            lib_logger.handlers.clear()
            lib_logger.propagate = True
            lib_logger.setLevel(logging.WARNING)

        logger.info(
            "loading PaddleOCR",
            extra={
                "det_model": settings.paddle_det_model,
                "rec_model": settings.paddle_rec_model,
                "device": settings.paddle_device,
                "mkldnn": settings.mkldnn_enabled,
                "cpu_threads": settings.paddle_cpu_threads,
            },
        )
        self._ocr = PaddleOCR(
            text_detection_model_name=settings.paddle_det_model,
            text_recognition_model_name=settings.paddle_rec_model,
            # Screenshots are upright and flat - skip document orientation/unwarping models.
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_rec_score_thresh=settings.paddle_min_score,
            device=settings.paddle_device,
            enable_mkldnn=settings.mkldnn_enabled,
            cpu_threads=settings.paddle_cpu_threads,
        )
        # Warm-up: first inference initialises kernels; do it before reporting ready.
        self.recognize(np.full((64, 256, 3), 255, dtype=np.uint8), [])

    def recognize(self, image: NDArray[np.uint8], langs: Sequence[str]) -> list[TextBlock]:
        if self._ocr is None:
            raise RuntimeError("PaddleOCR engine is not loaded")
        bgr = np.ascontiguousarray(image[:, :, ::-1])  # PaddleX expects OpenCV-style BGR arrays
        blocks: list[TextBlock] = []
        for result in self._ocr.predict(bgr):
            texts = result["rec_texts"]
            scores = result["rec_scores"]
            boxes = result["rec_boxes"]
            for text, score, box in zip(texts, scores, boxes, strict=False):
                text = str(text).strip()
                if not text:
                    continue
                x1, y1, x2, y2 = (int(v) for v in box[:4])
                blocks.append(TextBlock(text=text, bbox=(x1, y1, x2, y2), confidence=float(score)))
        return blocks

    def close(self) -> None:
        self._ocr = None
