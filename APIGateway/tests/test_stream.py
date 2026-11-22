import json

import httpx

from tests.conftest import PNG_BYTES, WEB

URLS = {"llm_service_url": "http://llm:8001", "image_recognition_url": "http://img:8003"}

CLAIM_A = "MSZ wydało komunikat w sprawie wiz"
CLAIM_B = "Policja zatrzymała 3 osoby w Gdańsku"

RESULT = {
    "status": "completed",
    "claims": [
        {"claim": CLAIM_A, "verdict": "confirmed", "explanation": "Tak.", "evidence_strength": "strong",
         "sources": [], "limitations": []},
        {"claim": CLAIM_B, "verdict": "insufficient_evidence", "explanation": "Nie wiem.",
         "evidence_strength": "none", "sources": [], "limitations": []},
    ],
    "summary": "Dwa wątki.",
    "limitations": [],
}

PIPELINE_EVENTS = [
    {"type": "progress", "stage": "step", "step": "extract_claims"},
    {"type": "progress", "stage": "claims", "claims": [{"id": "c1", "text": CLAIM_A, "type": "factual"},
                                                       {"id": "c2", "text": CLAIM_B, "type": "factual"}]},
    {"type": "progress", "stage": "claim_checked", "claim_id": "c2", "done": 1, "total": 2},
    {"type": "progress", "stage": "claim_checked", "claim_id": "c1", "done": 2, "total": 2},
    {"type": "result", "status": 200, "body": RESULT},
]


def ndjson(events: list[dict]) -> str:
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)


def handler(calls: list[str], stream: bool = True, result_status: int = 200):
    def _handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.host == "img" and request.url.path == "/v1/ocr":
            return httpx.Response(200, json={"text": f"{CLAIM_A}. {CLAIM_B}.", "blocks": [{"confidence": 0.9}]})
        if request.url.path == "/verify/stream":
            if not stream:
                return httpx.Response(404)
            events = PIPELINE_EVENTS[:-1] + [{"type": "result", "status": result_status,
                                              "body": RESULT if result_status == 200 else {"detail": "x"}}]
            return httpx.Response(200, text=ndjson(events), headers={"content-type": "application/x-ndjson"})
        if request.url.path == "/verify":
            return httpx.Response(200, json=RESULT)
        return httpx.Response(404)

    return _handle


def read(resp) -> list[dict]:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_claim_stream_forwards_pipeline_progress_then_result(make_client) -> None:
    calls: list[str] = []
    with make_client(handler(calls), **URLS) as c:
        events = read(c.post("/api/v1/verify/claim/stream", json={"text": f"{CLAIM_A}. {CLAIM_B}."}, headers=WEB))
        stages = [e.get("stage") for e in events if e["type"] == "progress"]
        assert stages[0] == "received"
        assert stages.index("claims") < stages.index("claim_checked")
        claims = next(e for e in events if e.get("stage") == "claims")["claims"]
        assert [x["text"] for x in claims] == [CLAIM_A, CLAIM_B]
        last = events[-1]
        assert last["type"] == "result" and last["status"] == 200
        assert [x["verdict"] for x in last["body"]["claims"]] == ["confirmed", "insufficient_evidence"]
        assert calls == ["/verify/stream"]

        # zapisane jak każde sprawdzenie
        stored = c.get(f"/api/v1/checks/{last['body']['check_id']}").json()
        assert stored["summary"] == "Dwa wątki."


def test_image_stream_reports_ocr_before_pipeline(make_client) -> None:
    calls: list[str] = []
    with make_client(handler(calls), **URLS) as c:
        events = read(c.post("/api/v1/verify/image/stream",
                             files={"file": ("zrzut.png", PNG_BYTES, "image/png")}, headers=WEB))
        stages = [e.get("stage") for e in events if e["type"] == "progress"]
        assert stages[:3] == ["received", "ocr", "ocr_done"]
        assert "claims" in stages
        assert events[-1]["body"]["input_type"] == "image"


def test_stream_falls_back_to_plain_verify_on_old_pipeline(make_client) -> None:
    calls: list[str] = []
    with make_client(handler(calls, stream=False), **URLS) as c:
        events = read(c.post("/api/v1/verify/claim/stream", json={"text": CLAIM_A}, headers=WEB))
        assert calls == ["/verify/stream", "/verify"]
        assert events[-1]["status"] == 200
        assert events[-1]["body"]["claims"][0]["verdict"] == "confirmed"


def test_stream_pipeline_limit_is_technical_status(make_client) -> None:
    with make_client(handler([], result_status=429), **URLS) as c:
        events = read(c.post("/api/v1/verify/claim/stream", json={"text": CLAIM_A}, headers=WEB))
        last = events[-1]
        assert last["status"] == 503
        assert last["body"]["status"] == "api_limit"
        assert last["body"]["claims"] == []


def test_stream_validation_errors_are_plain_http(make_client) -> None:
    with make_client(handler([]), **URLS) as c:
        r = c.post("/api/v1/verify/claim/stream", json={"text": "  "}, headers=WEB)
        assert r.status_code == 422
