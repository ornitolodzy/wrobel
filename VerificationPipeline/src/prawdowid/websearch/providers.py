"""Web search backends. Pick one in conf/*/parameters.yml (web_search.provider).

- ddgs:    DuckDuckGo via the `ddgs` library. No key; unofficial, fine for demos/tests.
- searxng: self-hosted SearXNG (JSON API). No key, no third-party tracking: preferred for deployment.
- brave:   Brave Search API. Needs BRAVE_API_KEY.
"""

from __future__ import annotations

import os

from .base import Hit, WebSearchError


class DDGSearch:
    def __init__(self, params: dict):
        self.region = params.get("region", "pl-pl")
        self.backend = params.get("backend", "auto")
        self.timeout = params.get("timeout_s", 8)

    def search(self, query: str, max_results: int) -> list[Hit]:
        from ddgs import DDGS

        try:
            rows = DDGS(timeout=self.timeout).text(query, region=self.region,
                                                   max_results=max_results, backend=self.backend)
        except Exception as e:  # ddgs raises on no results / rate limit
            if "No results" in str(e):
                return []
            raise WebSearchError(f"ddgs: {type(e).__name__}: {e}") from e
        return [Hit(r.get("title", ""), r["href"], r.get("body", "")) for r in rows if r.get("href")]


class SearxngSearch:
    def __init__(self, params: dict):
        self.base = params.get("api_base", "http://localhost:8080").rstrip("/")
        self.lang = params.get("language", "pl")
        self.timeout = params.get("timeout_s", 8)

    def search(self, query: str, max_results: int) -> list[Hit]:
        import httpx

        try:
            r = httpx.get(f"{self.base}/search", timeout=self.timeout,
                          params={"q": query, "format": "json", "language": self.lang})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise WebSearchError(f"searxng: {e}") from e
        rows = r.json().get("results", [])[:max_results]
        return [Hit(x.get("title", ""), x["url"], x.get("content", "")) for x in rows if x.get("url")]


class BraveSearch:
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, params: dict):
        self.key = os.environ.get("BRAVE_API_KEY") or params.get("api_key")
        if not self.key:
            raise WebSearchError("brave: set BRAVE_API_KEY")
        self.country = params.get("country", "pl")
        self.lang = params.get("language", "pl")
        self.timeout = params.get("timeout_s", 8)

    def search(self, query: str, max_results: int) -> list[Hit]:
        import httpx

        try:
            r = httpx.get(self.URL, timeout=self.timeout,
                          headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
                          params={"q": query, "count": max_results, "country": self.country,
                                  "search_lang": self.lang})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise WebSearchError(f"brave: {e}") from e
        rows = r.json().get("web", {}).get("results", [])
        return [Hit(x.get("title", ""), x["url"], x.get("description", "")) for x in rows if x.get("url")]
