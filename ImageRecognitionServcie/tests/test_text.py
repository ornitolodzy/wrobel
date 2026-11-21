from __future__ import annotations

from ocr_service.engines.base import TextBlock
from ocr_service.text import detect_language, group_lines, lines_to_text


def block(text: str, x1: int, y1: int, x2: int, y2: int) -> TextBlock:
    return TextBlock(text=text, bbox=(x1, y1, x2, y2), confidence=0.9)


def test_reading_order_groups_lines_and_sorts_left_to_right() -> None:
    blocks = [
        block("świat", 120, 12, 200, 38),
        block("drugi wiersz", 10, 60, 160, 85),
        block("Witaj", 10, 10, 100, 36),
    ]
    lines = group_lines(blocks)
    assert [[b.text for b in line] for line in lines] == [["Witaj", "świat"], ["drugi wiersz"]]
    assert lines_to_text(lines) == "Witaj świat\ndrugi wiersz"


def test_empty_blocks() -> None:
    assert group_lines([]) == []
    assert lines_to_text([]) == ""


def test_detect_polish() -> None:
    assert detect_language("Zażółć gęślą jaźń", ["pl", "en"]) == "pl"
    assert detect_language("Plik zapisano i nie ma błędów", ["en", "pl"]) == "pl"


def test_detect_english() -> None:
    assert detect_language("The file was saved and you can close this window", ["pl", "en"]) == "en"


def test_detect_single_candidate_and_empty() -> None:
    assert detect_language("anything", ["en"]) == "en"
    assert detect_language("12345", ["pl", "en"]) is None
    assert detect_language("", []) is None
