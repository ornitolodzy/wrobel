"""Kedro hooks = audit trail. Every node run is timed and recorded; errors name the step."""

from __future__ import annotations

import time
from typing import Any

from kedro.framework.hooks import hook_impl

from prawdowid.llm import usage


class TraceHooks:
    """One instance per check; collects a step-by-step trace for the audit log."""

    def __init__(self) -> None:
        self.trace: list[dict[str, Any]] = []
        self._started: dict[str, float] = {}

    @hook_impl
    def before_node_run(self, node) -> None:
        self._started[node.name] = time.perf_counter()

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
