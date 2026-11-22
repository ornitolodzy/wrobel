"""Reconciliation logic without a database: diff, payload mapping, paging."""

import httpx

from context_graph.config import Settings
from context_graph.sync import diff, reconcile, scraper_payload, scraper_revisions


def item(i: int, url: str, rev: int) -> dict:
    return {"id": i, "url": url, "revision": rev, "updated_at": "2026-11-22T01:00:00+00:00"}


def test_diff_finds_missing_stale_and_orphans():
    scraper = [
        item(1, "https://a.example/x/", 1),  # graph has it (canonical form, no trailing slash)
        item(2, "https://a.example/y", 3),  # graph has revision 2 -> stale
        item(3, "https://a.example/z", 1),  # graph has no revision (ingested before revisions) -> stale
        item(4, "https://a.example/new", 1),  # missing
        item(5, "https://A.example/w", 2),  # graph newer (late push already applied) -> fine
    ]
    graph = {
        "https://a.example/x": 1,
        "https://a.example/y": 2,
        "https://a.example/z": None,
        "https://a.example/w": 5,
        "https://seed.example/demo": None,  # not from the scraper -> orphan, reported only
    }
    missing, stale, orphans = diff(scraper, graph)
    assert [m["id"] for m in missing] == [4]
    assert sorted(s["id"] for s in stale) == [2, 3]
    assert orphans == ["https://seed.example/demo"]


def test_diff_keeps_newest_of_url_variants():
    scraper = [item(1, "https://a.example/x/", 1), item(2, "https://a.example/x", 4)]
    missing, _, _ = diff(scraper, {})
    assert [(m["id"], m["revision"]) for m in missing] == [(2, 4)]


def test_scraper_payload_matches_the_push():
    doc = {
        "id": 7,
        "url": "https://demagog.org.pl/a/",
        "source_id": "demagog-fake-news",
        "source_type": "fact_check",
        "title": "T",
        "publisher": "P",
        "published_at": "2026-11-01",
        "retrieved_at": "2026-11-02T10:00:00+00:00",
        "language": "pl",
        "text": "tekst",
        "content_hash": "h",
        "revision": 3,
        "extra": {"claim_reviews": [{"text": "A"}], "categories": ["x"]},
        "preview": None,
    }
    payload = scraper_payload(doc)
    assert payload["revision"] == 3 and payload["claims"] == [{"text": "A"}]
    assert "extra" not in payload and "id" not in payload


def test_revisions_are_paged():
    rows = [item(i, f"https://a.example/{i}", 1) for i in range(7)]

    def handler(request: httpx.Request) -> httpx.Response:
        limit, offset = int(request.url.params["limit"]), int(request.url.params["offset"])
        return httpx.Response(200, json={"total": len(rows), "items": rows[offset : offset + limit]})

    import context_graph.sync as sync

    old, sync.PAGE = sync.PAGE, 3
    try:
        with httpx.Client(base_url="http://scraper", transport=httpx.MockTransport(handler)) as client:
            assert [r["id"] for r in scraper_revisions(client)] == list(range(7))
    finally:
        sync.PAGE = old


def test_reconcile_without_scraper_url_reports_it():
    res = reconcile(None, None, Settings(scraper_url=""), None)
    assert res.error == "SCRAPER_URL is not set" and res.lag == 0
