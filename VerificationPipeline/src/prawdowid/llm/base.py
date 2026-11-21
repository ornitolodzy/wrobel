"""Provider-agnostic LLM interface. Nodes depend on this, never on a vendor library."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Technical failure (timeout, connection, 5xx, invalid output). Never a verdict."""


class LLMRefusal(LLMError):
    """The model declined to answer (content policy)."""


class ApiLimitError(LLMError):
    """Rate limit / quota (429) -> CheckResult.status = api_limit."""


@dataclass
class LLMResult(Generic[T]):
    parsed: T
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: float = 0.0


class LLMProvider(Protocol):
    def complete_json(self, system: str, user: str, schema: type[T]) -> LLMResult[T]: ...
