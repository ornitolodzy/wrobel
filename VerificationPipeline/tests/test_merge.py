"""select_context: reranker scale, threshold, diversity, budget and drop reasons in the trace."""

from datetime import datetime, timezone

import pytest

from prawdowid.llm import usage
from prawdowid.models import ClaimEvidence, Evidence, Query, RetrievalTrace
from prawdowid.pipelines.verification.nodes.context import select_context
from prawdowid.retrieval import reranker as rr

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
Q = Query(claim_id="c1", dense="RCB wydało alert powodziowy", sparse="RCB")
PARAMS = {"top_k": 8, "max_per_document": 2, "max_per_dup_group": 2, "rerank_min": 0.3,
          "max_context_chars": 10_000}


def ev(cid, score, doc=None, group=None, text="tekst " * 10, found_by=("dense",)):
    return Evidence(chunk_id=cid, doc_id=doc or cid, source_type="primary",
                    publisher="p", title="t", url=f"https://x/{doc or cid}", fetched_at=NOW,
                    dup_group=group or cid, text=text, rerank_score=score,
                    retrieval=RetrievalTrace(found_by=list(found_by), rrf=score))


class FakeReranker:
    model = "fake-reranker"

    def __init__(self, scores):
        self.scores = scores

    def score(self, query, texts):
        return [self.scores[t] for t in texts]


@pytest.fixture(autouse=True)
def _reset():
    usage.start()
    yield
    rr.override(None)


def run(corpus, params=PARAMS, rerank=False):
    out = select_context([ClaimEvidence(claim_id="c1", evidence=list(corpus))],
                         [Q], params, {"enabled": rerank, "top_n": 30})
    trace = next(c for c in usage.drain() if c.get("role") == "context")
    return out[0], trace


def test_reranker_reorders_by_relevance_not_rank():
    corpus = [ev("a", 0.9, text="fragment A"), ev("b", 0.8, text="fragment B"),
              ev("s", 0.35, text="fragment S", found_by=("sparse",))]   # low RRF, but relevant
    rr.override(FakeReranker({"fragment A": 0.4, "fragment B": 0.1, "fragment S": 0.95}))
    ce, trace = run(corpus, rerank=True)
    assert [e.chunk_id for e in ce.evidence] == ["s", "a"]
    s = ce.evidence[0]
    assert (s.retrieval.found_by, s.retrieval.rrf, s.retrieval.rerank_score, s.retrieval.final_rank) \
        == (["sparse"], 0.35, 0.95, 1)
    assert trace["reranker"] == "fake-reranker"
    assert trace["dropped"] == [{"chunk_id": "b", "url": "https://x/b", "score": 0.1,
                                 "reason": "poniżej progu rerank_min=0.3"}]


def test_without_reranker_scores_are_kept_and_nothing_left_means_early_stop():
    ce, trace = run([ev("a", 0.1), ev("b", 0.2)])
    assert ce.early_stop and ce.evidence == []
    assert {d["chunk_id"] for d in trace["dropped"]} == {"a", "b"}


def test_diversity_and_budget_reasons():
    corpus = [ev("a1", 0.9, doc="A"), ev("a2", 0.8, doc="A"), ev("a3", 0.7, doc="A"),
              ev("r1", 0.6, group="G"), ev("r2", 0.55, group="G"), ev("r3", 0.5, group="G"),
              ev("big", 0.45, text="x" * 9_000)]
    ce, trace = run(corpus, params={**PARAMS, "max_context_chars": 5_000})
    kept = [e.chunk_id for e in ce.evidence]
    assert kept == ["a1", "a2", "r1", "r2"]
    reasons = {d["chunk_id"]: d["reason"] for d in trace["dropped"]}
    assert reasons["a3"].startswith("limit 2 fragmentów") and reasons["r3"].startswith("przedruk")
    assert reasons["big"].startswith("budżet kontekstu")
