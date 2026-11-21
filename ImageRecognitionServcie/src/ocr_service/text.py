"""Post-processing of recognised blocks: reading order and language detection."""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence

from ocr_service.engines.base import TextBlock

_PL_DIACRITICS = frozenset("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
_PL_WORDS = frozenset(
    "i w z na się nie do to że jest o jak ale po co tak za od dla oraz lub przez czy są "
    "być jego jej ich ten ta te tym który która które może można jeśli aby już tylko "
    "także więc przy pod nad bez był była było wszystkie plik zapisz anuluj ustawienia "
    "wyślij zamknij otwórz usuń dodaj szukaj strona główna konto hasło ".split()
)
_EN_WORDS = frozenset(
    "the and of to in is you that it for on with as are this be or by not your from at "
    "have an was will can if all more about which their has but they we what when there "
    "use file save cancel settings send close open delete add search home account "
    "password ".split()
)
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def group_lines(blocks: Sequence[TextBlock]) -> list[list[TextBlock]]:
    """Group blocks into visual lines (top-to-bottom), each sorted left-to-right."""
    if not blocks:
        return []
    heights = [max(1, b.bbox[3] - b.bbox[1]) for b in blocks]
    tolerance = max(statistics.median(heights) * 0.5, 1.0)

    def center_y(b: TextBlock) -> float:
        return (b.bbox[1] + b.bbox[3]) / 2

    lines: list[list[TextBlock]] = []
    line_centers: list[float] = []
    for block in sorted(blocks, key=lambda b: (center_y(b), b.bbox[0])):
        cy = center_y(block)
        if lines and abs(cy - line_centers[-1]) <= tolerance:
            lines[-1].append(block)
            line_centers[-1] = statistics.fmean(center_y(b) for b in lines[-1])
        else:
            lines.append([block])
            line_centers.append(cy)
    return [sorted(line, key=lambda b: b.bbox[0]) for line in lines]


def lines_to_text(lines: Sequence[Sequence[TextBlock]]) -> str:
    return "\n".join(" ".join(b.text for b in line) for line in lines)


def detect_language(text: str, candidates: Sequence[str]) -> str | None:
    """Pick the most likely language among ``candidates`` (lightweight pl/en heuristic)."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return None
    scores = {lang: 0.0 for lang in candidates}
    if "pl" in scores:
        scores["pl"] = sum(
            2.0 if any(ch in _PL_DIACRITICS for ch in w) else 1.0 if w in _PL_WORDS else 0.0
            for w in words
        )
    if "en" in scores:
        scores["en"] = float(sum(1 for w in words if w in _EN_WORDS))
    best = max(candidates, key=lambda lang: scores[lang])  # ties -> first candidate
    return best
