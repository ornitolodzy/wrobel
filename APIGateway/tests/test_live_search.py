import json

import httpx

from tests.conftest import WEB

URLS = {"llm_service_url": "http://llm:8001", "page_scrapper_url": "http://scraper:8002"}
CLAIM = "MSZ wstrzymało wydawanie wiz dla obywateli Rosji"


def llm_result(verdict: str) -> dict:
    # "false:weak" = werdykt false ze słabymi przesłankami
    verdict, _, strength = verdict.partition(":")
    return {
        "status": "completed",
        "claims": [
            {
                "claim": CLAIM,
                "verdict": verdict,
                "explanation": "Wyjaśnienie.",
                "evidence_strength": strength or ("none" if verdict == "insufficient_evidence" else "strong"),
                "sources": [],
                "limitations": [],
            }
        ],
        "summary": "Podsumowanie.",
        "limitations": [],
    }


LIVE_FOUND = {
    "query": CLAIM,
    "terms": ["msz", "wiz", "obywateli", "rosji"],
    "sources": [
        {"source_id": "govpl-wyszukiwarka", "name": "Gov.pl", "status": "ok", "found": 3},
        {"source_id": "demagog-fake-news", "name": "Demagog", "status": "error", "found": 0, "message": "403"},
    ],
    "documents": [
        {
            "url": "https://www.gov.pl/web/dyplomacja/wizy-rosja",
            "title": "Komunikat MSZ: wizy dla obywateli Rosji",
            "publisher": "Ministerstwo Spraw Zagranicznych",
            "published_at": "2026-11-20",
            "source_id": "govpl-wyszukiwarka",
            "source_name": "Gov.pl",
            "source_type": "official",
            "new": True,
            "ingested": True,
        }
    ],
    "duration_ms": 1200,
}


def handler(calls: list[tuple[str, dict]], verdicts: list[str], live: dict | None = None):
    """LLM odpowiada kolejnymi werdyktami z listy; scraper zwraca wynik wyszukiwania na żywo."""

    def _handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.host == "llm" and request.url.path == "/verify":
            calls.append(("verify", body))
            return httpx.Response(200, json=llm_result(verdicts.pop(0)))
        if request.url.host == "scraper" and request.url.path == "/live-search":
            calls.append(("live", body))
            return httpx.Response(200, json=live if live is not None else LIVE_FOUND)
        return httpx.Response(404)

    return _handle


def test_live_search_reverifies_with_new_documents(make_client) -> None:
    calls: list[tuple[str, dict]] = []
    with make_client(handler(calls, ["insufficient_evidence", "confirmed"]), **URLS) as c:
        first = c.post("/api/v1/verify/claim", json={"text": CLAIM}, headers=WEB).json()
        assert first["claims"][0]["verdict"] == "insufficient_evidence"
        assert first["live_search"] is None

        r = c.post("/api/v1/verify/live", json={"check_id": first["check_id"]}, headers=WEB)
        assert r.status_code == 200, r.text
        body = r.json()

        assert [name for name, _ in calls] == ["verify", "live", "verify"]
        assert calls[1][1] == {"query": CLAIM, "max_documents": 5}
        assert calls[2][1]["content"] == {"text": CLAIM}

        assert body["check_id"] != first["check_id"]
        assert body["claims"][0]["verdict"] == "confirmed"
        live = body["live_search"]
        assert live["previous_check_id"] == first["check_id"]
        assert live["reverified"] is True
        assert live["improved"] is True
        assert live["documents"][0]["url"] == "https://www.gov.pl/web/dyplomacja/wizy-rosja"
        assert "przeszukano na żywo: Gov.pl." in live["message"]  # bez witryny, która odmówiła
        assert "Dołączone nowe strony: 1." in live["message"]
        assert "pozwoliły ocenić" in live["message"]

        # nowe sprawdzenie zapisane jak każde inne
        stored = c.get(f"/api/v1/checks/{body['check_id']}").json()
        assert stored["live_search"]["improved"] is True


def test_weak_decisive_verdict_after_live_search_keeps_previous_result(make_client) -> None:
    calls: list[tuple[str, dict]] = []
    with make_client(handler(calls, ["insufficient_evidence", "false:weak"]), **URLS) as c:
        first = c.post("/api/v1/verify/claim", json={"text": CLAIM}, headers=WEB).json()
        body = c.post("/api/v1/verify/live", json={"check_id": first["check_id"]}, headers=WEB).json()

        assert [name for name, _ in calls] == ["verify", "live", "verify"]
        assert body["claims"][0]["verdict"] == "insufficient_evidence"  # nie „fałsz” na słabych przesłankach
        live = body["live_search"]
        assert live["reverified"] is True
        assert live["improved"] is False
        assert live["documents"]  # strony nadal pokazane do przejrzenia
        assert "tylko słabe przesłanki" in live["message"]
        assert any("tylko słabe przesłanki" in lim for lim in body["limitations"])


def test_moderate_verdict_after_live_search_is_accepted(make_client) -> None:
    with make_client(handler([], ["insufficient_evidence", "misleading:moderate"]), **URLS) as c:
        first = c.post("/api/v1/verify/claim", json={"text": CLAIM}, headers=WEB).json()
        body = c.post("/api/v1/verify/live", json={"check_id": first["check_id"]}, headers=WEB).json()
        assert body["claims"][0]["verdict"] == "misleading"
        assert body["live_search"]["improved"] is True


def test_live_search_without_new_documents_keeps_result_and_skips_llm(make_client) -> None:
    calls: list[tuple[str, dict]] = []
    empty = {**LIVE_FOUND, "documents": []}
    with make_client(handler(calls, ["insufficient_evidence"], empty), **URLS) as c:
        first = c.post("/api/v1/verify/claim", json={"text": CLAIM}, headers=WEB).json()
        body = c.post("/api/v1/verify/live", json={"check_id": first["check_id"]}, headers=WEB).json()

        assert [name for name, _ in calls] == ["verify", "live"]
        assert body["check_id"] == first["check_id"]
        assert body["claims"] == first["claims"]
        assert body["live_search"]["reverified"] is False
        assert body["live_search"]["message"] == "Przeszukano na żywo: Gov.pl - brak nowych materiałów na ten temat."


def test_live_search_only_for_insufficient_evidence(make_client) -> None:
    with make_client(handler([], ["confirmed"]), **URLS) as c:
        first = c.post("/api/v1/verify/claim", json={"text": CLAIM}, headers=WEB).json()
        r = c.post("/api/v1/verify/live", json={"check_id": first["check_id"]}, headers=WEB)
        assert r.status_code == 409


def test_live_search_unknown_check(make_client) -> None:
    with make_client(handler([], []), **URLS) as c:
        r = c.post("/api/v1/verify/live", json={"check_id": "00000000-0000-0000-0000-000000000000"}, headers=WEB)
        assert r.status_code == 404


def test_live_search_scraper_down_returns_previous_result(make_client) -> None:
    def _handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "llm":
            return httpx.Response(200, json=llm_result("insufficient_evidence"))
        raise httpx.ConnectError("down")

    with make_client(_handle, **URLS) as c:
        first = c.post("/api/v1/verify/claim", json={"text": CLAIM}, headers=WEB).json()
        body = c.post("/api/v1/verify/live", json={"check_id": first["check_id"]}, headers=WEB).json()
        assert body["claims"] == first["claims"]
        assert body["live_search"]["message"].startswith("Wyszukiwanie na żywo nie powiodło się")
