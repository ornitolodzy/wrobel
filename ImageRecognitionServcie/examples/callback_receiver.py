"""Minimal callback receiver for testing callback mode end to end.

    uv run python examples/callback_receiver.py            # listens on :9000
    # start the OCR service with OCR_CALLBACK_URL=http://localhost:9000/ocr-results
    curl http://localhost:9000/results/<request_id>         # fetch a delivered result

Keeps the last 1000 deliveries in memory and prints each one. CORS is open so the local
tester page (tools/ocr-tester.html) can poll it. Replace with your own endpoint in production.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="OCR callback receiver (example)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

# request_id -> {"received_at", "authorized", "payload"}
deliveries: OrderedDict[str, dict[str, Any]] = OrderedDict()
EXPECTED_TOKEN = os.environ.get("RECEIVER_TOKEN")  # must match OCR_CALLBACK_TOKEN if set


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ocr-results")
async def receive(
    request: Request, authorization: str | None = Header(default=None)
) -> dict[str, bool]:
    if EXPECTED_TOKEN and authorization != f"Bearer {EXPECTED_TOKEN}":
        raise HTTPException(401, "bad token")
    payload: dict[str, Any] = await request.json()
    deliveries[payload["request_id"]] = {
        "received_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "authorized": bool(EXPECTED_TOKEN),
        "payload": payload,
    }
    while len(deliveries) > 1000:
        deliveries.popitem(last=False)
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return {"ok": True}


@app.get("/results/{request_id}")
async def get_result(request_id: str) -> dict[str, Any]:
    if request_id not in deliveries:
        raise HTTPException(404, "not delivered (yet)")
    return deliveries[request_id]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
