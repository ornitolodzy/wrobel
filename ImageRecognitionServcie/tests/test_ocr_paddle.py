"""End-to-end OCR with the real PaddleOCR engine.

Downloads model weights on the first run (into OCR_MODEL_DIR or ~/.paddlex).
Skip with: ``pytest -m "not ocr"``.
"""

from __future__ import annotations

import io
import os
import unicodedata
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from ocr_service.app import create_app
from tests.conftest import find_font, make_settings, wait_ready

pytestmark = pytest.mark.ocr

POLISH = "Zażółć gęślą jaźń"
ENGLISH = "Settings saved successfully"


def render(lines: list[str], dark: bool = False, size: int = 28) -> bytes:
    font_path = find_font()
    if font_path is None:
        pytest.skip("no TrueType font with Polish glyphs found on this system")
    font = ImageFont.truetype(font_path, size)
    bg, fg = ("#1e1e1e", "#d4d4d4") if dark else ("white", "black")
    img = Image.new("RGB", (900, 60 + 60 * len(lines)), bg)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((30, 30 + 60 * i), line, font=font, fill=fg)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


@pytest.fixture(scope="module")
def ocr_client() -> Iterator[TestClient]:
    settings = make_settings(
        model_dir=os.environ.get("OCR_MODEL_DIR") or None,
        paddle_cpu_threads=os.cpu_count() or 4,
    )
    app = create_app(settings, setup_logging=False)
    with TestClient(app) as client:
        wait_ready(client, timeout=900)
        yield client


def test_polish_diacritics(ocr_client: TestClient) -> None:
    response = ocr_client.post("/v1/ocr", files={"file": ("pl.png", render([POLISH, ENGLISH]))})
    assert response.status_code == 200, response.text
    body = response.json()
    lines = [normalize(line) for line in body["text"].splitlines()]
    assert lines == [POLISH, ENGLISH]
    assert body["lang"] == "pl"
    assert body["engine"] == "paddleocr"
    assert len(body["blocks"]) == 2
    first = body["blocks"][0]
    assert normalize(first["text"]) == POLISH
    assert first["confidence"] > 0.8
    x1, y1, x2, y2 = first["bbox"]
    assert 0 <= x1 < x2 <= 900
    assert 0 <= y1 < y2 <= 180


def test_dark_theme_screenshot(ocr_client: TestClient) -> None:
    response = ocr_client.post(
        "/v1/ocr",
        params={"output": "text"},
        files={"file": ("dark.png", render([POLISH], dark=True))},
    )
    assert response.status_code == 200, response.text
    assert normalize(response.json()["text"]) == POLISH


def test_english_only(ocr_client: TestClient) -> None:
    response = ocr_client.post(
        "/v1/ocr",
        params={"langs": "en", "output": "text"},
        files={"file": ("en.png", render(["The quick brown fox jumps over the lazy dog"]))},
    )
    assert response.status_code == 200
    body = response.json()
    assert normalize(body["text"]) == "The quick brown fox jumps over the lazy dog"
    assert body["lang"] == "en"


def test_blank_image_returns_empty_text(ocr_client: TestClient) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (800, 400), "white").save(buf, "PNG")
    response = ocr_client.post("/v1/ocr", files={"file": ("blank.png", buf.getvalue())})
    assert response.status_code == 200
    assert response.json()["text"] == ""
    assert response.json()["blocks"] == []
    assert response.json()["lang"] is None
