import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["official", "fact_check", "disinfo_db"]
Adapter = Literal[
    "govpl_list", "html_list", "odyseusz", "rss", "sejm_eli", "google_factcheck", "euvsdisinfo_mcp", "file_import",
    "live_only",
]
LiveSearchKind = Literal["wp_rest", "html", "rendered", "factcheck_api"]

# Adapters that read an API, an MCP server or a file, not the site's HTML pages:
# their URLs are not sites a single page may be scraped from (POST /scrap/page).
NON_PAGE_ADAPTERS = frozenset({"sejm_eli", "google_factcheck", "euvsdisinfo_mcp", "file_import"})

# Path segments a page must share with a source URL to count as that source's page
# (/web/rcb/<artykuł> -> rcb-komunikaty, /web/fundusze-regiony/<artykuł> -> no gov.pl list source).
_MIN_SHARED_SEGMENTS = 2


def site_of(url: str) -> str | None:
    """`scheme://host[:port]` of an http(s) URL, lowercase; None for anything else."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return f"{parts.scheme}://{parts.netloc.lower()}"


def normalize_page_url(url: str) -> str:
    """Lowercase scheme and host, drop the fragment: the form a page is matched and stored under."""
    parts = urlsplit(url.strip())
    return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower(), fragment="").geturl()


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def _shared_segments(a: list[str], b: list[str]) -> int:
    count = 0
    for x, y in zip(a, b):
        if x != y:
            break
        count += 1
    return count


class LiveSearchConfig(BaseModel):
    """Wyszukiwarka witryny używana przy wyszukiwaniu na żywo (gdy w bazie brak potwierdzonych danych).

    kind: wp_rest  - WordPress REST API (`{url}/wp-json/wp/v2/search`), `url` = adres witryny
          html     - zwykła strona wyników, `url` z `{query}`, linki wybierane przez `link_pattern`
          rendered - jak html, ale strona wyników budowana JavaScriptem (Playwright)
          factcheck_api - Google Fact Check Tools API (claims:search), `url` = endpoint API;
                          rekordy ClaimReview zapisujemy wprost, bez pobierania stron weryfikatorów
    """

    model_config = ConfigDict(extra="forbid")

    kind: LiveSearchKind
    url: str
    link_pattern: str | None = None
    exclude_pattern: str | None = None
    max_results: int = Field(default=5, ge=1, le=20)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    name: str
    publisher: str
    source_type: SourceType
    adapter: Adapter
    url: str
    active: bool = True
    max_items: int = Field(default=20, ge=1, le=500)
    refresh_known: bool = False
    license: str = ""
    description: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    live_search: LiveSearchConfig | None = None

    def page_urls(self) -> list[str]:
        """Source URLs that point at the site itself (the list / feed and the site search)."""
        if not self.active or self.adapter in NON_PAGE_ADAPTERS:
            return []
        urls = [self.url]
        if self.live_search is not None and self.live_search.kind != "factcheck_api":
            urls.append(self.live_search.url)
        return [u for u in urls if site_of(u)]

    def sites(self) -> set[str]:
        return {site for u in self.page_urls() if (site := site_of(u))}

    def article_pattern_matches(self, url: str) -> bool:
        cfg = self.live_search
        if cfg is None or not cfg.link_pattern:
            return False
        return bool(re.search(cfg.link_pattern, url)) and not (
            cfg.exclude_pattern and re.search(cfg.exclude_pattern, url)
        )


class Catalog(BaseModel):
    sources: list[SourceConfig]

    def get(self, source_id: str) -> SourceConfig | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def page_sites(self) -> list[str]:
        """Every `scheme://host` a single page may be scraped from, in catalog order."""
        return list(dict.fromkeys(site for s in self.sources for u in s.page_urls() if (site := site_of(u))))

    def source_for_page(self, url: str) -> SourceConfig | None:
        """The catalog source a page belongs to, or None when its site is not in the catalog.

        The page must start with the address of a listed site (`scheme://host/`). Several sources
        can share a site (www.gov.pl), so the most specific one wins:
        1. the one whose URL shares at least two leading path segments with the page (longest first),
        2. the one whose live_search link_pattern accepts the page (the site-wide source, e.g. gov.pl search),
        3. the first one in the catalog.
        """
        site = site_of(url)
        if site is None:
            return None
        page = _segments(url)
        best: tuple[tuple[int, bool, int], SourceConfig] | None = None
        for index, source in enumerate(self.sources):
            urls = [u for u in source.page_urls() if site_of(u) == site]
            if not urls:
                continue
            shared = max(_shared_segments(page, _segments(u)) for u in urls)
            rank = (shared if shared >= _MIN_SHARED_SEGMENTS else 0, source.article_pattern_matches(url), -index)
            if best is None or rank > best[0]:
                best = (rank, source)
        return best[1] if best else None


def load_catalog(path: str | Path) -> Catalog:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    catalog = Catalog.model_validate(data)
    ids = [s.id for s in catalog.sources]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate source ids in catalog: {sorted(duplicates)}")
    return catalog
