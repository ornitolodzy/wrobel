"""Chunk embeddings. Same call as the Verification Pipeline's OllamaEmbedder (POST /api/embed),
so the corpus and the queries are embedded by the same model.

With provider "none" chunks are stored without vectors; `POST /embeddings/backfill` fills them
later (e.g. once the SSH tunnel to the Bielik / bge-m3 server is up).
"""

from __future__ import annotations

from typing import Protocol

import httpx

from context_graph.config import Settings


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model: str | None

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class NoEmbedder:
    model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("embeddings are disabled (EMBEDDINGS_PROVIDER=none)")


class OllamaEmbedder:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.model = settings.embeddings_model
        self.dim = settings.embeddings_dim
        self.base = settings.embeddings_api_base.rstrip("/")
        self.batch = settings.embeddings_batch
        self.client = httpx.Client(timeout=settings.embeddings_timeout_s, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            part = texts[i : i + self.batch]
            try:
                r = self.client.post(f"{self.base}/api/embed", json={"model": self.model, "input": part})
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise EmbeddingError(f"ollama {self.model}: {e}") from e
            vecs = r.json().get("embeddings") or []
            if len(vecs) != len(part):
                raise EmbeddingError(f"ollama {self.model}: got {len(vecs)} vectors for {len(part)} texts")
            if vecs and len(vecs[0]) != self.dim:
                raise EmbeddingError(f"ollama {self.model}: dim {len(vecs[0])} != configured {self.dim}")
            out += vecs
        return out


def make_embedder(settings: Settings) -> Embedder:
    if settings.embeddings_provider == "ollama":
        return OllamaEmbedder(settings)
    return NoEmbedder()
