"""Pluggable web search. The verification node depends on this interface only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Hit:
    title: str
    url: str
    snippet: str = ""


class WebSearchError(RuntimeError):
    pass


class WebSearch(Protocol):
    def search(self, query: str, max_results: int) -> list[Hit]: ...


class NoSearch:
    """Default in conf/base: no network in tests and CI."""

    def search(self, query: str, max_results: int) -> list[Hit]:
        return []


_overrides: dict[str, WebSearch] = {}


def override(name: str, engine: WebSearch) -> None:
    _overrides[name] = engine


def get_search(params: dict) -> WebSearch:
    name = params.get("provider", "mock")
    if name in _overrides:
        return _overrides[name]
    if name == "mock":
        return NoSearch()
    from . import providers

    factory = {"ddgs": providers.DDGSearch, "searxng": providers.SearxngSearch,
               "brave": providers.BraveSearch}.get(name)
    if factory is None:
        raise ValueError(f"unknown web search provider: {name!r}")
    return factory(params)
