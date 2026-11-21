import asyncio
import re
from collections.abc import AsyncIterator

from bs4 import BeautifulSoup

from app.catalog import SourceConfig
from app.db import DocumentIn
from app.extract import Extracted, clean_text, parse_date
from app.fetch import FetchError
from app.sources.base import AdapterContext

EXPAND_SELECTOR = "mat-expansion-panel-header"
WAIT_SELECTOR = "main h1"
_NOISE_SELECTORS = [
    "nav", "form", "button", "input", "mat-icon", "script", "style", "mat-sidenav", "ui-nav-accordion",
    ".sr-only", ".cdk-visually-hidden", "ui-email-subscription-card", "google-map", ".gm-style",
]
_NOISE_CARD_TITLES = ("Subskrypcja e-mail", "Zgłoszenie podróży")
_DROP_PREFIXES = [
    "Przejdź do", "Zobacz więcej na stronie tematycznej", "Czym są ostrzeżenia",
    "Zobacz profil kraju", "Zobacz wszystkie kraje", "Dowiedz się więcej", "keyboard_arrow",
]


def _clean_main(html: str) -> tuple[BeautifulSoup, str]:
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("main#main-content") or soup.find("main") or soup.body or soup
    for title in _NOISE_CARD_TITLES:
        for el in main.find_all(string=lambda s, t=title: s and s.strip() == t):
            card = el.find_parent("mat-card") or el.find_parent("ui-email-subscription-card")
            if card is not None:
                card.decompose()
    for selector in _NOISE_SELECTORS:
        for el in main.select(selector):
            el.decompose()
    return main, clean_text(main.get_text("\n"), drop_prefixes=_DROP_PREFIXES)


def _last_update(text: str):
    dates = [parse_date(m) for m in re.findall(r"Ostatnia aktualizacja:\s*\n?\s*([^\n]+)", text)]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


PUBLISHER = "Ministerstwo Spraw Zagranicznych"
HOME_TITLE = "Odyseusz (MSZ): aktualne informacje dla podróżujących"


def _profile_details(main, text: str, code: str) -> tuple[str, dict]:
    heading = main.find("h1")
    country = heading.get_text(" ", strip=True) if heading else code
    level = re.search(r"Poziom\s+(\d)\s*-\s*([^\n]+)", text)
    extra = {
        "country_code": code,
        "country": country,
        "warning_level": int(level[1]) if level else None,
        "warning_label": level[2].strip() if level else None,
    }
    return f"Odyseusz (MSZ): {country} - informacje dla podróżujących", extra


def extract_odyseusz(html: str, url: str) -> Extracted:
    main, text = _clean_main(html)
    code = re.search(r"odyseusz\.gov\.pl/([A-Z]{2})/?$", url)
    if code:
        title, _ = _profile_details(main, text, code[1])
    else:
        heading = main.find("h1")
        title = heading.get_text(" ", strip=True) if heading else HOME_TITLE
    return Extracted(title=title, publisher=PUBLISHER, published_at=_last_update(text), language="pl", text=text)


def parse_profile(html: str, url: str, code: str, source: SourceConfig) -> DocumentIn:
    main, text = _clean_main(html)
    title, extra = _profile_details(main, text, code)
    return DocumentIn(
        url=url,
        source_id=source.id,
        source_type=source.source_type,
        title=title,
        publisher=source.publisher,
        published_at=_last_update(text),
        text=text,
        extra=extra,
    )


def parse_home(html: str, url: str, source: SourceConfig) -> DocumentIn:
    _, text = _clean_main(html)
    return DocumentIn(
        url=url,
        source_id=source.id,
        source_type=source.source_type,
        title=HOME_TITLE,
        publisher=source.publisher,
        published_at=_last_update(text),
        text=text,
        extra={"page": "home"},
    )


def parse_country_codes(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    codes: list[str] = []
    for link in soup.select("a[href]"):
        match = re.fullmatch(r"(?:https://odyseusz\.gov\.pl)?/([A-Z]{2})", link["href"])
        if match and match[1] not in codes:
            codes.append(match[1])
    return codes


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    source = ctx.source
    base = source.url.rstrip("/")
    await ctx.require_robots(base + "/")

    home = await ctx.renderer.render(base + "/", wait_selector=WAIT_SELECTOR)
    yield parse_home(home.html, base + "/", source)

    wanted = source.options.get("countries") or "all"
    if wanted == "all":
        listing = await ctx.renderer.render(base + "/kraje", wait_selector=WAIT_SELECTOR)
        codes = parse_country_codes(listing.html)
    else:
        codes = [str(c).upper() for c in wanted]

    async def render_profile(code: str) -> tuple[str, str, str | None, str | None]:
        url = f"{base}/{code}"
        try:
            page = await ctx.renderer.render(url, expand_selector=EXPAND_SELECTOR, wait_selector=WAIT_SELECTOR)
            return code, url, page.html, None
        except FetchError as exc:
            return code, url, None, exc.message

    pending = [
        asyncio.create_task(render_profile(code))
        for code in codes[: max(ctx.max_items - 1, 0)]
        if not ctx.should_skip(f"{base}/{code}")
    ]
    try:
        for finished in asyncio.as_completed(pending):
            code, url, html, error = await finished
            if html is None:
                ctx.add_error(url, error or "błąd renderowania")
                continue
            doc = parse_profile(html, url, code, source)
            if len(doc.text) < 100:
                ctx.add_error(url, "profil kraju bez treści")
                continue
            yield doc
    finally:
        for task in pending:
            task.cancel()
