"""Hybrid retriever over the Postgres corpus: pgvector (dense) + tsvector (sparse), fused by RRF.

Explainability: the SQL returns each list's rank and score, the RRF sum and the full-text match
(ts_headline), so every retrieved chunk carries a RetrievalTrace that reproduces its position.
Ties are broken by chunk id, so the order is deterministic for a given corpus version.
"""

from __future__ import annotations

import re

import numpy as np

from prawdowid.db import connect
from prawdowid.embeddings import get_embedder
from prawdowid.llm import usage
from prawdowid.models import Evidence, Query, RetrievalTrace

_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = {"jest", "się", "oraz", "przez", "które", "który", "która", "tego", "była", "było", "będzie"}

SQL = """
WITH dense AS (
    SELECT id, 1 - (embedding <=> %(qvec)s) AS score,
           row_number() OVER (ORDER BY embedding <=> %(qvec)s, id) AS rnk
    FROM chunk WHERE embedding IS NOT NULL
    ORDER BY embedding <=> %(qvec)s, id LIMIT %(k)s
), sparse AS (
    SELECT c.id, ts_rank(c.tsv, q) AS score,
           row_number() OVER (ORDER BY ts_rank(c.tsv, q) DESC, c.id) AS rnk
    FROM chunk c, to_tsquery('simple', %(tsq)s) q
    WHERE %(tsq)s <> '' AND c.tsv @@ q
    ORDER BY score DESC, c.id LIMIT %(k)s
), fused AS (
    SELECT coalesce(d.id, s.id) AS id,
           d.rnk AS dense_rank, d.score AS dense_score,
           s.rnk AS sparse_rank, s.score AS sparse_score,
           coalesce(1.0 / (%(rrf_k)s + d.rnk), 0) + coalesce(1.0 / (%(rrf_k)s + s.rnk), 0) AS rrf
    FROM dense d FULL OUTER JOIN sparse s ON d.id = s.id
)
SELECT f.id, f.dense_rank, f.dense_score, f.sparse_rank, f.sparse_score, f.rrf,
       c.text, c.position, c.document_id, c.corpus_version,
       CASE WHEN %(tsq)s <> '' THEN ts_headline('simple', c.text, to_tsquery('simple', %(tsq)s),
            'MaxFragments=2, MaxWords=18, MinWords=5, StartSel=<b>, StopSel=</b>') END AS matched,
       d.url, d.title, d.published_at, d.fetched_at, d.dup_group,
       s.publisher, s.type
FROM fused f
JOIN chunk c ON c.id = f.id
JOIN document d ON d.id = c.document_id
JOIN source s ON s.id = d.source_id
ORDER BY f.rrf DESC, f.id
LIMIT %(k)s
"""

NEIGHBOURS_SQL = """
SELECT position, text FROM chunk
WHERE document_id = %(doc)s AND position BETWEEN %(lo)s AND %(hi)s AND position <> %(pos)s
ORDER BY position
"""


def tsquery_for(q: Query, max_terms: int = 12) -> str:
    """OR of keyword prefixes ('czech:*' also matches 'Czechami'): tolerant to Polish inflection.
    Only [\\w] characters reach to_tsquery, so user text cannot inject tsquery syntax."""
    terms = []
    for raw in _WORD.findall(f"{q.sparse} {q.dense}"):
        w = raw.lower()
        if raw.isupper() and len(raw) >= 2 and not raw.isdigit():
            t = w  # acronym (RCB, MSZ, PAP, VAT): exact word, it carries the meaning
        elif w.isdigit():
            t = w
        elif len(w) > 3 and w not in _STOP:
            t = w[:6] + ":*"
        else:
            continue
        if t not in terms:
            terms.append(t)
    return " | ".join(terms[:max_terms])


class PgRetriever:
    def __init__(self, params: dict):
        self.params = params
        self.embedder = get_embedder(params.get("embeddings", {}))

    def search(self, query: Query, params: dict) -> list[Evidence]:
        k = params.get("candidates_per_query", 30)
        rrf_k = params.get("rrf_k", 60)
        with connect(params.get("db")) as conn:
            version = conn.execute(
                "SELECT id, embedding_model FROM corpus_version ORDER BY id DESC LIMIT 1").fetchone()
            if version is None:
                usage.record(role="retrieval", event="empty_corpus", claim_id=query.claim_id)
                return []
            corpus_version, corpus_model = version
            if corpus_model != self.embedder.model:
                usage.record(role="retrieval", event="embedding_model_mismatch",
                             claim_id=query.claim_id, corpus_model=corpus_model,
                             query_model=self.embedder.model)
                return []
            qvec = np.array(self.embedder.embed([query.dense])[0], dtype=np.float32)
            tsq = tsquery_for(query)
            with conn.transaction():
                ef = int(params.get("hnsw_ef_search", 100))
                conn.execute(f"SET LOCAL hnsw.ef_search = {ef}")
                rows = conn.execute(SQL, {"qvec": qvec, "k": k, "rrf_k": rrf_k, "tsq": tsq}).fetchall()
            evidence = [self._row(r, rrf_k, corpus_version) for r in rows]
            if params.get("neighbours", 0):
                self._add_neighbours(conn, rows, evidence, params)

        usage.record(role="retrieval", backend="postgres", claim_id=query.claim_id,
                     corpus_version=corpus_version, embedding_model=self.embedder.model,
                     tsquery=tsq, candidates=len(evidence),
                     dense_hits=sum(e.retrieval.dense_rank is not None for e in evidence),
                     sparse_hits=sum(e.retrieval.sparse_rank is not None for e in evidence),
                     hnsw_ef_search=params.get("hnsw_ef_search", 100))
        return evidence

    @staticmethod
    def _row(r, rrf_k: int, corpus_version: int) -> Evidence:
        (cid, d_rank, d_score, s_rank, s_score, rrf, text, position, doc_id, chunk_cv, matched,
         url, title, published_at, fetched_at, dup_group, publisher, stype) = r
        found_by = [n for n, rank in (("dense", d_rank), ("sparse", s_rank)) if rank is not None]
        # Without a cross-encoder the RRF sum is the relevance: normalized to 0..1 by its maximum
        # (rank 1 in both lists = 2/(k+1)), so it is comparable to rerank_min.
        score = float(rrf) / (2.0 / (rrf_k + 1))
        return Evidence(
            chunk_id=cid, doc_id=doc_id, source_type=stype, publisher=publisher, title=title or url,
            url=url, published_at=published_at, fetched_at=fetched_at, dup_group=dup_group,
            text=text, rerank_score=round(score, 4),
            retrieval=RetrievalTrace(
                found_by=found_by, dense_rank=d_rank,
                dense_score=round(float(d_score), 4) if d_score is not None else None,
                sparse_rank=s_rank,
                sparse_score=round(float(s_score), 4) if s_score is not None else None,
                matched=matched, rrf=round(float(rrf), 6), corpus_version=chunk_cv or corpus_version,
            ),
        )

    @staticmethod
    def _add_neighbours(conn, rows, evidence: list[Evidence], params: dict) -> None:
        """Glue neighbouring chunks of the top results, so a sentence cut by chunking stays quotable."""
        n = int(params.get("neighbours", 0))
        for r, e in zip(rows[: params.get("neighbours_top", 5)], evidence):
            position = r[7]
            nb = conn.execute(NEIGHBOURS_SQL, {"doc": e.doc_id, "lo": position - n,
                                               "hi": position + n, "pos": position}).fetchall()
            before = [t for p, t in nb if p < position]
            after = [t for p, t in nb if p > position]
            e.text = "\n".join([*before, e.text, *after])
            e.retrieval.neighbours = [p for p, _ in nb]
