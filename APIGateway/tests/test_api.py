import json

import httpx

from tests.conftest import ADMIN, DISCORD, PNG_BYTES, WEB

LLM_OK = {
    "status": "completed",
    "claims": [
        {
            "claim": "Naruszono przestrzeń powietrzną dnia 20.11",
            "verdict": "confirmed",
            "explanation": "Komunikat MON potwierdza zdarzenie.",
            "evidence_strength": "strong",
            "strength_rationale": "2 niezależne źródła urzędowe, aktualne",
            "sources": [
                {
                    "title": "Komunikat",
                    "publisher": "MON",
                    "url": "https://www.gov.pl/web/obrona-narodowa",
                    "published_at": "2026-11-20",
                    "fragment": "...",
                }
            ],
        }
    ],
    "summary": "Twierdzenie potwierdzone.",
    "limitations": [],
    "usage": {"input_tokens": 1200, "output_tokens": 300, "cost_usd": 0.004},
}


def llm_handler(captured: list[dict] | None = None, response: httpx.Response | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/verify":
            if captured is not None:
                captured.append(json.loads(request.content))
            return response or httpx.Response(200, json=LLM_OK)
        if request.url.path == "/text-recognition-from-img":
            return httpx.Response(200, json={"text": "Naruszono przestrzeń powietrzną dnia 20.11, kontakt: a@b.pl"})
        return httpx.Response(404)

    return handler


LLM_URLS = {"llm_service_url": "http://llm:8001", "image_recognition_url": "http://img:8003"}


def test_health(make_client) -> None:
    with make_client() as c:
        assert c.get("/health").json() == {"status": "ok"}
        services = c.get("/health/services", headers=WEB).json()
        assert {s["name"]: s["status"] for s in services} == {
            "llm": "mock",
            "page_scrapper": "mock",
            "image_recognition": "mock",
        }


def test_verify_works_without_key(make_client) -> None:
    with make_client() as c:
        r = c.post("/api/v1/verify/claim", json={"text": "test twierdzenia"})
        assert r.status_code == 200
        assert r.json()["channel"] == "unknown"


def test_channel_from_header(make_client) -> None:
    with make_client() as c:
        assert c.post("/api/v1/verify/claim", json={"text": "test"}, headers={"X-Channel": "Discord"}).json()["channel"] == "discord"
        assert c.post("/api/v1/verify/claim", json={"text": "test"}, headers={"X-Channel": "hacker"}).json()["channel"] == "unknown"


def test_admin_endpoints_require_admin_key(make_client) -> None:
    with make_client() as c:
        assert c.get("/api/v1/admin/events").status_code == 401
        assert c.get("/api/v1/admin/events", headers={"X-API-Key": "zly"}).status_code == 401
        assert c.patch("/api/v1/config", json={"mock_mode": True}).status_code == 401
        assert c.post("/api/v1/scrap/all", json={}).status_code == 401
        events = c.get("/api/v1/admin/events", params={"event_type": "auth_failed"}, headers=ADMIN).json()
        assert len(events) == 4


def test_admin_endpoints_disabled_without_admin_key(make_client) -> None:
    with make_client(admin_api_key="") as c:
        assert c.get("/api/v1/admin/events").status_code == 403
        assert c.patch("/api/v1/config", json={"mock_mode": True}, headers={"X-API-Key": ""}).status_code == 403
        assert c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}).status_code == 200


def test_claim_mock_mode_is_honest(make_client) -> None:
    with make_client() as c:
        r = c.post("/api/v1/verify/claim", json={"text": "Naruszono przestrzeń powietrzną dnia 20.11"}, headers=WEB)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "completed"
        assert body["mock"] is True
        assert body["channel"] == "web"
        assert body["claims"][0]["verdict"] == "insufficient_evidence"
        assert r.headers["X-Check-Id"] == body["check_id"]


def test_claim_forwards_to_llm_with_options_and_model(make_client) -> None:
    captured: list[dict] = []
    with make_client(llm_handler(captured), **LLM_URLS) as c:
        r = c.post(
            "/api/v1/verify/claim",
            json={"text": "Naruszono przestrzeń, pisz: jan@example.com"},
            headers=DISCORD,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["mock"] is False
        assert body["pii_masked"] is True
        assert body["claims"][0]["verdict"] == "confirmed"
        assert body["channel"] == "discord"
        assert "usage" not in body

        sent = captured[0]
        assert sent["input_type"] == "claim"
        assert "jan@example.com" not in sent["content"]["text"]
        assert "[EMAIL]" in sent["content"]["text"]
        assert sent["options"] == {"language": "pl", "max_claims": 3, "max_sources": 5}
        assert sent["model"] == {"provider": "mistral", "name": "mistral-large-latest"}


def test_same_result_across_channels(make_client) -> None:
    with make_client(llm_handler(), **LLM_URLS) as c:
        a = c.post("/api/v1/verify/claim", json={"text": "To samo twierdzenie"}, headers=WEB).json()
        b = c.post("/api/v1/verify/claim", json={"text": "To samo twierdzenie"}, headers=DISCORD).json()
        assert a["claims"] == b["claims"]


def test_llm_api_limit_is_not_a_verdict(make_client) -> None:
    with make_client(llm_handler(response=httpx.Response(429)), **LLM_URLS) as c:
        r = c.post("/api/v1/verify/claim", json={"text": "jakieś twierdzenie"}, headers=WEB)
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "api_limit"
        assert body["claims"] == []
        assert body["error"]["code"] == "api_limit"


def test_llm_unreachable(make_client) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with make_client(boom, **LLM_URLS) as c:
        r = c.post("/api/v1/verify/claim", json={"text": "jakieś twierdzenie"}, headers=WEB)
        assert r.status_code == 502
        assert r.json()["status"] == "error"
        assert r.json()["error"]["code"] == "service_unavailable"


def test_llm_bad_contract(make_client) -> None:
    bad = httpx.Response(200, json={"claims": [{"claim": "x", "verdict": "PRAWDA"}]})
    with make_client(llm_handler(response=bad), **LLM_URLS) as c:
        r = c.post("/api/v1/verify/claim", json={"text": "jakieś twierdzenie"}, headers=WEB)
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "bad_response"


def test_needs_clarification_passthrough(make_client) -> None:
    resp = httpx.Response(200, json={"status": "needs_clarification", "clarification_question": "Którego dnia?"})
    with make_client(llm_handler(response=resp), **LLM_URLS) as c:
        body = c.post("/api/v1/verify/claim", json={"text": "coś się stało"}, headers=WEB).json()
        assert body["status"] == "needs_clarification"
        assert body["clarification_question"] == "Którego dnia?"


def test_claim_validation(make_client) -> None:
    with make_client(max_text_length=50) as c:
        assert c.post("/api/v1/verify/claim", json={"text": "ab"}, headers=WEB).status_code == 422
        assert c.post("/api/v1/verify/claim", json={"text": "x" * 51}, headers=WEB).status_code == 422
        r = c.post("/api/v1/verify/claim", json={"text": "ok tekst", "options": {"max_claims": 1}}, headers=WEB)
        assert r.status_code == 422
        assert all("input" not in e for e in r.json()["errors"])
        r = c.post("/api/v1/verify/claim", json={"text": "ok tekst", "hack": 1}, headers=WEB)
        assert r.status_code == 422


def test_url_ssrf_protection(make_client) -> None:
    with make_client() as c:
        for bad in [
            "http://localhost/admin",
            "http://127.0.0.1/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data",
            "ftp://example.com/file",
            "https://user:pass@example.com/",
            "https://example.com:8080/",
            "https://intranet/",
            "javascript:alert(1)",
        ]:
            r = c.post("/api/v1/verify/url", json={"url": bad}, headers=WEB)
            assert r.status_code == 422, bad


def test_url_ok(make_client) -> None:
    captured: list[dict] = []
    with make_client(llm_handler(captured), **LLM_URLS) as c:
        r = c.post("/api/v1/verify/url", json={"url": "https://www.gov.pl/web/msz#sekcja"}, headers=WEB)
        assert r.status_code == 200
        assert r.json()["input_type"] == "url"
        assert captured[0]["content"] == {"url": "https://www.gov.pl/web/msz"}


def test_image_flow_ocr_then_llm(make_client) -> None:
    captured: list[dict] = []
    with make_client(llm_handler(captured), **LLM_URLS) as c:
        r = c.post(
            "/api/v1/verify/image",
            files={"file": ("zrzut.png", PNG_BYTES, "image/png")},
            headers=WEB,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["input_type"] == "image"
        assert "[EMAIL]" in body["ocr_text"]
        assert "a@b.pl" not in json.dumps(captured[0])
        assert captured[0]["content"]["ocr_text"].startswith("Naruszono")


def test_image_rejects_non_images(make_client) -> None:
    with make_client() as c:
        r = c.post("/api/v1/verify/image", files={"file": ("x.png", b"<?php echo 1; ?>", "image/png")}, headers=WEB)
        assert r.status_code == 415
        r = c.post("/api/v1/verify/image", files={"file": ("x.png", b"", "image/png")}, headers=WEB)
        assert r.status_code == 422


def test_image_too_large(make_client) -> None:
    with make_client(max_image_size_mb=1) as c:
        big = PNG_BYTES + b"\x00" * (1024 * 1024)
        r = c.post("/api/v1/verify/image", files={"file": ("x.png", big, "image/png")}, headers=WEB)
        assert r.status_code == 413


def test_image_ocr_empty_needs_clarification(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "   "})

    with make_client(handler, **LLM_URLS) as c:
        r = c.post("/api/v1/verify/image", files={"file": ("x.png", PNG_BYTES, "image/png")}, headers=WEB)
        assert r.status_code == 200
        assert r.json()["status"] == "needs_clarification"


def test_text_recognition_endpoint(make_client) -> None:
    with make_client(llm_handler(), **LLM_URLS) as c:
        r = c.post("/api/v1/text-recognition-from-img", files={"file": ("x.png", PNG_BYTES, "image/png")}, headers=WEB)
        assert r.status_code == 200
        assert r.json()["text"].startswith("Naruszono")


def test_scrap_all_admin_only(make_client) -> None:
    with make_client() as c:
        assert c.post("/api/v1/scrap/all", json={}, headers=WEB).status_code == 401
        r = c.post("/api/v1/scrap/all", json={}, headers=ADMIN)
        assert r.status_code == 200
        assert r.json()["mock"] is True


def test_rate_limit(make_client) -> None:
    with make_client(rate_limit_per_minute=2) as c:
        for _ in range(2):
            assert c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=WEB).status_code == 200
        r = c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=WEB)
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=DISCORD).status_code == 429


def test_runtime_config(make_client) -> None:
    captured: list[dict] = []
    with make_client(llm_handler(captured), **LLM_URLS) as c:
        assert c.get("/api/v1/config", headers=WEB).json()["llm_provider"] == "mistral"
        assert c.patch("/api/v1/config", json={"llm_model": "x"}, headers=WEB).status_code == 401
        assert c.patch("/api/v1/config", json={"nieznane": 1}, headers=ADMIN).status_code == 422

        r = c.patch("/api/v1/config", json={"llm_provider": "bielik", "llm_model": "bielik-11b", "default_max_claims": 2}, headers=ADMIN)
        assert r.status_code == 200
        assert r.json()["llm_model"] == "bielik-11b"

        c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=WEB)
        assert captured[-1]["model"] == {"provider": "bielik", "name": "bielik-11b"}
        assert captured[-1]["options"]["max_claims"] == 2

        r = c.patch("/api/v1/config", json={"mock_mode": True}, headers=ADMIN)
        body = c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=WEB).json()
        assert body["mock"] is True

        events = c.get("/api/v1/admin/events", params={"event_type": "config_changed"}, headers=ADMIN).json()
        assert len(events) == 2


def test_checks_get_and_delete(make_client) -> None:
    with make_client() as c:
        check_id = c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=WEB).json()["check_id"]

        assert c.get(f"/api/v1/checks/{check_id}").json()["check_id"] == check_id
        assert c.get("/api/v1/checks/00000000-0000-0000-0000-000000000000").status_code == 404

        assert c.delete(f"/api/v1/checks/{check_id}", headers=WEB).json() == {"check_id": check_id, "deleted": True}
        assert c.get(f"/api/v1/checks/{check_id}").status_code == 404
        assert c.delete(f"/api/v1/checks/{check_id}").status_code == 404


def test_audit_log_records_check(make_client) -> None:
    with make_client(llm_handler(), **LLM_URLS) as c:
        c.post("/api/v1/verify/claim", json={"text": "twierdzenie z mailem a@b.pl"}, headers=WEB)
        events = c.get("/api/v1/admin/events", headers=ADMIN).json()
        types = [e["event_type"] for e in events]
        assert "check_completed" in types
        assert "pii_masked" in types
        completed = next(e for e in events if e["event_type"] == "check_completed")
        assert completed["detail"]["usage"]["cost_usd"] == 0.004
        assert completed["detail"]["verdicts"] == ["confirmed"]


def test_openapi_available(make_client) -> None:
    with make_client() as c:
        paths = c.get("/openapi.json").json()["paths"]
        for p in [
            "/api/v1/verify/claim",
            "/api/v1/verify/url",
            "/api/v1/verify/image",
            "/api/v1/scrap/all",
            "/api/v1/text-recognition-from-img",
            "/api/v1/checks/{check_id}",
            "/api/v1/config",
        ]:
            assert p in paths


def test_rate_limit_uses_real_ip_behind_trusted_proxy(make_client) -> None:
    with make_client(rate_limit_per_minute=1, trusted_proxies="testclient,0.0.0.0/0") as c:
        user_a = {"X-Forwarded-For": "203.0.113.10"}
        user_b = {"X-Forwarded-For": "203.0.113.20"}
        assert c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=user_a).status_code == 200
        assert c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=user_b).status_code == 200
        assert c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers=user_a).status_code == 429


def test_forwarded_for_ignored_from_untrusted_client(make_client) -> None:
    with make_client(rate_limit_per_minute=1, trusted_proxies="10.0.0.1") as c:
        assert c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        r = c.post("/api/v1/verify/claim", json={"text": "twierdzenie"}, headers={"X-Forwarded-For": "2.2.2.2"})
        assert r.status_code == 429


SCRAPED_PAGE = {
    "url": "https://www.gov.pl/web/rcb/alert",
    "final_url": "https://www.gov.pl/web/rcb/alert",
    "title": "Alert RCB - atak z powietrza",
    "publisher": "Rządowe Centrum Bezpieczeństwa",
    "published_at": "2026-11-20",
    "retrieved_at": "2026-11-21T15:00:00Z",
    "text": "UWAGA! Zagrożenie atakiem z powietrza. Kontakt: rcb@example.com",
    "rendered": False,
}
SCRAPER_URLS = {**LLM_URLS, "page_scrapper_url": "http://scraper:8002"}


def scraper_handler(scrape_response: httpx.Response, captured: list[dict]):
    base = llm_handler(captured)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/scrape":
            return scrape_response
        return base(request)

    return handler


def test_url_without_scraper_sends_only_url(make_client) -> None:
    captured: list[dict] = []
    with make_client(llm_handler(captured), **LLM_URLS) as c:
        body = c.post("/api/v1/verify/url", json={"url": "https://www.gov.pl/web/rcb/alert"}, headers=WEB).json()
        assert captured[0]["content"] == {"url": "https://www.gov.pl/web/rcb/alert"}
        assert any("PageScrapper nie jest podłączony" in lim for lim in body["limitations"])
        assert body["page"] is None


def test_url_is_scraped_before_llm(make_client) -> None:
    captured: list[dict] = []
    with make_client(scraper_handler(httpx.Response(200, json=SCRAPED_PAGE), captured), **SCRAPER_URLS) as c:
        r = c.post("/api/v1/verify/url", json={"url": "https://www.gov.pl/web/rcb/alert"}, headers=WEB)
        assert r.status_code == 200, r.text
        body = r.json()
        content = captured[0]["content"]
        assert content["url"] == "https://www.gov.pl/web/rcb/alert"
        assert content["title"] == "Alert RCB - atak z powietrza"
        assert content["published_at"] == "2026-11-20"
        assert "Zagrożenie atakiem z powietrza" in content["text"]
        assert "rcb@example.com" not in content["text"]
        assert body["page"]["publisher"] == "Rządowe Centrum Bezpieczeństwa"
        assert body["pii_masked"] is True


def test_unreadable_page_needs_clarification(make_client) -> None:
    captured: list[dict] = []
    unreadable = httpx.Response(422, json={"detail": {"code": "no_text", "message": "Nie udało się odczytać treści strony. Wklej tekst albo prześlij zrzut ekranu."}})
    with make_client(scraper_handler(unreadable, captured), **SCRAPER_URLS) as c:
        r = c.post("/api/v1/verify/url", json={"url": "https://www.pap.pl/"}, headers=WEB)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "needs_clarification"
        assert "zrzut ekranu" in body["clarification_question"]
        assert body["claims"] == []
        assert captured == []


def test_scraper_down_is_technical_error(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/scrape":
            raise httpx.ConnectError("refused", request=request)
        return llm_handler()(request)

    with make_client(handler, **SCRAPER_URLS) as c:
        r = c.post("/api/v1/verify/url", json={"url": "https://www.gov.pl/web/rcb/alert"}, headers=WEB)
        assert r.status_code == 502
        assert r.json()["status"] == "error"
        assert r.json()["error"]["code"] == "service_unavailable"


SOURCES = [{"id": "rcb-komunikaty", "source_type": "official", "documents": 5}]
RUN_ID = "aae0142c-d505-4230-ac88-cbf36a87130a"


def knowledge_handler(seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        path = request.url.path
        if path == "/sources":
            return httpx.Response(200, json=SOURCES)
        if path == "/stats":
            return httpx.Response(200, json={"documents": 280, "domain_count": 10})
        if path == "/scrap/runs":
            return httpx.Response(200, json=[{"run_id": RUN_ID, "status": "finished"}])
        if path == f"/scrap/runs/{RUN_ID}":
            return httpx.Response(200, json={"run_id": RUN_ID, "status": "finished"})
        if path == "/documents":
            return httpx.Response(200, json={"total": 1, "items": [{"id": 1}]})
        if path == "/documents/1":
            return httpx.Response(200, json={"id": 1, "text": "pełna treść"})
        return httpx.Response(404, json={"detail": "Nie znaleziono."})

    return handler


def test_sources_are_public(make_client) -> None:
    seen: list[str] = []
    with make_client(knowledge_handler(seen), page_scrapper_url="http://scraper:8002") as c:
        r = c.get("/api/v1/sources")
        assert r.status_code == 200
        assert r.json() == SOURCES
        assert c.get("/api/v1/corpus/stats").json()["domain_count"] == 10


def test_knowledge_admin_endpoints(make_client) -> None:
    seen: list[str] = []
    with make_client(knowledge_handler(seen), page_scrapper_url="http://scraper:8002") as c:
        for path in ["/api/v1/scrap/runs", f"/api/v1/scrap/runs/{RUN_ID}", "/api/v1/documents", "/api/v1/documents/1"]:
            assert c.get(path).status_code == 401, path
            assert c.get(path, headers=ADMIN).status_code == 200, path

        c.get("/api/v1/documents", params={"source_id": "rcb-komunikaty", "limit": 5}, headers=ADMIN)
        assert seen[-1] == "http://scraper:8002/documents?limit=5&offset=0&source_id=rcb-komunikaty"
        assert c.get("/api/v1/documents/999", headers=ADMIN).status_code == 404
        assert c.get("/api/v1/scrap/runs/nie-uuid", headers=ADMIN).status_code == 422
        assert c.get("/api/v1/documents", params={"source_id": "../admin"}, headers=ADMIN).status_code == 422


def test_knowledge_without_scraper(make_client) -> None:
    with make_client() as c:
        r = c.get("/api/v1/sources")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "not_configured"
