from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from app.live_search import _hits, extract_terms, is_relevant
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


def test_relevance_requires_proper_nouns_not_common_words() -> None:
    terms = extract_terms("Donald Tusk jest premierem Polski?")
    assert {t.word for t in terms if t.proper} == {"donald", "tusk"}
    assert [t.word for t in terms if t.common] == ["polski"]
    body = "Premier Donald Tusk przedstawił dziś plan. " * 10
    # samo „Polska” w tytule nie wystarcza
    assert not is_relevant("Nowości od CERT Polska", body, terms)
    assert is_relevant("Premier Donald Tusk o planie rządu", body, terms)
    # nazwa własna w tytule, ale treść o czymś innym
    assert not is_relevant("Tusk na meczu", "Relacja z meczu piłkarskiego. " * 10, terms)


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


FACTCHECK_RESPONSE = {
    "claims": [
        {
            "text": "PAP usunęła depeszę o wypowiedzi ministra",
            "claimant": "Użytkownik X",
            "claimReview": [{
                "publisher": {"name": "Demagog", "site": "demagog.org.pl"},
                "url": "https://demagog.org.pl/fake_news/pap-nie-usunela-depeszy/",
                "title": "PAP nie usunęła depeszy",
                "reviewDate": "2026-11-10T00:00:00Z",
                "textualRating": "Fałsz",
                "languageCode": "pl",
            }],
        },
        {
            "text": "Szczepionki zmieniają DNA",
            "claimReview": [{"publisher": {"name": "AFP"}, "url": "https://afp.example/dna",
                             "title": "Nie, szczepionki nie zmieniają DNA", "textualRating": "Fałsz"}],
        },
    ]
}


def factcheck_handler(status: int = 200, searches: list | None = None):
    base = live_handler([])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "factchecktools.googleapis.com":
            if searches is not None:
                searches.append(dict(request.url.params))
            if status != 200:
                return httpx.Response(status, json={"error": {"message": "API key not valid"}})
            return httpx.Response(200, json=FACTCHECK_RESPONSE)
        return base(request)

    return handler


PAP_QUERY = "Czy PAP usunęła depeszę o wypowiedzi ministra?"


def test_factcheck_api_stores_rating_records_without_fetching_pages(make_client) -> None:
    searches: list[dict] = []
    with make_client(factcheck_handler(searches=searches), renderer=FakeRenderer(),
                     sources_file=LIVE_CATALOG, google_factcheck_api_key="k") as c:
        body = c.post("/live-search", json={"query": PAP_QUERY}).json()
        status = {s["source_id"]: s for s in body["sources"]}
        assert status["google-factcheck"]["status"] == "ok"
        assert searches[0]["languageCode"] == "pl" and searches[0]["key"] == "k"

        docs = [d for d in body["documents"] if d["source_id"] == "google-factcheck"]
        # rekord o DNA odrzucony: nie dotyczy PAP ani depeszy
        assert [d["url"] for d in docs] == ["https://demagog.org.pl/fake_news/pap-nie-usunela-depeszy/"]
        assert docs[0]["publisher"] == "Demagog"

        stored = c.get("/documents", params={"source_id": "google-factcheck"}).json()["items"][0]
        full = c.get(f"/documents/{stored['id']}").json()
        assert "Ocena weryfikatora: Fałsz" in full["text"]
        assert full["extra"]["live_search"] is True


def test_factcheck_api_without_key_is_skipped(make_client) -> None:
    with make_client(factcheck_handler(), renderer=FakeRenderer(), sources_file=LIVE_CATALOG) as c:
        body = c.post("/live-search", json={"query": PAP_QUERY}).json()
        status = {s["source_id"]: s for s in body["sources"]}["google-factcheck"]
        assert status["status"] == "skipped"
        assert "GOOGLE_FACTCHECK_API_KEY" in status["message"]


def test_factcheck_api_failure_is_reported_not_treated_as_no_results(make_client) -> None:
    with make_client(factcheck_handler(status=403), renderer=FakeRenderer(),
                     sources_file=LIVE_CATALOG, google_factcheck_api_key="bad") as c:
        body = c.post("/live-search", json={"query": PAP_QUERY}).json()
        status = {s["source_id"]: s for s in body["sources"]}["google-factcheck"]
        assert status["status"] == "error"
        assert status["message"].startswith("Błąd dostępu do API weryfikatorów")


def test_capitalised_first_word_is_a_name_and_question_words_are_dropped() -> None:
    terms = extract_terms("Czy Wrocław wydaje na komunikację około 700 mln zł. Dlaczego Demagog podaje 650 mln zł?")
    words = {t.word: t for t in terms}
    assert "dlaczego" not in words
    assert words["wrocław"].proper
    claim_terms = extract_terms("Wrocław wydaje na komunikację 650 mln zł.")
    assert {t.word for t in claim_terms if t.proper} == {"wrocław"}
    # artykuł o innej kwocie z tą samą liczbą, bez Wrocławia w tytule, nie przechodzi
    body = "Samorządy wnioskują o 650 mln zł na komunikację i obronność. " * 10
    assert not is_relevant("Dolnośląskie samorządy wnioskują o blisko 650 mln zł", body, claim_terms)
