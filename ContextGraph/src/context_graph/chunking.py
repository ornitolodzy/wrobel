"""Split text into sentence-aligned chunks (deterministic, no model)."""

from __future__ import annotations

import re

from context_graph.textnorm import normalize_space

# Split after . ! ? … when the next sentence starts with an uppercase letter, digit or quote.
# A dot after a number ("20.11.") is a date, not a sentence end, unless a capital follows.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9„\"«(])")


def split_sentences(text: str) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []
    return [s.strip() for s in SENTENCE_END.split(text) if s.strip()]


def chunk_text(text: str, max_chars: int = 600) -> list[str]:
    """Group whole sentences into chunks of at most `max_chars` (a longer sentence stays whole)."""
    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        chunks.append(current)
    return chunks
