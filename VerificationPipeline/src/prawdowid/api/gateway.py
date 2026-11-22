"""Service contracts of the stack around the pipeline (docker-compose in the repo root).

- POST /verify      called by the API Gateway (LLM_SERVICE_URL); input already validated,
                    PII-masked and, for URLs/images, turned into text by PageScrapper / OCR.
- POST /verify/stream  the same, as NDJSON: progress events ("found 3 claims", "claim 2/3 checked")
                    while the check runs, then one {"type": "result"} line with the /verify body.
- POST /ingest      called by PageScrapper for every new or changed document (RAG_INGEST_URL).
- POST /ingest/sync backfill from the PageScrapper `documents` table (same Postgres).

The verdict is still decided only by runtime.verify() -> policy.py; this module maps formats.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from prawdowid import progress
from prawdowid.models import CheckResult, SourceRef, Verdict
from prawdowid.runtime import load_parameters, verify

logger = logging.getLogger("prawdowid.api")
router = APIRouter()

# Pipeline labels (Polish, policy.py) -> gateway contract (APIGateway/app/schemas.py).
VERDICTS = {
    "potwierdzone": "confirmed",
    "falszywe": "false",
    "wprowadzajace_w_blad": "misleading",
    "brak_dowodow": "insufficient_evidence",
    "nie_podlega_weryfikacji": "not_verifiable",
}
STRENGTH = {"wysoka": "strong", "średnia": "moderate", "niska": "weak"}
# Gateway runtime config (PATCH /api/v1/config) uses this provider to mean "keep conf/*".
USE_PIPELINE_CONFIG = {"", "pipeline", "mock", "conf"}
MAX_INPUT_CHARS = 10_000


class GatewayModel(BaseModel):
    provider: str = "pipeline"
    name: str = ""


class GatewayOptions(BaseModel):
    language: str = "pl"
    max_claims: int | None = Field(default=None, ge=1, le=10)
    max_sources: int | None = Field(default=None, ge=1, le=20)


class GatewayVerifyRequest(BaseModel):
    check_id: str
    input_type: Literal["claim", "url", "image"]
    content: dict[str, Any] | None = None
    options: GatewayOptions = Field(default_factory=GatewayOptions)
    model: GatewayModel = Field(default_factory=GatewayModel)


class IngestDocument(BaseModel):
    url: str
    text: str
    source_id: str | None = None
    source_type: str | None = None
    title: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    retrieved_at: str | None = None
    language: str | None = None
    content_hash: str | None = None


def text_for(req: GatewayVerifyRequest) -> str:
    c = req.content or {}
    if req.input_type == "image":
        text = c.get("ocr_text") or ""
    elif req.input_type == "url":
        # Page text is untrusted data; the title helps claim extraction keep the topic.
        text = "\n\n".join(p for p in (c.get("title"), c.get("text")) if p)
    else:
        text = c.get("text") or ""
    return text.strip()[:MAX_INPUT_CHARS]


def params_for(req: GatewayVerifyRequest) -> dict:
    """conf/* parameters + per-request overrides sent by the gateway (no rebuild needed)."""
    params = copy.deepcopy(load_parameters())
    if req.options.max_claims:
        params["llm"]["max_claims"] = req.options.max_claims
    provider, name = req.model.provider.strip().lower(), req.model.name.strip()
    if provider not in USE_PIPELINE_CONFIG and name:
        model = name if "/" in name else f"{provider}/{name}"
        for role, cfg in params["llm"]["roles"].items():
            params["llm"]["roles"][role] = {**cfg, "provider": "litellm", "model": model}
    return params


def _source(ref: SourceRef) -> dict:
    return {
        "title": ref.title,
        "publisher": ref.publisher,
        "url": ref.url,
        "published_at": ref.published_at.date().isoformat() if ref.published_at else None,
        "retrieved_at": ref.fetched_at.isoformat(),
        "fragment": ref.quote,
        "source_type": ref.source_type,
    }


def _claim(v: Verdict, claim_text: str, max_sources: int | None) -> dict:
    sources = [s for s in v.sources if s.role != "podlinkowane"] + \
              [s for s in v.sources if s.role == "podlinkowane"]
    return {
        "claim": claim_text,
        "verdict": VERDICTS[v.label],
        # The answer's "why" (checked LLM sentences or the template), like in the full answer text.
        "explanation": " ".join([v.lead, v.explanation] if v.explanation else [v.lead, *v.reasons]).strip(),
        "evidence_strength": STRENGTH.get(v.strength) if v.sources else "none",
        "strength_rationale": "; ".join(v.reasons) or None,
        "sources": [_source(s) for s in sources[:max_sources or len(sources)]],
        "limitations": list(v.warnings),
    }


def usage_of(result: CheckResult) -> dict:
    """Models, tokens and cost per call, for the gateway audit log."""
    calls = [c for step in result.trace for c in step.get("calls", [])]
    llm = [c for c in calls if c.get("model")]
    return {
        "pipeline_check_id": result.check_id,
        "models": sorted({c["model"] for c in llm}),
        "llm_calls": len(llm),
        "tokens": sum(int(c.get("input_tokens", 0) or 0) + int(c.get("output_tokens", 0) or 0) for c in llm),
        "cost_usd": round(sum(float(c.get("cost_usd", 0) or 0) for c in llm), 6),
        "steps_ms": {s["step"]: s["ms"] for s in result.trace},
    }


def to_gateway(result: CheckResult, max_sources: int | None) -> dict:
    claims = {c.id: c.text for c in result.claims}
    out = {
        "status": "completed",
        "claims": [_claim(v, claims.get(v.claim_id, v.claim_id), max_sources) for v in result.verdicts],
        "summary": result.answer or None,
        "limitations": list(result.limitations),
        "clarification_question": None,
        "usage": usage_of(result),
    }
    if result.status == "needs_clarification" or not result.verdicts:
        out["status"] = "needs_clarification"
        out["clarification_question"] = result.answer or (
            "Nie znaleziono w treści twierdzenia, które da się sprawdzić. "
            "Podaj konkretne zdanie do weryfikacji.")
    return out


EMPTY_INPUT = {"status": "needs_clarification", "claims": [], "limitations": [],
               "clarification_question": "Brak treści do sprawdzenia. Wpisz twierdzenie, które mam zweryfikować."}


def gateway_reply(result: CheckResult, max_sources: int | None) -> tuple[int, Any]:
    """HTTP status + body of /verify for a finished check (also the last line of /verify/stream)."""
    if result.status == "api_limit":
        # 429 -> gateway status api_limit (technical status, not a verdict).
        return 429, {"detail": result.answer}
    if result.status == "error":
        failed = next((s for s in result.trace if s.get("status") == "error"), {})
        logger.warning("pipeline error in step %s: %s", failed.get("step"), failed.get("error"))
        return 502, {"detail": {"answer": result.answer, "step": failed.get("step")}}
    return 200, to_gateway(result, max_sources)


@router.post("/verify", summary="Weryfikacja w kontrakcie API Gateway (LLM_SERVICE_URL)")
async def verify_for_gateway(req: GatewayVerifyRequest) -> dict:
    text = text_for(req)
    if len(text) < 3:
        return EMPTY_INPUT
    result = await run_in_threadpool(verify, text, "gateway", params_for(req))
    code, body = gateway_reply(result, req.options.max_sources)
    if code != 200:
        raise HTTPException(status_code=code, detail=body["detail"])
    return body


def public_progress(event: dict) -> dict:
    """Progress event in the gateway vocabulary (verdict labels as in /verify)."""
    if event.get("stage") == "verdicts":
        event = {**event, "items": [{**it, "verdict": VERDICTS.get(it.get("label"), "insufficient_evidence")}
                                    for it in event.get("items", [])]}
    return {"type": "progress", **event}


@router.post("/verify/stream", summary="Jak /verify, ale strumień NDJSON: postęp sprawdzania, a na końcu wynik")
async def verify_stream_for_gateway(req: GatewayVerifyRequest) -> StreamingResponse:
    text = text_for(req)
    params = params_for(req)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    def run() -> CheckResult:
        progress.listen(lambda e: loop.call_soon_threadsafe(queue.put_nowait, public_progress(e)))
        try:
            return verify(text, "gateway", params)
        finally:
            progress.listen(None)

    def line(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str) + "\n"

    async def lines():
        if len(text) < 3:
            yield line({"type": "result", "status": 200, "body": EMPTY_INPUT})
            return
        task = asyncio.ensure_future(run_in_threadpool(run))
        task.add_done_callback(lambda _: queue.put_nowait(None))
        while (event := await queue.get()) is not None:
            yield line(event)
        try:
            code, body = gateway_reply(task.result(), req.options.max_sources)
        except Exception as e:  # unexpected crash: a technical status, never a verdict
            logger.exception("pipeline crashed")
            code, body = 502, {"detail": {"answer": f"Błąd techniczny: {type(e).__name__}"}}
        yield line({"type": "result", "status": code, "body": body})

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/ingest", summary="Przyjmuje dokument z PageScrapper (RAG_INGEST_URL): chunk + embed + zapis")
async def ingest(doc: IngestDocument | list[IngestDocument]) -> dict:
    from prawdowid.corpus import store_documents

    docs = [d.model_dump() for d in (doc if isinstance(doc, list) else [doc])]
    return await run_in_threadpool(store_documents, docs, load_parameters())


@router.post("/ingest/sync", summary="Indeksuje wszystkie dokumenty zebrane przez PageScrapper")
async def ingest_sync() -> dict:
    from prawdowid.corpus import sync_from_scrapper

    return await run_in_threadpool(sync_from_scrapper, load_parameters())


@router.get("/corpus/stats", summary="Stan indeksu: dokumenty, fragmenty, wersja korpusu, model embeddingów")
async def corpus_stats() -> dict:
    def _stats() -> dict:
        from prawdowid.db import connect

        with connect(load_parameters().get("db")) as conn:
            docs, chunks = conn.execute(
                "SELECT (SELECT count(*) FROM document), (SELECT count(*) FROM chunk)").fetchone()
            version = conn.execute(
                "SELECT id, embedding_model, created_at FROM corpus_version ORDER BY id DESC LIMIT 1").fetchone()
            by_type = dict(conn.execute(
                "SELECT s.type, count(*) FROM document d JOIN source s ON s.id = d.source_id GROUP BY s.type").fetchall())
        return {"documents": docs, "chunks": chunks, "by_source_type": by_type,
                "corpus_version": version[0] if version else None,
                "embedding_model": version[1] if version else None}

    return await run_in_threadpool(_stats)
