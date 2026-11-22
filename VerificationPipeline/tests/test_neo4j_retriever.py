"""Neo4jRetriever without a database: canned query results, driver patched."""

from datetime import datetime, timezone

from prawdowid.llm import usage
from prawdowid.models import Query
from prawdowid.retrieval.neo4j import Neo4jRetriever, lucene_query

T = datetime(2026, 11, 1, tzinfo=timezone.utc)


def row(cid, score, url=None):
    return {"chunk_id": cid, "text": f"tekst {cid}", "section": "tresc", "position": 0, "doc_id": f"d{cid}",
            "url": url or f"https://www.gov.pl/web/rcb/{cid}", "title": cid, "published_at": None,
            "fetched_at": T, "dup_group": f"d{cid}", "publisher": "RCB", "source_type": "primary",
            "score": score}


class Canned(Neo4jRetriever):
    def __init__(self, dense, sparse, version=None):
        super().__init__({"embeddings": {"provider": "mock", "dim": 8}, "neo4j": {}})
        self.dense, self.sparse, self.version, self.seen = dense, sparse, version, []

    def run(self, query, **params):
        self.seen.append((query, params))
        if "vector.queryNodes" in query:
            return self.dense
        if "fulltext.queryNodes" in query:
            return self.sparse
        if "CorpusVersion" in query:
            return [self.version] if self.version else []
        return []


Q = Query(claim_id="c1", dense="RCB wydało alert powodziowy", sparse="RCB alert")


def test_rrf_fusion_keeps_both_ranks_and_is_deterministic():
    r = Canned(dense=[row("a", 0.9), row("b", 0.8)], sparse=[row("b", 7.0), row("c", 5.0)],
               version={"id": 3, "embedding_model": "mock"})
    usage.start()
    ev = r.search(Q, {"candidates_per_query": 10, "rrf_k": 60})
    assert [e.chunk_id for e in ev] == ["b", "a", "c"]          # b found by both lists
    b = ev[0].retrieval
    assert (b.found_by, b.dense_rank, b.sparse_rank, b.corpus_version) == (["dense", "sparse"], 2, 1, 3)
    assert ev[1].retrieval.dense_rank == 1 and ev[2].retrieval.sparse_rank == 2   # 1/61 > 1/62
    rec = next(c for c in usage.drain() if c.get("backend") == "neo4j")
    assert rec["dense_hits"] == 2 and rec["sparse_hits"] == 2


def test_text_corpus_version_from_graph_is_kept():
    # ContextGraph writes (:CorpusVersion {id: "current"}); the contract does not fix the id type.
    r = Canned(dense=[row("a", 0.9)], sparse=[], version={"id": "current", "embedding_model": "mock"})
    ev = r.search(Q, {})
    assert [e.chunk_id for e in ev] == ["a"] and ev[0].retrieval.corpus_version == "current"


def test_graph_being_re_embedded_returns_nothing():
    # Same model name, but ContextGraph reports vectors from two models (switch in progress).
    r = Canned(dense=[row("a", 0.9)], sparse=[], version={"id": "current", "embedding_model": "mock", "consistent": False})
    usage.start()
    assert r.search(Q, {}) == []
    assert any(c.get("event") == "embedding_model_mixed" for c in usage.drain())
    ok = Canned(dense=[row("a", 0.9)], sparse=[], version={"id": "current", "embedding_model": "mock", "consistent": True})
    assert [e.chunk_id for e in ok.search(Q, {})] == ["a"]


def test_embedding_model_mismatch_returns_nothing():
    r = Canned(dense=[row("a", 0.9)], sparse=[], version={"id": 1, "embedding_model": "bge-m3"})
    usage.start()
    assert r.search(Q, {}) == []
    assert any(c.get("event") == "embedding_model_mismatch" for c in usage.drain())


def test_lucene_query_cannot_inject_syntax():
    q = Query(claim_id="c1", dense='RCB" OR *:* AND title:(x) ~2 alert^5', sparse="")
    lq = lucene_query(q)
    assert lq == "rcb OR title* OR 2 OR alert* OR 5"
    assert not any(ch in lq for ch in '":()^~') and " AND " not in lq


def test_driver_is_read_only(monkeypatch):
    seen = {}

    class FakeDriver:
        def execute_query(self, query, parameters_=None, database_=None, routing_=None):
            seen.update(routing=routing_, params=parameters_)
            return [], None, None

    import neo4j
    monkeypatch.setattr(neo4j.GraphDatabase, "driver", staticmethod(lambda *a, **k: FakeDriver()))
    monkeypatch.setenv("NEO4J_PASSWORD", "x")
    Neo4jRetriever({"embeddings": {"provider": "mock"}, "neo4j": {}}).run("RETURN 1", a=1)
    assert seen == {"routing": "r", "params": {"a": 1}}
