"""Step 0: input normalization, PII masking, injection flag (code)."""

from __future__ import annotations

import re

from prawdowid.models import NormalizedInput

MAX_CHARS = 4000

_PII_PATTERNS = {
    "PESEL": re.compile(r"\b\d{11}\b"),
    "IBAN": re.compile(r"\bPL\s?\d{2}(?:\s?\d{4}){6}\b", re.IGNORECASE),
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "TEL": re.compile(r"(?<!\d)(?:\+48[\s-]?)?\d{3}[\s-]?\d{3}[\s-]?\d{3}(?!\d)"),
}

# Heuristic (PL + EN); can be replaced with a classifier
# (protectai/deberta-v3-base-prompt-injection-v2). Result is a FLAG, not a block: the text is still
# checked as data, the flag is shown to the user and logged as an audit event by the gateway.
_INJECTION_HINTS = re.compile(
    r"(\b(ignore|disregard|forget)\b(\W+\w+){0,3}\W+(instructions?|rules|prompt)"
    r"|\b(zignoruj|ignoruj|pomiń|zapomnij)\b(\W+\w+){0,3}\W+(instrukcj\w*|polece\w*|zasad\w*|regu\w*)"
    r"|system prompt|prompt\w* systemow\w*|developer mode|tryb\w* (deweloper|programist)\w*"
    r"|jesteś teraz|you are now|\bact as\b"
    r"|(uwaga|instrukcja|polecenie|wiadomość) dla (modelu|ai|asystenta|czatu|bota|sztucznej inteligencji)"
    r"|\basystencie\b|\bassistant:|\bsystem:"
    r"|(odpowiedz|answer|respond|oceń|uznaj)\W+(\w+\W+){0,3}(potwierdzon\w*|prawdziw\w*|true|confirmed)"
    r"|(klucz\w* api|api keys?))",
    re.IGNORECASE,
)


def mask_pii(text: str) -> tuple[str, bool]:
    masked = text
    for label, pattern in _PII_PATTERNS.items():
        masked = pattern.sub(f"[{label}]", masked)
    return masked, masked != text


def normalize(raw_input: str, channel: str = "web") -> NormalizedInput:
    text = " ".join(raw_input.split())[:MAX_CHARS]
    text, masked = mask_pii(text)
    return NormalizedInput(
        text=text,
        channel=channel,
        pii_masked=masked,
        injection_suspected=bool(_INJECTION_HINTS.search(text)),
    )
