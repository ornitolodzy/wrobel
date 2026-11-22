"""Client of the API Gateway. The bot is only a channel adapter: all verification logic,
validation, PII masking and audit log live behind the gateway (same as for the web UI)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

CHANNEL_HEADERS = {"X-Channel": "discord"}
MAX_TEXT_CHARS = 5000            # gateway limit for /verify/claim
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_URL_ONLY = re.compile(r"^<?(https?://\S+?)>?$", re.IGNORECASE)   # <url> = Discord "no preview"
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff")


@dataclass(frozen=True)
class Request:
    """What to verify, derived from a Discord message / command."""

    kind: str                     # claim | url | image
    text: str = ""
    image: bytes | None = None
    filename: str = "image.png"
    mime: str = "image/png"


def classify(text: str) -> Request | None:
    """Text from the user -> claim or URL (a message that is just a link checks the page)."""
    text = (text or "").strip()
    if not text:
        return None
    m = _URL_ONLY.match(text)
    if m:
        return Request(kind="url", text=m.group(1))
    return Request(kind="claim", text=text)


def live_eligible(data: dict[str, Any]) -> bool:
    """Same rule as the web UI (Frontend gateway-results.ts): a real, completed check without
    confirmed data, not yet searched live. Keeps both channels giving the same answer."""
    return (data.get("status") == "completed" and not data.get("mock") and not data.get("live_search")
            and isinstance(data.get("check_id"), str)
            and any(c.get("verdict") == "insufficient_evidence" for c in data.get("claims") or []))


@dataclass
class Result:
    """Gateway answer, or a technical problem described for the user (never a verdict)."""

    ok: bool
    data: dict[str, Any] | None = None
    problem: str | None = None
    retry_after: int | None = None


class GatewayClient:
    def __init__(self, base_url: str, timeout_s: float = 120.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s,
                                        headers=CHANNEL_HEADERS, transport=transport)

    async def close(self) -> None:
        await self.client.aclose()

    async def verify(self, req: Request) -> Result:
        try:
            if req.kind == "image":
                resp = await self.client.post(
                    "/api/v1/verify/image", files={"file": (req.filename, req.image or b"", req.mime)})
            elif req.kind == "url":
                resp = await self.client.post("/api/v1/verify/url", json={"url": req.text})
            else:
                resp = await self.client.post("/api/v1/verify/claim", json={"text": req.text[:MAX_TEXT_CHARS]})
        except httpx.TimeoutException:
            return Result(ok=False, problem="Weryfikacja trwała zbyt długo. Spróbuj ponownie za chwilę.")
        except httpx.HTTPError:
            return Result(ok=False, problem="Serwis weryfikacji jest chwilowo niedostępny.")
        return self._parse(resp)

    async def live(self, check_id: str) -> Result:
        """Live search in trusted sites + re-verification of an insufficient_evidence check
        (POST /api/v1/verify/live), the same step the web UI starts automatically."""
        try:
            resp = await self.client.post("/api/v1/verify/live", json={"check_id": check_id})
        except httpx.TimeoutException:
            return Result(ok=False, problem="Wyszukiwanie na żywo trwało zbyt długo.")
        except httpx.HTTPError:
            return Result(ok=False, problem="Nie udało się teraz wyszukać na żywo.")
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> Result:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After")
            return Result(ok=False, problem="Za dużo zapytań do serwisu weryfikacji. Spróbuj ponownie za chwilę.",
                          retry_after=int(retry) if retry and retry.isdigit() else None)
        # Completed checks and technical statuses (api_limit 503, error 502) come as a CheckResponse.
        if isinstance(body, dict) and "check_id" in body:
            return Result(ok=True, data=body)
        if resp.status_code in (400, 404, 409, 413, 415, 422):
            detail = body.get("detail") if isinstance(body, dict) else None
            msg = detail if isinstance(detail, str) else "Nie udało się przyjąć tej treści do sprawdzenia."
            return Result(ok=False, problem=msg)
        return Result(ok=False, problem=f"Serwis weryfikacji zwrócił błąd ({resp.status_code}).")
