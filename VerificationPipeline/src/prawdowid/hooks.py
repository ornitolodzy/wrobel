"""Kedro hooks = audit trail. Every node run is timed and recorded; errors name the step."""

from __future__ import annotations

import time
from typing import Any

from kedro.framework.hooks import hook_impl

from prawdowid import progress
from prawdowid.llm import usage


class TraceHooks:
    """One instance per check; collects a step-by-step trace for the audit log."""

    def __init__(self) -> None:
        self.trace: list[dict[str, Any]] = []
        self._started: dict[str, float] = {}

    @hook_impl
    def before_node_run(self, node) -> None:
        self._started[node.name] = time.perf_counter()
        progress.emit("step", step=node.name)

    @hook_impl
    def after_node_run(self, node, outputs) -> None:
        entry = {
            "step": node.name,
            "ms": round((time.perf_counter() - self._started.pop(node.name)) * 1000, 2),
            "outputs": {k: _size(v) for k, v in outputs.items()},
            "status": "ok",
        }
        if calls := usage.drain():
            entry["calls"] = calls
        self.trace.append(entry)
        _progress_of(node.name, outputs)

    @hook_impl
    def on_node_error(self, error, node) -> None:
        started = self._started.pop(node.name, time.perf_counter())
        self.trace.append(
            {
                "step": node.name,
                "ms": round((time.perf_counter() - started) * 1000, 2),
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
                "calls": usage.drain(),
            }
        )


def _size(value: Any) -> Any:
    return len(value) if isinstance(value, (list, dict)) else type(value).__name__


def _progress_of(step: str, outputs: dict[str, Any]) -> None:
    """What the user can already be told after a step (streamed by /verify/stream)."""
    if step == "extract_claims":
        claims = outputs.get("claims") or []
        progress.emit("claims", claims=[{"id": c.id, "text": c.text, "type": c.type} for c in claims])
    elif step == "select_context":
        progress.emit("evidence", items=[
            {"claim_id": ce.claim_id, "sources": len({e.url for e in ce.evidence if e.section == "tresc"})}
            for ce in outputs.get("evidence") or []
        ])
    elif step == "decide":
        progress.emit("verdicts", items=[
            {"claim_id": v.claim_id, "label": v.label} for v in outputs.get("verdicts") or []
        ])
