"""Image validation (content sniffing, decompression-bomb guards) and OCR preprocessing.

Images are only ever held in memory - nothing here touches the disk.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps, UnidentifiedImageError

from ocr_service.exceptions import ApiError

RgbImage = NDArray[np.uint8]


class ImageFormat(StrEnum):
    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WEBP"


def sniff_format(data: bytes) -> ImageFormat | None:
    """Detect the format from magic bytes - the file name / declared MIME type is never trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ImageFormat.PNG
    if data.startswith(b"\xff\xd8\xff"):
        return ImageFormat.JPEG
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ImageFormat.WEBP
    return None


def decode_image(data: bytes, max_pixels: int) -> Image.Image:
    """Validate and decode an uploaded image into an RGB Pillow image."""
    if not data:
        raise ApiError(400, "empty_file", "The uploaded file is empty.")

    fmt = sniff_format(data)
    if fmt is None:
        raise ApiError(
            415, "unsupported_media_type", "Unsupported image format. Accepted: PNG, JPEG, WebP."
        )

    try:
        with warnings.catch_warnings():
            # Pillow only *warns* between MAX_IMAGE_PIXELS and 2x that; treat it as an error.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data), formats=[fmt.value])
            width, height = image.size  # read from the header, nothing decoded yet
            if width <= 0 or height <= 0:
                raise ApiError(400, "invalid_image", "Image has invalid dimensions.")
            if width * height > max_pixels:
                raise _too_many_pixels(width, height, max_pixels)
            image.seek(0)  # animated PNG/WebP: OCR the first frame only
            image.load()
    except ApiError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ApiError(413, "image_too_large", "Image dimensions are too large.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
        raise ApiError(
            400, "invalid_image", "The image is corrupted or cannot be decoded."
        ) from exc

    oriented: Image.Image = ImageOps.exif_transpose(image) or image
    return _to_rgb(oriented)


def _too_many_pixels(width: int, height: int, max_pixels: int) -> ApiError:
    return ApiError(
        413,
        "image_too_large",
        f"Image has {width}x{height}={width * height} pixels, the limit is {max_pixels}.",
    )


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"))
        visible = alpha > 0
        luminance = np.asarray(rgba.convert("L"))
        # Light content on a transparent background (e.g. dark-UI exports) -> black backdrop,
        # otherwise white, so the text never disappears into the background.
        light = bool(visible.any()) and float(luminance[visible].mean()) > 160
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255) if light else (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


@dataclass(frozen=True, slots=True)
class PreprocessOptions:
    upscale_target: int = 1280
    max_upscale: float = 3.0
    dark_mode: bool = True
    dark_threshold: int = 110


@dataclass(frozen=True, slots=True)
class PreprocessedImage:
    image: RgbImage  # H x W x 3, uint8, RGB
    scale: float  # processed size / original size
    inverted: bool
    original_width: int
    original_height: int


def is_dark(gray: NDArray[np.uint8], threshold: int) -> bool:
    """Dark theme = median brightness below threshold (the background dominates the median)."""
    return float(np.median(gray)) < threshold


def preprocess(image: Image.Image, options: PreprocessOptions) -> PreprocessedImage:
    rgb: RgbImage = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))
    height, width = rgb.shape[:2]

    scale = 1.0
    longest = max(width, height)
    if options.upscale_target and longest < options.upscale_target:
        scale = min(options.upscale_target / longest, options.max_upscale)
        if scale > 1.01:
            new_size = (round(width * scale), round(height * scale))
            rgb = np.asarray(cv2.resize(rgb, new_size, interpolation=cv2.INTER_CUBIC), np.uint8)
        else:
            scale = 1.0

    inverted = False
    if options.dark_mode:
        gray = np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
        if is_dark(gray, options.dark_threshold):
            rgb = np.asarray(cv2.bitwise_not(rgb), dtype=np.uint8)
            inverted = True

    return PreprocessedImage(
        image=rgb,
        scale=scale,
        inverted=inverted,
        original_width=width,
        original_height=height,
    )
