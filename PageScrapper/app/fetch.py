import asyncio
import ipaddress
import json
import re
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.config import Settings

Resolver = Callable[[str], Awaitable[list[str]]]

_ALLOWED_PORTS = {None, 80, 443}
_ATTEMPTS = 2
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".corp")
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_TEXT_TYPES = _HTML_TYPES + ("text/plain", "application/rss+xml", "application/xml", "text/xml", "application/atom+xml", "application/json", "text/event-stream", "text/csv")


class FetchError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def is_html(self) -> bool:
        return self.content_type.startswith(_HTML_TYPES)


@dataclass
class _Raw:
    status_code: int
    headers: httpx.Headers
    content: bytes


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([a-zA-Z0-9_-]+)""", re.IGNORECASE)


def _decode(content: bytes, content_type_header: str) -> str:
    charset = None
    if "charset=" in content_type_header.lower():
        charset = content_type_header.lower().split("charset=", 1)[1].split(";")[0].strip().strip("\"'")
    if not charset:
        match = _META_CHARSET.search(content[:4096])
        charset = match.group(1).decode("ascii") if match else "utf-8"
    try:
        return content.decode(charset, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def assert_public_url(url: str, resolve: Resolver) -> None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError("invalid_url", "Niepoprawny adres URL.", 422) from exc
    if parts.scheme not in ("http", "https"):
        raise FetchError("invalid_url", "Dozwolone są tylko adresy http:// i https://.", 422)
    if parts.username or parts.password:
        raise FetchError("invalid_url", "Adres URL nie może zawierać danych logowania.", 422)
    if port not in _ALLOWED_PORTS:
        raise FetchError("invalid_url", "Dozwolone są tylko porty 80 i 443.", 422)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        raise FetchError("blocked_url", "Adresy lokalne i wewnętrzne są niedozwolone.", 422)

    try:
        addresses = [host] if _is_ip(host) else await resolve(host)
    except OSError as exc:
        raise FetchError("dns_error", "Nie można znaleźć podanej domeny.", 422) from exc
    if not addresses:
        raise FetchError("dns_error", "Nie można znaleźć podanej domeny.", 422)
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise FetchError("blocked_url", "Adres prowadzi do sieci prywatnej lub zastrzeżonej.", 422)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class Fetcher:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve: Resolver = system_resolver,
    ) -> None:
        self.settings = settings
        self.resolve = resolve
        self.client = httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": settings.user_agent, "Accept-Language": "pl,en;q=0.8"},
            transport=transport,
            follow_redirects=False,
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def get(self, url: str, *, polite: bool = False, params: dict | None = None) -> FetchResult:
        current = url
        for _ in range(self.settings.max_redirects + 1):
            await assert_public_url(current, self.resolve)
            if polite:
                await self._wait_turn(current)
            raw = await self._request_with_retry(current, params)
            params = None
            if raw.status_code in (301, 302, 303, 307, 308):
                location = raw.headers.get("location")
                if not location:
                    raise FetchError("bad_redirect", "Strona zwróciła niepoprawne przekierowanie.")
                current = urljoin(current, location)
                continue
            return self._to_result(url, current, raw)
        raise FetchError("too_many_redirects", "Za dużo przekierowań.")

    async def get_json(self, url: str, params: dict | None = None, *, polite: bool = False) -> dict:
        result = await self.get(url, params=params, polite=polite)
        try:
            return json.loads(result.text)
        except ValueError as exc:
            raise FetchError("bad_response", "Niepoprawna odpowiedź JSON.") from exc

    async def post(self, url: str, payload: dict, headers: dict[str, str]) -> FetchResult:
        await assert_public_url(url, self.resolve)
        raw = await self._request_with_retry(url, None, method="POST", payload=payload, headers=headers)
        return self._to_result(url, url, raw)

    async def _request_with_retry(self, url: str, params: dict | None, **kwargs) -> _Raw:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                return await self._request(url, params, **kwargs)
            except FetchError as exc:
                if exc.code not in ("fetch_error", "timeout") or attempt == _ATTEMPTS:
                    raise
                await asyncio.sleep(self.settings.retry_delay_seconds)
        raise FetchError("fetch_error", "Nie udało się połączyć ze stroną.")

    async def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = None
            try:
                result = await self.get(f"{origin}/robots.txt")
                if result.status_code < 400:
                    parser = RobotFileParser()
                    parser.parse(result.text.splitlines())
            except FetchError:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self.settings.user_agent, url)

    async def _request(
        self,
        url: str,
        params: dict | None,
        *,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Raw:
        try:
            async with self.client.stream(method, url, params=params, json=payload, headers=headers) as response:
                if response.is_redirect:
                    return _Raw(response.status_code, response.headers, b"")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.settings.max_page_bytes:
                        raise FetchError("too_large", "Strona jest za duża.", 413)
                    chunks.append(chunk)
                return _Raw(response.status_code, response.headers, b"".join(chunks))
        except httpx.TimeoutException as exc:
            raise FetchError("timeout", "Strona nie odpowiedziała w wyznaczonym czasie.", 504) from exc
        except httpx.HTTPError as exc:
            raise FetchError("fetch_error", "Nie udało się połączyć ze stroną.") from exc

    def _to_result(self, url: str, final_url: str, raw: _Raw) -> FetchResult:
        header = raw.headers.get("content-type", "")
        content_type = header.split(";")[0].strip().lower()
        if raw.status_code >= 400:
            raise FetchError("http_error", f"Strona zwróciła błąd {raw.status_code}.")
        if content_type and not content_type.startswith(_TEXT_TYPES):
            raise FetchError("unsupported_content", f"Nieobsługiwany typ treści: {content_type}.", 415)
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=raw.status_code,
            content_type=content_type or "text/html",
            text=_decode(raw.content, header),
            headers=dict(raw.headers),
        )

    async def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._last_hit.get(host, 0.0)
            if elapsed < self.settings.crawl_delay_seconds:
                await asyncio.sleep(self.settings.crawl_delay_seconds - elapsed)
            self._last_hit[host] = time.monotonic()
