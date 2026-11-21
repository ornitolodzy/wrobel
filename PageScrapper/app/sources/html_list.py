import re
from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.db import DocumentIn
from app.extract import extract_article, parse_date
from app.fetch import FetchError
from app.sources.base import AdapterContext


def find_article_links(html: str, base_url: str, pattern: str) -> list[str]:
    regex = re.compile(pattern)
    host = urlsplit(base_url).netloc
    links: list[str] = []
    for a in BeautifulSoup(html, "lxml").select("a[href]"):
        url = urljoin(base_url, a["href"]).split("#")[0]
        if urlsplit(url).netloc == host and regex.search(url) and url not in links:
            links.append(url)
    return links


def parse_page(html: str, url: str, ctx: AdapterContext) -> DocumentIn:
    source = ctx.source
    extracted = extract_article(html, url)
    published_at = extracted.published_at
    date_selector = source.options.get("date_selector")
    if date_selector:
        element = BeautifulSoup(html, "lxml").select_one(date_selector)
        published_at = (parse_date(element.get_text(" ", strip=True)) if element else None) or published_at
    return DocumentIn(
        url=url,
        source_id=source.id,
        source_type=source.source_type,
        title=extracted.title,
        publisher=source.publisher,
        published_at=published_at,
        text=extracted.text,
    )


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    source = ctx.source
    pattern = source.options.get("link_pattern")
    if not pattern:
        raise ValueError(f"Źródło {source.id}: brak options.link_pattern")
    await ctx.require_robots(source.url)
    listing = await ctx.fetcher.get(source.url, polite=True)
    for url in find_article_links(listing.text, listing.final_url, pattern)[: ctx.max_items]:
        if ctx.should_skip(url):
            continue
        if not await ctx.fetcher.allowed_by_robots(url):
            ctx.add_error(url, "robots.txt nie pozwala")
            continue
        try:
            page = await ctx.fetcher.get(url, polite=True)
        except FetchError as exc:
            ctx.add_error(url, exc.message)
            continue
        yield parse_page(page.text, url, ctx)
