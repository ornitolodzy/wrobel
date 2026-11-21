"""Callback mode (OCR_CALLBACK_URL): 202 + result POSTed to the configured URL."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory, FakeEngine, image_bytes

CALLBACK_URL = "http://receiver.internal/ocr-results"


class Receiver:
    """Fake callback endpoint: records requests, can fail the first N deliveries."""

    def __init__(self, fail_first: int = 0, status: int = 500) -> None:
        self.requests: list[httpx.Request] = []
        self.fail_first = fail_first
        self.status = status
        self._lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.requests.append(request)
            if len(self.requests) <= self.fail_first:
                return httpx.Response(self.status)
        return httpx.Response(200, json={"ok": True})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def wait(self, count: int = 1, timeout_s: float = 5.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.requests) >= count:
                    return [json.loads(r.content) for r in self.requests]
            time.sleep(0.02)
        raise AssertionError(f"expected {count} callback(s), got {len(self.requests)}")


def callback_client(make_client: ClientFactory, receiver: Receiver, **overrides: Any) -> TestClient:
    return make_client(
        callback_url=CALLBACK_URL,
        callback_transport=receiver.transport,
        callback_retry_backoff_s=0.01,
        **overrides,
    )


def test_accepts_and_posts_result_to_callback_url(make_client: ClientFactory) -> None:
    receiver = Receiver()
    client = callback_client(make_client, receiver)
    response = client.post(
        "/v1/ocr",
        files={"file": ("a.png", image_bytes(size=(1600, 900)))},
        headers={"X-Request-ID": "order-42"},
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "accepted", "request_id": "order-42"}

    (payload,) = receiver.wait()
    request = receiver.requests[0]
    assert str(request.url) == CALLBACK_URL
    assert request.method == "POST"
    assert request.headers["x-request-id"] == "order-42"
    assert "authorization" not in request.headers
    assert payload["status"] == "ok"
    assert payload["request_id"] == "order-42"
    assert payload["text"] == "Zażółć gęślą jaźń"
    assert payload["lang"] == "pl"
    assert payload["engine"] == "fake"
    assert payload["blocks"][0]["bbox"] == [0, 0, 1600, 900]
    assert "error" not in payload


def test_output_text_omits_blocks_in_callback(make_client: ClientFactory) -> None:
    receiver = Receiver()
    client = callback_client(make_client, receiver)
    client.post("/v1/ocr", params={"output": "text"}, files={"file": ("a.png", image_bytes())})
    (payload,) = receiver.wait()
    assert payload["status"] == "ok"
    assert "blocks" not in payload


def test_bearer_token_is_sent(make_client: ClientFactory) -> None:
    receiver = Receiver()
    client = callback_client(make_client, receiver, callback_token="hook-secret")
    client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    receiver.wait()
    assert receiver.requests[0].headers["authorization"] == "Bearer hook-secret"


def test_cheap_validation_errors_stay_synchronous(make_client: ClientFactory) -> None:
    receiver = Receiver()
    client = callback_client(make_client, receiver)
    bad_format = client.post("/v1/ocr", files={"file": ("a.png", b"plain text")})
    assert bad_format.status_code == 415
    bad_lang = client.post(
        "/v1/ocr", params={"langs": "de"}, files={"file": ("a.png", image_bytes())}
    )
    assert bad_lang.status_code == 422
    time.sleep(0.2)
    assert receiver.requests == []


def test_processing_error_is_delivered_as_callback(make_client: ClientFactory) -> None:
    receiver = Receiver()
    client = callback_client(make_client, receiver)
    data = image_bytes(size=(400, 400), text="abc")
    truncated = data[: len(data) // 2]  # valid PNG signature, broken body
    response = client.post("/v1/ocr", files={"file": ("a.png", truncated)})
    assert response.status_code == 202
    (payload,) = receiver.wait()
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "invalid_image"
    assert payload["request_id"] == response.json()["request_id"]
    assert "text" not in payload


def test_delivery_is_retried_on_server_errors(make_client: ClientFactory) -> None:
    receiver = Receiver(fail_first=2, status=503)
    client = callback_client(make_client, receiver, callback_retries=3)
    client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    payloads = receiver.wait(count=3)
    assert len(payloads) == 3
    assert all(p["status"] == "ok" for p in payloads)


def test_client_errors_from_receiver_are_not_retried(make_client: ClientFactory) -> None:
    receiver = Receiver(fail_first=10, status=400)
    client = callback_client(make_client, receiver, callback_retries=3)
    client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    receiver.wait(count=1)
    time.sleep(0.3)
    assert len(receiver.requests) == 1


def test_gives_up_after_configured_retries(make_client: ClientFactory) -> None:
    receiver = Receiver(fail_first=100, status=500)
    client = callback_client(make_client, receiver, callback_retries=2)
    client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    receiver.wait(count=3)
    time.sleep(0.3)
    assert len(receiver.requests) == 3  # 1 attempt + 2 retries
    metrics = client.get("/metrics").text
    assert 'ocr_callbacks_total{result="failed"}' in metrics


def test_busy_when_too_many_jobs_pending(make_client: ClientFactory) -> None:
    receiver = Receiver()
    client = callback_client(
        make_client, receiver, engine=FakeEngine(delay_s=0.5), workers=1, max_queue=0
    )
    first = client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    second = client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    assert first.status_code == 202
    assert second.status_code == 503
    assert second.json()["error"]["code"] == "busy"
    receiver.wait()


def test_synchronous_mode_unchanged_without_callback_url(client: TestClient) -> None:
    response = client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    assert response.status_code == 200
    assert response.json()["text"] == "Zażółć gęślą jaźń"


@pytest.mark.parametrize(("threshold", "expected_blocks"), [(0.5, 1), (0.995, 0)])
def test_min_confidence_filters_blocks(
    make_client: ClientFactory, threshold: float, expected_blocks: int
) -> None:
    client = make_client(min_confidence=threshold)  # FakeEngine reports 0.99
    body = client.post("/v1/ocr", files={"file": ("a.png", image_bytes())}).json()
    assert len(body["blocks"]) == expected_blocks
    assert body["text"] == ("Zażółć gęślą jaźń" if expected_blocks else "")


def test_readyz_reports_callback_delivery(make_client: ClientFactory) -> None:
    client = callback_client(make_client, Receiver())
    assert client.get("/readyz").json()["delivery"] == "callback"


def test_callback_payload_is_documented(make_client: ClientFactory) -> None:
    client = make_client()
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "CallbackPayload" in schemas
    assert "AcceptedResponse" in schemas
