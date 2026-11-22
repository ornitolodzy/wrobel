"""CheckedClaim layer without a database: rating labels, claimant kinds, attribution rule."""

from datetime import date

import pytest

from context_graph.claims import claim_rows, claimant_kind, normalize_rating
from context_graph.models import ClaimIn, DocumentIn


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("fałsz", "falsz"),
        ("Fałsz", "falsz"),
        ("Nieprawda", "falsz"),
        ("Przerobione zdjęcie", "falsz"),
        ("Wygenerowane przez AI", "falsz"),
        ("Manipulacja", "manipulacja"),
        ("Brak kontekstu", "manipulacja"),
        ("Wprowadza w błąd", "manipulacja"),
        ("Częściowa prawda", "czesciowa_prawda"),
        ("Nieweryfikowalne", "nieweryfikowalne"),
        ("Prawda", "prawda"),
        ("Satyra", "satyra"),
        ("Coś innego", "inne"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_rating(raw, label):
    assert normalize_rating(raw) == label


@pytest.mark.parametrize(
    ("name", "schema_type", "kind"),
    [
        # Authors seen in real ClaimReview markup (Demagog, FakeNews.pl, Google Fact Check):
        ("Użytkownicy Facebooka", "Person", "generic"),
        ("Użytkownik Facebooka", "Person", "generic"),
        ("Wiele źródeł", "Person", "generic"),
        ("Konto w mediach społecznościowych", None, "generic"),
        ("@coolfonpl", "Person", "account"),
        ("Goniec", "Person", "person"),
        ("Sputnik", "Organization", "organization"),
        ("Donald Trump", "Person", "person"),
        ("", "Person", None),
        (None, None, None),
    ],
)
def test_claimant_kind(name, schema_type, kind):
    assert claimant_kind(name, schema_type) == kind


def test_attribution_only_when_the_fact_checker_declares_a_specific_author():
    claims = [
        ClaimIn(text="Niemieckie radiowozy jadą do Warszawy.", claimant="Użytkownicy Facebooka", rating="fałsz"),
        ClaimIn(text="Robot zaatakował klienta.", claimant="Goniec", claimant_type="Person"),
        ClaimIn(text="Polska wysłała wojsko.", claimant="Jan Kowalski", origin="llm"),
        ClaimIn(text="Wybory sfałszowano.", claimant="Jan Kowalski", origin="google_factcheck"),
    ]
    rows, _, attributions = claim_rows("doc1", claims)
    assert len(rows) == 4
    assert [(a["name"], a["method"]) for a in attributions] == [
        ("Goniec", "claimreview"),
        ("Jan Kowalski", "google_factcheck"),
    ]
    generic = next(r for r in rows if r["claimant_kind"] == "generic")
    assert generic["claimant_text"] == "Użytkownicy Facebooka" and generic["rating"] == "falsz"
    llm = next(r for r in rows if r["origin"] == "llm")
    assert llm["claimant_text"] == "Jan Kowalski"  # kept for audit, never an ATTRIBUTED_TO edge


def test_claim_rows_merge_duplicates_and_appearances():
    reel = "https://www.facebook.com/reel/1"
    claims = [
        ClaimIn(text="Niemieckie  radiowozy jadą do Warszawy.", appearances=[reel, reel, " "]),
        ClaimIn(text="niemieckie radiowozy jadą do warszawy."),
    ]
    rows, appearances, _ = claim_rows("doc1", claims)
    assert len(rows) == 1 and rows[0]["text"] == "Niemieckie radiowozy jadą do Warszawy."
    assert [(a["url"], a["domain"]) for a in appearances] == [(reel, "www.facebook.com")]
    assert claim_rows("doc2", claims)[0][0]["id"] != rows[0]["id"]  # claims are per checking document


def test_scraper_payload_with_claims_and_bad_dates():
    doc = DocumentIn.model_validate(
        {
            "url": "https://demagog.org.pl/fake_news/x/",
            "text": "tekst",
            "retrieved_at": "2026-11-21T15:00:00+00:00",
            "claims": [
                {"text": "A", "claim_date": "2026-10-31", "review_date": "kiedyś", "origin": "claimreview"},
                {"text": "B", "claim_date": "2026-10-01T00:00:00Z", "appearances": []},
            ],
        }
    )
    assert doc.claims[0].claim_date == date(2026, 10, 31) and doc.claims[0].review_date is None
    assert doc.claims[1].claim_date == date(2026, 10, 1)
    assert DocumentIn(url="https://x.example/a", text="t").claims == []
