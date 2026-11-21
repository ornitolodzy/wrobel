"""Online entry point: one function that every channel (web, Discord) calls.

The Pipeline and parameters are built once; per request only an in-memory
DataCatalog and a hook manager are created (no KedroSession in the hot path).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from functools import lru_cache
from pathlib import Path

from kedro.config import OmegaConfigLoader
from kedro.framework.hooks.manager import _create_hook_manager
from kedro.io import DataCatalog, MemoryDataset
from kedro.runner import SequentialRunner

from prawdowid.hooks import TraceHooks
from prawdowid.llm import ApiLimitError, LLMRefusal
from prawdowid.llm import usage
from prawdowid.models import CheckResult
from prawdowid.pipelines.verification import create_pipeline

PROJECT_ROOT = Path(os.environ.get("PRAWDOWID_ROOT", Path(__file__).resolve().parents[2]))


@lru_cache(maxsize=1)
def _pipeline():
    return create_pipeline()


@lru_cache(maxsize=1)
def load_parameters(conf_source: str | None = None) -> dict:
    loader = OmegaConfigLoader(
        conf_source=conf_source or str(PROJECT_ROOT / "conf"),
        base_env="base",
        default_run_env="local",
        merge_strategy={"parameters": "soft"},  # local overrides only the keys it lists
    )
    return dict(loader["parameters"])


def _catalog(raw: str, channel: str, params: dict) -> DataCatalog:
    datasets = {
        "raw_input": MemoryDataset(raw),
        "channel": MemoryDataset(channel),
        **{f"params:{k}": MemoryDataset(v) for k, v in params.items()},
    }
    return DataCatalog(datasets=datasets)


def check_id_for(raw: str, params: dict) -> str:
    """Deterministic key: same input + same config -> same cache entry (scenario G)."""
    digest = hashlib.sha256((" ".join(raw.split()) + repr(sorted(params.items()))).encode())
    return digest.hexdigest()[:16]


def verify(raw: str, channel: str = "web", params: dict | None = None) -> CheckResult:
    params = params or load_parameters()
    tracer = TraceHooks()
    hooks = _create_hook_manager()
    hooks.register(tracer)
    check_id = f"{check_id_for(raw, params)}-{uuid.uuid4().hex[:6]}"
    usage.start()
    try:
        out = SequentialRunner().run(_pipeline(), _catalog(raw, channel, params), hooks)
        result = out["result"].load()
    except ApiLimitError:
        return CheckResult(check_id=check_id, status="api_limit", trace=tracer.trace,
                           answer="Usługa jest chwilowo przeciążona. Spróbuj ponownie za chwilę.")
    except LLMRefusal:
        return CheckResult(check_id=check_id, status="error", trace=tracer.trace,
                           answer="Model odmówił analizy tej treści. To nie jest ocena treści.")
    except Exception:
        return CheckResult(check_id=check_id, status="error", trace=tracer.trace,
                           answer="Wystąpił błąd techniczny. To nie jest ocena treści.")
    status = "ok" if result["claims"] else "needs_clarification"
    return CheckResult(check_id=check_id, status=status, trace=tracer.trace, **result)
