"""End-to-end against a real Neo4j. The database at NEO4J_TEST_URI is WIPED.

docker-compose up -d neo4j
NEO4J_TEST_URI=bolt://localhost:7687 pytest
"""

from __future__ import annotations

import os

import pytest

from context_graph.example_data import CLAIM, IMAGES, PAGES

TEST_URI = os.environ.get("NEO4J_TEST_URI")
pytestmark = pytest.mark.skipif(not TEST_URI, reason="set NEO4J_TEST_URI to run (wipes the database)")


@pytest.fixture(scope="module")
def client():
    os.environ["NEO4J_URI"] = TEST_URI
    from fastapi.testclient import TestClient

    from context_graph.api import app
    from context_graph.config import get_settings

    get_settings.cache_clear()
    with TestClient(app) as c:
        c.app.state.graph.write("MATCH (n) DETACH DELETE n")
        for page in PAGES:
            assert c.post("/ingest/page", json=page).status_code == 200
        for image in IMAGES:
            assert c.post("/ingest/image", json=image).status_code == 200
        yield c


def graph(client):
    return client.app.state.graph


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_nodes_are_connected_by_typed_relations(client):
    rels = {r["t"]: r["n"] for r in graph(client).read("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n")}
    for t in [
        "PUBLISHED_BY",
        "PART_OF",
        "NEXT",
        "MENTIONS",
        "CO_OCCURS",
        "CITES",
        "CORRECTS",
        "REPRINTS",
        "HAS_IMAGE",
        "HAS_TEXT",
    ]:
        assert rels.get(t, 0) > 0, t
    # every relationship is auditable
    assert graph(client).read("MATCH ()-[r]->() WHERE r.method IS NULL RETURN count(r) AS n")[0]["n"] == 0


def test_reingest_is_idempotent(client):
    count = "MATCH (n) RETURN count(n) AS n"
    before = graph(client).read(count)[0]["n"]
    client.post("/ingest/page", json=PAGES[0])
    assert graph(client).read(count)[0]["n"] == before


def test_reprint_points_to_original(client):
    rows = graph(client).read("MATCH (a:Document)-[:REPRINTS]->(b:Document) RETURN a.url AS a, b.url AS b")
    assert rows == [
        {"a": "https://news-b.example/swiat/obiekt-nad-lubelszczyzna", "b": "https://news-a.example/kraj/obiekt-20-11"}
    ]


def test_relation_requires_verbatim_quote(client):
    body = {"type": "CORRECTS", "src": PAGES[3]["url"], "dst": PAGES[0]["url"], "quote": "tego zdania nie ma w tekście"}
    assert client.post("/relations", json=body).status_code == 422
    body["quote"] = "Informację o naruszeniu oceniamy jako fałszywą."
    assert client.post("/relations", json=body).status_code == 200


def test_context_for_claim(client):
    res = client.post("/context", json={"text": CLAIM, "hops": 2, "limit": 8}).json()

    found = {e["key"]: e["found"] for e in res["entities"]}
    assert found["date:--11-20"] is True

    items = res["items"]
    assert items, res
    assert all(i["path"] for i in items)
    urls = {i["url"] for i in items}
    # direct hit, the correction (via CORRECTS) and the primary source are all in context
    assert "https://news-a.example/kraj/obiekt-20-11" in urls
    assert "https://factcheck.example/analizy/obiekt-20-11" in urls
    assert any("corrected" in i["flags"] for i in items)
    assert any("correction" in i["flags"] for i in items)
    # the reprint is collapsed, not a second confirmation
    texts = [i["text"] for i in items]
    assert len(texts) == len(set(texts))

    saved = graph(client).read(
        "MATCH (:Claim {id: $id})-[r:CONTEXT]->(c:Chunk) RETURN count(r) AS n", id=res["claim_id"]
    )
    assert saved[0]["n"] == len(items)


def test_context_reaches_ocr_text(client):
    res = client.post("/context", json={"text": "PILNE MON zwołuje sztab", "save": False}).json()
    assert any(i["section"] == "ocr" for i in res["items"])


def test_entity_neighbourhood(client):
    res = client.get("/entities/MON").json()
    assert res["key"] == "mon"
    assert res["related"] and res["chunks"]
    assert client.get("/entities/Nieistniejąca Encja").status_code == 404
