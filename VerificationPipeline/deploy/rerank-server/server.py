"""Reranker service on the GPU server (next to Ollama/Bielik): bge-reranker-v2-m3, fp16 on CUDA.

Same scoring as prawdowid.retrieval.reranker.CrossEncoderReranker (sigmoid of the logit, 0..1),
so thresholds (rerank_min) and eval results carry over unchanged.

    POST /rerank  {"query": "...", "texts": ["...", ...], "max_length": 512}
                  -> {"model": "...", "device": "cuda", "scores": [0.97, 0.01, ...], "ms": 41.2}
    GET  /health  -> {"status": "ok", "model": "...", "device": "cuda"}

Listens on 127.0.0.1 only; the pipeline reaches it through the SSH tunnel.
Run: RERANK_MODEL=BAAI/bge-reranker-v2-m3 uvicorn server:app --host 127.0.0.1 --port 8081
"""

from __future__ import annotations

import os
import threading
import time

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
BATCH = int(os.environ.get("RERANK_BATCH", "32"))
MAX_TEXTS = int(os.environ.get("RERANK_MAX_TEXTS", "100"))
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

tokenizer = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForSequenceClassification.from_pretrained(MODEL, torch_dtype=DTYPE).to(DEVICE).eval()
lock = threading.Lock()  # one batch on the GPU at a time; requests are short

app = FastAPI(title="rerank-server")


class RerankIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    texts: list[str] = Field(max_length=MAX_TEXTS)
    max_length: int = Field(default=512, ge=32, le=1024)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL, "device": DEVICE}


@app.post("/rerank")
def rerank(body: RerankIn) -> dict:
    if not body.texts:
        return {"model": MODEL, "device": DEVICE, "scores": [], "ms": 0.0}
    t0 = time.perf_counter()
    scores: list[float] = []
    try:
        with lock, torch.inference_mode():
            for i in range(0, len(body.texts), BATCH):
                part = body.texts[i:i + BATCH]
                enc = tokenizer([body.query] * len(part), part, padding=True, truncation=True,
                                max_length=body.max_length, return_tensors="pt").to(DEVICE)
                logits = model(**enc).logits.view(-1).float()
                scores += torch.sigmoid(logits).tolist()
    except torch.cuda.OutOfMemoryError as e:
        torch.cuda.empty_cache()
        raise HTTPException(503, f"GPU out of memory: {e}") from e
    return {"model": MODEL, "device": DEVICE, "scores": scores,
            "ms": round((time.perf_counter() - t0) * 1000, 1)}
