from __future__ import annotations

import io
import struct
import zlib

import numpy as np
import pytest
from PIL import Image

from ocr_service.errors import ApiError
from ocr_service.imaging import (
    ImageFormat,
    PreprocessOptions,
    decode_image,
    preprocess,
    sniff_format,
)
from tests.conftest import image_bytes


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [("PNG", ImageFormat.PNG), ("JPEG", ImageFormat.JPEG), ("WEBP", ImageFormat.WEBP)],
)
def test_sniff_supported(fmt: str, expected: ImageFormat) -> None:
    assert sniff_format(image_bytes(fmt)) is expected


@pytest.mark.parametrize("data", [b"", b"GIF89a....", b"%PDF-1.7", b"<svg></svg>", b"BM\0\0"])
def test_sniff_unsupported(data: bytes) -> None:
    assert sniff_format(data) is None


def _png_header_only(width: int, height: int) -> bytes:
    """A tiny PNG whose header claims a huge image (decompression-bomb shape)."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b""))
        + chunk(b"IEND", b"")
    )


def test_decompression_bomb_is_rejected_before_decoding() -> None:
    with pytest.raises(ApiError) as info:
        decode_image(_png_header_only(50_000, 50_000), max_pixels=40_000_000)
    assert info.value.status_code == 413
    assert info.value.code == "image_too_large"


def test_pixel_limit_below_pillow_limit() -> None:
    with pytest.raises(ApiError) as info:
        decode_image(_png_header_only(5_000, 5_000), max_pixels=1_000_000)
    assert info.value.code == "image_too_large"


def test_rgba_and_grayscale_become_rgb() -> None:
    for mode, color in [("RGBA", (10, 20, 30, 0)), ("L", 128), ("P", 3), ("LA", (40, 255))]:
        image = decode_image(image_bytes(mode=mode, color=color), 10_000_000)
        assert image.mode == "RGB"


def test_light_text_on_transparent_background_stays_visible() -> None:
    img = Image.new("RGBA", (100, 40), (0, 0, 0, 0))
    img.paste((255, 255, 255, 255), (10, 10, 90, 30))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    rgb = np.asarray(decode_image(buf.getvalue(), 10_000_000))
    assert rgb[0, 0].tolist() == [0, 0, 0]  # dark backdrop
    assert rgb[20, 50].tolist() == [255, 255, 255]


def test_exif_orientation_applied() -> None:
    img = Image.new("RGB", (200, 100), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    assert decode_image(buf.getvalue(), 10_000_000).size == (100, 200)


def test_upscale_small_images() -> None:
    img = Image.new("RGB", (400, 100), "white")
    result = preprocess(img, PreprocessOptions(upscale_target=1280, max_upscale=3.0))
    assert result.scale == pytest.approx(3.0)
    assert result.image.shape == (300, 1200, 3)


def test_no_upscale_for_large_images() -> None:
    img = Image.new("RGB", (1920, 1080), "white")
    result = preprocess(img, PreprocessOptions())
    assert result.scale == 1.0
    assert result.image.shape == (1080, 1920, 3)


def test_dark_theme_is_inverted() -> None:
    img = Image.new("RGB", (1600, 900), (30, 30, 30))
    result = preprocess(img, PreprocessOptions())
    assert result.inverted
    assert result.image[0, 0].tolist() == [225, 225, 225]


def test_light_theme_is_not_inverted() -> None:
    result = preprocess(Image.new("RGB", (1600, 900), (250, 250, 250)), PreprocessOptions())
    assert not result.inverted


def test_dark_mode_detection_can_be_disabled() -> None:
    img = Image.new("RGB", (1600, 900), "black")
    assert not preprocess(img, PreprocessOptions(dark_mode=False)).inverted
