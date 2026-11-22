import json

import httpx

from tests import fixtures as fx
from tests.conftest import FakeRenderer, site_handler, wait_for_run


def test_health(make_client) -> None:
    with make_client() as c:
        assert c.get("/health").json() == {"status": "ok"}


def test_scrape_govpl_article(make_client) -> None:
    with make_client() as c:
        r = c.post("/scrape", json={"url": "https://www.gov.pl/web/rcb/alert-1"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["title"] == "Alert RCB - atak z powietrza"
        assert body["published_at"] == "2026-11-20"
        assert body["publisher"] == "Rządowe Centrum Bezpieczeństwa"
        assert body["rendered"] is False


def test_scrape_generic_article(make_client) -> None:
    with make_client() as c:
        body = c.post("/scrape", json={"url": "https://demagog.org.pl/fake_news/fake-1/"}).json()
        assert body["title"] == "To nie jest prawdziwe zdjęcie"
        assert body["published_at"] == "2026-11-20"
        assert "przerobione" in body["text"]


def test_scrape_falls_back_to_browser_for_js_pages(make_client) -> None:
    url = "https://spa.example.com/news"
    handler = site_handler({url: fx.html("<html><body><div id='root'></div></body></html>")})
    renderer = FakeRenderer({url: fx.generic_article("Artykuł z aplikacji JS", "2026-11-18")})
    with make_client(handler, renderer=renderer) as c:
        body = c.post("/scrape", json={"url": url}).json()
        assert body["rendered"] is True
        assert body["title"] == "Artykuł z aplikacji JS"
        assert renderer.calls == [url]


def test_scrape_render_never(make_client) -> None:
    url = "https://spa.example.com/news"
    handler = site_handler({url: fx.html("<html><body><div id='root'></div></body></html>")})
    renderer = FakeRenderer({url: fx.generic_article("x", "2026-11-18")})
    with make_client(handler, renderer=renderer) as c:
        r = c.post("/scrape", json={"url": url, "render": "never"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "no_text"
        assert renderer.calls == []


def test_scrape_odyseusz_uses_browser(make_client) -> None:
    handler = site_handler({"https://odyseusz.gov.pl/UA": fx.html("<html><body><app-root></app-root></body></html>")})
    with make_client(handler) as c:
        body = c.post("/scrape", json={"url": "https://odyseusz.gov.pl/UA"}).json()
        assert body["rendered"] is True
        assert body["title"] == "Odyseusz (MSZ): Ukraina - informacje dla podróżujących"
        assert body["published_at"] == "2026-07-02"


def test_scrape_errors(make_client) -> None:
    handler = site_handler(
        {
            "https://example.com/file.pdf": httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
            "https://example.com/missing": httpx.Response(404),
        }
    )
    with make_client(handler) as c:
        assert c.post("/scrape", json={"url": "https://example.com/file.pdf"}).status_code == 415
        assert c.post("/scrape", json={"url": "https://example.com/missing"}).status_code == 502
        r = c.post("/scrape", json={"url": "http://169.254.169.254/latest/meta-data"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "blocked_url"
        assert c.post("/scrape", json={"url": "https://example.com/", "extra": 1}).status_code == 422


def test_scrap_all_collects_both_source_types(make_client) -> None:
    with make_client(google_factcheck_api_key="test-key") as c:
        r = c.post("/scrap/all", json={})
        assert r.status_code == 202
        run = wait_for_run(c, r.json()["run_id"])
        results = run["results"]
        assert run["status"] == "finished"
        assert run["request"]["trigger"] == "api"
        assert results["rcb-komunikaty"]["new"] == 2
        assert results["demagog-fake-news"]["new"] == 2
        assert results["odyseusz-msz"]["status"] == "ok"
        assert results["google-factcheck"]["new"] == 1
        assert results["policja-aktualnosci"]["new"] == 2
        assert results["isap-dziennik-ustaw"]["new"] == 2
        assert results["euvsdisinfo-baza"]["status"] == "skipped"
        assert results["baza-incydentow"]["status"] == "skipped"

        sources = {s["id"]: s for s in c.get("/sources").json()}
        assert sources["rcb-komunikaty"]["documents"] == 2
        assert sources["google-factcheck"]["configured"] is True

        docs = c.get("/documents", params={"limit": 100}).json()
        types = {d["source_type"] for d in docs["items"]}
        assert types == {"official", "fact_check"}
        assert all(d["preview"] and d["text"] is None for d in docs["items"])

        fake_2 = next(d for d in docs["items"] if d["url"].endswith("fake-2/"))
        full = c.get(f"/documents/{fake_2['id']}").json()
        assert "nigdy nie padł" in full["text"]

        demagog = c.get("/documents", params={"source_id": "demagog-fake-news"}).json()["items"]
        assert all("Obalamy fałszywe informacje" not in c.get(f"/documents/{d['id']}").json()["text"] for d in demagog)


def test_scrap_all_second_run_skips_known(make_client) -> None:
    with make_client() as c:
        wait_for_run(c, c.post("/scrap/all", json={"source_ids": ["rcb-komunikaty", "odyseusz-msz"]}).json()["run_id"])
        run = wait_for_run(c, c.post("/scrap/all", json={"source_ids": ["rcb-komunikaty", "odyseusz-msz"]}).json()["run_id"])
        assert run["results"]["rcb-komunikaty"]["new"] == 0
        assert run["results"]["rcb-komunikaty"]["skipped_known"] == 2
        assert run["results"]["odyseusz-msz"]["unchanged"] >= 1


def test_google_skipped_without_key(make_client) -> None:
    with make_client() as c:
        run = wait_for_run(c, c.post("/scrap/all", json={"source_ids": ["google-factcheck"]}).json()["run_id"])
        assert run["results"]["google-factcheck"]["status"] == "skipped"
        sources = {s["id"]: s for s in c.get("/sources").json()}
        assert sources["google-factcheck"]["configured"] is False
        assert sources["euvsdisinfo-baza"]["configured"] is False
        assert sources["baza-incydentow"]["configured"] is False


def test_scrap_all_limits_and_validation(make_client) -> None:
    with make_client() as c:
        assert c.post("/scrap/all", json={"source_ids": ["nie-ma"]}).status_code == 422
        run = wait_for_run(c, c.post("/scrap/all", json={"source_ids": ["rcb-komunikaty"], "max_items_per_source": 1}).json()["run_id"])
        assert run["results"]["rcb-komunikaty"]["new"] == 1


def test_scrap_all_without_body(make_client) -> None:
    with make_client() as c:
        r = c.post("/scrap/all")
        assert r.status_code == 202
        wait_for_run(c, r.json()["run_id"])


def test_rag_forwarding(make_client) -> None:
    sent: list[dict] = []
    base = site_handler()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "http://rag:8004/ingest":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return base(request)

    with make_client(handler, rag_ingest_url="http://rag:8004/ingest") as c:
        run = wait_for_run(c, c.post("/scrap/all", json={"source_ids": ["rcb-komunikaty"]}).json()["run_id"])
        assert run["results"]["rcb-komunikaty"]["rag_sent"] == 2
        assert {d["url"] for d in sent} == {"https://www.gov.pl/web/rcb/alert-1", "https://www.gov.pl/web/rcb/alert-2"}
        assert sent[0]["source_type"] == "official"


def test_docs_can_be_disabled(make_client) -> None:
    with make_client(docs_enabled=False) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/health").status_code == 200


def test_docs_enabled_by_default(make_client) -> None:
    with make_client() as c:
        assert c.get("/openapi.json").status_code == 200


def test_js_app_with_seo_text_is_rendered(make_client) -> None:
    url = "https://spa.example.com/zadania"
    seo = "<p>" + "Krótki opis strony dla wyszukiwarek. " * 8 + "</p>"
    handler = site_handler({url: fx.html(f"<html><body>{seo}<div id='root'></div><script src='/app.js'></script></body></html>")})
    renderer = FakeRenderer({url: fx.generic_article("Pełna lista zadań", "2026-11-21")})
    with make_client(handler, renderer=renderer) as c:
        body = c.post("/scrape", json={"url": url}).json()
        assert renderer.calls == [url]
        assert body["rendered"] is True


def test_rag_forwarding_carries_claim_reviews(make_client) -> None:
    sent: list[dict] = []
    base = site_handler({
        "https://demagog.org.pl/fake_news/fake-1/": fx.html(
            fx.fact_check_article("To nie jest prawdziwe zdjęcie", "2026-11-20", fx.CLAIM_REVIEW_GRAPH)),
    })

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "http://rag:8004/ingest":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return base(request)

    with make_client(handler, rag_ingest_url="http://rag:8004/ingest") as c:
        wait_for_run(c, c.post("/scrap/all", json={"source_ids": ["demagog-fake-news"]}).json()["run_id"])
        by_url = {d["url"]: d for d in sent}
        [claim] = by_url["https://demagog.org.pl/fake_news/fake-1/"]["claims"]
        assert claim["text"] == "Niemieckie radiowozy jadą do Warszawy."
        assert claim["rating"] == "fałsz"
        assert by_url["https://demagog.org.pl/fake_news/fake-2/"]["claims"] == []  # page failed: RSS text only


def test_document_revisions_endpoint(make_client) -> None:
    with make_client() as c:
        wait_for_run(c, c.post("/scrap/all", json={"source_ids": ["rcb-komunikaty"]}).json()["run_id"])
        body = c.get("/documents/revisions").json()
        assert body["total"] == 2 and len(body["items"]) == 2
        assert all(i["revision"] == 1 and i["updated_at"] for i in body["items"])
        one = c.get(f"/documents/{body['items'][0]['id']}").json()
        assert one["revision"] == 1 and one["url"] == body["items"][0]["url"]
        assert c.get("/documents/revisions", params={"limit": 1, "offset": 1}).json()["items"][0]["id"] == body["items"][1]["id"]


KPO_URL = "https://www.gov.pl/web/fundusze-regiony/od-strategicznych-inwestycji-i-reform-po-projekty-lokalne--kpo-zmienia-polske"


def test_scrap_page_stores_one_catalog_page(make_client) -> None:
    handler = site_handler({KPO_URL: fx.html(fx.govpl_article("KPO zmienia Polskę", "18.11.2026", "Projekty lokalne z KPO. " * 10))})
    with make_client(handler) as c:
        r = c.post("/scrap/page", json={"url": KPO_URL})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["outcome"] == "new" and body["final_url"] == KPO_URL
        assert body["source_id"] == "rcb-komunikaty"  # the only www.gov.pl source in the test catalog
        assert body["source_type"] == "official"
        assert body["title"] == "KPO zmienia Polskę" and body["published_at"] == "2026-11-18"
        assert body["rag"] == "disabled"

        doc = c.get(f"/documents/{body['document_id']}").json()
        assert doc["url"] == KPO_URL and doc["source_id"] == "rcb-komunikaty"

        again = c.post("/scrap/page", json={"url": KPO_URL + "#sekcja"}).json()
        assert again["outcome"] == "unchanged" and again["document_id"] == body["document_id"]

        run = c.get(f"/scrap/runs/{body['run_id']}").json()
        assert run["status"] == "finished" and run["request"]["trigger"] == "page"
        assert run["results"]["rcb-komunikaty"]["new"] == 1


def test_scrap_page_rejects_pages_outside_catalog(make_client) -> None:
    with make_client() as c:
        for url in ("https://example.com/artykul", "http://www.gov.pl/web/rcb/alert-1",
                    "https://www.gov.pl@example.com/x", "https://api.sejm.gov.pl/eli/acts/DU/2026/3"):
            r = c.post("/scrap/page", json={"url": url})
            assert r.status_code == 422, url
            assert r.json()["detail"]["code"] == "not_in_catalog"
            assert "https://www.gov.pl" in r.json()["detail"]["allowed_sites"]
        assert c.post("/scrap/page", json={"url": "https://www.gov.pl/web/rcb/alert-1", "source_id": "policja-aktualnosci"}).status_code == 422
        assert c.post("/scrap/page", json={"url": "https://www.gov.pl/web/rcb/alert-1", "source_id": "nie-ma"}).status_code == 422


def test_scrap_page_respects_robots_and_redirects(make_client) -> None:
    handler = site_handler({
        "https://policja.pl/pol/aktualnosci/9,Przekierowanie.html": httpx.Response(302, headers={"location": "https://example.com/x"}),
        "https://example.com/x": fx.html(fx.generic_article("Obca strona", "2026-11-20")),
    })
    with make_client(handler) as c:
        r = c.post("/scrap/page", json={"url": "https://policja.pl/pol/zablokowane/1"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "robots_disallowed"
        r = c.post("/scrap/page", json={"url": "https://policja.pl/pol/aktualnosci/9,Przekierowanie.html"})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "not_in_catalog"
        assert c.get("/documents").json()["total"] == 0
        assert c.get("/scrap/runs").json()[0]["status"] == "finished_with_errors"


def test_scrap_page_pushes_to_rag_with_claim_reviews(make_client) -> None:
    sent: list[dict] = []
    url = "https://demagog.org.pl/fake_news/fake-1/"
    base = site_handler({url: fx.html(fx.fact_check_article("To nie jest prawdziwe zdjęcie", "2026-11-20", fx.CLAIM_REVIEW_GRAPH))})

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "http://rag:8004/ingest":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return base(request)

    with make_client(handler, rag_ingest_url="http://rag:8004/ingest") as c:
        body = c.post("/scrap/page", json={"url": url}).json()
        assert body["source_id"] == "demagog-fake-news" and body["source_type"] == "fact_check"
        assert body["rag"] == "sent" and body["claim_reviews"] == 1
        [doc] = sent
        assert doc["url"] == url and doc["claims"][0]["rating"] == "fałsz"
        assert c.post("/scrap/page", json={"url": url}).json()["rag"] == "skipped"
        assert len(sent) == 1
