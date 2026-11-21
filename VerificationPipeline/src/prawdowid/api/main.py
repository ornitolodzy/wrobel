"""HTTP API. Thin layer: validation + channel tag; all logic is in runtime.verify()."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from prawdowid.models import CheckResult
from prawdowid.runtime import load_parameters, verify

app = FastAPI(title="Prawdowid", version="0.1.0")


class CheckRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    channel: str = "web"


@app.on_event("startup")
def _warmup() -> None:
    load_parameters()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/check", response_model=CheckResult)
async def check(req: CheckRequest) -> CheckResult:
    # Kedro nodes are sync: run the pipeline in a worker thread.
    return await run_in_threadpool(verify, req.text, req.channel)
