from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from ocr_service.app import create_app
from ocr_service.config import Settings
from ocr_service.engines.base import OcrEngine, TextBlock

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def find_font() -> str | None:
    return next((p for p in FONT_CANDIDATES if Path(p).is_file()), None)


class FakeEngine(OcrEngine):
    """Deterministic engine: returns one block spanning the whole processed image."""

    name = "fake"

    def __init__(
        self,
        text: str = "Zażółć gęślą jaźń",
        load_event: threading.Event | None = None,
        fail_load: bool = False,
        delay_s: float = 0.0,
    ) -> None:
        self.text = text
        self.load_event = load_event
        self.fail_load = fail_load
        self.delay_s = delay_s
        self.calls: list[tuple[int, ...]] = []

    @property
    def supported_langs(self) -> frozenset[str]:
        return frozenset({"pl", "en"})

    def load(self) -> None:
        if self.load_event is not None:
            self.load_event.wait(10)
        if self.fail_load:
            raise RuntimeError("weights not found")

    def recognize(self, image: NDArray[np.uint8], langs: Sequence[str]) -> list[TextBlock]:
        if self.delay_s:
            time.sleep(self.delay_s)
        self.calls.append(image.shape)
        h, w = image.shape[:2]
        return [TextBlock(text=self.text, bbox=(0, 0, w, h), confidence=0.99)]


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"log_json": True, "log_level": "WARNING", "workers": 1}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def wait_ready(client: TestClient, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/readyz").status_code == 200:
            return
        time.sleep(0.02)
    raise AssertionError("service did not become ready")


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client() -> Iterator[ClientFactory]:
    clients: list[TestClient] = []

    def factory(
        engine: OcrEngine | None = None,
        wait: bool = True,
        callback_transport: Any = None,
        **overrides: Any,
    ) -> TestClient:
        settings = make_settings(**overrides)
        eng = engine or FakeEngine()
        app = create_app(
            settings,
            engine_factory=lambda: eng,
            setup_logging=False,
            callback_transport=callback_transport,
        )
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        if wait:
            wait_ready(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: ClientFactory) -> TestClient:
    return make_client()


def image_bytes(
    fmt: str = "PNG",
    size: tuple[int, int] = (320, 80),
    color: str | tuple[int, ...] = "white",
    mode: str = "RGB",
    text: str | None = None,
) -> bytes:
    img = Image.new(mode, size, color)
    if text:
        draw = ImageDraw.Draw(img)
        font_path = find_font()
        font = ImageFont.truetype(font_path, 20) if font_path else ImageFont.load_default()
        draw.text((10, 10), text, font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()
