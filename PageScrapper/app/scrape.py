import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.config import Settings
from app.extract import Extracted, clean_text, extract_article, looks_like_js_shell
from app.fetch import FetchError, Fetcher
from app.render import Renderer
from app.schemas import Page
from app.sources.govpl import extract_govpl
from app.sources.odyseusz import EXPAND_SELECTOR, WAIT_SELECTOR, extract_odyseusz

logger = logging.getLogger("scraper.scrape")

_MIN_USABLE_CHARS = 50


def _extract_html(html: str, url: str) -> Extracted:
    host = urlsplit(url).netloc
    if host == "gov.pl" or host.endswith(".gov.pl"):
        site_specific = extract_govpl(html)
        if site_specific is not None and len(site_specific.text) >= _MIN_USABLE_CHARS:
            return site_specific
    return extract_article(html, url)


@dataclass
class ScrapedPage:
    page: Page
    html: str | None  # HTML the text was extracted from (fetched or rendered); None for text/plain
    site_publisher: str | None  # publisher from the page metadata; Page.publisher falls back to the host


async def scrape_url(url: str, mode: str, fetcher: Fetcher, renderer: Renderer, settings: Settings) -> Page:
    return (await fetch_page(url, mode, fetcher, renderer, settings)).page


async def fetch_page(url: str, mode: str, fetcher: Fetcher, renderer: Renderer, settings: Settings) -> ScrapedPage:
    result = await fetcher.get(url)
    final_url = result.final_url
    rendered = False
    html: str | None = result.text if result.is_html else None

    if urlsplit(final_url).netloc == "odyseusz.gov.pl":
        page = await renderer.render(final_url, expand_selector=EXPAND_SELECTOR, wait_selector=WAIT_SELECTOR)
        extracted, final_url, rendered = extract_odyseusz(page.html, page.final_url), page.final_url, True
        html = page.html
    elif result.is_html:
        extracted = _extract_html(result.text, final_url)
        too_short = len(extracted.text) < settings.min_text_chars
        wants_render = mode == "always" or (mode == "auto" and (too_short or looks_like_js_shell(result.text)))
        if settings.render_enabled and wants_render:
            try:
                page = await renderer.render(final_url)
                candidate = _extract_html(page.html, page.final_url)
                if len(candidate.text) > len(extracted.text):
                    extracted, final_url, rendered, html = candidate, page.final_url, True, page.html
            except FetchError as exc:
                if mode == "always":
                    raise
                logger.warning("Rendering failed for %s: %s", final_url, exc.code)
    elif result.content_type == "text/plain":
        extracted = Extracted(title=None, publisher=None, published_at=None, language=None, text=clean_text(result.text))
    else:
        raise FetchError("unsupported_content", f"Nieobsługiwany typ treści: {result.content_type}.", 415)

    if len(extracted.text) < _MIN_USABLE_CHARS:
        raise FetchError(
            "no_text",
            "Nie udało się odczytać treści strony (mogła zablokować automatyczny odczyt). "
            "Wklej tekst albo prześlij zrzut ekranu.",
            422,
        )

    out = Page(
        url=url,
        final_url=final_url,
        title=extracted.title,
        publisher=extracted.publisher or urlsplit(final_url).netloc,
        published_at=extracted.published_at,
        retrieved_at=datetime.now(timezone.utc),
        language=extracted.language,
        text=extracted.text,
        rendered=rendered,
    )
    return ScrapedPage(page=out, html=html, site_publisher=extracted.publisher)
