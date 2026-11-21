"""Pluggable text embeddings (same idea as LLM providers): config decides where they run."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class MockEmbedder:
    """Deterministic pseudo-embeddings from word hashes (bag of words, L2-normalized).
    No network; texts sharing words get similar vectors, which is enough for tests."""

    def __init__(self, params: dict):
        self.model = "mock"
        self.dim = params.get("dim", 1024)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in t.lower().split():
                h = int.from_bytes(hashlib.blake2b(w[:5].encode(), digest_size=8).digest(), "big")
                v[h % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


_overrides: dict[str, Embedder] = {}


def override(name: str, embedder: Embedder) -> None:
    _overrides[name] = embedder


def get_embedder(params: dict) -> Embedder:
    name = params.get("provider", "mock")
    if name in _overrides:
        return _overrides[name]
    if name == "mock":
        return MockEmbedder(params)
    if name == "ollama":
        from .ollama import OllamaEmbedder

        return OllamaEmbedder(params)
    raise ValueError(f"unknown embeddings provider: {name!r}")
