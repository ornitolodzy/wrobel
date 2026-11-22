"""CheckedClaim layer against a real Neo4j. The database at NEO4J_TEST_URI is WIPED.

NEO4J_TEST_URI=bolt://localhost:7687 pytest
"""

from __future__ import annotations

import os

import httpx
import pytest

from context_graph.config import Settings
from context_graph.embeddings import OllamaEmbedder

TEST_URI = os.environ.get("NEO4J_TEST_URI")
pytestmark = pytest.mark.skipif(not TEST_URI, reason="set NEO4J_TEST_URI to run (wipes the database)")

REEL = "https://www.facebook.com/reel/1"
# PageScrapper payload with ClaimReview (app/claimreview.py there), as in the real Demagog page.
DEMAGOG = {
    "url": "https://demagog.org.pl/fake_news/niemiecka-policja-nie-jedzie-do-warszawy/",
    "source_id": "demagog-fake-news",
    "source_type": "fact_check",
    "title": "Niemiecka policja nie jedzie do Warszawy. Ten film wprowadza w błąd",
    "publisher": "Stowarzyszenie Demagog",
    "published_at": "2026-11-02",
    "retrieved_at": "2026-11-21T15:00:00+00:00",
    "text": "Nagranie pokazuje niemieckie radiowozy w Niemczech, a nie w drodze do Warszawy.",
    "claims": [
        {
            "text": "Niemieckie radiowozy jadą do Warszawy.",
            "claimant": "Użytkownicy Facebooka",
            "claimant_type": "Person",
            "appearances": [REEL, "https://www.facebook.com/reel/2"],
            "rating": "fałsz",
            "claim_date": "2026-10-31",
            "review_date": "2026-11-02",
            "origin": "claimreview",
        },
        {
            "text": "Robot w rosyjskim sklepie zaatakował klienta.",
            "claimant": "Goniec",
            "claimant_type": "Person",
            "appearances": [REEL],
            "rating": "Manipulacja",
            "origin": "claimreview",
        },
    ],
}
KONKRET24 = {
    "url": "https://konkret24.tvn24.pl/swiat/na-trybunal-jada-niemieckie-wozy-wojskowe",
    "source_id": "konkret24",
    "source_type": "fact_check",
    "title": "Na Trybunał jadą niemieckie wozy wojskowe? Nie",
    "publisher": "Konkret24 (TVN24)",
    "published_at": "2026-11-01",
    "text": "Film pokazuje ćwiczenia sprzed lat.",
    "claims": [{"text": "Niemieckie wozy wojskowe jadą na Trybunał.", "claimant": "Goniec", "origin": "llm"}],
}


@pytest.fixture(scope="module")
def client():
    os.environ["NEO4J_URI"] = TEST_URI
    from fastapi.testclient import TestClient

    from context_graph.api import app
    from context_graph.config import get_settings

    get_settings.cache_clear()
    with TestClient(app) as c:
        c.app.state.graph.write("MATCH (n) DETACH DELETE n")
        assert c.post("/ingest/document", json=DEMAGOG).status_code == 200
        assert c.post("/ingest/document", json=KONKRET24).status_code == 200
        yield c


def read(client, query: str, **params):
    return client.app.state.graph.read(query, **params)


def test_claims_hang_off_the_checking_document(client):
    rows = read(
        client,
        "MATCH (d:Document)-[r:CHECKS]->(c:CheckedClaim) RETURN d.url AS url, c.text AS text, "
        "c.rating AS rating, c.rating_raw AS raw, c.claim_date AS cd, r.method AS method ORDER BY text",
    )
    by_text = {r["text"]: r for r in rows}
    assert set(by_text) == {
        "Niemieckie radiowozy jadą do Warszawy.",
        "Robot w rosyjskim sklepie zaatakował klienta.",
        "Niemieckie wozy wojskowe jadą na Trybunał.",
    }
    first = by_text["Niemieckie radiowozy jadą do Warszawy."]
    assert (first["rating"], first["raw"], first["method"]) == ("falsz", "fałsz", "claimreview")
    assert first["cd"].year == 2026 and first["cd"].day == 31


def test_document_rating_falls_back_to_claim_review(client):
    rows = read(client, "MATCH (d:Document {url: $url}) RETURN d.rating AS r", url=DEMAGOG["url"].rstrip("/"))
    assert rows == [{"r": "fałsz"}]  # what the pipeline's `rating` query reads for fact-checks


def test_attributed_only_when_declared_and_specific(client):
    rows = read(
        client,
        "MATCH (c:CheckedClaim)-[r:ATTRIBUTED_TO]->(p:Person) "
        "RETURN c.text AS text, p.name AS name, p.kind AS kind, r.declared_by AS by",
    )
    # Generic "Użytkownicy Facebooka" is not a person; the LLM claim about Goniec is not attributed.
    robot = "Robot w rosyjskim sklepie zaatakował klienta."
    assert rows == [{"text": robot, "name": "Goniec", "kind": "person", "by": "fact_checker"}]
    generic = read(
        client,
        "MATCH (c:CheckedClaim {text: 'Niemieckie radiowozy jadą do Warszawy.'}) "
        "RETURN c.claimant_text AS t, c.claimant_kind AS k",
    )
    assert generic == [{"t": "Użytkownicy Facebooka", "k": "generic"}]


def test_appearances_are_shared_nodes(client):
    rows = read(client, "MATCH (c:CheckedClaim)-[:APPEARED_IN]->(a:Appearance {url: $u}) RETURN count(c) AS n", u=REEL)
    assert rows == [{"n": 2}]


def test_reingest_is_idempotent_and_replaces_claims(client):
    count = "MATCH (n) RETURN count(n) AS n"
    before = read(client, count)[0]["n"]
    client.post("/ingest/document", json=DEMAGOG)
    assert read(client, count)[0]["n"] == before

    corrected = {**DEMAGOG, "claims": DEMAGOG["claims"][:1]}
    client.post("/ingest/document", json=corrected)
    texts = {r["t"] for r in read(client, "MATCH (c:CheckedClaim) RETURN c.text AS t")}
    assert "Robot w rosyjskim sklepie zaatakował klienta." not in texts
    assert read(client, "MATCH (p:Person) RETURN count(p) AS n") == [{"n": 0}]  # orphan author removed
    assert read(client, "MATCH (a:Appearance) RETURN count(a) AS n") == [{"n": 2}]
    client.post("/ingest/document", json=DEMAGOG)


def test_every_new_relationship_is_auditable(client):
    rows = read(
        client,
        "MATCH ()-[r:CHECKS|APPEARED_IN|ATTRIBUTED_TO]->() WHERE r.method IS NULL OR r.created_at IS NULL "
        "RETURN count(r) AS n",
    )
    assert rows == [{"n": 0}]


def vector(text: str) -> list[float]:
    """Deterministic unit vector: equal texts -> equal vectors."""
    seed = sum(map(ord, text))
    raw = [((seed * (i + 1)) % 97) / 97 + 0.01 for i in range(1024)]
    norm = sum(v * v for v in raw) ** 0.5
    return [v / norm for v in raw]


def ollama(request: httpx.Request) -> httpx.Response:
    body = __import__("json").loads(request.content)
    return httpx.Response(200, json={"embeddings": [vector(t) for t in body["input"]]})


def test_claims_are_embedded_and_searchable(client):
    client.app.state.embedder = OllamaEmbedder(Settings(), transport=httpx.MockTransport(ollama))
    res = client.post("/embeddings/backfill").json()
    assert res["claims_embedded"] == 3 and res["claims_remaining"] == 0 and res["remaining"] == 0

    claim = "Niemieckie radiowozy jadą do Warszawy."
    rows = read(
        client,
        "CALL db.index.vector.queryNodes('checked_claim_embedding', 1, $q) YIELD node, score "
        "RETURN node.text AS text, score",
        q=vector(claim),
    )
    assert rows[0]["text"] == claim and rows[0]["score"] > 0.99

    # A new document embeds its claims on ingest, not only on backfill.
    new = {**KONKRET24, "url": KONKRET24["url"] + "-2", "claims": [{"text": "Nowe twierdzenie.", "origin": "llm"}]}
    client.post("/ingest/document", json=new)
    assert read(client, "MATCH (c:CheckedClaim) WHERE c.embedding IS NULL RETURN count(c) AS n") == [{"n": 0}]
