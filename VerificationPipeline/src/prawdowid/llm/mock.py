"""Deterministic offline provider: lets tests and CI run without keys or network."""

from __future__ import annotations

from collections.abc import Callable

from .base import LLMResult, T


class MockProvider:
    """Returns `responder(schema, user)` if given; otherwise signals 'use rule-based fallback'."""

    def __init__(self, responder: Callable[[type, str], object] | None = None, model: str = "mock"):
        self.responder = responder
        self.model = model

    def complete_json(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        if self.responder is None:
            raise NotImplementedError("mock provider has no responder")
        out = self.responder(schema, user)
        if isinstance(out, Exception):
            raise out
        parsed = out if isinstance(out, schema) else schema.model_validate(out)
        return LLMResult(parsed=parsed, model=self.model, cost_usd=0.0)
