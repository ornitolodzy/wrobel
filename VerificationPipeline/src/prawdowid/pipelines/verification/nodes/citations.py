"""Step 7: citation validator (code). Unverified quotes drop the stance."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from prawdowid.models import ClaimEvidence, Stance

_QUOTES = str.maketrans({"„": '"', "”": '"', "“": '"', "«": '"', "»": '"', "–": "-", "—": "-"})


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_QUOTES)).strip().lower()


def quote_in_text(quote: str, text: str, fuzzy_min: int = 90) -> bool:
    q, t = _norm(quote), _norm(text)
    if not q:
        return False
    return q in t or fuzz.partial_ratio(q, t) >= fuzzy_min


def validate_citations(
    stances_raw: list[Stance], evidence: list[ClaimEvidence], citation_params: dict
) -> list[Stance]:
    chunks = {e.chunk_id: e.text for ce in evidence for e in ce.evidence}
    fuzzy_min = citation_params.get("fuzzy_min", 90)
    return [
        s
        for s in stances_raw
        if s.label == "irrelevant"
        or (s.chunk_id in chunks and quote_in_text(s.quote, chunks[s.chunk_id], fuzzy_min))
    ]
