"""Step 3b: final context per claim from the knowledge base candidates (no web access).

1. cross-encoder reranker (if enabled) -> a 0..1 relevance scale. Without it, scores come from
   the retriever (e.g. normalized RRF), which only reflects order, not relevance: the eval showed
   off-topic claims scoring ~0.9, so "Nie znaleziono informacji" is only reliable with the reranker;
2. candidates below rerank_min dropped; none left -> early stop (no LLM call);
3. diversity caps (per document, per reprint group), top_k and a text budget;
4. every dropped candidate is written to the trace with its reason (explainability).
Headline/rating chunks ride along with their document; primary material linked from a kept
document (via_url, e.g. a fact-checker's CITES relation in the graph) is kept too.
"""

from __future__ import annotations

from prawdowid.llm import usage
from prawdowid.models import ClaimEvidence, Evidence, Query, RetrievalTrace
from prawdowid.retrieval.reranker import get_reranker

from .retrieval import diversify_explained


def select_context(
    evidence_corpus: list[ClaimEvidence], queries: list[Query], retrieval_params: dict,
    reranker_params: dict | None = None,
) -> list[ClaimEvidence]:
    reranker = get_reranker(reranker_params or {})
    by_query = {q.claim_id: q for q in queries}
    rp = retrieval_params
    out = []
    for ce in evidence_corpus:
        cid = ce.claim_id
        content = [e for e in ce.evidence if e.section == "tresc"]
        for e in content:
            if e.retrieval is None:
                e.retrieval = RetrievalTrace(found_by=["unknown"])
        dropped: list[tuple[Evidence, str]] = []

        if reranker is not None and content and cid in by_query:
            top_n = (reranker_params or {}).get("top_n", 30)
            content.sort(key=lambda e: (-e.rerank_score, e.chunk_id))
            dropped += [(e, f"poza top_n={top_n} rerankera") for e in content[top_n:]]
            content = content[:top_n]
            scores = reranker.score(by_query[cid].dense, [e.text for e in content])
            for e, sc in zip(content, scores):
                e.rerank_score = e.retrieval.rerank_score = round(float(sc), 4)
            usage.record(role="reranker", claim_id=cid, model=reranker.model, scored=len(content))

        ranked = sorted(content, key=lambda e: (-e.rerank_score, e.chunk_id))
        dropped += [(e, f"poniżej progu rerank_min={rp['rerank_min']}")
                    for e in ranked if e.rerank_score < rp["rerank_min"]]
        ranked = [e for e in ranked if e.rerank_score >= rp["rerank_min"]]
        kept, cut = diversify_explained(ranked, rp["top_k"], rp["max_per_document"],
                                        rp["max_per_dup_group"], rp.get("max_context_chars"))
        dropped += cut
        for i, e in enumerate(kept, start=1):
            e.retrieval.final_rank = i

        kept_docs = {e.doc_id for e in kept}
        kept_urls = {e.url for e in kept}
        meta = [e for e in ce.evidence if e.section != "tresc"
                and (e.doc_id in kept_docs or (e.via_url and e.via_url in kept_urls))]
        usage.record(
            role="context", claim_id=cid, candidates=len(ce.evidence), kept=len(kept),
            reranker=reranker.model if reranker else None,
            dropped=[{"chunk_id": e.chunk_id, "url": e.url, "score": e.rerank_score, "reason": why}
                     for e, why in dropped[:50]],
        )
        out.append(ClaimEvidence(claim_id=cid, evidence=kept + meta, early_stop=not kept))
    return out
