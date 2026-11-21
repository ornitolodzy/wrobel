"""Prometheus metrics (process-wide, default registry)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "ocr_http_requests_total",
    "HTTP requests by route and status code.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "ocr_http_request_duration_seconds",
    "Total HTTP request latency.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
OCR_DURATION = Histogram(
    "ocr_processing_duration_seconds",
    "Time spent on decoding, preprocessing and recognition of one image.",
    ["engine"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
OCR_QUEUE_WAIT = Histogram(
    "ocr_queue_wait_seconds",
    "Time a request waited for a free engine instance.",
    buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30),
)
ERRORS = Counter("ocr_errors_total", "Error responses by error code.", ["code"])
INFLIGHT = Gauge("ocr_inflight_requests", "OCR requests admitted (running + waiting).")
ENGINE_READY = Gauge("ocr_engine_ready", "1 when the OCR engine is loaded and serving.")
