import asyncio
import gzip
from datetime import date
from urllib.parse import urlsplit

import httpx
import pytest

from app.catalog import SourceConfig, load_catalog
from app.config import Settings
from app.extract import parse_date
from app.fetch import FetchError, Fetcher, assert_public_url
from app.sources.google_factcheck import review_to_document
from app.sources.govpl import parse_article, parse_listing
from app.sources.odyseusz import parse_country_codes, parse_profile
from app.sources.rss import parse_feed
from tests import fixtures as fx
from tests.conftest import fake_resolver

SOURCE = SourceConfig(
    id="test-src", name="Test", publisher="Test publisher", source_type="official",
    adapter="govpl_list", url="https://www.gov.pl/web/rcb/komunikaty",
)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20.11.2026", date(2026, 11, 20)),
        ("2 lipca 2026", date(2026, 7, 2)),
        ("Ostatnia aktualizacja: 19 listopada 2026", date(2026, 11, 19)),
        ("2026-10-03T00:00:00Z", date(2026, 10, 3)),
        ("Fri, 20 Nov 2026 14:59:06 +0000", date(2026, 11, 20)),
        ("brak daty", None),
        ("31.02.2026", None),
    ],
)
def test_parse_date(value, expected) -> None:
    assert parse_date(value) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "http://internal.example/",
        "https://example.com:8080/",
        "https://user:pass@example.com/",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "http://intranet.local/",
    ],
)
def test_blocks_non_public_urls(url) -> None:
    with pytest.raises(FetchError):
        run(assert_public_url(url, fake_resolver))


def test_allows_public_url() -> None:
    run(assert_public_url("https://www.gov.pl/web/rcb", fake_resolver))


def _fetcher(handler, **overrides) -> Fetcher:
    return Fetcher(Settings(_env_file=None, **overrides), transport=httpx.MockTransport(handler), resolve=fake_resolver)


def test_redirect_to_private_address_is_blocked() -> None:
    def handler(request):
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})

    with pytest.raises(FetchError) as exc:
        run(_fetcher(handler).get("https://example.com/"))
    assert exc.value.code == "blocked_url"


def test_gzip_body_is_decoded_once() -> None:
    body = gzip.compress("<html><body>Zażółć gęślą jaźń</body></html>".encode("utf-8"))

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-encoding": "gzip", "content-type": "text/html; charset=utf-8"})

    result = run(_fetcher(handler).get("https://example.com/"))
    assert "Zażółć gęślą jaźń" in result.text


def test_charset_from_meta_tag() -> None:
    body = '<html><head><meta charset="iso-8859-2"></head><body>Łódź</body></html>'.encode("iso-8859-2")

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    assert "Łódź" in run(_fetcher(handler).get("https://example.com/")).text


def test_too_large_page_is_rejected() -> None:
    def handler(request):
        return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "text/html"})

    with pytest.raises(FetchError) as exc:
        run(_fetcher(handler, max_page_bytes=1000).get("https://example.com/"))
    assert exc.value.code == "too_large"


def test_govpl_listing_and_article() -> None:
    items = parse_listing(fx.GOVPL_LISTING, "https://www.gov.pl/web/rcb/komunikaty")
    assert [i.url for i in items] == ["https://www.gov.pl/web/rcb/alert-1", "https://www.gov.pl/web/rcb/alert-2"]
    assert items[0].published_at == date(2026, 11, 20)

    doc = parse_article(fx.govpl_article("Tytuł", "20.11.2026", "Treść komunikatu."), items[0].url, SOURCE)
    assert doc.title == "Tytuł"
    assert doc.published_at == date(2026, 11, 20)
    assert doc.publisher == "Rządowe Centrum Bezpieczeństwa"
    assert "Treść komunikatu." in doc.text
    assert "stopka" not in doc.text


def test_rss_feed() -> None:
    entries = parse_feed(fx.RSS_FEED)
    assert len(entries) == 2
    assert entries[0]["url"] == "https://demagog.org.pl/fake_news/fake-1/"
    assert entries[0]["published_at"] == date(2026, 11, 20)
    assert entries[0]["categories"] == ["Fake news"]


def test_odyseusz_profile_cleanup() -> None:
    doc = parse_profile(fx.odyseusz_profile("Ukraina", "2 lipca 2026", 4, "Nie jedź"), "https://odyseusz.gov.pl/UA", "UA", SOURCE)
    assert doc.title == "Odyseusz (MSZ): Ukraina - informacje dla podróżujących"
    assert doc.published_at == date(2026, 7, 2)
    assert doc.extra["warning_level"] == 4
    assert doc.extra["warning_label"] == "Nie jedź"
    for noise in ("Moje podróże", "Subskrypcja e-mail", "keyboard_arrow", "1 z 3"):
        assert noise not in doc.text
    assert "W razie alarmu udaj się do schronu." in doc.text


def test_odyseusz_country_codes() -> None:
    html = '<a href="/UA">Ukraina</a><a href="/kraje">x</a><a href="https://odyseusz.gov.pl/IT">Włochy</a><a href="/UA">dup</a>'
    assert parse_country_codes(html) == ["UA", "IT"]


def test_google_review_to_document() -> None:
    claim = fx.GOOGLE_RESPONSE["claims"][0]
    doc = review_to_document(claim, claim["claimReview"][0], "Ukraina", "google-factcheck", "fact_check")
    assert doc.publisher == "Demagog"
    assert doc.published_at == date(2026, 10, 3)
    assert "Ocena weryfikatora: Fałsz" in doc.text
    assert review_to_document({}, {"title": "bez url"}, "q", "g", "fact_check") is None


def test_production_catalog_meets_task_requirements() -> None:
    catalog = load_catalog("sources.yaml")
    keyless = [s for s in catalog.sources if s.active and s.adapter not in ("google_factcheck", "euvsdisinfo_mcp", "file_import")]
    assert {"official", "fact_check"} <= {s.source_type for s in keyless}
    assert len({urlsplit(s.url).netloc for s in keyless}) >= 10
    assert sum(s.max_items for s in keyless) >= 100
    assert len({s.id for s in catalog.sources}) == len(catalog.sources)
    assert all(s.license for s in catalog.sources)


def test_listing_page_keeps_all_cards_and_drops_cookie_banner() -> None:
    from app.extract import extract_article

    cards = "".join(
        f"<section><h2>Zadanie {i}</h2><p>Opis zadania numer {i}: zbuduj rozwiązanie dla instytucji publicznej, które pomoże obywatelom.</p></section>"
        for i in range(15)
    )
    html = (
        "<html><head><title>Zadania</title></head><body>"
        "<div id='cookie-consent'>Używamy plików cookies, aby strona działała.</div>"
        f"<main>{cards}</main></body></html>"
    )
    text = extract_article(html, "https://example.com/zadania").text
    assert "Zadanie 0" in text and "Zadanie 14" in text
    assert "cookies" not in text


def test_js_shell_detection() -> None:
    from app.extract import looks_like_js_shell

    assert looks_like_js_shell('<html><body><div id="root"></div><script src="a.js"></script></body></html>')
    assert looks_like_js_shell("<html><body><app-root></app-root></body></html>")
    assert not looks_like_js_shell("<html><body><main>" + "Treść artykułu. " * 40 + "</main></body></html>")
