"""Offline corpus ingestion: source catalog -> fetch -> chunk -> dedup -> embed -> Postgres.

Skeleton: each node is a TODO with the intended contract. Fetched content is
untrusted data. Never bypass paywalls (task spec).
"""

from __future__ import annotations

import logging

from prawdowid.catalog import load_catalog, seed_urls

log = logging.getLogger(__name__)


def load_source_catalog(ingestion_params: dict) -> list[dict]:
    """One row per seed URL (start pages from the catalog), with its publisher and type."""
    catalog = load_catalog(ingestion_params["sources_path"])
    return [{"url": url, "publisher": src["publisher"], "type": src["type"]}
            for url, src in seed_urls(catalog)]


def fetch_documents(sources: list[dict]) -> list[dict]:
    # TODO: httpx + trafilatura (text, title, published_at), store fetched_at.
    log.warning("fetch_documents: not implemented, %d sources skipped", len(sources))
    return []


def chunk_documents(documents: list[dict], ingestion_params: dict) -> list[dict]:
    # TODO: split by paragraphs to ~chunk_chars with overlap.
    return []


def assign_dup_groups(chunks: list[dict]) -> list[dict]:
    # TODO: datasketch MinHash LSH on documents -> dup_group (reprints != independent).
    return chunks


def embed_and_store(chunks: list[dict], retrieval_params: dict) -> int:
    # TODO: embed with retrieval_params["embedding_model"], upsert into db (db/schema.sql).
    return len(chunks)
