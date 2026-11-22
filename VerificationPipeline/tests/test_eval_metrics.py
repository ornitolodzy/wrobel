"""Retrieval eval metrics: ranks before/after the reranker and honest intervals."""

from prawdowid.pipelines.eval.nodes import _wilson, retrieval_report


def _row(rid, relevant, rank, final_rank, best=0.9, rel=0.9):
    return {"id": rid, "relevant_urls": relevant, "first_relevant_rank": rank, "final_rank": final_rank,
            "reciprocal_rank": 1 / rank if rank else 0.0, "relevant_score": rel, "best_score": best,
            "recall@10": 1.0 if rank and rank <= 10 else 0.0, "relevant_in_final_context": bool(final_rank)}


def test_wilson_interval_is_not_a_point():
    lo, hi = _wilson(29, 29)
    assert 0.85 < lo < 0.9 and hi == 1.0
    assert _wilson(0, 0) is None


def test_report_separates_retrieval_and_final_context():
    params = {"retrieval": {"rerank_min": 0.3}, "embeddings": {"provider": "mock", "model": "m"},
              "reranker": {"enabled": True}, "llm": {"roles": {}}}
    rows = [_row("a", ["u"], 1, 1), _row("b", ["u"], 3, None, rel=0.1), _row("c", ["u"], 12, None),
            _row("n", [], None, None, best=0.05)]
    r = retrieval_report(rows, params)
    assert r["recall@1"]["value"] == 0.333 and r["recall@3"]["value"] == 0.667
    assert r["recall@10"]["value"] == 0.667 and r["relevant_in_final_context"]["value"] == 0.333
    assert r["negatives_rejected"]["value"] == 1.0 and r["negatives"] == 1
