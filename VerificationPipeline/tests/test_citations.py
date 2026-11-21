"""Citation validator level 1 (verbatim after normalization) and quote kinds (ADR 0003)."""

import pytest

from prawdowid.pipelines.verification.nodes.citations import check_quote, quote_in_text

SRC = "Rzecznik MSZ: pismo w sprawie Czech „nie pochodzi” z MSZ i jest fałszywe – to dezinformacja."


@pytest.mark.parametrize("quote", [
    'pismo w sprawie Czech "nie pochodzi" z MSZ',            # typographic quotes unified
    "PISMO W SPRAWIE CZECH NIE POCHODZI Z MSZ",               # case
    "pismo w sprawie Czech nie pochodzi z MSZ i jest fałszywe - to dezinformacja",  # dashes
    "Rzecznik MSZ: pismo … jest fałszywe",                    # ellipsis segments, in order
    "Rzecznik MSZ [...] jest fałszywe – to dezinformacja",
])
def test_accepted(quote):
    assert quote_in_text(quote, SRC)


@pytest.mark.parametrize("quote, reason", [
    ("pismo w sprawie Czech pochodzi z MSZ", "nie występuje"),           # negation dropped
    ("pismo w sprawie Czech nie pochodzi z MSZ i jest prawdziwe", "nie występuje"),
    ("jest fałszywe … Rzecznik MSZ: pismo", "nie występuje"),             # segments out of order
    ("MSZ", "za krótki"),
    ("Czech", "za krótki"),
    ("nie pochodzi z", "za krótki"),
    ("pismo w sprawie Czechosłowacji nie pochodzi", "nie występuje"),    # word boundary
])
def test_rejected(quote, reason):
    check = check_quote(quote, SRC)
    assert not check.ok and reason in check.reason


def test_negation_case_has_fuzzy_score_only_as_diagnostics():
    check = check_quote("pismo w sprawie Czech pochodzi z MSZ", SRC)
    assert not check.ok and check.fuzzy_score and check.fuzzy_score > 80


def test_rating_label_must_equal_the_label_on_page():
    assert check_quote("Fałsz", "Fałsz", "ocena").ok
    assert check_quote("fałsz", "Fałsz", "ocena").ok
    assert not check_quote("Prawda", "Fałsz", "ocena").ok
    assert not check_quote("Fałsz", "Częściowy fałsz", "ocena").ok


def test_headline_may_be_short_but_verbatim():
    assert check_quote("Komunikat MSZ", "Komunikat MSZ", "naglowek").ok
    assert not check_quote("Komunikat PAP", "Komunikat MSZ", "naglowek").ok
