"""schema.org ClaimReview from fact-check pages (JSON-LD), so the graph gets the checked claim,
its author and the rating exactly as the fact-checker declared them, without an LLM.

Demagog and FakeNews.pl publish it; Konkret24 and EUvsDisinfo articles do not (empty result).
Output is the wire format of ContextGraph `ClaimIn` and is stored in `Document.extra["claim_reviews"]`.
"""

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from app.extract import parse_date

MAX_APPEARANCES = 20


def _types(node: dict) -> set[str]:
    value = node.get("@type")
    return {value} if isinstance(value, str) else {t for t in value or [] if isinstance(t, str)}


def _walk(data: Any):
    """Every dict in a JSON-LD document: top-level object, list, or @graph."""
    if isinstance(data, dict):
        yield data
        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value)
    elif isinstance(data, list):
        for item in data:
            yield from _walk(item)


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        value = re.sub(r"\s+", " ", value).strip()
        return value or None
    return None


def _date(value: Any) -> str | None:
    parsed = parse_date(value) if isinstance(value, str) else None
    return parsed.isoformat() if parsed else None


def _urls(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    urls = []
    for item in items:
        url = item.get("url") if isinstance(item, dict) else item
        if isinstance(url, str) and url.strip().startswith(("http://", "https://")):
            url = url.strip()
            if url not in urls:
                urls.append(url)
    return urls[:MAX_APPEARANCES]


def _review(node: dict) -> dict | None:
    text = _text(node.get("claimReviewed"))
    if not text:
        return None
    item = _first(node.get("itemReviewed"))
    item = item if isinstance(item, dict) else {}
    author = _first(item.get("author"))
    author = author if isinstance(author, dict) else {"name": author} if isinstance(author, str) else {}
    rating = _first(node.get("reviewRating"))
    rating = rating if isinstance(rating, dict) else {}
    author_types = _types(author)
    appearances = _urls(item.get("appearance"))
    appearances += [u for u in _urls(item.get("firstAppearance")) if u not in appearances]
    return {
        "text": text,
        "claimant": _text(author.get("name")),
        "claimant_type": "Organization" if "Organization" in author_types else "Person" if author_types else None,
        "appearances": appearances[:MAX_APPEARANCES],
        "rating": _text(rating.get("alternateName")) or _text(rating.get("name")),
        "claim_date": _date(item.get("datePublished")),
        "review_date": _date(node.get("datePublished")),
        "origin": "claimreview",
    }


def extract_claim_reviews(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    reviews: list[dict] = []
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or script.get_text()
        if "ClaimReview" not in raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in _walk(data):
            if "ClaimReview" in _types(node) and (review := _review(node)) and review not in reviews:
                reviews.append(review)
    return reviews
