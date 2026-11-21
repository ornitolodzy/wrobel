"""Per-request record of LLM calls (model, tokens, cost), attached to the trace by hooks."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar("llm_calls", default=None)


def start() -> None:
    _calls.set([])


def record(**call: Any) -> None:
    calls = _calls.get()
    if calls is not None:
        calls.append(call)


def drain() -> list[dict[str, Any]]:
    """Return and clear calls recorded since the last drain (one node's worth)."""
    calls = _calls.get()
    if not calls:
        return []
    out = list(calls)
    calls.clear()
    return out
