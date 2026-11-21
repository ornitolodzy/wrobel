import pytest

from app.pii import mask_pii


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("pisz na jan.kowalski@example.com", "EMAIL"),
        ("mój PESEL to 44051401359", "PESEL"),
        ("dzwoń +48 600 700 800", "TELEFON"),
        ("dzwoń 600-700-800", "TELEFON"),
        ("dzwoń 600700800", "TELEFON"),
        ("konto PL61 1090 1014 0000 0712 1981 2874", "IBAN"),
        ("karta 4111 1111 1111 1111", "KARTA"),
        ("dowód ABC123456", "NR_DOWODU"),
    ],
)
def test_masks_pii(text: str, label: str) -> None:
    masked, found = mask_pii(text)
    assert f"[{label}]" in masked
    assert label in found


@pytest.mark.parametrize(
    "text",
    [
        "Rząd przeznaczył 500 000 000 zł na obronność w 2024 roku.",
        "Naruszono przestrzeń powietrzną dnia 20.11.2026 o 14:30.",
        "Ustawa z dnia 12 marca 2022 r. (Dz.U. 2022 poz. 583)",
    ],
)
def test_does_not_mask_facts(text: str) -> None:
    masked, found = mask_pii(text)
    assert masked == text
    assert found == []
