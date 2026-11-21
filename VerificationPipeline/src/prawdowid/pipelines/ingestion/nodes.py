"""Offline corpus ingestion: catalog seeds -> crawl (1 level) -> dedup -> chunk -> embed -> Postgres.

Pages are fetched with the same safe Fetcher as web evidence (public IPs only, robots.txt, size and
time limits, no paywall bypass). Fetched text is untrusted data. The run is incremental: a document
whose text hash did not change is not re-embedded. Every run that changes the index records a new
corpus_version, which retrieval writes into each check's trace.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import numpy as np

from prawdowid.catalog import domain_of, load_catalog, match_source, seed_urls
from prawdowid.text import assign_dup_groups, chunk_text
from prawdowid.websearch.fetch import get_fetcher

log = logging.getLogger(__name__)

_SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx", ".xls", ".xlsx",
             ".odt", ".ods", ".mp3", ".mp4", ".rss", ".xml", ".svg")
_SKIP_PARTS = ("/kontakt", "/login", "/logowanie", "/szukaj", "/search", "/mapa-strony", "/rss",
               "/deklaracja-dostepnosci", "/polityka-prywatnosci", "/cookies", "/newsletter",
               "/en/", "/uk/", "/ru/", "/de/", "/fr/")


def _entry_key(src: dict | None) -> tuple[str, str] | None:
    return (src["domain"].lower(), src.get("path", "")) if src else None


def load_source_catalog(ingestion_params: dict) -> list[dict]:
    """One row per seed URL (start pages from the catalog), with its catalog entry."""
    catalog = load_catalog(ingestion_params["sources_path"])
    return [{"url": url, "publisher": src["publisher"], "type": src["type"],
             "domain": src["domain"].lower(), "path": src.get("path", "")}
            for url, src in seed_urls(catalog)]


def article_links(page_links: list[tuple[str, str]], seed: dict, catalog, limit: int,
                  min_anchor: int = 15) -> list[str]:
    """Links on a start page that look like articles of the SAME catalog entry (same institution):
    path with >= 2 segments, not a file, not navigation, anchor text long enough to be a headline."""
    seed_key, seed_url = (seed["domain"], seed["path"]), seed["url"].split("#")[0].rstrip("/")
    out = []
    for url, anchor in page_links:
        clean = url.split("#")[0]
        p = urlparse(clean)
        low = p.path.lower()
        if clean.rstrip("/") == seed_url or low.endswith(_SKIP_EXT) or any(s in low for s in _SKIP_PARTS):
            continue
        if len([s for s in p.path.split("/") if s]) < 2 or len(anchor.strip()) < min_anchor:
            continue
        if _entry_key(match_source(clean, catalog)) != seed_key or clean in out:
            continue
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def _document(page, seed: dict, depth: int, now: datetime) -> dict:
    return {
        "url": page.url, "title": page.title or page.url, "text": page.text,
        "published_at": page.published_at, "fetched_at": now, "http_status": page.http_status,
        "domain": domain_of(page.url), "source_key": (seed["domain"], seed["path"]),
        "publisher": seed["publisher"], "type": seed["type"], "seed_url": seed["url"],
        "depth": depth, "content_hash": hashlib.sha256(page.text.encode()).hexdigest(),
    }


def _crawl_domain(seeds: list[dict], params: dict, catalog, fetcher, log_rows: list) -> list[dict]:
    """Sequential within one domain (politeness delay); domains run in parallel."""
    delay, min_chars = params.get("delay_s", 1.0), params.get("min_chars", 200)
    docs, seen = [], set()
    now = datetime.now(timezone.utc)

    def get(url: str):
        if url in seen:
            return None
        seen.add(url)
        try:
            page = fetcher.fetch(url)
        except Exception as e:
            log_rows.append({"url": url, "status": "error", "reason": f"{type(e).__name__}: {e}"})
            return None
        finally:
            time.sleep(delay)
        if page is None or len(page.text) < min_chars:
            log_rows.append({"url": url, "status": "no_text"})
            return None
        return page

    for seed in seeds:
        page = get(seed["url"])
        if page is None:
            continue
        docs.append(_document(page, seed, 0, now))
        for link in article_links(page.links, seed, catalog, params.get("links_per_seed", 15)):
            if len(docs) >= params.get("max_pages_per_domain", 120):
                break
            sub = get(link)
            if sub is not None:
                docs.append(_document(sub, seed, 1, now))
    return docs


def crawl(seeds: list[dict], ingestion_params: dict) -> dict:
    catalog = load_catalog(ingestion_params["sources_path"])
    fetcher = get_fetcher({**ingestion_params, "respect_robots": True})
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for s in seeds:
        by_domain[domain_of(s["url"])].append(s)
    log_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=ingestion_params.get("parallel_domains", 6)) as pool:
        parts = list(pool.map(
            lambda group: _crawl_domain(group, ingestion_params, catalog, fetcher, log_rows),
            by_domain.values()))
    docs = [d for part in parts for d in part]
    return {"documents": docs, "log": log_rows, "seeds": len(seeds)}


def dedup(crawled: dict) -> list[dict]:
    """Exact duplicates (same text hash) dropped; near-duplicates share a dup_group (reprints)."""
    seen, docs = set(), []
    for d in crawled["documents"]:
        if d["content_hash"] in seen or any(x["url"] == d["url"] for x in docs):
            continue
        seen.add(d["content_hash"])
        docs.append(d)
    groups = assign_dup_groups([(d["url"], d["domain"], d["text"]) for d in docs], prefix="doc")
    for d in docs:
        d["dup_group"] = groups[d["url"]]
    return docs


def doc_id(url: str) -> str:
    return "doc:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def chunk_documents(documents: list[dict], ingestion_params: dict) -> list[dict]:
    chunks = []
    for d in documents:
        for i, text in enumerate(chunk_text(d["text"], ingestion_params.get("chunk_chars", 1200),
                                            ingestion_params.get("chunk_overlap", 150))):
            chunks.append({"id": f"{doc_id(d['url'])}:{i}", "document_id": doc_id(d["url"]),
                           "url": d["url"], "position": i, "text": text})
    return chunks


def embed_and_store(documents: list[dict], chunks: list[dict], ingestion_params: dict,
                    embeddings_params: dict) -> dict:
    from prawdowid.db import connect
    from prawdowid.embeddings import get_embedder

    embedder = get_embedder(embeddings_params)
    catalog = load_catalog(ingestion_params["sources_path"])
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        by_doc[c["document_id"]].append(c)

    with connect(ingestion_params.get("db")) as conn:
        source_ids = {}
        for src in catalog:
            row = conn.execute(
                """INSERT INTO source (domain, path, publisher, type, license, update_policy, active)
                   VALUES (%s, %s, %s, %s, %s, %s, true)
                   ON CONFLICT (domain, path) DO UPDATE SET publisher = EXCLUDED.publisher,
                       type = EXCLUDED.type, license = EXCLUDED.license,
                       update_policy = EXCLUDED.update_policy
                   RETURNING id""",
                (src["domain"].lower(), src.get("path", ""), src["publisher"], src["type"],
                 src.get("license"), src.get("update")),
            ).fetchone()
            source_ids[_entry_key(src)] = row[0]

        last = conn.execute("SELECT embedding_model FROM corpus_version ORDER BY id DESC LIMIT 1").fetchone()
        model_changed = last is not None and last[0] != embedder.model
        existing = dict(conn.execute("SELECT url, content_hash FROM document").fetchall())
        changed = [d for d in documents
                   if model_changed or existing.get(d["url"]) != d["content_hash"]]
        stats = {"documents_seen": len(documents), "documents_changed": len(changed),
                 "documents_unchanged": len(documents) - len(changed),
                 "embedding_model": embedder.model, "model_changed": model_changed}
        if not changed:
            stats["corpus_version"] = None
            return stats

        to_embed = [c for d in changed for c in by_doc[doc_id(d["url"])]]
        vectors = embedder.embed([c["text"] for c in to_embed]) if to_embed else []
        version = conn.execute(
            "INSERT INTO corpus_version (embedding_model, dim, n_docs, n_chunks, params) "
            "VALUES (%s, %s, 0, 0, %s) RETURNING id",
            (embedder.model, embedder.dim, json.dumps(
                {k: ingestion_params.get(k) for k in ("chunk_chars", "chunk_overlap", "links_per_seed")})),
        ).fetchone()[0]
        for d in changed:
            did = doc_id(d["url"])
            conn.execute(
                """INSERT INTO document (id, source_id, url, title, published_at, fetched_at,
                       content_hash, dup_group, seed_url, depth)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title,
                       published_at = EXCLUDED.published_at, fetched_at = EXCLUDED.fetched_at,
                       content_hash = EXCLUDED.content_hash, dup_group = EXCLUDED.dup_group,
                       seed_url = EXCLUDED.seed_url, depth = EXCLUDED.depth""",
                (did, source_ids[d["source_key"]], d["url"], d["title"], d["published_at"],
                 d["fetched_at"], d["content_hash"], d["dup_group"], d["seed_url"], d["depth"]),
            )
            conn.execute("DELETE FROM chunk WHERE document_id = %s", (did,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunk (id, document_id, position, text, embedding, corpus_version) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(c["id"], c["document_id"], c["position"], c["text"],
                  np.array(v, dtype=np.float32), version) for c, v in zip(to_embed, vectors)],
            )
        n_docs, n_chunks = conn.execute(
            "SELECT (SELECT count(*) FROM document), (SELECT count(*) FROM chunk)").fetchone()
        conn.execute("UPDATE corpus_version SET n_docs = %s, n_chunks = %s WHERE id = %s",
                     (n_docs, n_chunks, version))
        stats.update(corpus_version=version, chunks_embedded=len(to_embed),
                     corpus_documents=n_docs, corpus_chunks=n_chunks)
    return stats


def report(crawled: dict, documents: list[dict], store_stats: dict, ingestion_params: dict) -> dict:
    from prawdowid.runtime import PROJECT_ROOT

    per_domain: dict[str, int] = defaultdict(int)
    for d in documents:
        per_domain[d["domain"]] += 1
    out = {
        "seeds": crawled["seeds"], "documents": len(documents),
        "domains": len(per_domain), "per_domain": dict(sorted(per_domain.items())),
        "failed_or_empty": crawled["log"], "store": store_stats,
        "requirements": {"min_100_documents": len(documents) >= 100,
                         "min_10_domains": len(per_domain) >= 10},
    }
    path = PROJECT_ROOT / ingestion_params.get("report_path", "data/ingestion/report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    log.info("ingestion: %s documents from %s domains", len(documents), len(per_domain))
    return out
