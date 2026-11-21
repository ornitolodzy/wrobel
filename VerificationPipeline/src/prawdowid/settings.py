"""Kedro project settings (used by `kedro run` / `kedro viz`)."""

from prawdowid.hooks import TraceHooks

HOOKS = (TraceHooks(),)

# conf/local overrides only the parameter keys it lists (e.g. a single LLM role).
# base_env/default_run_env must be explicit in Kedro 1.x, otherwise base and local are read as one
# environment and every overridden key is reported as a duplicate.
CONFIG_LOADER_ARGS = {
    "base_env": "base",
    "default_run_env": "local",
    "merge_strategy": {"parameters": "soft"},
}
