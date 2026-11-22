"""Progress events of a running check (streamed to the user: "found 3 threads, thread 1 checked...").

Same mechanism as llm.usage: a ContextVar set for one request. Nodes and hooks call emit();
without a listener (batch runs, tests, the plain /verify endpoint) emit() does nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

Listener = Callable[[dict[str, Any]], None]

_listener: ContextVar[Listener | None] = ContextVar("progress_listener", default=None)


def listen(listener: Listener | None) -> None:
    _listener.set(listener)


def emit(stage: str, **data: Any) -> None:
    listener = _listener.get()
    if listener is None:
        return
    try:
        listener({"stage": stage, **data})
    except Exception:  # progress is best effort: never break the check
        pass
