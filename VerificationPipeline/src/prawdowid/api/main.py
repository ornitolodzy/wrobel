"""HTTP API. Thin layer: validation + channel tag; all logic is in runtime.verify()."""

from __future__ import annotations

import logging
import os
import threading
import time

from fastapi import FastAPI
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from prawdowid.api.gateway import router as gateway_router
from prawdowid.models import CheckResult
from prawdowid.runtime import load_parameters, verify

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("prawdowid.api")

app = FastAPI(title="Prawdowid Verification Pipeline", version="0.1.0")
app.include_router(gateway_router)


class CheckRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    channel: str = "web"


def _prepare_corpus(attempts: int = 15, delay_s: float = 2.0) -> None:
    """Postgres backend: create the schema and index what PageScrapper already collected."""
    from prawdowid.corpus import ensure_schema, sync_from_scrapper

    params = load_parameters()
    for attempt in range(1, attempts + 1):
        try:
            ensure_schema(params.get("db"))
            break
        except Exception as exc:  # DB still starting
            logger.warning("schema: database unavailable (%s), attempt %d/%d", exc, attempt, attempts)
            time.sleep(delay_s)
    else:
        logger.error("schema: giving up, retrieval will return no evidence")
        return
    if os.environ.get("PRAWDOWID_SYNC_ON_STARTUP", "true").lower() == "true":
        try:
            logger.info("corpus sync from PageScrapper: %s", sync_from_scrapper(params))
        except Exception:
            logger.exception("corpus sync failed")


@app.on_event("startup")
def _warmup() -> None:
    params = load_parameters()
    roles = {r: f"{c.get('provider')}:{c.get('model')}" for r, c in params["llm"]["roles"].items()}
    logger.info("LLM roles: %s | retrieval: %s | embeddings: %s", roles,
                params["retrieval"].get("backend"), params["embeddings"].get("provider"))
    if params["retrieval"].get("backend") == "postgres":
        threading.Thread(target=_prepare_corpus, name="corpus-sync", daemon=True).start()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/config", summary="Aktywna konfiguracja pipeline'u (bez sekretów)")
def config() -> dict:
    p = load_parameters()
    return {
        "llm_roles": {r: {"provider": c.get("provider"), "model": c.get("model")}
                      for r, c in p["llm"]["roles"].items()},
        "max_claims": p["llm"].get("max_claims"),
        "retrieval_backend": p["retrieval"].get("backend"),
        "embeddings": {k: p["embeddings"].get(k) for k in ("provider", "model")},
        "reranker_enabled": p["reranker"].get("enabled"),
        "rerank_min": p["retrieval"].get("rerank_min"),
    }


@app.post("/check", response_model=CheckResult)
async def check(req: CheckRequest) -> CheckResult:
    # Kedro nodes are sync: run the pipeline in a worker thread.
    return await run_in_threadpool(verify, req.text, req.channel)
