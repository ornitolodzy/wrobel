"""Pure-ASGI middleware: request context (id, access log, metrics) and body size limit."""

from __future__ import annotations

import json
import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ocr_service.errors import ApiError, error_payload
from ocr_service.logging_setup import request_id_var
from ocr_service.metrics import ERRORS, HTTP_LATENCY, HTTP_REQUESTS

access_logger = logging.getLogger("ocr_service.access")

REQUEST_ID_HEADER = "x-request-id"


def _route_label(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    # Unmatched paths are collapsed to keep metric label cardinality bounded.
    return str(path) if path else "unmatched"


class RequestContextMiddleware:
    """Assigns a request id, writes a JSON access-log line and records HTTP metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming[:64] if incoming else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        status = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = _route_label(scope)
            method = scope["method"]
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
            HTTP_LATENCY.labels(method=method, route=route).observe(elapsed)
            if route not in ("/healthz", "/readyz", "/metrics"):
                access_logger.info(
                    "request",
                    extra={
                        "method": method,
                        "path": scope["path"],
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 1),
                        "client": (scope.get("client") or ("-",))[0],
                    },
                )
            request_id_var.reset(token)


class BodyTooLargeError(ApiError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            413, "payload_too_large", f"Request body exceeds the limit of {limit} bytes."
        )


class BodySizeLimitMiddleware:
    """Rejects oversized bodies early (Content-Length) and while streaming (chunked uploads)."""

    def __init__(self, app: ASGIApp, max_body_bytes: int, path_prefix: str = "/v1/") -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.path_prefix):
            await self.app(scope, receive, send)
            return

        content_length = dict(scope["headers"]).get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = -1
            if declared > self.max_body_bytes:
                await self._reject(send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    # Raised inside the endpoint's body read -> handled by ApiError handler.
                    raise BodyTooLargeError(self.max_body_bytes)
            return message

        await self.app(scope, limited_receive, send)

    async def _reject(self, send: Send) -> None:
        ERRORS.labels(code="payload_too_large").inc()
        body = json.dumps(
            error_payload(
                "payload_too_large",
                f"Request body exceeds the limit of {self.max_body_bytes} bytes.",
            )
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
