from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.catalog import SourceConfig
from app.db import DocumentIn
from app.extract import Extracted, clean_text, extract_article, parse_date
from app.fetch import FetchError
from app.sources.base import AdapterContext


@dataclass
class ListItem:
    url: str
    title: str
    published_at: date | None


def parse_listing(html: str, base_url: str) -> list[ListItem]:
    soup = BeautifulSoup(html, "lxml")
    base_host = urlsplit(base_url).netloc
    items: list[ListItem] = []
    seen: set[str] = set()
    for link in soup.select("div.title > a[href]"):
        url = urljoin(base_url, link["href"]).split("#")[0]
        if urlsplit(url).netloc != base_host or url in seen:
            continue
        seen.add(url)
        container = link.find_parent("li") or link.parent.parent
        date_el = container.select_one("span.date") if container else None
        items.append(
            ListItem(
                url=url,
                title=link.get_text(" ", strip=True),
                published_at=parse_date(date_el.get_text(strip=True)) if date_el else None,
            )
        )
    return items


def extract_govpl(html: str) -> Extracted | None:
    soup = BeautifulSoup(html, "lxml")
    article = soup.select_one("article.article-area__article") or soup.select_one("article#main-content")
    if article is None:
        return None
    site = soup.find("meta", property="og:site_name")
    title_el = article.find(["h2", "h1"])
    date_el = article.select_one("p.event-date")
    parts = [el.get_text("\n") for el in (article.select_one("p.intro"), article.select_one("div.editor-content")) if el is not None]
    return Extracted(
        title=title_el.get_text(" ", strip=True) if title_el else None,
        publisher=site["content"].strip() if site and site.get("content") else None,
        published_at=parse_date(date_el.get_text(strip=True)) if date_el else None,
        language="pl",
        text=clean_text("\n".join(parts)),
    )


def parse_article(html: str, url: str, source: SourceConfig, fallback: ListItem | None = None) -> DocumentIn:
    extracted = extract_govpl(html) or extract_article(html, url)
    return DocumentIn(
        url=url,
        source_id=source.id,
        source_type=source.source_type,
        title=extracted.title or (fallback.title if fallback else None),
        publisher=extracted.publisher or source.publisher,
        published_at=extracted.published_at or (fallback.published_at if fallback else None),
        text=extracted.text,
    )


def _page_url(base: str, page: int) -> str:
    if page == 1:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}page={page}&size=10"


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    source = ctx.source
    await ctx.require_robots(source.url)
    max_pages = int(source.options.get("max_pages", 10))
    considered = 0
    for page in range(1, max_pages + 1):
        listing = await ctx.fetcher.get(_page_url(source.url, page), polite=True)
        items = parse_listing(listing.text, listing.final_url)
        if not items:
            return
        for item in items:
            if considered >= ctx.max_items:
                return
            considered += 1
            if ctx.should_skip(item.url):
                continue
            try:
                result = await ctx.fetcher.get(item.url, polite=True)
            except FetchError as exc:
                ctx.add_error(item.url, exc.message)
                continue
            yield parse_article(result.text, item.url, source, fallback=item)
