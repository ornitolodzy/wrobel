"""Importable test engine for worker processes (``OCR_ENGINE=tests.fake_engine:ProcessFakeEngine``).

Behaviour is driven by the processed image width so tests can trigger it per request:
  width 13 -> the worker process dies (simulated native crash)
  width 17 -> the worker hangs (simulated stuck inference)
Env vars (inherited by spawned workers): FAKE_OCR_DELAY_S, FAKE_OCR_FAIL_LOAD.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from ocr_service.config import Settings
from ocr_service.engines.base import OcrEngine, TextBlock

CRASH_WIDTH = 13
HANG_WIDTH = 17


class ProcessFakeEngine(OcrEngine):
    name = "fake-process"

    def __init__(self, settings: Settings) -> None:
        self._langs = frozenset(settings.langs)
        self._delay = float(os.environ.get("FAKE_OCR_DELAY_S", "0"))

    @property
    def supported_langs(self) -> frozenset[str]:
        return self._langs

    def load(self) -> None:
        if os.environ.get("FAKE_OCR_FAIL_LOAD") == "1":
            raise RuntimeError("weights not found")

    def recognize(self, image: NDArray[np.uint8], langs: Sequence[str]) -> list[TextBlock]:
        h, w = image.shape[:2]
        if w == CRASH_WIDTH:
            os._exit(3)
        if w == HANG_WIDTH:
            time.sleep(3600)
        if self._delay:
            time.sleep(self._delay)
        return [TextBlock(text=f"pid {os.getpid()}", bbox=(0, 0, w, h), confidence=0.9)]
