"""Embeddings from an Ollama server (e.g. bge-m3 next to Bielik, via the SSH tunnel)."""

from __future__ import annotations

from .base import EmbeddingError


class OllamaEmbedder:
    def __init__(self, params: dict):
        self.model = params.get("model", "bge-m3")
        self.dim = params.get("dim", 1024)
        self.base = params.get("api_base", "http://127.0.0.1:11435").rstrip("/")
        self.batch = params.get("batch", 16)
        self.timeout = params.get("timeout_s", 60)

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            part = texts[i:i + self.batch]
            try:
                r = httpx.post(f"{self.base}/api/embed", timeout=self.timeout,
                               json={"model": self.model, "input": part})
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
