"""Retrieval boundary. Verification depends on this interface, not on a database.

The corpus (ingestion, chunks, embeddings) may later live in a separate knowledge-base
service; only a new Retriever adapter is needed then (see docs/adr/0002-bazy-danych.md).
"""

from __future__ import annotations

from typing import Protocol

from prawdowid.models import Evidence, Query


class Retriever(Protocol):
    def search(self, query: Query, params: dict) -> list[Evidence]:
        """Return reranked candidates (best first) for one claim's query."""
        ...


class EmptyRetriever:
    """Mock backend: no corpus -> every factual claim ends as brak_dowodow."""

    def search(self, query: Query, params: dict) -> list[Evidence]:
        return []


_overrides: dict[str, Retriever] = {}


def override(backend: str, retriever: Retriever) -> None:
    _overrides[backend] = retriever


def get_retriever(params: dict) -> Retriever:
    backend = params.get("backend", "mock")
    if backend in _overrides:
        return _overrides[backend]
    if backend == "mock":
        return EmptyRetriever()
    raise NotImplementedError(f"retrieval backend {backend!r}: implement a Retriever adapter")
