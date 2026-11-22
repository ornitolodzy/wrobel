"""Reconciliation against a real Neo4j and a fake PageScrapper. The database at NEO4J_TEST_URI is WIPED.

NEO4J_TEST_URI=bolt://localhost:7687 pytest
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from context_graph.config import Settings
from context_graph.embeddings import OllamaEmbedder

TEST_URI = os.environ.get("NEO4J_TEST_URI")
pytestmark = pytest.mark.skipif(not TEST_URI, reason="set NEO4J_TEST_URI to run (wipes the database)")


def doc(i: int, text: str, revision: int = 1) -> dict:
    return {
        "id": i,
        "url": f"https://www.gov.pl/web/rcb/komunikat-{i}/",
        "source_id": "rcb-komunikaty",
        "source_type": "official",
        "title": f"Komunikat {i}",
        "publisher": "Rządowe Centrum Bezpieczeństwa",
        "published_at": "2026-11-20",
        "retrieved_at": "2026-11-21T10:00:00+00:00",
        "updated_at": f"2026-11-21T10:00:0{revision}+00:00",
        "language": "pl",
        "text": text,
        "content_hash": f"h{i}-{revision}",
        "revision": revision,
        "extra": None,
    }


# The fake scraper's documents table (the source of truth in these tests).
SCRAPER: dict[int, dict] = {}


def scraper(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/documents/revisions":
        items = [{k: d[k] for k in ("id", "url", "revision", "updated_at")} for d in SCRAPER.values()]
        return httpx.Response(200, json={"total": len(items), "items": items})
    if path.startswith("/documents/"):
        d = SCRAPER.get(int(path.rsplit("/", 1)[1]))
        return httpx.Response(200, json=d) if d else httpx.Response(404)
    return httpx.Response(404)


def vector(text: str) -> list[float]:
    seed = sum(map(ord, text))
    raw = [((seed * (i + 1)) % 97) / 97 + 0.01 for i in range(1024)]
    norm = sum(v * v for v in raw) ** 0.5
    return [v / norm for v in raw]


def embedder(model: str = "bge-m3", fail: bool = False) -> OllamaEmbedder:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500)
        return httpx.Response(200, json={"embeddings": [vector(t) for t in json.loads(request.content)["input"]]})

    return OllamaEmbedder(Settings(embeddings_model=model), transport=httpx.MockTransport(handler))


@pytest.fixture(scope="module")
def client():
    os.environ["NEO4J_URI"] = TEST_URI
    os.environ["SCRAPER_URL"] = "http://scraper"
    from fastapi.testclient import TestClient

    from context_graph.api import app
    from context_graph.config import get_settings

    get_settings.cache_clear()
    SCRAPER.clear()
    SCRAPER.update(
        {i: doc(i, f"Komunikat numer {i}. Treść alertu RCB dla mieszkańców powiatu {i}.") for i in (1, 2, 3)}
    )
    try:
        with TestClient(app) as c:
            c.app.state.graph.write("MATCH (n) DETACH DELETE n")
            c.app.state.scraper_transport = httpx.MockTransport(scraper)
            yield c
    finally:
        os.environ.pop("SCRAPER_URL", None)
        get_settings.cache_clear()


def reconcile(client, dry_run: bool = False) -> dict:
    return client.post("/sync/reconcile", params={"dry_run": dry_run}).json()


def read(client, query: str, **params):
    return client.app.state.graph.read(query, **params)


def test_empty_graph_is_filled_from_the_scraper(client):
    dry = reconcile(client, dry_run=True)
    assert (dry["missing"], dry["ingested"], dry["lag"]) == (3, 0, 3)
    assert read(client, "MATCH (d:Document) RETURN count(d) AS n") == [{"n": 0}]  # dry run wrote nothing

    res = reconcile(client)
    assert (res["missing"], res["ingested"], res["failed"], res["lag"]) == (3, 3, 0, 0)
    revs = read(client, "MATCH (d:Document) RETURN d.scraper_revision AS r")
    assert [r["r"] for r in revs] == [1, 1, 1]
    assert client.get("/sync/status").json()["lag"] == 0
    assert reconcile(client)["ingested"] == 0  # idempotent


def test_lost_push_is_repaired(client):
    # The scraper stored revision 2, but the push never reached the graph.
    SCRAPER[2] = doc(2, "Komunikat numer 2 zaktualizowany: alert odwołany.", revision=2)
    res = reconcile(client)
    assert (res["stale"], res["ingested"], res["lag"]) == (1, 1, 0)
    rows = read(
        client,
        "MATCH (c:Chunk)-[:PART_OF]->(d:Document {scraper_revision: 2}) WHERE c.section = 'tresc' RETURN c.text AS t",
    )
    assert rows == [{"t": "Komunikat numer 2 zaktualizowany: alert odwołany."}]


def test_late_push_of_an_older_revision_is_ignored(client):
    old = {k: v for k, v in doc(2, "Stara treść, która dotarła za późno.", revision=1).items() if k != "id"}
    res = client.post("/ingest/document", json=old).json()
    assert res["skipped"] is True
    texts = [r["t"] for r in read(client, "MATCH (c:Chunk) WHERE c.section = 'tresc' RETURN c.text AS t")]
    assert "Stara treść, która dotarła za późno." not in texts


def test_orphans_are_reported_not_deleted(client):
    client.post("/ingest/document", json={"url": "https://seed.example/demo", "text": "Dane demonstracyjne."})
    res = reconcile(client)
    assert res["orphans"] == 1 and res["orphan_urls"] == ["https://seed.example/demo"]
    assert read(client, "MATCH (d:Document {url: 'https://seed.example/demo'}) RETURN count(d) AS n") == [{"n": 1}]


def test_vectors_missing_after_an_outage_are_added(client):
    client.app.state.embedder = embedder(fail=True)  # tunnel down during the push
    SCRAPER[4] = doc(4, "Komunikat numer 4 dodany w czasie awarii tunelu.")
    pushed = {k: v for k, v in SCRAPER[4].items() if k != "id"}
    res = client.post("/ingest/document", json=pushed).json()
    assert res["skipped"] is False and res["unembedded"] > 0  # PageScrapper treats this as not accepted

    client.app.state.embedder = embedder()  # tunnel back
    res = reconcile(client)
    assert res["unembedded_chunks"] == 0 and res["consistent"] is True and res["lag"] == 0
    version = read(client, "MATCH (v:CorpusVersion) RETURN v.embedding_model AS m, v.scraper_documents AS n")
    assert version == [{"m": "bge-m3", "n": 4}]  # watermark written once in sync


def test_model_switch_never_mixes_vector_spaces(client):
    client.app.state.embedder = embedder("bge-m3-v2")
    partial = client.post("/embeddings/backfill", params={"limit": 2}).json()
    assert partial["embedded"] == 2 and partial["remaining"] > 0
    assert read(client, "MATCH (v:CorpusVersion) RETURN v.embedding_model AS m, v.consistent AS ok") == [
        {"m": "bge-m3-v2", "ok": False}
    ]  # the pipeline refuses the graph until the switch is complete

    res = reconcile(client)
    assert res["consistent"] is True and res["unembedded_chunks"] == 0 and res["lag"] == 0
    other = read(
        client,
        "MATCH (c) WHERE (c:Chunk OR c:CheckedClaim) AND c.embedding_model <> 'bge-m3-v2' RETURN count(c) AS n",
    )
    assert other == [{"n": 0}]
