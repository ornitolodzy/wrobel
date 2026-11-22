import asyncio
import gzip
import json
from datetime import date, datetime
from urllib.parse import urlsplit

import httpx
import pytest

from app.catalog import SourceConfig, load_catalog
from app.claimreview import extract_claim_reviews
from app.config import Settings
from app.db import Database, DocumentIn
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


def test_claim_review_graph_form() -> None:
    [review] = extract_claim_reviews(f"<html><head>{fx.CLAIM_REVIEW_GRAPH}</head></html>")
    assert review == {
        "text": "Niemieckie radiowozy jadą do Warszawy.",
        "claimant": "Użytkownicy Facebooka",
        "claimant_type": "Person",
        "appearances": ["https://www.facebook.com/reel/1", "https://www.facebook.com/reel/2"],
        "rating": "fałsz",
        "claim_date": "2026-10-31",
        "review_date": "2026-11-02",
        "origin": "claimreview",
    }


def test_claim_review_single_object_skips_broken_and_empty() -> None:
    reviews = extract_claim_reviews(f"<html><head>{fx.CLAIM_REVIEW_SINGLE}</head></html>")
    assert len(reviews) == 1  # invalid JSON and a review without claimReviewed are ignored
    assert reviews[0]["claimant"] == "Sputnik"
    assert reviews[0]["claimant_type"] == "Organization"
    assert reviews[0]["appearances"] == ["https://archive.ph/abc"]
    assert reviews[0]["rating"] == "Manipulacja"
    assert reviews[0]["claim_date"] is None


def test_page_without_claim_review() -> None:
    assert extract_claim_reviews(fx.generic_article("Zwykły artykuł", "2026-11-20")) == []


def test_google_review_carries_claim_review() -> None:
    claim = fx.GOOGLE_RESPONSE["claims"][0]
    doc = review_to_document(claim, claim["claimReview"][0], "Ukraina", "google-factcheck", "fact_check")
    [review] = doc.extra["claim_reviews"]
    assert review["text"] == "Polska wysłała wojsko na Ukrainę"
    assert review["claimant"] == "Konto w mediach społecznościowych"
    assert (review["rating"], review["claim_date"], review["origin"]) == ("Fałsz", "2026-10-01", "google_factcheck")


def test_upsert_resends_when_only_metadata_changes(tmp_path) -> None:
    async def scenario() -> list[str]:
        db = Database(f"sqlite+aiosqlite:///{tmp_path / 'u.db'}")
        await db.init()
        base = dict(url="https://demagog.org.pl/a/", source_id="d", source_type="fact_check",
                    title="T", publisher="P", published_at=date(2026, 11, 1), text="tekst " * 50)
        outcomes = [
            (await db.upsert(DocumentIn(**base)))[0],
            (await db.upsert(DocumentIn(**base)))[0],
            (await db.upsert(DocumentIn(**base, extra={"claim_reviews": [{"text": "x"}]})))[0],
            (await db.upsert(DocumentIn(**base, extra={"claim_reviews": [{"text": "x"}]})))[0],
        ]
        await db.close()
        return outcomes

    assert asyncio.run(scenario()) == ["new", "unchanged", "updated", "unchanged"]


def test_revision_grows_on_every_stored_change(tmp_path) -> None:
    async def scenario() -> list[int]:
        db = Database(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
        await db.init()
        base = dict(url="https://demagog.org.pl/r/", source_id="d", source_type="fact_check",
                    title="T", publisher="P", published_at=date(2026, 11, 1), text="tekst " * 50)
        revisions = []
        for doc in (
            DocumentIn(**base),                                   # new -> 1
            DocumentIn(**base),                                   # unchanged -> 1
            DocumentIn(**base, extra={"claim_reviews": [1]}),     # metadata only -> 2
            DocumentIn(**{**base, "text": "inny " * 50}, extra={"claim_reviews": [1]}),  # text -> 3
        ):
            _, stored = await db.upsert(doc)
            revisions.append(stored.revision)
        total, rows = await db.revisions(10, 0)
        await db.close()
        assert total == 1 and rows[0][1] == base["url"] and rows[0][2] == 3 and rows[0][3] is not None
        return revisions

    assert asyncio.run(scenario()) == [1, 1, 2, 3]


def test_old_documents_table_is_migrated(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, url VARCHAR(2048) UNIQUE, source_id VARCHAR(64), "
        "source_type VARCHAR(32), title TEXT, publisher VARCHAR(256), published_at DATE, "
        "retrieved_at DATETIME, language VARCHAR(16), text TEXT, content_hash VARCHAR(64), extra JSON)"
    )
    con.execute("INSERT INTO documents (url, source_id, source_type, retrieved_at, text, content_hash) "
                "VALUES ('https://x.example/a', 's', 'official', '2026-11-01 10:00:00', 't', 'h')")
    con.commit()
    con.close()

    async def scenario():
        db = Database(f"sqlite+aiosqlite:///{path}")
        await db.init()
        await db.init()  # idempotent on every start
        result = await db.revisions(10, 0)
        await db.close()
        return result

    total, rows = asyncio.run(scenario())
    assert total == 1 and rows[0][2] == 1 and rows[0][3] is not None  # backfilled from retrieved_at


def test_ingest_targets() -> None:
    s = Settings(rag_ingest_url="http://pipeline/ingest", rag_ingest_urls=" http://graph/ingest/document, ,http://pipeline/ingest")
    assert s.ingest_targets() == ["http://graph/ingest/document", "http://pipeline/ingest"]
    assert Settings().ingest_targets() == []


def test_rag_client_pushes_to_every_receiver() -> None:
    from types import SimpleNamespace

    from app.ingest import RagClient

    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.host, body))
        if request.url.host == "graph":
            return httpx.Response(200, json={"id": "d", "chunks": 3, "embedded": 0, "unembedded": UNEMBEDDED[0]})
        return httpx.Response(200, json={"changed": 1, "chunks": 2})

    UNEMBEDDED = [0]  # unchanged re-push: nothing newly embedded, nothing missing -> accepted
    doc = SimpleNamespace(url="https://x.example/a", source_id="s", source_type="official", title="T", publisher="P",
                          published_at=None, retrieved_at=datetime(2026, 11, 1), language="pl", text="t",
                          content_hash="h", extra={"claim_reviews": [{"text": "A"}]}, revision=4)

    async def send() -> bool:
        client = RagClient(["http://graph/ingest/document", "http://pipeline/ingest"], transport=httpx.MockTransport(handler))
        try:
            return await client.send(doc)
        finally:
            await client.close()

    assert asyncio.run(send()) is True
    assert {h for h, _ in seen} == {"graph", "pipeline"}
    assert all(b["revision"] == 4 and b["claims"] == [{"text": "A"}] for _, b in seen)
    UNEMBEDDED[0] = 3  # stored without vectors (tunnel down): not accepted, reconcile repairs it
    assert asyncio.run(send()) is False


def test_rag_client_one_failing_receiver_fails_the_push() -> None:
    from types import SimpleNamespace

    from app.ingest import RagClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503) if request.url.host == "graph" else httpx.Response(200, json={})

    doc = SimpleNamespace(url="u", source_id="s", source_type="official", title=None, publisher=None,
                          published_at=None, retrieved_at=datetime(2026, 11, 1), language=None, text="t",
                          content_hash="h", extra=None, revision=1)

    async def send() -> bool:
        client = RagClient(["http://graph/x", "http://pipeline/y"], transport=httpx.MockTransport(handler))
        try:
            return await client.send(doc)
        finally:
            await client.close()

    assert asyncio.run(send()) is False
