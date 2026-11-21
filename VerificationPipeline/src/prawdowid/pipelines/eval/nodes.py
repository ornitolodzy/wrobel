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
        rows.append(
            {
                "id": case["id"],
                "scenario": case.get("scenario"),
                "expected": case.get("expected_label"),
                "expected_status": case.get("expected_status", "ok"),
                "status": res.status,
                "predicted": res.verdicts[0].label if res.verdicts else None,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        )
    return rows


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


def report(rows: list[dict], eval_params: dict) -> dict:
    out = {
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
