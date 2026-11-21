import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("IBAN", re.compile(r"\bPL\s?\d{2}(?:\s?\d{4}){6}\b", re.IGNORECASE)),
    ("KARTA", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    ("PESEL", re.compile(r"\b\d{11}\b")),
    ("TELEFON", re.compile(r"(?<![\w+])(?:\+48[ -]?\d{3}[ -]?\d{3}[ -]?\d{3}|\d{3}-\d{3}-\d{3}|\d{9})(?!\w)")),
    ("NR_DOWODU", re.compile(r"\b[A-Z]{3}\s?\d{6}\b")),
]


def mask_pii(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    for label, pattern in _PATTERNS:
        text, count = pattern.subn(f"[{label}]", text)
        if count:
            found.append(label)
    return text, found
