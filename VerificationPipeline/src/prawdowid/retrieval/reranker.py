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
    where = "local"

    def __init__(self, params: dict):
        self.model = params.get("model", "BAAI/bge-reranker-v2-m3")
        self.max_length = params.get("max_length", 512)
        self.batch = params.get("batch", 8)
        self._tok = self._net = None
        self._lock = threading.Lock()
        # One scoring at a time: torch already uses every CPU core, so parallel requests only
        # thrash each other (measured: 31 s alone, 65 s each with three at once).
        self._infer_lock = threading.Lock()

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
        with self._infer_lock, torch.no_grad():
            for i in range(0, len(texts), self.batch):
                part = texts[i:i + self.batch]
                enc = self._tok([query] * len(part), part, padding=True, truncation=True,
                                max_length=self.max_length, return_tensors="pt")
                logits = self._net(**enc).logits.view(-1).tolist()
                out += [1 / (1 + math.exp(-x)) for x in logits]  # sigmoid -> 0..1
        return out


class RerankerError(RuntimeError):
    pass


class RemoteReranker:
    """Same model and 0..1 scale, served on a GPU (deploy/rerank-server, reached via SSH tunnel).
    If the server is unreachable and `fallback: local` is set (and torch is installed), the
    local CPU reranker takes over, so a dropped tunnel slows checks down instead of failing them."""

    where = "remote"

    def __init__(self, params: dict):
        self.model = params.get("model", "BAAI/bge-reranker-v2-m3")
        self.api_base = params.get("api_base", "http://host.docker.internal:8081").rstrip("/")
        self.max_length = params.get("max_length", 512)
        self.timeout = params.get("timeout_s", 30)
        self.fallback = CrossEncoderReranker(params) if params.get("fallback") == "local" else None

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        import httpx

        from prawdowid.llm import usage

        try:
            r = httpx.post(f"{self.api_base}/rerank", timeout=self.timeout,
                           json={"query": query, "texts": texts, "max_length": self.max_length})
            r.raise_for_status()
            data = r.json()
            scores = data["scores"]
            if len(scores) != len(texts):
                raise RerankerError(f"got {len(scores)} scores for {len(texts)} texts")
            usage.record(role="reranker_remote", api_base=self.api_base, device=data.get("device"),
                         server_ms=data.get("ms"), scored=len(texts))
            return [float(s) for s in scores]
        except (httpx.HTTPError, KeyError, ValueError, RerankerError) as e:
            usage.record(role="reranker_remote", event="remote_failed", api_base=self.api_base,
                         error=f"{type(e).__name__}: {e}"[:300],
                         fallback="local" if self.fallback else None)
            if self.fallback is None:
                raise RerankerError(f"reranker {self.api_base}: {e}") from e
            return self.fallback.score(query, texts)


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
    provider = params.get("provider", "local")
    key = f"{provider}:{params.get('model', 'BAAI/bge-reranker-v2-m3')}:{params.get('api_base', '')}"
    if key not in _cache:
        if provider == "remote":
            _cache[key] = RemoteReranker(params)
        elif provider == "local":
            _cache[key] = CrossEncoderReranker(params)
        else:
            raise ValueError(f"unknown reranker provider: {provider!r}")
    return _cache[key]
