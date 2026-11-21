from __future__ import annotations

import base64
import threading

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory, FakeEngine, image_bytes, wait_ready


def assert_error(response, status: int, code: str) -> None:  # type: ignore[no-untyped-def]
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]


# ---------------------------------------------------------------- probes
def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_readyz_ready(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "engine": "fake",
        "workers": 1,
        "langs": ["en", "pl"],
    }


def test_readyz_and_ocr_while_loading(make_client: ClientFactory) -> None:
    gate = threading.Event()
    client = make_client(engine=FakeEngine(load_event=gate), wait=False)
    try:
        assert client.get("/healthz").status_code == 200
        assert_error(client.get("/readyz"), 503, "not_ready")
        response = client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
        assert_error(response, 503, "not_ready")
        assert response.headers["retry-after"]
    finally:
        gate.set()
    wait_ready(client)


def test_readyz_when_engine_fails(make_client: ClientFactory) -> None:
    client = make_client(engine=FakeEngine(fail_load=True), wait=False)
    for _ in range(200):
        response = client.get("/readyz")
        if response.json()["error"]["code"] == "engine_failed":
            break
    assert_error(response, 503, "engine_failed")
    assert "weights not found" in response.json()["error"]["message"]


def test_metrics(client: TestClient) -> None:
    client.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "ocr_http_requests_total" in response.text
    assert "ocr_processing_duration_seconds" in response.text
    assert "ocr_engine_ready" in response.text


def test_openapi_docs(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    body = schema["paths"]["/v1/ocr"]["post"]["requestBody"]["content"]
    assert {"multipart/form-data", "application/json"} <= set(body)


def test_unknown_route_uses_error_format(client: TestClient) -> None:
    assert_error(client.get("/nope"), 404, "not_found")
    assert_error(client.get("/v1/ocr"), 405, "method_not_allowed")


# ---------------------------------------------------------------- OCR happy paths
def test_ocr_multipart_blocks(client: TestClient) -> None:
    response = client.post("/v1/ocr", files={"file": ("shot.png", image_bytes(size=(1600, 900)))})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "Zażółć gęślą jaźń"
    assert body["lang"] == "pl"
    assert body["engine"] == "fake"
    assert isinstance(body["duration_ms"], int)
    assert body["blocks"] == [
        {"text": "Zażółć gęślą jaźń", "bbox": [0, 0, 1600, 900], "confidence": 0.99}
    ]


def test_ocr_output_text_omits_blocks(client: TestClient) -> None:
    response = client.post(
        "/v1/ocr", params={"output": "text"}, files={"file": ("a.png", image_bytes())}
    )
    assert response.status_code == 200
    assert "blocks" not in response.json()


def test_ocr_form_fields_override_query(client: TestClient) -> None:
    response = client.post(
        "/v1/ocr",
        params={"output": "blocks", "langs": "pl,en"},
        files={"file": ("a.png", image_bytes())},
        data={"output": "text", "langs": "en"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "blocks" not in body
    assert body["lang"] == "en"


def test_ocr_json_base64(client: TestClient) -> None:
    encoded = base64.b64encode(image_bytes("JPEG")).decode()
    response = client.post("/v1/ocr", json={"image": encoded, "langs": "pl", "output": "blocks"})
    assert response.status_code == 200, response.text
    assert response.json()["lang"] == "pl"


def test_ocr_json_data_url(client: TestClient) -> None:
    encoded = base64.b64encode(image_bytes("WEBP")).decode()
    response = client.post("/v1/ocr", json={"image": f"data:image/webp;base64,{encoded}"})
    assert response.status_code == 200, response.text


def test_format_detected_by_content_not_extension(client: TestClient) -> None:
    response = client.post(
        "/v1/ocr", files={"file": ("looks-like.png", image_bytes("JPEG"), "image/png")}
    )
    assert response.status_code == 200


def test_small_image_is_upscaled_and_boxes_mapped_back() -> None:
    engine = FakeEngine()
    from ocr_service.app import create_app
    from tests.conftest import make_settings

    app = create_app(make_settings(), engine_factory=lambda: engine, setup_logging=False)
    with TestClient(app) as client:
        wait_ready(client)
        response = client.post("/v1/ocr", files={"file": ("a.png", image_bytes(size=(200, 50)))})
    assert response.status_code == 200
    assert engine.calls[-1][:2] == (150, 600)  # 3x max upscale
    assert response.json()["blocks"][0]["bbox"] == [0, 0, 200, 50]


# ---------------------------------------------------------------- invalid input
def test_not_an_image(client: TestClient) -> None:
    response = client.post("/v1/ocr", files={"file": ("a.png", b"hello, I am text", "image/png")})
    assert_error(response, 415, "unsupported_media_type")


def test_gif_rejected(client: TestClient) -> None:
    response = client.post("/v1/ocr", files={"file": ("a.gif", image_bytes("GIF"))})
    assert_error(response, 415, "unsupported_media_type")


def test_truncated_png(client: TestClient) -> None:
    data = image_bytes(size=(400, 400), text="abc")
    response = client.post("/v1/ocr", files={"file": ("a.png", data[: len(data) // 2])})
    assert_error(response, 400, "invalid_image")


def test_empty_file(client: TestClient) -> None:
    response = client.post("/v1/ocr", files={"file": ("a.png", b"")})
    assert_error(response, 400, "empty_file")


def test_missing_file_field(client: TestClient) -> None:
    response = client.post("/v1/ocr", data={"langs": "pl"}, files={"other": ("a.png", b"x")})
    assert_error(response, 422, "missing_file")


def test_wrong_content_type(client: TestClient) -> None:
    response = client.post("/v1/ocr", content=image_bytes(), headers={"content-type": "image/png"})
    assert_error(response, 415, "unsupported_media_type")


def test_invalid_base64(client: TestClient) -> None:
    assert_error(client.post("/v1/ocr", json={"image": "@@not-base64@@"}), 400, "invalid_base64")


def test_invalid_json_body(client: TestClient) -> None:
    assert_error(client.post("/v1/ocr", json={"img": "abc"}), 422, "validation_error")


def test_unsupported_language(client: TestClient) -> None:
    response = client.post(
        "/v1/ocr", params={"langs": "pl,de"}, files={"file": ("a.png", image_bytes())}
    )
    assert_error(response, 422, "unsupported_language")


def test_invalid_output_param(client: TestClient) -> None:
    response = client.post(
        "/v1/ocr", params={"output": "xml"}, files={"file": ("a.png", image_bytes())}
    )
    assert_error(response, 422, "validation_error")


# ---------------------------------------------------------------- limits
def test_file_too_large(make_client: ClientFactory) -> None:
    client = make_client(max_file_bytes=2048)
    data = image_bytes(size=(600, 600), text="x" * 40) + b"\0" * 3000
    response = client.post("/v1/ocr", files={"file": ("a.png", data)})
    assert response.status_code == 413
    assert response.json()["error"]["code"] in {"file_too_large", "payload_too_large"}


def test_file_slightly_over_limit_is_file_too_large(make_client: ClientFactory) -> None:
    client = make_client(max_file_bytes=4096)
    data = image_bytes() + b"\0" * 4096
    assert_error(client.post("/v1/ocr", files={"file": ("a.png", data)}), 413, "file_too_large")


def test_body_too_large_by_content_length(make_client: ClientFactory) -> None:
    client = make_client(max_file_bytes=1024)
    response = client.post("/v1/ocr", files={"file": ("a.png", b"\x89PNG" + b"\0" * 200_000)})
    assert_error(response, 413, "payload_too_large")


def test_base64_too_large(make_client: ClientFactory) -> None:
    client = make_client(max_file_bytes=4096)
    encoded = base64.b64encode(b"\0" * 6000).decode()
    assert_error(client.post("/v1/ocr", json={"image": encoded}), 413, "file_too_large")


def test_too_many_pixels(make_client: ClientFactory) -> None:
    client = make_client(max_pixels=100_000)
    response = client.post("/v1/ocr", files={"file": ("a.png", image_bytes(size=(1000, 200)))})
    assert_error(response, 413, "image_too_large")


# ---------------------------------------------------------------- API key
@pytest.fixture
def secured(make_client: ClientFactory) -> TestClient:
    return make_client(api_key="s3cret")


def test_api_key_missing(secured: TestClient) -> None:
    response = secured.post("/v1/ocr", files={"file": ("a.png", image_bytes())})
    assert_error(response, 401, "missing_api_key")


def test_api_key_invalid(secured: TestClient) -> None:
    response = secured.post(
        "/v1/ocr", files={"file": ("a.png", image_bytes())}, headers={"X-API-Key": "nope"}
    )
    assert_error(response, 401, "invalid_api_key")


def test_api_key_valid(secured: TestClient) -> None:
    response = secured.post(
        "/v1/ocr", files={"file": ("a.png", image_bytes())}, headers={"X-API-Key": "s3cret"}
    )
    assert response.status_code == 200


def test_probes_do_not_require_api_key(secured: TestClient) -> None:
    assert secured.get("/healthz").status_code == 200
    assert secured.get("/readyz").status_code == 200
    assert secured.get("/metrics").status_code == 200
