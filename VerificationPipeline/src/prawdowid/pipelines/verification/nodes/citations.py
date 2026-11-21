"""Step 7: citation validator (code). See docs/adr/0003-walidator-cytatow.md.

Level 1: the quote must appear VERBATIM (after normalization) in the text the model was shown
(Evidence.text). No graph lookup, no live re-fetch. Fuzzy matching is diagnostics only.
Level 2: the chunk's section (ocena / tresc / naglowek) becomes the quote kind, which limits
what the answer may say on its basis (applied in policy.source_refs and compose).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from prawdowid.llm import usage
from prawdowid.models import ClaimEvidence, QuoteKind, Stance

_CHARS = str.maketrans({"„": '"', "”": '"', "“": '"', "«": '"', "»": '"', "‚": "'", "’": "'",
                        "–": "-", "—": "-", " ": " "})
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
# Omission markers inside a quote: "A … B", "A ... B", "A [...] B", "A (…) B"
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:\.\.\.|…)\s*\]|\(\s*(?:\.\.\.|…)\s*\)|\.\.\.|…)\s*")

MIN_WORDS = 4
MIN_CHARS = 20


def normalize(text: str) -> str:
    """Lowercase, unify quotes/dashes, drop punctuation, collapse whitespace."""
    text = _PUNCT.sub(" ", text.translate(_CHARS).lower())
    return " ".join(text.split())


@dataclass
class CitationCheck:
    ok: bool
    reason: str
    fuzzy_score: float | None = None  # diagnostics only, never used to accept


def check_quote(quote: str, text: str, kind: QuoteKind = "tresc",
                min_words: int = MIN_WORDS, min_chars: int = MIN_CHARS) -> CitationCheck:
    segments = [normalize(s) for s in _ELLIPSIS.split(quote)]
    segments = [s for s in segments if s]
    if not segments:
        return CitationCheck(False, "pusty cytat")
    target = normalize(text)

    if kind == "ocena":  # a rating label is short by nature: must equal the label on the page
        ok = " ".join(segments) == target
        return CitationCheck(ok, "ok" if ok else "etykieta oceny nie zgadza się ze stroną")

    words = sum(len(s.split()) for s in segments)
    chars = sum(len(s) for s in segments)
    if kind == "tresc" and (words < min_words or chars < min_chars):
        return CitationCheck(False, f"za krótki ({words} słów): nic nie dowodzi")

    padded, pos = f" {target} ", 0
    for seg in segments:  # every segment, in order, on word boundaries
        i = padded.find(f" {seg} ", pos)
        if i < 0:
            score = fuzz.partial_ratio(" ".join(segments), target)
            return CitationCheck(False, "nie występuje dosłownie w źródle", round(score, 1))
        pos = i + len(seg) + 1
    return CitationCheck(True, "ok")


def quote_in_text(quote: str, text: str, kind: QuoteKind = "tresc") -> bool:
    return check_quote(quote, text, kind).ok


def validate_citations(
    stances_raw: list[Stance], evidence: list[ClaimEvidence], citation_params: dict
) -> list[Stance]:
    chunks = {e.chunk_id: e for ce in evidence for e in ce.evidence}
    kept, rejected = [], 0
    for s in stances_raw:
        if s.label == "irrelevant":
            kept.append(s)
            continue
        e = chunks.get(s.chunk_id)
        if e is None:
            check = CitationCheck(False, "nieznany fragment")
        else:
            check = check_quote(s.quote, e.text, e.section,
                                citation_params.get("min_words", MIN_WORDS),
                                citation_params.get("min_chars", MIN_CHARS))
        if check.ok:
            kept.append(s)
        else:
            rejected += 1
            usage.record(role="citations", event="rejected", claim_id=s.claim_id,
                         chunk_id=s.chunk_id, reason=check.reason, quote=s.quote[:200],
                         fuzzy_score=check.fuzzy_score)
    usage.record(role="citations", checked=len(stances_raw), rejected=rejected)
    return kept
