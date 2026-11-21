"""Fetch a web page found by search and extract its main text (trafilatura).

Fetched content is UNTRUSTED DATA (prompt injection vector). Safety rules:
- only http(s) on public IP addresses (no SSRF into localhost / private networks), checked on every redirect;
- size and time limits; robots.txt respected; no paywall bypass (we take what the server returns).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

DEFAULT_UA = "PrawdowidBot/0.1 (fact-checking research prototype)"


@dataclass
class Page:
    url: str
    title: str
    text: str
    published_at: datetime | None
    sitename: str | None


class FetchError(RuntimeError):
    pass


class Fetcher:
    def __init__(self, params: dict):
        self.timeout = params.get("timeout_s", 6)
        self.max_bytes = params.get("max_bytes", 2_000_000)
        self.ua = params.get("user_agent", DEFAULT_UA)
        self.respect_robots = params.get("respect_robots", True)
        self.max_redirects = 3

    def fetch(self, url: str) -> Page | None:
        html, final_url = self._download(url)
        return extract(html, final_url)

    # ---- internals ----

    def _download(self, url: str) -> tuple[str, str]:
        import httpx

        for _ in range(self.max_redirects + 1):
            check_public_url(url)
            if self.respect_robots and not _robots_allow(url, self.ua, self.timeout):
                raise FetchError(f"robots.txt disallows {url}")
            try:
                with httpx.stream("GET", url, timeout=self.timeout, follow_redirects=False,
                                  headers={"User-Agent": self.ua, "Accept": "text/html"}) as r:
                    if r.is_redirect:
                        url = urljoin(url, r.headers.get("location", ""))
                        continue
                    r.raise_for_status()
                    ctype = r.headers.get("content-type", "")
                    if "html" not in ctype:
                        raise FetchError(f"not HTML ({ctype or 'unknown'}): {url}")
                    body = bytearray()
                    for part in r.iter_bytes():
                        body += part
                        if len(body) > self.max_bytes:
                            raise FetchError(f"page larger than {self.max_bytes} bytes: {url}")
                    return body.decode(r.encoding or "utf-8", errors="replace"), url
            except httpx.HTTPError as e:
                raise FetchError(f"{type(e).__name__}: {e}") from e
        raise FetchError(f"too many redirects: {url}")


def check_public_url(url: str) -> None:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"unsupported URL: {url}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise FetchError(f"DNS: {parts.hostname}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError(f"non-public address {ip} for {parts.hostname}")


@lru_cache(maxsize=256)
def _robots(origin: str, timeout: float) -> RobotFileParser | None:
    import httpx

    rp = RobotFileParser()
    try:
        r = httpx.get(f"{origin}/robots.txt", timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        return None  # unreachable robots.txt: allowed (standard behaviour)
    if r.status_code >= 400:
        return None
    rp.parse(r.text.splitlines())
    return rp


def _robots_allow(url: str, ua: str, timeout: float) -> bool:
    p = urlparse(url)
    rp = _robots(f"{p.scheme}://{p.netloc}", timeout)
    return True if rp is None else rp.can_fetch(ua, url)


def extract(html: str, url: str) -> Page | None:
    import trafilatura

    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True, include_comments=False,
                                      include_tables=False, favor_precision=True)
    if doc is None:
        return None
    d = doc.as_dict() if hasattr(doc, "as_dict") else doc
    text = (d.get("text") or "").strip()
    if not text:
        return None
    published = None
    if d.get("date"):
        try:
            published = datetime.fromisoformat(d["date"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except ValueError:
            published = None
    return Page(url=url, title=(d.get("title") or "").strip(), text=text,
                published_at=published, sitename=d.get("sitename"))


_override: Fetcher | None = None


def override(fetcher) -> None:
    """Test hook: replace the network fetcher."""
    global _override
    _override = fetcher


def get_fetcher(params: dict):
    return _override or Fetcher(params)
