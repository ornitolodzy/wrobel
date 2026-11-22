"""The graph must satisfy the Verification Pipeline contract (VerificationPipeline docs/neo4j-kontrakt.md).

The Cypher below is copied from VerificationPipeline src/prawdowid/retrieval/neo4j.py (DEFAULT_QUERIES):
if these queries return the required columns, the pipeline reads this graph without custom queries.

docker-compose up -d neo4j
NEO4J_TEST_URI=bolt://localhost:7687 pytest      # WIPES the database
"""

from __future__ import annotations

import hashlib
import math
import os

import httpx
import pytest

from context_graph.config import Settings
from context_graph.embeddings import OllamaEmbedder

TEST_URI = os.environ.get("NEO4J_TEST_URI")
pytestmark = pytest.mark.skipif(not TEST_URI, reason="set NEO4J_TEST_URI to run (wipes the database)")

PIPELINE_RETURN = """
MATCH (c)-[:PART_OF]->(d:Document)-[:PUBLISHED_BY]->(s:Source)
RETURN c.id AS chunk_id, c.text AS text, coalesce(c.section, 'tresc') AS section,
       coalesce(c.position, 0) AS position, d.id AS doc_id, d.url AS url, d.title AS title,
       d.published_at AS published_at, d.fetched_at AS fetched_at,
       coalesce(d.dup_group, d.id) AS dup_group, s.publisher AS publisher, s.type AS source_type,
       score
ORDER BY score DESC, chunk_id
"""
PIPELINE_QUERIES = {
    "dense": "CALL db.index.vector.queryNodes($vector_index, $k, $qvec) YIELD node AS c, score" + PIPELINE_RETURN,
    "sparse": "CALL db.index.fulltext.queryNodes($fulltext_index, $lucene, {limit: $k}) "
    "YIELD node AS c, score" + PIPELINE_RETURN,
    "rating": "MATCH (d:Document {id: $doc_id}) WHERE d.rating IS NOT NULL RETURN d.rating AS rating",
    "cites": """
        MATCH (d:Document {id: $doc_id})-[:CITES]->(p:Document)-[:PUBLISHED_BY]->(s:Source)
        RETURN p.id AS doc_id, p.url AS url, p.title AS title, p.published_at AS published_at,
               p.fetched_at AS fetched_at, coalesce(p.dup_group, p.id) AS dup_group,
               s.publisher AS publisher, s.type AS source_type
        ORDER BY p.url LIMIT $limit
    """,
    "corpus_version": "MATCH (v:CorpusVersion) RETURN v.id AS id, v.embedding_model AS embedding_model, "
    "coalesce(v.consistent, true) AS consistent ORDER BY v.id DESC LIMIT 1",
}
CHUNK_COLUMNS = {
    "chunk_id",
    "text",
    "section",
    "position",
    "doc_id",
    "url",
    "title",
    "published_at",
    "fetched_at",
    "dup_group",
    "publisher",
    "source_type",
    "score",
}

RCB = {
    "url": "https://www.gov.pl/web/rcb/alert-rcb---atak-z-powietrza-2011",
    "source_id": "rcb-komunikaty",
    "source_type": "official",
    "title": "Alert RCB - atak z powietrza (20.11)",
    "publisher": "Rządowe Centrum Bezpieczeństwa",
    "published_at": "2026-11-20",
    "retrieved_at": "2026-11-21T15:00:00+00:00",
    "language": "pl",
    "text": "UWAGA! Zagrożenie atakiem z powietrza. Znajdź bezpieczne miejsce. "
    "Alert wysłano 20.11 do mieszkańców Lubelszczyzny. RCB prosi o stosowanie się do poleceń służb.",
    "content_hash": "abc123",
}
MON = {
    "url": "https://www.gov.pl/web/obrona-narodowa/wicepremier-o-naruszeniu",
    "source_id": "mon-aktualnosci",
    "source_type": "official",
    "title": "Wicepremier o naruszeniu polskiej przestrzeni powietrznej",
    "publisher": "Ministerstwo Obrony Narodowej",
    "published_at": "2026-11-20",
    "retrieved_at": "2026-11-21T15:01:00+00:00",
    "text": "W nocy z 19 na 20 listopada rosyjskie drony naruszyły polską przestrzeń powietrzną. "
    "MON potwierdza, że nikt nie ucierpiał.",
}
OLD_NEWS = {
    "url": "https://www.gov.pl/web/obrona-narodowa/archiwum-2025",
    "source_id": "mon-aktualnosci",
    "source_type": "official",
    "title": "Komunikat archiwalny",
    "publisher": "Ministerstwo Obrony Narodowej",
    "published_at": "2025-11-21",
    "text": "Dnia 20.11 odbyły się ćwiczenia obrony powietrznej. Nie doszło do naruszenia przestrzeni powietrznej.",
}
FACTCHECK = {
    "url": "https://demagog.org.pl/fake_news/drony-nad-polska/",
    "source_id": "demagog-fake-news",
    "source_type": "fact_check",
    "title": "Drony nad Polską? Sprawdzamy nagranie",
    "publisher": "Stowarzyszenie Demagog",
    "published_at": "2026-11-21",
    "rating": "Fałsz",
    "links": [MON["url"]],
    "text": "Nagranie krążące w sieci pochodzi z 2022 roku i nie pokazuje wydarzeń z 20 listopada 2026.",
}


def fake_vector(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    raw = [((seed[i % 32] + i * 7) % 251) / 251.0 + 0.01 for i in range(1024)]
    norm = math.sqrt(sum(v * v for v in raw))
    return [v / norm for v in raw]


def ollama(request: httpx.Request) -> httpx.Response:
    body = __import__("json").loads(request.content)
    assert body["model"] == "bge-m3"
    return httpx.Response(200, json={"embeddings": [fake_vector(t) for t in body["input"]]})


@pytest.fixture(scope="module")
def client():
    os.environ["NEO4J_URI"] = TEST_URI
    from fastapi.testclient import TestClient

    from context_graph.api import app
    from context_graph.config import get_settings

    get_settings.cache_clear()
    with TestClient(app) as c:
        c.app.state.graph.write("MATCH (n) DETACH DELETE n")
        yield c


def graph(client):
    return client.app.state.graph


def run(client, name: str, **params):
    return graph(client).read(PIPELINE_QUERIES[name], **params)


def test_scraper_payload_without_embeddings(client):
    res = client.post("/ingest/document", json=RCB).json()
    assert res["chunks"] >= 2 and res["embedded"] == 0
    doc = graph(client).read("MATCH (d:Document {id: $id}) RETURN d", id=res["id"])[0]["d"]
    assert doc["fetched_at"].year == 2026 and doc["published_at"].day == 20
    assert doc["catalog_type"] == "official" and doc["source_type"] == "primary"
    assert doc["catalog_id"] == "rcb-komunikaty" and doc["scraper_hash"] == "abc123"
    assert doc["dup_group"] == res["id"]
    assert run(client, "corpus_version") == []


def test_full_text_is_kept_verbatim(client):
    rows = graph(client).read(
        "MATCH (c:Chunk)-[:PART_OF]->(:Document {url: $url}) WHERE c.section = 'tresc' "
        "RETURN c.text AS t ORDER BY c.position",
        url=RCB["url"],
    )
    assert " ".join(r["t"] for r in rows) == RCB["text"]


def test_same_domain_different_publishers_stay_apart(client):
    client.post("/ingest/document", json=MON)
    rows = graph(client).read(
        "MATCH (d:Document)-[:PUBLISHED_BY]->(s:Source) WHERE d.url IN $urls RETURN d.url AS url, s.publisher AS p",
        urls=[RCB["url"], MON["url"]],
    )
    assert {r["url"]: r["p"] for r in rows} == {
        RCB["url"]: "Rządowe Centrum Bezpieczeństwa",
        MON["url"]: "Ministerstwo Obrony Narodowej",
    }


def test_pipeline_sparse_query_returns_contract_columns(client):
    rows = run(client, "sparse", fulltext_index="chunk_text", lucene="przestr* OR powiet*", k=10)
    assert rows
    assert set(rows[0]) == CHUNK_COLUMNS
    assert all(r["published_at"] and r["fetched_at"] and r["publisher"] and r["source_type"] for r in rows)


def test_rating_and_cites_for_factcheck(client):
    res = client.post("/ingest/document", json=FACTCHECK).json()
    assert run(client, "rating", doc_id=res["id"]) == [{"rating": "Fałsz"}]
    cited = run(client, "cites", doc_id=res["id"], limit=5)
    assert [c["url"] for c in cited] == [MON["url"]]
    assert cited[0]["publisher"] == "Ministerstwo Obrony Narodowej"


def test_dates_keep_the_year(client):
    client.post("/ingest/document", json=OLD_NEWS)
    rows = graph(client).read(
        "MATCH (c:Chunk)-[m:MENTIONS]->(e:Entity {type: 'DATE'}) "
        "MATCH (c)-[:PART_OF]->(d:Document {url: $url}) RETURN e.key AS key, m.year_inferred AS inferred",
        url=OLD_NEWS["url"],
    )
    assert {"key": "date:2025-11-20", "inferred": True} in rows

    res = client.post("/context", json={"text": "Co się stało 20.11 w przestrzeni powietrznej?", "save": False}).json()
    assert next(e for e in res["entities"] if e["type"] == "DATE")["found"] is True
    years = {i["published_at"][:4] for i in res["items"] if i["published_at"]}
    assert {"2025", "2026"} <= years


def test_stats(client):
    stats = client.get("/stats").json()
    assert stats["documents"] == 4
    assert stats["by_source_type"] == {"primary": 3, "factcheck": 1}
    assert stats["documents_without_published_at"] == 0
    assert stats["chunks_embedded"] == 0


def test_backfill_without_embedder_reports_it(client):
    res = client.post("/embeddings/backfill").json()
    assert res["embedded"] == 0 and res["error"] == "embeddings disabled" and res["remaining"] > 0


def test_embeddings_and_pipeline_dense_query(client):
    client.app.state.embedder = OllamaEmbedder(Settings(), transport=httpx.MockTransport(ollama))
    res = client.post("/embeddings/backfill").json()
    assert res["embedded"] > 0 and res["remaining"] == 0 and res["model"] == "bge-m3"
    assert run(client, "corpus_version") == [{"id": "current", "embedding_model": "bge-m3", "consistent": True}]

    target = graph(client).read(
        "MATCH (c:Chunk)-[:PART_OF]->(:Document {url: $url}) WHERE c.section = 'tresc' RETURN c.text AS t LIMIT 1",
        url=MON["url"],
    )[0]["t"]
    rows = run(client, "dense", vector_index="chunk_embedding", k=3, qvec=fake_vector(target))
    assert set(rows[0]) == CHUNK_COLUMNS
    assert rows[0]["text"] == target and rows[0]["score"] > 0.99

    changed = {**MON, "text": MON["text"] + " Sprawę wyjaśnia prokuratura."}
    out = client.post("/ingest/document", json=changed).json()
    assert out["embedded"] >= 1
    assert graph(client).read("MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS n")[0]["n"] == 0
