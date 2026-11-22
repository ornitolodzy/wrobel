"""Eval harness: runs the online verification on labelled cases and computes metrics."""

from __future__ import annotations

import copy
import json
import statistics
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from prawdowid.runtime import PROJECT_ROOT, _env_overrides, verify


def _deployed(parameters: dict) -> dict:
    """`kedro run` reads conf/ only; apply the same env knobs as the API (embeddings, reranker,
    LLM, backend) so eval measures what is deployed."""
    return _env_overrides(copy.deepcopy(parameters))

LABELS = ["potwierdzone", "falszywe", "wprowadzajace_w_blad", "brak_dowodow", "nie_podlega_weryfikacji"]


def load_cases(eval_params: dict) -> list[dict]:
    path = PROJECT_ROOT / eval_params["cases_path"]
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_cases(cases: list[dict], parameters: dict) -> list[dict]:
    parameters = _deployed(parameters)
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


def url_key(url: str) -> str:
    """Comparable form of a URL: the knowledge graph stores canonical URLs (lowercase host, no
    trailing slash, no fragment), the cases keep them as published. Query strings are kept."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))


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


def _claims_for(case: dict, parameters: dict) -> list:
    """`claim`: one ready claim (retrieval only). `input`: raw text as a user would paste it;
    claims are extracted by the configured LLM first, exactly as in production (end-to-end)."""
    from prawdowid.models import Claim
    from prawdowid.pipelines.verification.nodes.claims import extract_claims
    from prawdowid.pipelines.verification.nodes.normalize import normalize

    if "input" not in case:
        return [Claim(id="c1", text=case["claim"], type="factual")]
    claims = extract_claims(normalize(case["input"], "eval"), parameters["llm"])
    return [c for c in claims if c.type == "factual"]


def _eval_query(q, parameters: dict, relevant: list[str]) -> dict:
    from prawdowid.pipelines.verification.nodes.context import select_context
    from prawdowid.pipelines.verification.nodes.retrieval import retrieve_rerank

    corpus = retrieve_rerank([q], parameters["retrieval"], parameters["embeddings"])
    raw = list(dict.fromkeys(url_key(e.url) for e in corpus[0].evidence))
    final = select_context(corpus, [q], parameters["retrieval"], parameters.get("reranker"))
    final_ev = [e for e in final[0].evidence if e.section == "tresc"]  # rerank order, after threshold
    final_urls = list(dict.fromkeys(url_key(e.url) for e in final_ev))
    # Every scored candidate (kept or dropped) with its final relevance score. Only page text: graph
    # context (fact-checker rating "Fałsz", cited headline) is not reranked and keeps its parent's
    # raw score, so counting it would make every negative look like it has evidence.
    reranked = {}
    for e in corpus[0].evidence:
        if e.section != "tresc":
            continue
        reranked[url_key(e.url)] = max(reranked.get(url_key(e.url), -1.0), e.rerank_score)
    hit = next((e for e in corpus[0].evidence if url_key(e.url) in relevant), None)
    return {
        "claim": q.dense,
        "rank_retrieval": first_relevant_rank(raw, relevant),
        "rank_final": first_relevant_rank(final_urls, relevant),
        "relevant_found_by": hit.retrieval.found_by if hit and hit.retrieval else None,
        "relevant_score": max((s for u, s in reranked.items() if u in relevant), default=None),
        "best_score": max(reranked.values(), default=None),
        "relevant_in_final_context": any(u in relevant for u in final_urls),
        "raw": raw,
    }


def run_retrieval_cases(cases: list[dict], parameters: dict) -> list[dict]:
    """Knowledge-base candidates -> select_context (reranker, threshold, diversity), per case.
    With several extracted claims, the claim that finds the relevant source best counts."""
    from prawdowid.pipelines.verification.nodes.queries import build_queries

    parameters = _deployed(parameters)
    if not cases or parameters["retrieval"].get("backend", "mock") == "mock":
        return []
    rows = []
    for case in cases:
        relevant = case.get("relevant_urls", [])
        keys = [url_key(u) for u in relevant]
        claims = _claims_for(case, parameters)
        per_claim = [_eval_query(q, parameters, keys) for q in build_queries(claims)]
        if not per_claim:  # LLM found nothing to check
            per_claim = [{"claim": None, "rank_retrieval": None, "rank_final": None,
                          "relevant_found_by": None, "relevant_score": None, "best_score": None,
                          "relevant_in_final_context": False, "raw": []}]
        best = min(per_claim, key=lambda r: (r["rank_final"] or 999, r["rank_retrieval"] or 999))
        rank = best["rank_retrieval"]
        rows.append({
            "id": case["id"], "kind": case.get("kind"), "input": case.get("input", case.get("claim")),
            "claims": [r["claim"] for r in per_claim], "relevant_urls": relevant,
            "first_relevant_rank": rank, "final_rank": best["rank_final"],
            "recall@10": round(recall_at_k(best["raw"], keys, 10), 3) if relevant else None,
            "reciprocal_rank": round(1 / rank, 3) if rank else 0.0,
            "relevant_found_by": best["relevant_found_by"],
            "relevant_score": best["relevant_score"],
            "best_score": max((r["best_score"] for r in per_claim if r["best_score"] is not None), default=None),
            "relevant_in_final_context": best["relevant_in_final_context"],
        })
    return rows


def _wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson interval: with 29/29 the honest statement is 'at least ~0.88', not 'exactly 1.0'."""
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(c - h, 3), round(c + h, 3)]


def _at(rows: list[dict], key: str, k: int) -> dict:
    hits = sum(1 for r in rows if r[key] is not None and r[key] <= k)
    return {"value": round(hits / len(rows), 3), "ci95": _wilson(hits, len(rows))}


def retrieval_report(rows: list[dict], parameters: dict) -> dict:
    parameters = _deployed(parameters)
    if not rows:
        return {"note": "retrieval eval skipped (no cases or retrieval.backend == mock)"}
    pos = [r for r in rows if r["relevant_urls"]]
    neg = [r for r in rows if not r["relevant_urls"]]
    rr = parameters.get("reranker") or {}
    t = parameters["retrieval"]["rerank_min"]
    return {
        "cases": len(rows), "positives": len(pos), "negatives": len(neg),
        "embedding_model": f"{parameters['embeddings'].get('provider')}:{parameters['embeddings'].get('model')}",
        "reranker": bool(rr.get("enabled")), "reranker_provider": rr.get("provider", "local"),
        "rerank_min": t,
        # retrieval (bge-m3 + full-text, RRF): is the source among the candidates at all?
        "recall@1": _at(pos, "first_relevant_rank", 1) if pos else None,
        "recall@3": _at(pos, "first_relevant_rank", 3) if pos else None,
        "recall@10": _at(pos, "first_relevant_rank", 10) if pos else None,
        "mrr": round(sum(r["reciprocal_rank"] for r in pos) / len(pos), 3) if pos else None,
        # after reranker + threshold + diversity: what the LLM actually sees
        "final@1": _at(pos, "final_rank", 1) if pos else None,
        "relevant_in_final_context": _at(pos, "final_rank", 10_000) if pos else None,
        "negatives_rejected": ({"value": round(sum((r["best_score"] or 0) < t for r in neg) / len(neg), 3),
                                "ci95": _wilson(sum((r["best_score"] or 0) < t for r in neg), len(neg))}
                               if neg else None),
        "threshold": calibrate_threshold(rows, t),
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
