"""Role config (from parameters.yml) -> provider instance."""

from __future__ import annotations

from .base import LLMProvider
from .mock import MockProvider

_cache: dict[tuple, LLMProvider] = {}
_overrides: dict[str, LLMProvider] = {}


def is_mock(role_cfg: dict) -> bool:
    """True -> node uses its rule-based fallback instead of calling a model."""
    name = role_cfg.get("provider", "mock")
    return name == "mock" and name not in _overrides


def override(provider_name: str, provider: LLMProvider) -> None:
    """Test hook: register a provider instance under a name (e.g. 'fake')."""
    _overrides[provider_name] = provider


def clear_overrides() -> None:
    _overrides.clear()


def get_provider(role_cfg: dict, prices: dict | None = None) -> LLMProvider:
    name = role_cfg.get("provider", "mock")
    if name in _overrides:
        return _overrides[name]
    key = (name, role_cfg.get("model"), role_cfg.get("api_base"))
    if key not in _cache:
        if name == "mock":
            _cache[key] = MockProvider()
        elif name == "litellm":
            from .litellm_provider import LiteLLMProvider

            _cache[key] = LiteLLMProvider(role_cfg, prices or {})
        elif name == "nli":
            from .nli_provider import NLIStanceProvider

            _cache[key] = NLIStanceProvider(role_cfg)
        else:
            raise ValueError(f"unknown LLM provider: {name!r}")
    return _cache[key]
