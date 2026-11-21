"""ADR 0003 + 0005: a fact-checker's rating and the material it CITES come from the knowledge
graph (Neo4j, other team), not from fetching pages.

Question: "Czy PAP usunęła depeszę o wypowiedzi X?" -> "Nie. Demagog ocenił ... jako fałsz: ..."
plus the PAP dispatch (CITES relation) with its headline: existence in the base, not content.
"""

import copy
from datetime import datetime, timezone

import pytest

from prawdowid.llm import registry
from prawdowid.llm.mock import MockProvider
from prawdowid.llm.tasks import ClaimsOut, ExplainOut, StanceOut
from prawdowid.pipelines.verification.nodes.claims import question_to_claim
from prawdowid.retrieval import base as retrieval_base
from prawdowid.retrieval.neo4j import Neo4jRetriever
from prawdowid.runtime import load_parameters, verify

DEMAGOG = "https://demagog.org.pl/fake_news/pap-usunela-depesze-o-wypowiedzi/"
PAP = "https://www.pap.pl/aktualnosci/wypowiedz-ministra-depesza"
REASON = "depesza była nadal dostępna w niezmienionej formie"
T = datetime(2026, 11, 3, tzinfo=timezone.utc)
DEMAGOG_CHUNK = {
    "chunk_id": "dem:0", "text": f"W sieci krąży twierdzenie, że PAP usunęła depeszę. Sprawdziliśmy: {REASON} na stronie agencji.",
    "section": "tresc", "position": 0, "doc_id": "dem", "url": DEMAGOG,
    "title": "PAP usunęła depeszę o wypowiedzi ministra? To nieprawda", "published_at": T,
    "fetched_at": T, "dup_group": "dem", "publisher": "Demagog", "source_type": "factcheck", "score": 0.9,
}


class FakeGraph(Neo4jRetriever):
    """Neo4jRetriever with the database replaced by canned query results."""

    def __init__(self, params):
        super().__init__(params)
        self.calls = []

    def run(self, query, **params):
        self.calls.append(query.split()[0:3])
        if "vector.queryNodes" in query or "fulltext.queryNodes" in query:
            return [DEMAGOG_CHUNK]
        if "d.rating" in query:
            return [{"rating": "Fałsz"}] if params["doc_id"] == "dem" else []
        if "[:CITES]" in query:
            return [{"doc_id": "pap", "url": PAP, "title": "Minister: wypowiedź w sprawie depeszy",
                     "published_at": T, "fetched_at": T, "dup_group": "pap", "publisher": "PAP",
                     "source_type": "secondary"}] if params["doc_id"] == "dem" else []
        if "CorpusVersion" in query:
            return [{"id": 7, "embedding_model": "mock"}]
        return []


def stance(user):
    items = []
    for block in user.split("<source ")[1:]:
        sid = int(block.split('id="')[1].split('"')[0])
        if 'publisher="Demagog"' in block and 'section="ocena"' in block:
            items.append({"source_id": sid, "label": "refutes", "quote": "Fałsz", "rationale": "ocena"})
        elif 'publisher="Demagog"' in block and REASON in block:
            items.append({"source_id": sid, "label": "refutes", "quote": REASON, "rationale": "dostępna"})
        else:
            items.append({"source_id": sid, "label": "irrelevant", "quote": "", "rationale": ""})
    return {"items": items, "injection_suspected": False}


@pytest.fixture
def result():
    p = copy.deepcopy(load_parameters())
    p["retrieval"]["backend"] = "fake-graph"
    p["embeddings"] = {"provider": "mock", "dim": 8}
    graph = FakeGraph({**p["retrieval"], "embeddings": p["embeddings"]})
    retrieval_base.override("fake-graph", graph)
    claim = {"claims": [{"text": "PAP usunęła depeszę o wypowiedzi ministra.", "type": "factual",
                         "entities": ["PAP"], "time_ref": None, "is_question": True}]}
    registry.override("fake", MockProvider(lambda schema, user: {
        ClaimsOut: lambda: claim,
        StanceOut: lambda: stance(user),
        ExplainOut: lambda: {"sentences": [{"text": "Demagog ocenił twierdzenie o usunięciu depeszy "
                                                    f"jako fałsz: {REASON}.", "sources": [1]}]},
    }[schema](), model="fake"))
    for role in p["llm"]["roles"].values():
        role["provider"] = "fake"
    r = verify("Czy PAP usunęła depeszę o wypowiedzi ministra?", params=p)
    registry.clear_overrides()
    return r, graph


def test_answer_matches_expected_format(result):
    r, _ = result
    first = r.answer.splitlines()[0]
    assert first == f"Nie. Demagog ocenił twierdzenie o usunięciu depeszy jako fałsz: {REASON}. [1]"
    assert r.verdicts[0].label == "falszywe" and r.verdicts[0].lead == "Nie."


def test_rating_comes_from_graph(result):
    r, _ = result
    dem = r.verdicts[0].sources[0]
    assert (dem.n, dem.url, dem.source_type) == (1, DEMAGOG, "factcheck")
    assert [(q.kind, q.text) for q in dem.quotes] == [("ocena", "Fałsz"), ("tresc", REASON)]


def test_primary_material_from_cites_relation(result):
    r, _ = result
    pap = r.verdicts[0].sources[1]
    assert (pap.role, pap.url, pap.via, pap.kind) == ("podlinkowane", PAP, 1, "naglowek")
    assert "Źródło pierwotne: PAP „Minister: wypowiedź w sprawie depeszy” [2], w bazie źródeł od 2026-11-03" in r.answer
    assert "a nie prawdziwość jego treści" in r.answer


def test_no_web_access_in_verification(result):
    r, _ = result
    steps = [t["step"] for t in r.trace]
    assert not any("web" in s for s in steps)
    roles = {c.get("role") for t in r.trace for c in t.get("calls", [])}
    assert "web_search" not in roles and "web_fetch" not in roles
    ret = next(c for t in r.trace for c in t.get("calls", []) if c.get("backend") == "neo4j")
    assert ret["corpus_version"] == 7 and ret["graph_context"] == 2


@pytest.mark.parametrize("q, claim, is_q", [
    ("Czy PAP usunęła depeszę?", "PAP usunęła depeszę.", True),
    ("czy PAP nie usunęła depeszy?", "PAP nie usunęła depeszy.", True),   # negation kept
    ("PAP usunęła depeszę.", "PAP usunęła depeszę.", False),
])
def test_question_to_claim(q, claim, is_q):
    assert question_to_claim(q) == (claim, is_q)
