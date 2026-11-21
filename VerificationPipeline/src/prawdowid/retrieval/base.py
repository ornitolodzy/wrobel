"""Retrieval boundary. Verification depends on this interface, not on a database.

Backends: neo4j (knowledge graph of the other team, the only context source in production,
ADR 0005), postgres (our own corpus from the ingestion pipeline), mock (tests).
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
_instances: dict[tuple, Retriever] = {}


def override(backend: str, retriever: Retriever) -> None:
    _overrides[backend] = retriever


def get_retriever(params: dict) -> Retriever:
    backend = params.get("backend", "mock")
    if backend in _overrides:
        return _overrides[backend]
    if backend == "mock":
        return EmptyRetriever()
    if backend == "neo4j":
        from .neo4j import Neo4jRetriever

        key = ("neo4j", repr(sorted(params.get("neo4j", {}).items())),
               params.get("embeddings", {}).get("model"))
        if key not in _instances:  # reuse the driver (connection pool) between checks
            _instances[key] = Neo4jRetriever(params)
        return _instances[key]
    if backend == "postgres":
        from .postgres import PgRetriever

        return PgRetriever(params)
    raise NotImplementedError(f"retrieval backend {backend!r}: implement a Retriever adapter")
