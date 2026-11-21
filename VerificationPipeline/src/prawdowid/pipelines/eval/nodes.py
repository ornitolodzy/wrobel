"""Eval harness: runs the online verification on labelled cases and computes metrics."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from prawdowid.runtime import PROJECT_ROOT, verify

LABELS = ["potwierdzone", "falszywe", "wprowadzajace_w_blad", "brak_dowodow", "nie_podlega_weryfikacji"]


def load_cases(eval_params: dict) -> list[dict]:
    path = PROJECT_ROOT / eval_params["cases_path"]
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_cases(cases: list[dict], parameters: dict) -> list[dict]:
    rows = []
    for case in cases:
        t0 = time.perf_counter()
        res = verify(case["input"], channel="eval", params=parameters)
        calls = [c for t in res.trace for c in t.get("calls", [])]
        cit = [c for c in calls if c.get("role") == "citations" and "checked" in c]
        rows.append(
            {
                "id": case["id"],
                "scenario": case.get("scenario"),
                "expected": case.get("expected_label"),
                "expected_status": case.get("expected_status", "ok"),
                "status": res.status,
                "predicted": res.verdicts[0].label if res.verdicts else None,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "quotes_checked": sum(c["checked"] for c in cit),
                "quotes_rejected": sum(c["rejected"] for c in cit),
                "decisive_verdicts": sum(v.label not in ("brak_dowodow", "nie_podlega_weryfikacji")
                                         for v in res.verdicts),
                "decisive_with_sources": sum(bool(v.sources) for v in res.verdicts
                                             if v.label not in ("brak_dowodow", "nie_podlega_weryfikacji")),
            }
        )
    return rows


def citation_metrics(rows: list[dict]) -> dict:
    """Quote correctness = verified / checked; coverage = decisive verdicts backed by a source."""
    checked = sum(r.get("quotes_checked", 0) for r in rows)
    rejected = sum(r.get("quotes_rejected", 0) for r in rows)
    decisive = sum(r.get("decisive_verdicts", 0) for r in rows)
    covered = sum(r.get("decisive_with_sources", 0) for r in rows)
    return {
        "quotes_checked": checked,
        "quote_correctness": round((checked - rejected) / checked, 3) if checked else None,
        "decisive_verdicts": decisive,
        "citation_coverage": round(covered / decisive, 3) if decisive else None,
    }


# ---------- retrieval evaluation (RAG 5) ----------

def load_retrieval_cases(eval_params: dict) -> list[dict]:
    path = PROJECT_ROOT / eval_params.get("retrieval_cases_path", "tests/eval/retrieval_cases.jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def first_relevant_rank(urls: list[str], relevant: list[str]) -> int | None:
    for i, u in enumerate(urls, start=1):
        if u in relevant:
            return i
    return None


def recall_at_k(urls: list[str], relevant: list[str], k: int = 10) -> float:
    return len(set(urls[:k]) & set(relevant)) / len(set(relevant)) if relevant else 0.0


def calibrate_threshold(rows: list[dict], current: float) -> dict:
    """Threshold that best separates claims WITH relevant evidence (score of the first relevant
    hit must pass) from claims without any (best score must not pass). Reported, not applied."""
    pos = [r["relevant_score"] for r in rows if r["relevant_urls"]]
    neg = [r["best_score"] for r in rows if not r["relevant_urls"]]
    pos = [x if x is not None else -1.0 for x in pos]
    neg = [x if x is not None else -1.0 for x in neg]
    if not pos and not neg:
        return {"suggested_rerank_min": None, "note": "no cases"}

    def acc(t):
        return (sum(x >= t for x in pos) + sum(x < t for x in neg)) / (len(pos) + len(neg))

    candidates = sorted({round(x, 4) for x in pos + neg if x >= 0} | {current})
    best = max(candidates, key=lambda t: (acc(t), -abs(t - current)))
    return {"current_rerank_min": current, "current_accuracy": round(acc(current), 3),
            "suggested_rerank_min": best, "suggested_accuracy": round(acc(best), 3),
            "positives": len(pos), "negatives": len(neg)}


def run_retrieval_cases(cases: list[dict], parameters: dict) -> list[dict]:
    """Retrieval only (no LLM): knowledge-base candidates -> select_context (reranker, threshold, diversity)."""
    from prawdowid.models import Claim
    from prawdowid.pipelines.verification.nodes.queries import build_queries
    from prawdowid.pipelines.verification.nodes.retrieval import retrieve_rerank
    from prawdowid.pipelines.verification.nodes.context import select_context

    if not cases or parameters["retrieval"].get("backend", "mock") == "mock":
        return []
    rows = []
    for case in cases:
        q = build_queries([Claim(id="c1", text=case["claim"], type="factual")])[0]
        corpus = retrieve_rerank([q], parameters["retrieval"], parameters["embeddings"])
        raw = list(dict.fromkeys(e.url for e in corpus[0].evidence))
        final = select_context(corpus, [q], parameters["retrieval"], parameters.get("reranker"))
        final_ev = [e for e in final[0].evidence if e.section == "tresc"]
        relevant = case.get("relevant_urls", [])
        scores = {e.url: e.rerank_score for e in sorted(final_ev, key=lambda e: e.rerank_score)}
        raw_scores = {}
        for e in corpus[0].evidence:
            raw_scores.setdefault(e.url, e.rerank_score)
        rank = first_relevant_rank(raw, relevant)
        hit = next((e for e in corpus[0].evidence if e.url in relevant), None)
        rows.append({
            "id": case["id"], "claim": case["claim"], "relevant_urls": relevant,
            "first_relevant_rank": rank,
            "recall@10": round(recall_at_k(raw, relevant, 10), 3) if relevant else None,
            "reciprocal_rank": round(1 / rank, 3) if rank else 0.0,
            "relevant_found_by": hit.retrieval.found_by if hit and hit.retrieval else None,
            "relevant_score": max((s for u, s in {**raw_scores, **scores}.items() if u in relevant),
                                  default=None),
            "best_score": max([e.rerank_score for e in final_ev] + [e.rerank_score for e in corpus[0].evidence],
                              default=None),
            "relevant_in_final_context": any(e.url in relevant for e in final_ev),
        })
    return rows


def retrieval_report(rows: list[dict], parameters: dict) -> dict:
    if not rows:
        return {"note": "retrieval eval skipped (no cases or retrieval.backend == mock)"}
    pos = [r for r in rows if r["relevant_urls"]]
    return {
        "cases": len(rows), "positives": len(pos),
        "recall@10": round(sum(r["recall@10"] for r in pos) / len(pos), 3) if pos else None,
        "mrr": round(sum(r["reciprocal_rank"] for r in pos) / len(pos), 3) if pos else None,
        "relevant_in_final_context": round(sum(r["relevant_in_final_context"] for r in pos) / len(pos), 3) if pos else None,
        "threshold": calibrate_threshold(rows, parameters["retrieval"]["rerank_min"]),
        "rows": rows,
    }


def macro_f1(rows: list[dict]) -> float:
    scored = [r for r in rows if r["expected"]]
    f1s = []
    for label in LABELS:
        tp = sum(r["predicted"] == label and r["expected"] == label for r in scored)
        fp = sum(r["predicted"] == label and r["expected"] != label for r in scored)
        fn = sum(r["predicted"] != label and r["expected"] == label for r in scored)
        if tp + fp + fn == 0:
            continue
        f1s.append(2 * tp / (2 * tp + fp + fn))
    return round(sum(f1s) / len(f1s), 3) if f1s else 0.0


def report(rows: list[dict], retrieval: dict, eval_params: dict) -> dict:
    out = {
        "citations": citation_metrics(rows),
        "retrieval": retrieval,
        "n": len(rows),
        "macro_f1": macro_f1(rows),
        "status_accuracy": round(
            sum(r["status"] == r["expected_status"] for r in rows) / max(len(rows), 1), 3
        ),
        "median_latency_ms": statistics.median([r["latency_ms"] for r in rows]) if rows else None,
        "rows": rows,
    }
    path = PROJECT_ROOT / eval_params["report_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return out
