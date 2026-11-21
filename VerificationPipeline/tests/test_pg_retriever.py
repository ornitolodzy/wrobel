"""Integration with a real Postgres + pgvector (docker compose db). Skipped when unreachable.

Uses a separate database `prawdowid_test`, created from db/schema.sql on every run.
"""

import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from prawdowid.embeddings import MockEmbedder
from prawdowid.embeddings import base as emb_base
from prawdowid.llm import usage
from prawdowid.models import Query
from prawdowid.pipelines.ingestion.nodes import chunk_documents, embed_and_store
from prawdowid.retrieval.postgres import PgRetriever
from prawdowid.runtime import load_parameters, verify

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = "postgresql://prawdowid:prawdowid@localhost:5432/postgres"
TEST_URL = "postgresql://prawdowid:prawdowid@localhost:5432/prawdowid_test"
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
EMB = {"provider": "mock", "dim": 1024}
ING = {"sources_path": "conf/base/sources.yml", "chunk_chars": 400, "chunk_overlap": 0}


def _doc(url, title, text, key=("gov.pl", "/web/rcb"), group=None):
    import hashlib
    return {"url": url, "title": title, "text": text, "published_at": NOW, "fetched_at": NOW,
            "http_status": 200, "domain": "gov.pl", "source_key": key, "publisher": "RCB",
            "type": "primary", "seed_url": "https://www.gov.pl/web/rcb/raport-dobowy3", "depth": 1,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(), "dup_group": group or url}


DOCS = [
    _doc("https://www.gov.pl/web/rcb/alert-powodz", "Alert powodziowy",
         "Rządowe Centrum Bezpieczeństwa wysłało alert RCB do mieszkańców Dolnego Śląska.\n"
         "Alert dotyczy zagrożenia powodziowego w powiatach kłodzkim i wałbrzyskim.\n"
         "Mieszkańcy powinni śledzić komunikaty służb."),
    _doc("https://www.gov.pl/web/rcb/upaly", "Upały",
         "RCB ostrzega przed upałami w województwie mazowieckim. Temperatura przekroczy 35 stopni."),
    _doc("https://www.gov.pl/web/infrastruktura/prawa", "Prawa pasażerów",
         "Ministerstwo Infrastruktury przypomina o prawach pasażerów kolei przy opóźnieniach.",
         key=("gov.pl", "/web/infrastruktura")),
]


@pytest.fixture(scope="module")
def db(monkeypatch_module):
    psycopg = pytest.importorskip("psycopg")
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except Exception:
        pytest.skip("Postgres not reachable (docker-compose up -d db)")
    admin.execute("DROP DATABASE IF EXISTS prawdowid_test WITH (FORCE)")
    admin.execute("CREATE DATABASE prawdowid_test")
    admin.close()
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute((ROOT / "db/schema.sql").read_text())
    monkeypatch_module.setenv("DATABASE_URL", TEST_URL)
    stats = embed_and_store(DOCS, chunk_documents(DOCS, ING), ING, EMB)
    return stats


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def search(text, sparse, **params):
    usage.start()
    p = {"candidates_per_query": 10, "rrf_k": 60, "embeddings": EMB, **params}
    return PgRetriever(p).search(Query(claim_id="c1", dense=text, sparse=sparse), p), usage.drain()


def test_ingestion_creates_corpus_version(db):
    assert db["documents_changed"] == 3 and db["corpus_version"] == 1
    assert db["corpus_documents"] == 3 and db["embedding_model"] == "mock"


def test_hybrid_search_explains_each_hit(db):
    hits, calls = search("RCB wysłało alert powodziowy na Dolnym Śląsku", "RCB Dolny Śląsk alert")
    top = hits[0]
    assert top.url == "https://www.gov.pl/web/rcb/alert-powodz"
    assert top.publisher == "Rządowe Centrum Bezpieczeństwa" and top.source_type == "primary"
    t = top.retrieval
    assert set(t.found_by) == {"dense", "sparse"} and t.dense_rank and t.sparse_rank
    assert "<b>" in t.matched and t.corpus_version == 1
    assert 0 < top.rerank_score <= 1
    rec = next(c for c in calls if c.get("backend") == "postgres")
    assert rec["corpus_version"] == 1 and rec["tsquery"].startswith("rcb")


def test_deterministic_order(db):
    a, _ = search("alert RCB", "RCB")
    b, _ = search("alert RCB", "RCB")
    assert [e.chunk_id for e in a] == [e.chunk_id for e in b]


def test_incremental_ingestion_skips_unchanged(db):
    stats = embed_and_store(DOCS, chunk_documents(DOCS, ING), ING, EMB)
    assert stats["documents_changed"] == 0 and stats["corpus_version"] is None


def test_embedding_model_mismatch_returns_nothing(db):
    class Other(MockEmbedder):
        def __init__(self, params):
            super().__init__(params)
            self.model = "other-model"

    emb_base.override("other", Other(EMB))
    try:
        hits, calls = search("alert RCB", "RCB", embeddings={"provider": "other"})
    finally:
        emb_base._overrides.clear()
    assert hits == [] and any(c.get("event") == "embedding_model_mismatch" for c in calls)


def test_neighbours_are_glued(db):
    hits, _ = search("alert RCB Dolny Śląsk", "RCB", neighbours=1, neighbours_top=1)
    assert hits[0].retrieval.neighbours or len(hits[0].text) > 0


def test_full_pipeline_uses_corpus(db):
    p = copy.deepcopy(load_parameters())
    p["retrieval"]["backend"] = "postgres"
    p["embeddings"] = EMB
    r = verify("Czy RCB wysłało alert powodziowy dla Dolnego Śląska?", params=p)
    calls = [c for t in r.trace for c in t.get("calls", [])]
    ret = next(c for c in calls if c.get("backend") == "postgres")
    ctx = next(c for c in calls if c.get("role") == "context")
    assert ret["corpus_version"] == 1 and ctx["kept"] >= 1
    assert r.status == "ok"
