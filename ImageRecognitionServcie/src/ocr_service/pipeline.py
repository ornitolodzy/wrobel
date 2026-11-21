"""The CPU-bound part of one OCR request: decode -> preprocess -> recognise -> map boxes back.

Runs inside a worker (process or thread); everything here is synchronous and picklable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ocr_service.config import Settings
from ocr_service.engines.base import OcrEngine, TextBlock
from ocr_service.imaging import PreprocessedImage, PreprocessOptions, decode_image, preprocess


@dataclass(frozen=True, slots=True)
class PipelineResult:
    blocks: list[TextBlock]  # in original-image coordinates, unordered
    width: int
    height: int
    scale: float
    inverted: bool


def preprocess_options(settings: Settings) -> PreprocessOptions:
    return PreprocessOptions(
        upscale_target=settings.preprocess_upscale_target,
        max_upscale=settings.preprocess_max_upscale,
        dark_mode=settings.preprocess_dark_mode,
        dark_threshold=settings.preprocess_dark_threshold,
    )


def run_pipeline(
    engine: OcrEngine,
    data: bytes,
    langs: Sequence[str],
    max_pixels: int,
    options: PreprocessOptions,
) -> PipelineResult:
    image = decode_image(data, max_pixels)
    try:
        prepared = preprocess(image, options)
    finally:
        image.close()
    blocks = engine.recognize(prepared.image, langs)
    return PipelineResult(
        blocks=to_original_coordinates(blocks, prepared),
        width=prepared.original_width,
        height=prepared.original_height,
        scale=prepared.scale,
        inverted=prepared.inverted,
    )


def to_original_coordinates(
    blocks: list[TextBlock], prepared: PreprocessedImage
) -> list[TextBlock]:
    """Map boxes from the (possibly upscaled) processed image back to the uploaded image."""
    w, h, s = prepared.original_width, prepared.original_height, prepared.scale

    def clamp(value: float, upper: int) -> int:
        return max(0, min(round(value), upper))

    return [
        TextBlock(
            text=b.text,
            bbox=(
                clamp(b.bbox[0] / s, w),
                clamp(b.bbox[1] / s, h),
                clamp(b.bbox[2] / s, w),
                clamp(b.bbox[3] / s, h),
            ),
            confidence=b.confidence,
        )
        for b in blocks
    ]
