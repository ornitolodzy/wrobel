import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.render import RenderResult
from tests import fixtures as fx

PRIVATE_HOSTS = {"internal.example": ["10.0.0.5"]}
TEST_CATALOG = Path(__file__).parent / "sources_test.yaml"


async def fake_resolver(host: str) -> list[str]:
    return PRIVATE_HOSTS.get(host, ["93.184.216.34"])


class FakeRenderer:
    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = pages or {}
        self.calls: list[str] = []

    async def render(self, url: str, *, expand_selector: str | None = None, wait_selector: str | None = None) -> RenderResult:
        self.calls.append(url)
        return RenderResult(final_url=url, html=self.pages.get(url, "<html><body></body></html>"))

    async def close(self) -> None:
        pass


def site_handler(extra: dict[str, httpx.Response] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    routes = {
        "https://www.gov.pl/robots.txt": httpx.Response(404),
        "https://www.gov.pl/web/rcb/komunikaty": fx.html(fx.GOVPL_LISTING),
        "https://www.gov.pl/web/rcb/komunikaty?page=2&size=10": fx.html("<html><body></body></html>"),
        "https://www.gov.pl/web/rcb/alert-1": fx.html(fx.govpl_article("Alert RCB - atak z powietrza", "20.11.2026", "UWAGA! Zagrożenie atakiem z powietrza. " * 5)),
        "https://www.gov.pl/web/rcb/alert-2": fx.html(fx.govpl_article("Alert RCB - woda niezdatna do picia", "19.11.2026", "Woda w powiecie nie nadaje się do picia. " * 5)),
        "https://www.gov.pl/web/obrona-narodowa/aktualnosci5": fx.html("<html><body></body></html>"),
        "https://demagog.org.pl/robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /\n"),
        "https://demagog.org.pl/fake_news/feed/": httpx.Response(200, text=fx.RSS_FEED, headers={"content-type": "application/rss+xml"}),
        "https://demagog.org.pl/fake_news/fake-1/": fx.html(fx.generic_article("To nie jest prawdziwe zdjęcie", "2026-11-20")),
        "https://demagog.org.pl/fake_news/fake-2/": httpx.Response(500),
        "https://odyseusz.gov.pl/robots.txt": httpx.Response(404),
        "https://factchecktools.googleapis.com/v1alpha1/claims:search": httpx.Response(200, json=fx.GOOGLE_RESPONSE),
        "https://policja.pl/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /pol/zablokowane\n"),
        "https://policja.pl/pol/aktualnosci": fx.html(fx.POLICJA_LISTING),
        "https://policja.pl/pol/aktualnosci/1001,Pierwszy-komunikat.html": fx.html(fx.policja_article("Pierwszy komunikat", "21.11.2026")),
        "https://policja.pl/pol/aktualnosci/1002,Drugi-komunikat.html": fx.html(fx.policja_article("Drugi komunikat", "20.11.2026")),
        "https://api.sejm.gov.pl/robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /\n"),
        "https://api.sejm.gov.pl/eli/acts/DU/2026": httpx.Response(200, json=fx.SEJM_LIST),
        "https://api.sejm.gov.pl/eli/acts/DU/2026/3": httpx.Response(200, json=fx.sejm_act(3)),
        "https://api.sejm.gov.pl/eli/acts/DU/2026/2": httpx.Response(200, json=fx.sejm_act(2)),
    }
    routes.update(extra or {})

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://euvsdisinfo-mcp-server.vercel.app/mcp":
            return mcp_handler(request)
        key = url.split("?query=")[0] if "factchecktools" in url else url
        return routes.get(key, httpx.Response(404))

    return handler


def mcp_handler(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != "Bearer test-token":
        return httpx.Response(401)
    body = json.loads(request.content)
    method = body.get("method")
    sse_headers = {"content-type": "text/event-stream", "mcp-session-id": "session-123"}
    if method == "initialize":
        return httpx.Response(200, text=fx.sse({"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": "2024-11-05"}}), headers=sse_headers)
    if request.headers.get("mcp-session-id") != "session-123":
        return httpx.Response(400)
    if method == "notifications/initialized":
        return httpx.Response(202)
    if method == "tools/call" and body["params"]["name"] == "cases":
        result = {"content": [{"type": "text", "text": "Returning cases"}], "structuredContent": fx.MCP_CASES}
        return httpx.Response(200, text=fx.sse({"jsonrpc": "2.0", "id": body["id"], "result": result}), headers=sse_headers)
    return httpx.Response(400)


ODYSEUSZ_PAGES = {
    "https://odyseusz.gov.pl/": fx.ODYSEUSZ_HOME,
    "https://odyseusz.gov.pl/UA": fx.odyseusz_profile("Ukraina", "2 lipca 2026", 4, "Nie jedź"),
    "https://odyseusz.gov.pl/IT": fx.odyseusz_profile("Włochy", "19 listopada 2026", 2, "Zachowaj szczególną ostrożność"),
}


@pytest.fixture
def make_client(tmp_path) -> Callable[..., Any]:
    @contextmanager
    def _make(handler=None, renderer: FakeRenderer | None = None, **overrides: Any) -> Iterator[TestClient]:
        base: dict[str, Any] = dict(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            sources_file=str(TEST_CATALOG),
            imports_dir=str(tmp_path / "imports"),
            crawl_delay_seconds=0,
            retry_delay_seconds=0,
        )
        base.update(overrides)
        settings = Settings(**base)
        app = create_app(
            settings,
            transport=httpx.MockTransport(handler or site_handler()),
            resolve=fake_resolver,
            renderer=renderer or FakeRenderer(ODYSEUSZ_PAGES),
        )
        with TestClient(app) as client:
            yield client

    return _make


def wait_for_run(client: TestClient, run_id: str, timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/scrap/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.1)
    raise AssertionError("scrape run did not finish in time")
