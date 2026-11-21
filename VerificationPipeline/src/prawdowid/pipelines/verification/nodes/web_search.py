"""Web search per factual claim -> links to check (title, url, snippet, catalog annotation).

Results are NOT evidence and do not change the verdict: a search hit has no verified quote.
Catalog domains (conf/base/sources.yml) are ranked first and labelled with their source type.
The query is the claim text after PII masking (step 0).
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

from prawdowid.catalog import domain_of, load_catalog, match_source
from prawdowid.llm import usage
from prawdowid.models import Query, WebResult
from prawdowid.websearch import get_search

_TYPE_ORDER = {"primary": 0, "factcheck": 1, "secondary": 2, None: 3}


def _blocked(domain: str, blocked: list[str]) -> bool:
    return any(domain == b or domain.endswith("." + b) for b in blocked)


def _search_strings(q: Query, params: dict) -> list[str]:
    """General query + one query restricted to each `site_queries` domain (e.g. gov.pl)."""
    base = q.dense[:300]
    return [base, *(f"{base} site:{d}" for d in params.get("site_queries", []))]


def _search_one(q: Query, params: dict, catalog: tuple[dict, ...]) -> list[WebResult]:
    engine = get_search(params)
    hits = []
    for text in _search_strings(q, params):
        try:
            hits += engine.search(text, params.get("max_results", 8))
        except Exception as e:  # search is best-effort: never fails the check
            usage.record(role="web_search", event="error", claim_id=q.claim_id, query=text,
                         reason=f"{type(e).__name__}: {e}")

    seen, rows = set(), []
    for h in hits:
        dom = domain_of(h.url)
        if not dom or h.url in seen or _blocked(dom, params.get("blocked_domains", [])):
            continue
        seen.add(h.url)
        src = match_source(h.url, catalog)
        rows.append(WebResult(
            claim_id=q.claim_id, rank=0, title=h.title.strip(), url=h.url, domain=dom,
            snippet=h.snippet.strip()[:400], in_catalog=src is not None,
            source_type=src.get("type") if src else None,
            publisher=src.get("publisher") if src else None,
        ))
    rows.sort(key=lambda r: _TYPE_ORDER[r.source_type])  # stable: keeps engine order within a type
    rows = rows[: params.get("max_results", 8)]
    for i, r in enumerate(rows, start=1):
        r.rank = i
    usage.record(role="web_search", claim_id=q.claim_id, provider=params.get("provider"),
                 results=len(rows), in_catalog=sum(r.in_catalog for r in rows))
    return rows


def web_search(queries: list[Query], web_params: dict) -> list[WebResult]:
    if not web_params.get("enabled", True) or not queries:
        return []
    catalog = load_catalog(web_params.get("catalog_path", "conf/base/sources.yml"))
    with ThreadPoolExecutor(max_workers=min(len(queries), 4)) as pool:
        futures = [pool.submit(contextvars.copy_context().run, _search_one, q, web_params, catalog)
                   for q in queries]
        return [r for f in futures for r in f.result()]
