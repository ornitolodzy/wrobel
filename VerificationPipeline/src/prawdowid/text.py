"""Text helpers shared by web evidence and corpus ingestion: chunking, lexical overlap, reprints."""

from __future__ import annotations

import re
import zlib

from prawdowid.models import Query

_WORD = re.compile(r"\w+", re.UNICODE)


def query_terms(q: Query) -> set[str]:
    """Crude Polish stemming: 5-letter prefixes of keywords (Czech/Czechy/Czechami -> czech)."""
    words = _WORD.findall(f"{q.sparse} {q.dense}")
    return {w.lower()[:5] for w in words
            if len(w) > 3 or w.isdigit() or (w.isupper() and len(w) >= 2)}  # keep acronyms: RCB, MSZ


def lexical_score(text: str, terms: set[str]) -> float:
    if not terms:
        return 0.0
    present = {w[:5] for w in _WORD.findall(text.lower())}
    return len(terms & present) / len(terms)


def _tail(chunk: str, overlap: int) -> str:
    """Last ~`overlap` chars of a chunk, starting at a sentence boundary when possible."""
    if overlap <= 0 or len(chunk) <= overlap:
        return ""
    tail = chunk[-overlap:]
    cut = tail.find(". ")
    return tail[cut + 2:].strip() if 0 <= cut < len(tail) - 2 else tail.strip()


def chunk_text(text: str, max_chars: int, overlap: int = 0) -> list[str]:
    """Paragraph-packed chunks of at most ~max_chars; `overlap` repeats the end of the previous
    chunk at the start of the next one, so a sentence on a boundary is quotable from one chunk."""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    cur = ""

    def close(c: str) -> str:
        chunks.append(c)
        return _tail(c, overlap)

    for p in paras:
        if cur and len(cur) + len(p) + 1 > max_chars:
            cur = close(cur)
        cur = f"{cur}\n{p}" if cur else p
        while len(cur) > max_chars:  # very long paragraph: hard split on sentence end
            cut = cur.rfind(". ", 0, max_chars) + 1 or max_chars
            head, cur = cur[:cut].strip(), cur[cut:].strip()
            tail = close(head)
            cur = f"{tail} {cur}".strip() if tail else cur
    if cur and (not chunks or cur != _tail(chunks[-1], overlap)):
        chunks.append(cur)
    return chunks


def _shingles(text: str, k: int = 5) -> set[int]:
    w = _WORD.findall(text.lower())
    # crc32, not hash(): stable across processes, so reprint groups are reproducible
    return {zlib.crc32(" ".join(w[i:i + k]).encode()) for i in range(max(len(w) - k + 1, 1))}


def assign_dup_groups(pages: list[tuple[str, str, str]], threshold: float = 0.5,
                      prefix: str = "web") -> dict[str, str]:
    """pages: (url, domain, text) -> {url: dup_group}. Near-duplicate texts share a group."""
    groups: dict[str, str] = {}
    sigs = [(url, dom, _shingles(text)) for url, dom, text in pages]
    for i, (url, dom, sig) in enumerate(sigs):
        groups[url] = f"{prefix}:{dom}:{zlib.crc32(url.encode()):08x}" if prefix != "web" else f"web:{dom}"
        for prev_url, _, prev_sig in sigs[:i]:
            inter = len(sig & prev_sig)
            if inter and inter / len(sig | prev_sig) >= threshold:
                groups[url] = groups[prev_url]
                break
    return groups
