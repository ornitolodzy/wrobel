import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.config import Settings
from app.fetch import FetchError, Resolver, assert_public_url

logger = logging.getLogger("scraper.render")

_SKIPPED_RESOURCES = {"image", "media", "font"}


@dataclass
class RenderResult:
    final_url: str
    html: str


class Renderer(Protocol):
    async def render(
        self, url: str, *, expand_selector: str | None = None, wait_selector: str | None = None
    ) -> RenderResult: ...

    async def close(self) -> None: ...


class PlaywrightRenderer:
    def __init__(self, settings: Settings, resolve: Resolver) -> None:
        self.settings = settings
        self.resolve = resolve
        self._playwright = None
        self._browser = None
        self._start_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(settings.max_concurrent_renders)
        self._host_ok: dict[str, bool] = {}

    async def _browser_instance(self):
        async with self._start_lock:
            if self._browser is None:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    args=["--disable-dev-shm-usage", "--no-sandbox"]
                )
            return self._browser

    async def render(
        self, url: str, *, expand_selector: str | None = None, wait_selector: str | None = None
    ) -> RenderResult:
        if not self.settings.render_enabled:
            raise FetchError("render_disabled", "Renderowanie stron jest wyłączone.", 503)
        await assert_public_url(url, self.resolve)
        browser = await self._browser_instance()
        timeout_ms = int(self.settings.render_timeout_seconds * 1000)
        async with self._slots:
            context = await browser.new_context(user_agent=self.settings.user_agent, service_workers="block", locale="pl-PL")
            try:
                page = await context.new_page()
                await page.route("**/*", self._guard)
                try:
                    if wait_selector:
                        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        await page.wait_for_selector(wait_selector, timeout=timeout_ms)
                        await self._settle(page)
                    else:
                        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except Exception as exc:
                    if "Timeout" not in type(exc).__name__:
                        raise FetchError("render_error", "Nie udało się wyrenderować strony.") from exc
                    logger.warning("Render timeout, using partial content: %s", url)
                await assert_public_url(page.url, self.resolve)
                if expand_selector:
                    await self._expand(page, expand_selector)
                return RenderResult(final_url=page.url, html=await page.content())
            finally:
                await context.close()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def _settle(self, page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass

    async def _expand(self, page, selector: str) -> None:
        for _ in range(4):
            clicked = await page.eval_on_selector_all(
                selector,
                "els => els.filter(e => e.getAttribute('aria-expanded') === 'false').map(e => e.click()).length",
            )
            if not clicked:
                return
            await page.wait_for_timeout(600)

    async def _guard(self, route) -> None:
        request = route.request
        if request.resource_type in _SKIPPED_RESOURCES:
            await route.abort()
            return
        url = request.url
        if url.startswith(("data:", "blob:")):
            await route.continue_()
            return
        if await self._is_public(url):
            await route.continue_()
        else:
            await route.abort()

    async def _is_public(self, url: str) -> bool:
        host = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
        if host not in self._host_ok:
            try:
                await assert_public_url(url, self.resolve)
                self._host_ok[host] = True
            except FetchError:
                self._host_ok[host] = False
        return self._host_ok[host]
