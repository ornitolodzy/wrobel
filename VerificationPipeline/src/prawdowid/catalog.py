"""Audited source catalog (conf/base/sources.yml): URL -> publisher and source type."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

import yaml


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


@lru_cache(maxsize=4)
def load_catalog(path: str) -> tuple[dict, ...]:
    from prawdowid.runtime import PROJECT_ROOT

    try:
        sources = yaml.safe_load((PROJECT_ROOT / path).read_text(encoding="utf-8"))["sources"]
    except (OSError, KeyError, TypeError):
        return ()
    return tuple(s for s in sources if s.get("active", True))


def match_source(url: str, catalog: tuple[dict, ...]) -> dict | None:
    """Most specific catalog entry for a URL: longest domain, then longest path prefix."""
    dom = domain_of(url)
    path = urlparse(url).path or "/"
    best, best_key = None, (-1, -1)
    for src in catalog:
        d = src["domain"].lower()
        if not (dom == d or dom.endswith("." + d)):
            continue
        p = src.get("path", "")
        if p and not (path == p or path.startswith(p.rstrip("/") + "/")):
            continue
        key = (len(d), len(p))
        if key > best_key:
            best, best_key = src, key
    return best


def seed_urls(catalog: tuple[dict, ...]) -> list[tuple[str, dict]]:
    return [(url, src) for src in catalog for url in src.get("seeds", [])]
