"""Corpus store fed by the PageScrapper (ingestion worker in the shared Postgres).

The scrapper owns fetching (catalog, robots.txt, SSRF checks) and keeps raw pages in its
`documents` table. This module turns them into what retrieval reads: `source` / `document` /
`chunk` rows with a tsvector and an embedding. Two entry points:

- store_documents(): one or more documents pushed by the scrapper (RAG_INGEST_URL -> /ingest)
- sync_from_scrapper(): backfill from the scrapper's `documents` table (startup, /ingest/sync)

Unchanged documents (same text hash, same embedding model) are skipped, so both are idempotent.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from typing import Any

from prawdowid.catalog import domain_of, load_catalog, match_source
from prawdowid.runtime import PROJECT_ROOT
from prawdowid.text import chunk_text

logger = logging.getLogger("prawdowid.corpus")

# PageScrapper source_type -> our source type (policy weights live in parameters.yml).
SCRAPPER_TYPES = {"official": "primary", "fact_check": "factcheck", "disinfo_db": "secondary"}


def doc_id(url: str) -> str:
    return "doc:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def ensure_schema(db_params: dict | None = None) -> None:
    """Apply db/schema.sql (all statements are IF NOT EXISTS; safe on every start).
    Plain connection: db.connect() registers the `vector` type, which exists only after this runs."""
    import psycopg

    from prawdowid.db import database_url

    sql = (PROJECT_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url(db_params), connect_timeout=5) as conn:
        conn.execute(sql)


def _as_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _upsert_catalog(conn, catalog: tuple[dict, ...]) -> dict[tuple[str, str], int]:
    ids = {}
    for src in catalog:
        key = (src["domain"].lower(), src.get("path", ""))
        row = conn.execute(
            """INSERT INTO source (domain, path, publisher, type, license, update_policy, active)
               VALUES (%s, %s, %s, %s, %s, %s, true)
               ON CONFLICT (domain, path) DO UPDATE SET publisher = EXCLUDED.publisher,
                   type = EXCLUDED.type, license = EXCLUDED.license,
                   update_policy = EXCLUDED.update_policy
               RETURNING id""",
            (*key, src["publisher"], src["type"], src.get("license"), src.get("update")),
        ).fetchone()
        ids[key] = row[0]
    return ids


def _source_id(conn, doc: dict, catalog: tuple[dict, ...], known: dict[tuple[str, str], int]) -> int:
    """Catalog entry wins (audited publisher/type); otherwise the scrapper's own metadata."""
    src = match_source(doc["url"], catalog)
    if src is not None:
        return known[(src["domain"].lower(), src.get("path", ""))]
    key = (domain_of(doc["url"]), "")
    if key in known:
        return known[key]
    stype = SCRAPPER_TYPES.get(doc.get("source_type") or "", "secondary")
    conn.execute(
        """INSERT INTO source (domain, path, publisher, type, license, active)
           VALUES (%s, %s, %s, %s, %s, true) ON CONFLICT (domain, path) DO NOTHING""",
        (*key, doc.get("publisher") or key[0], stype, "wg katalogu PageScrapper"),
    )
    known[key] = conn.execute(
        "SELECT id FROM source WHERE domain = %s AND path = %s", key).fetchone()[0]
    return known[key]


def store_documents(docs: list[dict], params: dict) -> dict:
    """Chunk + embed + store. `docs` use the PageScrapper RAG payload
    ({url, source_type, title, publisher, published_at, retrieved_at, text, ...})."""
    import numpy as np

    from prawdowid.db import connect
    from prawdowid.embeddings import get_embedder

    ing = params.get("ingestion", {})
    embedder = get_embedder(params.get("embeddings", {}))
    catalog = load_catalog(ing.get("sources_path", "conf/base/sources.yml"))
    docs = [d for d in docs if d.get("url") and (d.get("text") or "").strip()]
    stats: dict[str, Any] = {"received": len(docs), "changed": 0, "unchanged": 0,
                             "chunks": 0, "embedding_model": embedder.model}
    if not docs:
        return stats

    with connect(params.get("db")) as conn:
        known = _upsert_catalog(conn, catalog)
        last = conn.execute(
            "SELECT id, embedding_model FROM corpus_version ORDER BY id DESC LIMIT 1").fetchone()
        model_changed = last is not None and last[1] != embedder.model
        existing = dict(conn.execute(
            "SELECT url, content_hash FROM document WHERE url = ANY(%s)",
            ([d["url"] for d in docs],)).fetchall())

        changed = []
        for d in docs:
            d["content_hash"] = hashlib.sha256(d["text"].encode()).hexdigest()
            if model_changed or existing.get(d["url"]) != d["content_hash"]:
                changed.append(d)
        stats["unchanged"] = len(docs) - len(changed)
        if not changed:
            return stats

        # One corpus version per embedding model: incremental pushes extend it, a model switch
        # (or the first document) opens a new one. Checks record the version they searched.
        if last is None or model_changed:
            version = conn.execute(
                "INSERT INTO corpus_version (embedding_model, dim, n_docs, n_chunks, params) "
                "VALUES (%s, %s, 0, 0, '{\"source\": \"page-scrapper\"}') RETURNING id",
                (embedder.model, embedder.dim)).fetchone()[0]
        else:
            version = last[0]

        rows = []
        for d in changed:
            did = doc_id(d["url"])
            conn.execute(
                """INSERT INTO document (id, source_id, url, title, published_at, fetched_at,
                       content_hash, dup_group, seed_url, depth, extract_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 'page-scrapper')
                   ON CONFLICT (url) DO UPDATE SET source_id = EXCLUDED.source_id,
                       title = EXCLUDED.title, published_at = EXCLUDED.published_at,
                       fetched_at = EXCLUDED.fetched_at, content_hash = EXCLUDED.content_hash,
                       dup_group = EXCLUDED.dup_group, extract_version = EXCLUDED.extract_version""",
                (did, _source_id(conn, d, catalog, known), d["url"], d.get("title") or d["url"],
                 _as_datetime(d.get("published_at")),
                 _as_datetime(d.get("retrieved_at")) or datetime.now(timezone.utc),
                 d["content_hash"], "dup:" + d["content_hash"][:16], d.get("source_id")),
            )
            conn.execute("DELETE FROM chunk WHERE document_id = %s", (did,))
            pieces = chunk_text(d["text"], ing.get("chunk_chars", 1200), ing.get("chunk_overlap", 150))
            rows += [(f"{did}:{i}", did, i, t) for i, t in enumerate(pieces)]

        vectors = embedder.embed([r[3] for r in rows]) if rows else []
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunk (id, document_id, position, text, embedding, corpus_version) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(*r, np.array(v, dtype=np.float32), version) for r, v in zip(rows, vectors)],
            )
        n_docs, n_chunks = conn.execute(
            "SELECT (SELECT count(*) FROM document), (SELECT count(*) FROM chunk)").fetchone()
        conn.execute("UPDATE corpus_version SET n_docs = %s, n_chunks = %s WHERE id = %s",
                     (n_docs, n_chunks, version))
    stats.update(changed=len(changed), chunks=len(rows), corpus_version=version,
                 corpus_documents=n_docs, corpus_chunks=n_chunks)
    return stats


def sync_from_scrapper(params: dict, batch: int = 50) -> dict:
    """Backfill from the PageScrapper `documents` table in the same Postgres."""
    from prawdowid.db import connect

    with connect(params.get("db")) as conn:
        exists = conn.execute("SELECT to_regclass('public.documents') IS NOT NULL").fetchone()[0]
        if not exists:
            return {"scrapper_documents": 0, "detail": "brak tabeli documents (PageScrapper jeszcze nie wystartował)"}
        rows = conn.execute(
            """SELECT url, source_id, source_type, title, publisher, published_at, retrieved_at, text
               FROM documents ORDER BY id""").fetchall()
    cols = ("url", "source_id", "source_type", "title", "publisher", "published_at", "retrieved_at", "text")
    docs = [dict(zip(cols, r)) for r in rows]
    total: dict[str, Any] = {"scrapper_documents": len(docs), "changed": 0, "unchanged": 0, "chunks": 0}
    for i in range(0, len(docs), batch):
        part = store_documents(docs[i:i + batch], params)
        for k in ("changed", "unchanged", "chunks"):
            total[k] += part[k]
        total.update({k: v for k, v in part.items() if k.startswith("corpus") or k == "embedding_model"})
    return total
