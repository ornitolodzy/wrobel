from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from app.live_search import _hits, extract_terms
from tests import fixtures as fx
from tests.conftest import FakeRenderer

LIVE_CATALOG = str(Path(__file__).parent / "live_test.yaml")
QUERY = "Czy MSZ wydało komunikat w sprawie wiz dla obywateli Rosji?"

VISA_TEXT = "MSZ informuje: wstrzymanie wydawania wiz dla obywateli Rosji obowiązuje od 1 grudnia. " * 6


def govpl_page(title: str) -> str:
    return fx.govpl_article(title, "20.11.2026", VISA_TEXT)


GOVPL_RESULTS = """<html><body>
<a href="https://www.gov.pl/web/gov/ministerstwa">Ministerstwa</a>
<a href="https://www.gov.pl/web/dyplomacja/wizy-dla-obywateli-rosji">Komunikat MSZ: wizy dla obywateli Rosji</a>
<a href="https://www.gov.pl/web/dyplomacja/inny-temat">Dzień otwarty w ministerstwie</a>
</body></html>"""


def live_handler(searches: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        parts = urlsplit(url)
        if parts.path == "/robots.txt":
            return httpx.Response(404)
        if parts.netloc == "blocked.example.com":
            return httpx.Response(403)
        if url.startswith("https://demagog.org.pl/wp-json/wp/v2/search"):
            params = {k: v[0] for k, v in parse_qs(parts.query).items()}
            searches.append(params)
            # jak WordPress: wszystkie słowa muszą wystąpić; dla 3 słów brak trafień, dla 2 jest wynik
            if len(params["search"].split()) > 2:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[
                {"id": 1, "title": "Wizy dla obywateli Rosji? To&nbsp;fałsz", "type": "post",
                 "url": "https://demagog.org.pl/fake_news/wizy-rosja/"},
                {"id": 2, "title": "Obcy link", "type": "post", "url": "https://example.com/x"},
            ])
        if url == "https://demagog.org.pl/fake_news/wizy-rosja/":
            return fx.html(
                fx.generic_article("Wizy dla obywateli Rosji? To fałsz", "2026-11-19").replace(
                    "</article>", f"<p>{VISA_TEXT}</p></article>"
                )
            )
        if url.startswith("https://cert.pl/?s="):
            return fx.html('<html><body><a href="https://cert.pl/posts/2026/06/phishing/">Kampania phishingowa</a></body></html>')
        if url == "https://cert.pl/posts/2026/06/phishing/":
            return fx.html(fx.generic_article("Kampania phishingowa", "2026-06-01"))
        if url == "https://www.gov.pl/web/dyplomacja/wizy-dla-obywateli-rosji":
            return fx.html(govpl_page("Komunikat MSZ: wizy dla obywateli Rosji"))
        if url == "https://www.gov.pl/web/dyplomacja/inny-temat":
            return fx.html(govpl_page("Dzień otwarty w ministerstwie"))
        return httpx.Response(404)

    return handler


def test_extract_terms_keeps_key_words_and_stems_polish_inflection() -> None:
    terms = extract_terms(QUERY)
    assert [t.word for t in terms] == ["msz", "komunikat", "wiz", "obywateli", "rosji"]
    assert [t.stem for t in terms] == ["msz", "komunik", "wiz", "obywat", "rosj"]


def test_extract_terms_prefers_nouns_over_verbs() -> None:
    claim = "Ministerstwo Zdrowia wprowadziło nowe zasady szczepień przeciw grypie dla seniorów"
    words = [t.word for t in extract_terms(claim)]
    assert "grypie" in words
    assert "wprowadziło" not in words


def test_hits_match_word_starts_only() -> None:
    assert _hits("Raport o ruchach feministycznych", ["minist"]) == 0
    assert _hits("Komunikat Ministerstwa Zdrowia", ["minist", "zdrow"]) == 2


def test_extract_terms_ignores_question_words() -> None:
    assert extract_terms("Czy to prawda, że jest?") == []


def test_live_search_finds_relevant_pages_and_stores_them(make_client) -> None:
    searches: list[dict] = []
    renderer = FakeRenderer({"https://www.gov.pl/web/gov/szukaj?query=msz+komunikat+wiz+obywateli+rosji": GOVPL_RESULTS})
    with make_client(live_handler(searches), renderer=renderer, sources_file=LIVE_CATALOG) as c:
        r = c.post("/live-search", json={"query": QUERY, "max_documents": 5})
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["terms"] == ["msz", "komunikat", "wiz", "obywateli", "rosji"]
        status = {s["source_id"]: s for s in body["sources"]}
        assert status["demagog-fake-news"]["status"] == "ok"
        assert status["govpl-wyszukiwarka"]["found"] == 2  # bez linków /web/gov/
        assert status["blocked-site"]["status"] == "error"
        assert status["blocked-site"]["message"] == "Witryna blokuje automatyczne zapytania."

        # WordPress: najpierw 3 słowa (AND), po braku trafień 2
        assert [len(s["search"].split()) for s in searches] == [3, 2]
        assert searches[0]["type"] == "post"

        urls = {d["url"] for d in body["documents"]}
        assert urls == {
            "https://demagog.org.pl/fake_news/wizy-rosja/",
            "https://www.gov.pl/web/dyplomacja/wizy-dla-obywateli-rosji",
        }  # strony bez słów kluczowych w tytule (cert.pl, „Dzień otwarty”) odrzucone
        demagog = next(d for d in body["documents"] if "demagog" in d["url"])
        assert demagog["source_type"] == "fact_check"
        assert demagog["new"] is True

        stored = c.get("/documents", params={"source_id": "govpl-wyszukiwarka"}).json()
        assert stored["total"] == 1
        assert stored["items"][0]["extra"]["live_search"] is True

        # drugi raz: strony już są w bazie, więc nie pobieramy ich ponownie
        again = c.post("/live-search", json={"query": QUERY}).json()
        assert again["documents"] == []


def test_live_search_without_keywords_skips_sources(make_client) -> None:
    with make_client(live_handler([]), renderer=FakeRenderer(), sources_file=LIVE_CATALOG) as c:
        body = c.post("/live-search", json={"query": "Czy to prawda?"}).json()
        assert body["documents"] == []
        assert {s["status"] for s in body["sources"]} == {"skipped"}
