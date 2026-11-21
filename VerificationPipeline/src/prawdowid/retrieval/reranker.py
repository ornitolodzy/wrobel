"""Cross-encoder reranker (bge-reranker-v2-m3): scores (claim, chunk) pairs on one 0..1 scale,
so corpus chunks (RRF) and web pages (keyword overlap) become comparable. CPU, lazy-loaded.

Optional extra: pip install -e ".[rerank]" (torch CPU + transformers).
"""

from __future__ import annotations

import math
import threading
from typing import Protocol


class Reranker(Protocol):
    model: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, params: dict):
        self.model = params.get("model", "BAAI/bge-reranker-v2-m3")
        self.max_length = params.get("max_length", 512)
        self.batch = params.get("batch", 8)
        self._tok = self._net = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._net is None:
                import torch  # noqa: F401  (lazy: heavy)
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                self._tok = AutoTokenizer.from_pretrained(self.model)
                self._net = AutoModelForSequenceClassification.from_pretrained(self.model).eval()

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        self._load()
        import torch

        out: list[float] = []
        with torch.no_grad():
            for i in range(0, len(texts), self.batch):
                part = texts[i:i + self.batch]
                enc = self._tok([query] * len(part), part, padding=True, truncation=True,
                                max_length=self.max_length, return_tensors="pt")
                logits = self._net(**enc).logits.view(-1).tolist()
                out += [1 / (1 + math.exp(-x)) for x in logits]  # sigmoid -> 0..1
        return out


_cache: dict[str, Reranker] = {}
_override: Reranker | None = None


def override(reranker: Reranker | None) -> None:
    global _override
    _override = reranker


def get_reranker(params: dict) -> Reranker | None:
    if _override is not None:
        return _override
    if not params.get("enabled", False):
        return None
    key = params.get("model", "BAAI/bge-reranker-v2-m3")
    if key not in _cache:
        _cache[key] = CrossEncoderReranker(params)
    return _cache[key]
