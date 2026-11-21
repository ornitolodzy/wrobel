"""Step 3: hybrid retrieval (pgvector + full-text, RRF) + rerank (code).

Mock backend returns no evidence, which correctly yields `brak_dowodow`.
"""

from __future__ import annotations

from collections import defaultdict

from prawdowid.models import ClaimEvidence, Evidence, Query
from prawdowid.retrieval import get_retriever


def rrf_merge(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion of several ranked id lists."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] += 1.0 / (k + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)


def diversify(evidence: list[Evidence], top_k: int, max_per_doc: int, max_per_group: int):
    out, per_doc, per_group = [], defaultdict(int), defaultdict(int)
    for e in evidence:
        if per_doc[e.doc_id] >= max_per_doc or per_group[e.dup_group] >= max_per_group:
            continue
        out.append(e)
        per_doc[e.doc_id] += 1
        per_group[e.dup_group] += 1
        if len(out) >= top_k:
            break
    return out


def _search(query: Query, params: dict) -> list[Evidence]:
    return get_retriever(params).search(query, params)


def retrieve_rerank(queries: list[Query], retrieval_params: dict) -> list[ClaimEvidence]:
    results = []
    for q in queries:
        ranked = sorted(_search(q, retrieval_params), key=lambda e: e.rerank_score, reverse=True)
        ranked = diversify(
            ranked,
            retrieval_params["top_k"],
            retrieval_params["max_per_document"],
            retrieval_params["max_per_dup_group"],
        )
        early = not ranked or ranked[0].rerank_score < retrieval_params["rerank_min"]
        results.append(ClaimEvidence(claim_id=q.claim_id, evidence=ranked, early_stop=early))
    return results
