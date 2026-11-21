import re
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime

import trafilatura
from bs4 import BeautifulSoup

_PL_MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
}
_NOISE_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "form", "button", "svg", "iframe"]
_CONSENT_MARKERS = re.compile(r"cookie|consent|gdpr|rodo")


@dataclass
class Extracted:
    title: str | None
    publisher: str | None
    published_at: date | None
    language: str | None
    text: str


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})", value):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", value):
        return _safe_date(int(m[3]), int(m[2]), int(m[1]))
    if m := re.search(r"\b(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", value.lower()):
        month = _PL_MONTHS.get(m[2])
        if month:
            return _safe_date(int(m[3]), month, int(m[1]))
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def clean_text(text: str, drop_prefixes: list[str] | None = None) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if not line:
            continue
        if drop_prefixes and any(line.startswith(p) for p in drop_prefixes):
            continue
        lines.append(line)
    return "\n".join(lines)


def html_to_text(html: str, root_selector: str | None = None) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(_is_consent_banner):
        tag.decompose()
    root = soup.select_one(root_selector) if root_selector else None
    root = root or soup.find("main") or soup.find("article") or soup.body or soup
    return clean_text(root.get_text("\n"))


def _is_consent_banner(tag) -> bool:
    if tag.name in ("html", "body", "main", "article"):
        return False
    marker = " ".join([tag.get("id") or "", *(tag.get("class") or []), tag.get("aria-label") or ""]).lower()
    return bool(_CONSENT_MARKERS.search(marker))


def looks_like_js_shell(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one("#root, #app, app-root, #__next, #__nuxt")
    if root is not None and len(root.get_text(strip=True)) < 200:
        return True
    body = soup.body
    return body is not None and len(body.get_text(strip=True)) < 200 and bool(soup.find("script"))


def extract_article(html: str, url: str) -> Extracted:
    doc = trafilatura.bare_extraction(
        html,
        url=url,
        with_metadata=True,
        favor_precision=True,
        include_comments=False,
        include_tables=True,
        date_extraction_params={
            "extensive_search": False,
            "original_date": True,
            "max_date": date.today().isoformat(),
        },
    )
    data = doc.as_dict() if doc is not None and hasattr(doc, "as_dict") else (doc or {})
    text = clean_text(data.get("text") or "")
    full = html_to_text(html)
    is_listing = len(full) > 1000 and len(text) < 0.25 * len(full)
    if (len(text) < 200 or is_listing) and len(full) > len(text):
        text = full
    title = data.get("title") or _html_title(html)
    return Extracted(
        title=_strip_site_suffix(title),
        publisher=data.get("sitename") or data.get("hostname"),
        published_at=parse_date(data.get("date")),
        language=data.get("language"),
        text=text,
    )


def _html_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    return soup.title.get_text(strip=True) if soup.title else None


def _strip_site_suffix(title: str | None) -> str | None:
    if not title:
        return title
    for suffix in (" - Portal Gov.pl",):
        if title.endswith(suffix):
            title = title[: -len(suffix)]
            parts = title.rsplit(" - ", 1)
            return parts[0].strip() if len(parts) == 2 else title.strip()
    return title.strip()
