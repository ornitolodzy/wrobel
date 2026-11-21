"""Engine-agnostic OCR interface.

New back-ends (Tesseract, a local VLM such as Qwen-VL via Ollama/vLLM, ...) implement
:class:`OcrEngine` and register a factory in :mod:`ocr_service.engines.registry`.
The HTTP API, preprocessing, concurrency control and (future) queue workers stay unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

BBox = tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    bbox: BBox
    confidence: float


class OcrEngine(ABC):
    """A loaded OCR model.

    Contract:
    * :meth:`load` is called exactly once, at process start-up, in a worker thread.
    * :meth:`recognize` is blocking/CPU-bound and is always called from a worker thread.
      One instance is never used by two threads at the same time - the service keeps a pool
      of instances instead, so implementations do not need to be thread-safe.
    """

    #: Short identifier reported in API responses and metrics.
    name: str = "base"

    @property
    @abstractmethod
    def supported_langs(self) -> frozenset[str]:
        """ISO 639-1 codes this engine instance can read."""

    @abstractmethod
    def load(self) -> None:
        """Load model weights and warm up. May take a long time."""

    @abstractmethod
    def recognize(self, image: NDArray[np.uint8], langs: Sequence[str]) -> list[TextBlock]:
        """Return text blocks found in an RGB ``H x W x 3`` uint8 image.

        Bounding boxes are in the coordinates of ``image``. Order does not matter - the
        service sorts blocks into reading order.
        """

    def close(self) -> None:  # noqa: B027 - optional hook, intentionally empty
        """Release resources (optional)."""
