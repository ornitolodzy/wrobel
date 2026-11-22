"""Deterministic text normalisation: crude Polish stemming and Lucene query building.

Neo4j has no Polish analyzer, so inflection ("przestrzeń" / "przestrzeni") is handled on the
query side with prefix wildcards, and entity keys use fixed-length prefixes.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

WORD_RE = re.compile(r"\d{1,4}(?:[./]\d{1,4})*|[^\W\d_]+", re.UNICODE)

STOPWORDS = {
    "a",
    "aby",
    "ale",
    "albo",
    "bo",
    "być",
    "by",
    "był",
    "była",
    "było",
    "były",
    "czy",
    "dla",
    "do",
    "go",
    "i",
    "ich",
    "ja",
    "jak",
    "jako",
    "je",
    "jest",
    "jego",
    "jej",
    "już",
    "lub",
    "ma",
    "mu",
    "na",
    "nad",
    "nie",
    "niż",
    "o",
    "od",
    "oraz",
    "po",
    "pod",
    "przez",
    "przy",
    "się",
    "są",
    "ta",
    "tak",
    "te",
    "tego",
    "ten",
    "to",
    "tu",
    "tym",
    "w",
    "we",
    "z",
    "za",
    "ze",
    "że",
    "co",
    "który",
    "która",
    "które",
    "dnia",
    "dzień",
    "roku",
    "the",
    "of",
    "and",
}

LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def short_hash(text: str, n: int = 16) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def content_hash(text: str) -> str:
    return short_hash(normalize_space(text).lower(), 40)


def stem(word: str) -> str:
    """Fixed-length prefix stem: Polska/Polski/Polsce -> pols, Ukraina/Ukrainie -> ukrai."""
    w = word.lower()
    if len(w) >= 7:
        return w[:5]
    if len(w) >= 4:
        return w[:4]
    return w


def tokens(text: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(text)]


def lucene_query(text: str) -> str:
    """OR-query of prefix stems, e.g. 'naruszono przestrzeń 20.11' -> 'narusz* OR przestr* OR 20.11'."""
    terms: list[str] = []
    for tok in tokens(text):
        if tok in STOPWORDS or (len(tok) < 3 and not tok[0].isdigit()):
            continue
        if tok[0].isdigit():
            term = LUCENE_SPECIAL.sub(r"\\\1", tok)
        elif len(tok) >= 5:
            term = LUCENE_SPECIAL.sub(r"\\\1", tok[: max(4, len(tok) - 3)]) + "*"
        else:
            term = LUCENE_SPECIAL.sub(r"\\\1", tok)
        if term not in terms:
            terms.append(term)
    return " OR ".join(terms)


def normalize_quote(text: str) -> str:
    text = text.replace("„", '"').replace("”", '"').replace("“", '"').replace("’", "'")
    return normalize_space(text).lower()
