from collections.abc import AsyncIterator

from bs4 import BeautifulSoup

from app.claimreview import extract_claim_reviews
from app.db import DocumentIn
from app.extract import clean_text, extract_article, html_to_text, parse_date
from app.fetch import FetchError
from app.sources.base import AdapterContext


def parse_feed(xml: str) -> list[dict]:
    soup = BeautifulSoup(xml, "xml")
    entries = []
    for item in soup.find_all(["item", "entry"]):
        link_el = item.find("link")
        link = (link_el.get("href") or link_el.get_text(strip=True)) if link_el else None
        if not link:
            continue
        date_el = item.find(["pubDate", "published", "updated"])
        desc_el = item.find(["description", "summary"])
        entries.append(
            {
                "url": link.strip(),
                "title": item.find("title").get_text(strip=True) if item.find("title") else None,
                "published_at": parse_date(date_el.get_text(strip=True)) if date_el else None,
                "description": html_to_text(desc_el.get_text()) if desc_el else "",
                "categories": [c.get_text(strip=True) for c in item.find_all("category")],
            }
        )
    return entries


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    source = ctx.source
    await ctx.require_robots(source.url)
    feed = await ctx.fetcher.get(source.url, polite=True)
    drop = source.options.get("drop_lines_starting_with") or []
    excluded = {c.lower() for c in source.options.get("exclude_categories") or []}
    entries = [e for e in parse_feed(feed.text) if not excluded & {c.lower() for c in e["categories"]}]

    for entry in entries[: ctx.max_items]:
        url = entry["url"]
        if ctx.should_skip(url):
            continue
        text, title, published_at = entry["description"], entry["title"], entry["published_at"]
        extra: dict = {"categories": entry["categories"]} if entry["categories"] else {}
        try:
            page = await ctx.fetcher.get(url, polite=True)
            if reviews := extract_claim_reviews(page.text):
                extra["claim_reviews"] = reviews
            extracted = extract_article(page.text, page.final_url)
            if len(extracted.text) > len(text):
                text = extracted.text
            title = title or extracted.title
            published_at = published_at or extracted.published_at
        except FetchError as exc:
            ctx.add_error(url, f"{exc.message} (użyto opisu z RSS)")
        yield DocumentIn(
            url=url,
            source_id=source.id,
            source_type=source.source_type,
            title=title,
            publisher=source.publisher,
            published_at=published_at,
            text=clean_text(text, drop_prefixes=drop),
            language=source.options.get("language", "pl"),
            extra=extra,
        )
