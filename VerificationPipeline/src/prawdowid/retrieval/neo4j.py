"""Retriever over the knowledge graph in Neo4j (populated by another team, read-only here).

The graph schema is owned by the other team, so every Cypher query is configurable
(params `neo4j.queries.*`). Each query must return the columns listed in CHUNK_COLUMNS
(see docs/neo4j-kontrakt.md). Defaults assume the proposed contract:

    (:Chunk {id, text, position, section?, embedding})-[:PART_OF]->(:Document)
    (:Document {id, url, title, published_at, fetched_at, dup_group, rating?})-[:PUBLISHED_BY]->(:Source {publisher, type})
    (:Document)-[:CITES]->(:Document)          fact-checker -> material it checked
    (:CorpusVersion {id, embedding_model, consistent?})  optional, to verify the embedding model

Retrieval = vector index + full-text index, fused by RRF in Python with every rank kept in the
RetrievalTrace (same explainability as the Postgres retriever). Graph relations add context:
the fact-checker's rating and the headline of the material it CITES (chain of sources).
"""

from __future__ import annotations

import os
import re

from prawdowid.embeddings import get_embedder
from prawdowid.llm import usage
from prawdowid.models import Evidence, Query, RetrievalTrace

CHUNK_COLUMNS = ("chunk_id", "text", "section", "position", "doc_id", "url", "title",
                 "published_at", "fetched_at", "dup_group", "publisher", "source_type", "score")

_RETURN = """
MATCH (c)-[:PART_OF]->(d:Document)-[:PUBLISHED_BY]->(s:Source)
RETURN c.id AS chunk_id, c.text AS text, coalesce(c.section, 'tresc') AS section,
       coalesce(c.position, 0) AS position, d.id AS doc_id, d.url AS url, d.title AS title,
       d.published_at AS published_at, d.fetched_at AS fetched_at,
       coalesce(d.dup_group, d.id) AS dup_group, s.publisher AS publisher, s.type AS source_type,
       score
ORDER BY score DESC, chunk_id
"""

DEFAULT_QUERIES = {
    "dense": "CALL db.index.vector.queryNodes($vector_index, $k, $qvec) YIELD node AS c, score" + _RETURN,
    "sparse": "CALL db.index.fulltext.queryNodes($fulltext_index, $lucene, {limit: $k}) "
              "YIELD node AS c, score" + _RETURN,
    # Graph context for a retrieved document: its rating (fact-checkers) and what it CITES.
    "rating": "MATCH (d:Document {id: $doc_id}) WHERE d.rating IS NOT NULL RETURN d.rating AS rating",
    "cites": """
        MATCH (d:Document {id: $doc_id})-[:CITES]->(p:Document)-[:PUBLISHED_BY]->(s:Source)
        RETURN p.id AS doc_id, p.url AS url, p.title AS title, p.published_at AS published_at,
               p.fetched_at AS fetched_at, coalesce(p.dup_group, p.id) AS dup_group,
               s.publisher AS publisher, s.type AS source_type
        ORDER BY p.url LIMIT $limit
    """,
    # `consistent` false = the graph is being re-embedded with another model (mixed vector spaces).
    "corpus_version": "MATCH (v:CorpusVersion) RETURN v.id AS id, v.embedding_model AS embedding_model, "
                      "coalesce(v.consistent, true) AS consistent ORDER BY v.id DESC LIMIT 1",
}

_WORD = re.compile(r"\w+", re.UNICODE)
_LUCENE_OPS = {"or", "and", "not", "to"}  # operator words never become search terms


def lucene_query(q: Query, max_terms: int = 12) -> str:
    """OR of terms; long words as prefixes (czech* matches Czechami), acronyms exact.
    Only \\w characters pass, so user text cannot inject Lucene syntax."""
    terms = []
    for raw in _WORD.findall(f"{q.sparse} {q.dense}"):
        w = raw.lower()
        if w in _LUCENE_OPS:
            continue
        if raw.isupper() and len(raw) >= 2 and not raw.isdigit():
            t = w
        elif w.isdigit():
            t = w
        elif len(w) > 3:
            t = w[:6] + "*"
        else:
            continue
        if t not in terms:
            terms.append(t)
    return " OR ".join(terms[:max_terms])


def _native(value):
    """neo4j.time.DateTime -> datetime (the driver returns its own temporal types)."""
    return value.to_native() if hasattr(value, "to_native") else value


class Neo4jRetriever:
    def __init__(self, params: dict):
        self.cfg = params.get("neo4j", {})
        self.queries = {**DEFAULT_QUERIES, **(self.cfg.get("queries") or {})}
        self.embedder = get_embedder(params.get("embeddings", {}))
        self._driver = None

    # ---- database access (overridable in tests) ----

    def run(self, query: str, **params) -> list[dict]:
        if self._driver is None:
            from neo4j import GraphDatabase

            password = os.environ.get("NEO4J_PASSWORD") or self.cfg.get("password")
            self._driver = GraphDatabase.driver(
                os.environ.get("NEO4J_URI") or self.cfg.get("uri", "bolt://localhost:7687"),
                auth=(os.environ.get("NEO4J_USER") or self.cfg.get("user", "neo4j"), password),
                connection_timeout=self.cfg.get("timeout_s", 5),
            )
        records, _, _ = self._driver.execute_query(
            query, parameters_=params, database_=self.cfg.get("database") or None,
            routing_="r")  # read-only: we never write to the other team's graph
        return [dict(r) for r in records]

    # ---- retrieval ----

    def search(self, query: Query, params: dict) -> list[Evidence]:
        k = params.get("candidates_per_query", 30)
        rrf_k = params.get("rrf_k", 60)
        version = self._corpus_version(query)
        if version is False:
            return []
        qvec = self.embedder.embed([query.dense])[0]
        lucene = lucene_query(query)
        dense = self.run(self.queries["dense"], vector_index=self.cfg.get("vector_index", "chunk_embedding"),
                         k=k, qvec=qvec)
        sparse = self.run(self.queries["sparse"], fulltext_index=self.cfg.get("fulltext_index", "chunk_text"),
                          lucene=lucene, k=k) if lucene else []
        evidence = self._fuse(dense, sparse, rrf_k, k, version)
        evidence += self._graph_context(evidence)
        usage.record(role="retrieval", backend="neo4j", claim_id=query.claim_id,
                     corpus_version=version, embedding_model=self.embedder.model, lucene=lucene,
                     candidates=sum(e.section == "tresc" for e in evidence),
                     dense_hits=len(dense), sparse_hits=len(sparse),
                     graph_context=sum(e.section != "tresc" for e in evidence))
        return evidence

    def _corpus_version(self, query: Query):
        """Version id if the graph declares one; False if its embedding model differs from ours
        or the graph holds vectors from two models (re-embedding in progress)."""
        try:
            rows = self.run(self.queries["corpus_version"])
        except Exception:
            return None
        if not rows:
            return None
        if rows[0].get("embedding_model") and rows[0]["embedding_model"] != self.embedder.model:
            usage.record(role="retrieval", event="embedding_model_mismatch", claim_id=query.claim_id,
                         corpus_model=rows[0]["embedding_model"], query_model=self.embedder.model)
            return False
        if rows[0].get("consistent") is False:
            # Half re-embedded after a model switch: cosine scores across two models mean nothing.
            usage.record(role="retrieval", event="embedding_model_mixed", claim_id=query.claim_id,
                         corpus_model=rows[0].get("embedding_model"))
            return False
        return rows[0].get("id")

    @staticmethod
    def _fuse(dense: list[dict], sparse: list[dict], rrf_k: int, k: int, version) -> list[Evidence]:
        rows: dict[str, dict] = {}
        ranks: dict[str, dict] = {}
        for name, lst in (("dense", dense), ("sparse", sparse)):
            for rank, r in enumerate(lst, start=1):
                rows.setdefault(r["chunk_id"], r)
                ranks.setdefault(r["chunk_id"], {})[name] = (rank, r.get("score"))
        fused = []
        for cid, rk in ranks.items():
            rrf = sum(1.0 / (rrf_k + rank) for rank, _ in rk.values())
            fused.append((rrf, cid))
        fused.sort(key=lambda x: (-x[0], x[1]))  # deterministic: ties broken by chunk id
        out = []
        for rrf, cid in fused[:k]:
            r, rk = rows[cid], ranks[cid]
            d, s = rk.get("dense"), rk.get("sparse")
            out.append(Evidence(
                chunk_id=cid, doc_id=r["doc_id"], source_type=r.get("source_type") or "web",
                publisher=r.get("publisher") or "", title=r.get("title") or r["url"], url=r["url"],
                published_at=_native(r.get("published_at")), fetched_at=_native(r["fetched_at"]),
                dup_group=r.get("dup_group") or r["doc_id"], text=r["text"],
                section=r.get("section") or "tresc",
                rerank_score=round(rrf / (2.0 / (rrf_k + 1)), 4),
                retrieval=RetrievalTrace(
                    found_by=[n for n, v in (("dense", d), ("sparse", s)) if v],
                    dense_rank=d[0] if d else None,
                    dense_score=round(float(d[1]), 4) if d and d[1] is not None else None,
                    sparse_rank=s[0] if s else None,
                    sparse_score=round(float(s[1]), 4) if s and s[1] is not None else None,
                    rrf=round(rrf, 6), corpus_version=version,
                ),
            ))
        return out

    def _graph_context(self, evidence: list[Evidence]) -> list[Evidence]:
        """Rating of retrieved fact-checks and headlines of what they CITES (via_url)."""
        extra, seen_docs = [], set()
        for e in evidence:
            if e.doc_id in seen_docs:
                continue
            seen_docs.add(e.doc_id)
            base = dict(publisher=e.publisher, title=e.title, url=e.url, published_at=e.published_at,
                        fetched_at=e.fetched_at, dup_group=e.dup_group, doc_id=e.doc_id,
                        source_type=e.source_type, rerank_score=e.rerank_score,
                        retrieval=RetrievalTrace(found_by=["graph"]))
            if e.source_type == "factcheck":
                for i, row in enumerate(self.run(self.queries["rating"], doc_id=e.doc_id)):
                    if row.get("rating"):
                        extra.append(Evidence(chunk_id=f"{e.doc_id}:rating{i}", text=row["rating"],
                                              section="ocena", **base))
                for p in self.run(self.queries["cites"], doc_id=e.doc_id,
                                  limit=self.cfg.get("cites_limit", 2)):
                    if not p.get("title"):
                        continue
                    extra.append(Evidence(
                        chunk_id=f"{p['doc_id']}:title", doc_id=p["doc_id"], text=p["title"],
                        section="naglowek", title=p["title"], url=p["url"],
                        publisher=p.get("publisher") or "", source_type=p.get("source_type") or "web",
                        published_at=_native(p.get("published_at")), fetched_at=_native(p["fetched_at"]),
                        dup_group=p.get("dup_group") or p["doc_id"], rerank_score=e.rerank_score,
                        via_url=e.url, retrieval=RetrievalTrace(found_by=["graph"]),
                    ))
        return extra
