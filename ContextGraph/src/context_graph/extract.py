"""Entity extraction. `RegexExtractor` is deterministic and dependency-free; swap in spaCy/LLM
by implementing `EntityExtractor`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from context_graph.textnorm import stem

UPPER = "A-ZĄĆĘŁŃÓŚŹŻ"
LOWER = "a-ząćęłńóśźż"

MONTHS = {
    "stycz": 1,
    "lut": 2,
    "mar": 3,
    "kwie": 4,
    "maj": 5,
    "czerw": 6,
    "lip": 7,
    "sierp": 8,
    "wrze": 9,
    "paźdz": 10,
    "listop": 11,
    "grud": 12,
}

ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
NUMERIC_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?!\d)")
WORDY_DATE = re.compile(
    r"\b(\d{1,2})\s+(stycz\w*|lut\w*|mar\w*|kwie\w*|maj\w*|czerw\w*|lip\w*|sierp\w*|wrze\w*|"
    r"paźdz\w*|listop\w*|grud\w*)(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
ACRONYM = re.compile(rf"\b[{UPPER}]{{2,6}}\b")
PROPER = re.compile(rf"\b[{UPPER}][{LOWER}]+(?:[ -][{UPPER}][{LOWER}]+)*")
SENTENCE_PUNCT = set('.!?…:;„"«(')

# Small gazetteer so common places get a useful type; everything else is MISC.
GPE_STEMS = {
    "pols",
    "rosj",
    "biało",
    "ukrai",
    "litw",
    "łotw",
    "niem",
    "czech",
    "słowa",
    "węgr",
    "mosk",
    "kijó",
    "kijo",
    "mińs",
    "warsz",
    "berl",
    "pary",
    "unia",
    "nato",
    "euro",
}


@dataclass(frozen=True)
class Entity:
    key: str  # graph identity (normalized), e.g. "msz", "date:2026-11-20", "pols"
    name: str  # surface form, first seen
    type: str
    year_inferred: bool = False  # DATE only: the year came from the document's publication date


class EntityExtractor(Protocol):
    def extract(self, text: str, ref_date: date | None = None) -> list[Entity]: ...


def _month(word: str) -> int | None:
    w = word.lower()
    for prefix, num in MONTHS.items():
        if w.startswith(prefix):
            return num
    return None


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    value = int(raw)
    return value + 2000 if value < 100 else value


def date_entity(day: int, month: int, year: str | None, surface: str, ref_date: date | None = None) -> Entity | None:
    """Full date key with year (date:YYYY-MM-DD). Without a year in the text the year of the document's
    publication date is used and marked as inferred; with neither, an ISO month-day key (date:--MM-DD)
    that the context reader matches against every year."""
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    explicit = _year(year)
    resolved = explicit or (ref_date.year if ref_date else None)
    if resolved is None:
        return Entity(key=f"date:--{month:02d}-{day:02d}", name=surface, type="DATE")
    try:
        date(resolved, month, day)
    except ValueError:
        return None
    return Entity(
        key=f"date:{resolved:04d}-{month:02d}-{day:02d}",
        name=surface,
        type="DATE",
        year_inferred=explicit is None,
    )


class RegexExtractor:
    def extract(self, text: str, ref_date: date | None = None) -> list[Entity]:
        found: dict[str, Entity] = {}
        spans: list[tuple[int, int]] = []

        def add(ent: Entity | None, span: tuple[int, int]) -> None:
            if ent is None:
                return
            spans.append(span)
            found.setdefault(ent.key, ent)

        for m in ISO_DATE.finditer(text):
            add(date_entity(int(m.group(3)), int(m.group(2)), m.group(1), m.group(0)), m.span())
        for m in NUMERIC_DATE.finditer(text):
            if any(s <= m.start() < e for s, e in spans):
                continue
            add(date_entity(int(m.group(1)), int(m.group(2)), m.group(3), m.group(0), ref_date), m.span())
        for m in WORDY_DATE.finditer(text):
            month = _month(m.group(2))
            if month:
                add(date_entity(int(m.group(1)), month, m.group(3), m.group(0), ref_date), m.span())

        for m in ACRONYM.finditer(text):
            add(Entity(key=m.group(0).lower(), name=m.group(0), type="ORG"), m.span())

        for m in PROPER.finditer(text):
            if any(s <= m.start() < e for s, e in spans):
                continue
            words = re.split(r"[ -]", m.group(0))
            prefix = text[: m.start()].rstrip()
            at_start = not prefix or prefix[-1] in SENTENCE_PUNCT
            if at_start and stem(words[0]) not in GPE_STEMS:
                # First word of a sentence is capitalised anyway: keep only the rest of the name
                # (unless it is a known place: "Rosja naruszyła...").
                words = words[1:]
                if not words:
                    continue
            name = " ".join(words)
            key = " ".join(stem(w) for w in words)
            etype = "GPE" if len(words) == 1 and key in GPE_STEMS else "MISC"
            found.setdefault(key, Entity(key=key, name=name, type=etype))
        return list(found.values())
