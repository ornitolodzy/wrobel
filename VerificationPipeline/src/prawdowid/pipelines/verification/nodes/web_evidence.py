"""Turn top web search results into evidence: fetch -> extract -> chunk -> pick relevant chunks.

Pages from catalog domains keep their catalog source type; all others get type "web" (unvetted
publisher, low weight in the policy: cannot decide a verdict on their own). Near-identical pages
share a dup_group, so reprints are not counted as independent confirmations.
"""

from __future__ import annotations

import contextvars
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from prawdowid.llm import usage
from prawdowid.models import ClaimEvidence, Evidence, Query, WebResult
from prawdowid.websearch.fetch import get_fetcher

from .normalize import _INJECTION_HINTS
from .retrieval import diversify

_WORD = re.compile(r"\w+", re.UNICODE)


def _terms(q: Query) -> set[str]:
    """Crude Polish stemming: 5-letter prefixes of keywords (Czech/Czechy/Czechami -> czech)."""
    words = _WORD.findall(f"{q.sparse} {q.dense}".lower())
    return {w[:5] for w in words if len(w) > 3 or w.isdigit()}


def lexical_score(text: str, terms: set[str]) -> float:
    if not terms:
        return 0.0
    present = {w[:5] for w in _WORD.findall(text.lower())}
    return len(terms & present) / len(terms)


def chunk_text(text: str, max_chars: int) -> list[str]:
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 1 > max_chars:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{p}" if cur else p
        while len(cur) > max_chars:  # very long paragraph: hard split on sentence end
            cut = cur.rfind(". ", 0, max_chars) + 1 or max_chars
            chunks.append(cur[:cut].strip())
            cur = cur[cut:].strip()
    if cur:
        chunks.append(cur)
    return chunks


def _shingles(text: str, k: int = 5) -> set[int]:
    w = _WORD.findall(text.lower())
    return {hash(" ".join(w[i:i + k])) for i in range(max(len(w) - k + 1, 1))}


def assign_dup_groups(pages: list[tuple[str, str, str]], threshold: float = 0.5) -> dict[str, str]:
    """pages: (url, domain, text) -> {url: dup_group}. Near-duplicate texts share a group."""
    groups: dict[str, str] = {}
    sigs = [(url, dom, _shingles(text)) for url, dom, text in pages]
    for i, (url, dom, sig) in enumerate(sigs):
        groups[url] = f"web:{dom}"
        for prev_url, _, prev_sig in sigs[:i]:
            inter = len(sig & prev_sig)
            if inter and inter / len(sig | prev_sig) >= threshold:
                groups[url] = groups[prev_url]
                break
    return groups


def _fetch(result: WebResult, fetcher):
    try:
        page = fetcher.fetch(result.url)
    except Exception as e:  # best-effort: a failed page never fails the check
        usage.record(role="web_fetch", event="error", url=result.url, reason=f"{type(e).__name__}: {e}")
        return result, None
    if page is None:
        usage.record(role="web_fetch", event="no_text", url=result.url)
    elif _INJECTION_HINTS.search(page.text):
        usage.record(role="web_fetch", event="injection_suspected", url=result.url)
    return result, page


def fetch_web_evidence(
    web_results: list[WebResult], queries: list[Query], fetch_params: dict
) -> list[ClaimEvidence]:
    if not fetch_params.get("enabled", True) or not web_results:
        return []
    top_n = fetch_params.get("top_n_pages", 3)
    picked = [r for q in queries for r in [w for w in web_results if w.claim_id == q.claim_id][:top_n]]
    if not picked:
        return []
    fetcher = get_fetcher(fetch_params)
    with ThreadPoolExecutor(max_workers=min(len(picked), 6)) as pool:
        # copy the trace context HERE (caller thread), not inside the worker
        futures = [pool.submit(contextvars.copy_context().run, _fetch, r, fetcher) for r in picked]
        fetched = [f.result() for f in futures]

    now = datetime.now(timezone.utc)
    pages = [(r, p) for r, p in fetched if p is not None]
    groups = assign_dup_groups([(r.url, r.domain, p.text) for r, p in pages])
    by_query = {q.claim_id: q for q in queries}
    out: dict[str, list[Evidence]] = {}
    for r, page in pages:
        terms = _terms(by_query[r.claim_id])
        doc_id = "web:" + hashlib.sha1(r.url.encode()).hexdigest()[:12]
        scored = sorted(
            ((lexical_score(c, terms), i, c)
             for i, c in enumerate(chunk_text(page.text, fetch_params.get("chunk_chars", 1200)))),
            reverse=True,
        )[: fetch_params.get("chunks_per_page", 2)]
        for score, i, chunk in scored:
            out.setdefault(r.claim_id, []).append(Evidence(
                chunk_id=f"{doc_id}:{i}", doc_id=doc_id,
                source_type=r.source_type or "web",
                publisher=r.publisher or page.sitename or r.domain,
                title=page.title or r.title, url=r.url, published_at=page.published_at,
                fetched_at=now, dup_group=groups[r.url], text=chunk, rerank_score=round(score, 3),
            ))
    usage.record(role="web_fetch", pages_requested=len(picked), pages_ok=len(pages),
                 chunks=sum(len(v) for v in out.values()))
    return [ClaimEvidence(claim_id=cid, evidence=ev) for cid, ev in out.items()]


def merge_evidence(
    evidence_corpus: list[ClaimEvidence], web_evidence: list[ClaimEvidence], retrieval_params: dict
) -> list[ClaimEvidence]:
    """Corpus + web evidence per claim, re-ranked and diversified; early stop recomputed.

    NOTE: corpus scores come from the reranker, web scores are lexical overlap; once the
    cross-encoder reranker is wired in, rerank both together instead.
    """
    merged: dict[str, list[Evidence]] = {}
    order: list[str] = []
    for ce in [*evidence_corpus, *web_evidence]:
        if ce.claim_id not in merged:
            order.append(ce.claim_id)
        merged.setdefault(ce.claim_id, []).extend(ce.evidence)
    out = []
    for cid in order:
        ranked = sorted(merged[cid], key=lambda e: e.rerank_score, reverse=True)
        ranked = diversify(ranked, retrieval_params["top_k"], retrieval_params["max_per_document"],
                           retrieval_params["max_per_dup_group"])
        early = not ranked or ranked[0].rerank_score < retrieval_params["rerank_min"]
        out.append(ClaimEvidence(claim_id=cid, evidence=ranked, early_stop=early))
    return out
