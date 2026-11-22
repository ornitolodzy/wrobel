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
def load_parameters(conf_source: str | None = None, env: str | None = None) -> dict:
    """conf/base + one overriding environment: `env`, else $PRAWDOWID_ENV, else "local".
    Tests set PRAWDOWID_ENV=test (no such dir) so they never pick up local models or keys."""
    loader = OmegaConfigLoader(
        conf_source=conf_source or str(PROJECT_ROOT / "conf"),
        base_env="base",
        default_run_env="local",
        env=env or os.environ.get("PRAWDOWID_ENV") or "local",
        merge_strategy={"parameters": "soft"},  # local overrides only the keys it lists
    )
    return _env_overrides(dict(loader["parameters"]))


def _env_overrides(params: dict) -> dict:
    """Deployment knobs (docker-compose / .env) on top of conf/*, so swapping the model or the
    retrieval backend needs no rebuild:
      PRAWDOWID_LLM_PROVIDER  mock | litellm | nli          (all roles)
      PRAWDOWID_LLM_MODEL     LiteLLM model string           (all roles)
      PRAWDOWID_LLM_API_BASE  e.g. vLLM/Ollama endpoint for Bielik
      PRAWDOWID_RETRIEVAL_BACKEND  mock | postgres | neo4j
      PRAWDOWID_EMBEDDINGS_PROVIDER  mock | ollama
      PRAWDOWID_EMBEDDINGS_MODEL     e.g. bge-m3 (corpus and queries must use the same one)
      PRAWDOWID_EMBEDDINGS_API_BASE  e.g. http://host.docker.internal:11435 (Ollama via SSH tunnel)
      PRAWDOWID_RERANKER_ENABLED     true | false
      PRAWDOWID_RERANKER_PROVIDER    local (CPU, image with WITH_RERANK=true) | remote (GPU server)
      PRAWDOWID_RERANKER_API_BASE    e.g. http://host.docker.internal:8081 (deploy/rerank-server)
      PRAWDOWID_RERANKER_FALLBACK    local = use the CPU reranker if the server is unreachable
    """
    provider = os.environ.get("PRAWDOWID_LLM_PROVIDER", "").strip()
    model = os.environ.get("PRAWDOWID_LLM_MODEL", "").strip()
    api_base = os.environ.get("PRAWDOWID_LLM_API_BASE", "").strip()
    if provider or model or api_base:
        roles = params["llm"]["roles"]
        for role, cfg in roles.items():
            cfg = dict(cfg)
            if provider:
                cfg["provider"] = provider
            if model:
                cfg["model"] = model
            if api_base:
                cfg["api_base"] = api_base
            roles[role] = cfg
    if backend := os.environ.get("PRAWDOWID_RETRIEVAL_BACKEND", "").strip():
        params["retrieval"]["backend"] = backend
    emb = dict(params.get("embeddings") or {})
    for key in ("provider", "model", "api_base"):
        if value := os.environ.get(f"PRAWDOWID_EMBEDDINGS_{key.upper()}", "").strip():
            emb[key] = value
    params["embeddings"] = emb
    rr = dict(params.get("reranker") or {})
    if rerank := os.environ.get("PRAWDOWID_RERANKER_ENABLED", "").strip().lower():
        rr["enabled"] = rerank in ("1", "true", "yes")
    for key in ("provider", "api_base", "fallback"):
        if value := os.environ.get(f"PRAWDOWID_RERANKER_{key.upper()}", "").strip():
            rr[key] = value
    if rr or "reranker" in params:
        params["reranker"] = rr
    return params


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
