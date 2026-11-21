"""Kedro project settings (used by `kedro run` / `kedro viz`)."""

from prawdowid.hooks import TraceHooks

HOOKS = (TraceHooks(),)

# conf/local overrides only the parameter keys it lists (e.g. a single LLM role).
CONFIG_LOADER_ARGS = {"merge_strategy": {"parameters": "soft"}}
