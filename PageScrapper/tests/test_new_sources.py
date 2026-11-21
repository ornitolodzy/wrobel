import json
import time

import httpx

from tests import fixtures as fx
from tests.conftest import site_handler, wait_for_run


def _run(c, *source_ids: str) -> dict:
    return wait_for_run(c, c.post("/scrap/all", json={"source_ids": list(source_ids)}).json()["run_id"])


def _docs(c, source_id: str) -> list[dict]:
    items = c.get("/documents", params={"source_id": source_id}).json()["items"]
    return [c.get(f"/documents/{d['id']}").json() for d in items]


def test_html_list_source(make_client) -> None:
    with make_client() as c:
        run = _run(c, "policja-aktualnosci")
        assert run["results"]["policja-aktualnosci"]["new"] == 2
        docs = {d["url"]: d for d in _docs(c, "policja-aktualnosci")}
        assert set(docs) == {
            "https://policja.pl/pol/aktualnosci/1001,Pierwszy-komunikat.html",
            "https://policja.pl/pol/aktualnosci/1002,Drugi-komunikat.html",
        }
        first = docs["https://policja.pl/pol/aktualnosci/1001,Pierwszy-komunikat.html"]
        assert first["published_at"] == "2026-11-21"
        assert first["publisher"] == "Komenda Główna Policji"
        assert "prokuratury" in first["text"]


def test_sejm_eli_source(make_client) -> None:
    with make_client() as c:
        _run(c, "isap-dziennik-ustaw")
        docs = _docs(c, "isap-dziennik-ustaw")
        assert {d["url"] for d in docs} == {
            "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260000003",
            "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260000002",
        }
        act = docs[0]
        assert act["published_at"] == "2026-11-19"
        assert "Wejście w życie: 2026-12-04" in act["text"]
        assert "Słowa kluczowe: obrona narodowa, bezpieczeństwo" in act["text"]
        assert act["extra"]["status"] == "obowiązujący"


def test_euvsdisinfo_mcp_with_token(make_client) -> None:
    with make_client(euvsdisinfo_token="test-token") as c:
        run = _run(c, "euvsdisinfo-baza")
        assert run["results"]["euvsdisinfo-baza"]["status"] == "ok"
        assert run["results"]["euvsdisinfo-baza"]["new"] == 1
        doc = _docs(c, "euvsdisinfo-baza")[0]
        assert doc["source_type"] == "disinfo_db"
        assert doc["language"] == "en"
        assert doc["published_at"] is not None
        assert "Twierdzenie dezinformacyjne: Poland plans to annex western Ukraine." in doc["text"]
        assert "<p>" not in doc["text"]
        assert "https://disinfo.example/article" in doc["text"]
        assert {s["id"]: s for s in c.get("/sources").json()}["euvsdisinfo-baza"]["configured"] is True


def test_euvsdisinfo_wrong_token_is_reported(make_client) -> None:
    with make_client(euvsdisinfo_token="zly-token") as c:
        result = _run(c, "euvsdisinfo-baza")["results"]["euvsdisinfo-baza"]
        assert result["status"] == "error"


def test_file_import_json(make_client, tmp_path) -> None:
    imports = tmp_path / "imports"
    imports.mkdir()
    (imports / "incidents.json").write_text(
        json.dumps(
            {
                "data": {
                    "items": [
                        {"id": "inc-1", "url": "https://incydenty.example/1", "title": "Fałszywy alert", "claim": "Rzekomy alert o ewakuacji", "verdict_note": "Nieprawda", "date": "2026-11-18", "source": "MSZ"},
                        {"id": "inc-2", "title": "Bez linku", "claim": "Fałszywa zbiórka", "date": "17.11.2026"},
                        {"id": "inc-3", "title": "Pusty"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    with make_client() as c:
        assert {s["id"]: s for s in c.get("/sources").json()}["baza-incydentow"]["configured"] is True
        result = _run(c, "baza-incydentow")["results"]["baza-incydentow"]
        assert result["new"] == 2
        assert len(result["errors"]) == 1
        docs = {d["extra"]["record_id"]: d for d in _docs(c, "baza-incydentow")}
        assert docs["inc-1"]["url"] == "https://incydenty.example/1"
        assert docs["inc-1"]["publisher"] == "MSZ"
        assert "claim: Rzekomy alert o ewakuacji" in docs["inc-1"]["text"]
        assert docs["inc-2"]["url"] == "urn:prawdowid:baza-incydentow#inc-2"
        assert docs["inc-2"]["published_at"] == "2026-11-17"
        assert docs["inc-2"]["source_type"] == "disinfo_db"


def test_file_import_csv(make_client, tmp_path) -> None:
    imports = tmp_path / "imports"
    imports.mkdir()
    (imports / "incidents.csv").write_text(
        "id,title,claim,date\nc-1,Fałszywy komunikat,Rzekomy komunikat o skażeniu wody,2026-11-15\n",
        encoding="utf-8",
    )
    with make_client() as c:
        catalog = c.app.state.catalog.get("baza-incydentow")
        catalog.options = {**catalog.options, "file": "incidents.csv", "records_path": None}
        result = _run(c, "baza-incydentow")["results"]["baza-incydentow"]
        assert result["new"] == 1
        assert _docs(c, "baza-incydentow")[0]["published_at"] == "2026-11-15"


def test_file_import_rejects_path_traversal(make_client) -> None:
    with make_client() as c:
        catalog = c.app.state.catalog.get("baza-incydentow")
        catalog.options = {**catalog.options, "file": "../../sources.yaml"}
        result = _run(c, "baza-incydentow")["results"]["baza-incydentow"]
        assert result["status"] == "error"
        assert "imports" in result["message"]


def test_rss_category_filter(make_client) -> None:
    feed = fx.RSS_FEED.replace("<category>Fake news</category>", "<category>Szkolenie</category>")
    handler = site_handler({"https://demagog.org.pl/fake_news/feed/": httpx.Response(200, text=feed, headers={"content-type": "application/rss+xml"})})
    with make_client(handler) as c:
        _run(c, "demagog-fake-news")
        urls = {d["url"] for d in _docs(c, "demagog-fake-news")}
        assert urls == {"https://demagog.org.pl/fake_news/fake-2/"}


def test_scheduled_scrape_on_startup(make_client) -> None:
    with make_client(scrape_interval_minutes=60, scrape_on_startup=True) as c:
        for _ in range(100):
            runs = c.get("/scrap/runs").json()
            if runs and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        assert runs[0]["request"]["trigger"] == "schedule"


def test_no_schedule_by_default(make_client) -> None:
    with make_client() as c:
        assert c.get("/scrap/runs").json() == []


def test_corpus_stats(make_client) -> None:
    with make_client() as c:
        _run(c, "rcb-komunikaty", "policja-aktualnosci", "demagog-fake-news")
        stats = c.get("/stats").json()
        assert stats["documents"] == 6
        assert stats["domain_count"] == 3
        assert stats["domains"]["policja.pl"] == 2
        assert stats["by_source_type"] == {"official": 4, "fact_check": 2}
        assert stats["documents_without_date"] == 0


def test_transient_network_error_is_retried(make_client) -> None:
    base = site_handler()
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://www.gov.pl/web/rcb/alert-1":
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.RemoteProtocolError("Server disconnected", request=request)
        return base(request)

    with make_client(flaky) as c:
        body = c.post("/scrape", json={"url": "https://www.gov.pl/web/rcb/alert-1"}).json()
        assert body["title"] == "Alert RCB - atak z powietrza"
        assert calls["n"] == 2
