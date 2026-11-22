"""Step 3: hybrid retrieval (pgvector + full-text, RRF) + rerank (code).

Mock backend returns no evidence, which correctly yields `brak_dowodow`.
"""

from __future__ import annotations

from collections import defaultdict

from prawdowid.llm import usage
from prawdowid.models import ClaimEvidence, Evidence, Query
from prawdowid.retrieval import get_retriever


class RetrievalUnavailable(RuntimeError):
    """The corpus could not be searched (database, graph or embeddings down)."""


def rrf_merge(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion of several ranked id lists."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] += 1.0 / (k + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)


def diversify(evidence: list[Evidence], top_k: int, max_per_doc: int, max_per_group: int):
    return diversify_explained(evidence, top_k, max_per_doc, max_per_group)[0]


def diversify_explained(evidence: list[Evidence], top_k: int, max_per_doc: int, max_per_group: int,
                        max_chars: int | None = None):
    """Keep the best candidates under diversity caps and a text budget; return (kept, dropped)
    where dropped = [(evidence, reason)] so the trace says why each candidate was left out."""
    kept, dropped = [], []
    per_doc, per_group, chars = defaultdict(int), defaultdict(int), 0
    for e in evidence:
        if len(kept) >= top_k:
            dropped.append((e, f"poza top_k={top_k}"))
        elif per_doc[e.doc_id] >= max_per_doc:
            dropped.append((e, f"limit {max_per_doc} fragmentów z dokumentu"))
        elif per_group[e.dup_group] >= max_per_group:
            dropped.append((e, f"przedruk: grupa {e.dup_group} ma już {max_per_group}"))
        elif max_chars is not None and kept and chars + len(e.text) > max_chars:
            dropped.append((e, f"budżet kontekstu {max_chars} znaków"))
        else:
            kept.append(e)
            per_doc[e.doc_id] += 1
            per_group[e.dup_group] += 1
            chars += len(e.text)
    return kept, dropped


def retrieve_rerank(
    queries: list[Query], retrieval_params: dict, embeddings_params: dict | None = None
) -> list[ClaimEvidence]:
    """Knowledge-base candidates per claim, best first. Final selection (reranker, diversity,
    budget, threshold) happens in select_context."""
    params = {**retrieval_params, "embeddings": embeddings_params or {}}
    results = []
    for q in queries:
        try:
            found = get_retriever(params).search(q, params)
        except Exception as e:
            # The knowledge base could not be searched: that is a technical failure, not "no
            # evidence". Turning it into brak_dowodow would present an outage as an abstention
            # (task §2/§5, scenario J), so the check ends with status "error" instead.
            usage.record(role="retrieval", event="error", claim_id=q.claim_id,
                         reason=f"{type(e).__name__}: {e}")
            raise RetrievalUnavailable(f"knowledge base search failed: {type(e).__name__}") from e
        ranked = sorted(found, key=lambda e: e.rerank_score, reverse=True)
        results.append(ClaimEvidence(claim_id=q.claim_id, evidence=ranked, early_stop=not ranked))
    return results
