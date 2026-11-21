"""RAG building blocks without network or database."""

import httpx
import pytest

from prawdowid.catalog import load_catalog
from prawdowid.embeddings import EmbeddingError, MockEmbedder
from prawdowid.embeddings.ollama import OllamaEmbedder
from prawdowid.models import Query
from prawdowid.pipelines.eval.nodes import calibrate_threshold, first_relevant_rank, recall_at_k
from prawdowid.pipelines.ingestion.nodes import article_links, chunk_documents, crawl, dedup
from prawdowid.retrieval.postgres import tsquery_for
from prawdowid.text import assign_dup_groups, chunk_text
from prawdowid.websearch import fetch as web_fetch
from prawdowid.websearch.fetch import Page

CATALOG = load_catalog("conf/base/sources.yml")
RCB_SEED = {"url": "https://www.gov.pl/web/rcb/raport-dobowy3", "publisher": "RCB", "type": "primary",
            "domain": "gov.pl", "path": "/web/rcb"}
LONG = "Alert RCB dla mieszkańców Dolnego Śląska w związku z zagrożeniem powodziowym. " * 5


# ---- text ----
def test_chunk_overlap_repeats_end_of_previous_chunk():
    text = "\n".join(f"Zdanie numer {i} ma kilka słów treści. Drugie zdanie akapitu {i}." for i in range(30))
    chunks = chunk_text(text, 300, overlap=80)
    assert len(chunks) > 2 and all(len(c) <= 380 for c in chunks)
    assert chunks[1].split(".")[0] in chunks[0]          # start of chunk 2 = end of chunk 1


def test_dup_groups_are_stable_across_runs():
    pages = [("u1", "a.pl", LONG), ("u2", "b.pl", LONG + " dopisek"), ("u3", "c.pl", "inny tekst " * 20)]
    assert assign_dup_groups(pages, prefix="doc") == assign_dup_groups(pages, prefix="doc")
    g = assign_dup_groups(pages, prefix="doc")
    assert g["u1"] == g["u2"] != g["u3"]


# ---- embeddings ----
def test_mock_embedder_deterministic_normalized_and_similar():
    e = MockEmbedder({"dim": 64})
    a, b, c = e.embed(["alert RCB powódź", "alert RCB powodziowy", "prawa pasażerów kolei"])
    assert a == e.embed(["alert RCB powódź"])[0]
    assert abs(sum(x * x for x in a) - 1) < 1e-9
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))  # noqa: E731
    assert dot(a, b) > dot(a, c)


def test_ollama_embedder_batches_and_checks_dim(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json["input"])
        return httpx.Response(200, json={"embeddings": [[0.1] * 4 for _ in json["input"]]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    e = OllamaEmbedder({"dim": 4, "batch": 2, "api_base": "http://x"})
    assert len(e.embed(["a", "b", "c"])) == 3 and calls == [["a", "b"], ["c"]]
    with pytest.raises(EmbeddingError):
        OllamaEmbedder({"dim": 1024, "api_base": "http://x"}).embed(["a"])


# ---- ingestion ----
def test_article_links_same_institution_only():
    links = [
        ("https://www.gov.pl/web/rcb/alert-powodziowy-dolny-slask", "Alert powodziowy dla Dolnego Śląska"),
        ("https://www.gov.pl/web/rcb/raport-dobowy3", "Raport dobowy RCB z dzisiaj"),    # the seed itself
        ("https://www.gov.pl/web/mswia/komunikat-ministra", "Komunikat ministra spraw wewnętrznych"),  # other institution
        ("https://www.gov.pl/web/rcb/kontakt", "Kontakt z Rządowym Centrum"),            # navigation
        ("https://www.gov.pl/web/rcb/plik.pdf", "Raport w formacie PDF do pobrania"),    # file
        ("https://www.gov.pl/web/rcb/krotki", "Więcej"),                                 # short anchor
        ("https://example.com/web/rcb/x", "Obca strona z podobną ścieżką"),
    ]
    assert article_links(links, RCB_SEED, CATALOG, limit=10) == [
        "https://www.gov.pl/web/rcb/alert-powodziowy-dolny-slask"]


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def fetch(self, url):
        self.calls.append(url)
        return self.pages.get(url)


def test_crawl_one_level_with_report(monkeypatch):
    art = "https://www.gov.pl/web/rcb/alert-powodziowy-dolny-slask"
    empty = "https://www.gov.pl/web/rcb/mapa-zagrozen-aplikacja"
    pages = {
        RCB_SEED["url"]: Page(RCB_SEED["url"], "Raport dobowy", LONG, None, "gov.pl",
                              links=[(art, "Alert powodziowy dla Dolnego Śląska"),
                                     (empty, "Mapa zagrożeń - aplikacja interaktywna")]),
        art: Page(art, "Alert powodziowy", LONG + " Szczegóły alertu.", None, "gov.pl"),
        empty: Page(empty, "Mapa", "Włącz JavaScript.", None, "gov.pl"),   # JS app: too little text
    }
    fetcher = FakeFetcher(pages)
    web_fetch.override(fetcher)
    out = crawl([RCB_SEED], {"sources_path": "conf/base/sources.yml", "delay_s": 0, "min_chars": 100})
    assert [d["url"] for d in out["documents"]] == [RCB_SEED["url"], art]
    assert [d["depth"] for d in out["documents"]] == [0, 1]
    assert out["log"] == [{"url": empty, "status": "no_text"}]
    docs = dedup(out)
    assert docs[0]["dup_group"] == docs[1]["dup_group"]                  # near-identical texts
    chunks = chunk_documents(docs, {"chunk_chars": 200, "chunk_overlap": 0})
    assert chunks[0]["id"].endswith(":0") and {c["position"] for c in chunks} >= {0, 1}


# ---- retrieval ----
def test_tsquery_is_prefix_or_and_cannot_inject():
    q = Query(claim_id="c1", dense="Czechy & Polska | ! :* (granica)", sparse="Czechy")
    tsq = tsquery_for(q)
    assert tsq == "czechy:* | polska:* | granic:*"
    assert all(ch not in tsq.replace(" | ", "").replace(":*", "") for ch in "&!()|")


def test_acronyms_are_kept_in_queries():
    from prawdowid.text import query_terms
    q = Query(claim_id="c1", dense="RCB i MSZ: PAP podała, że VAT wzrósł", sparse="RCB MSZ")
    tsq = tsquery_for(q)
    assert {"rcb", "msz", "pap", "vat"} <= set(tsq.split(" | "))
    assert {"rcb", "msz", "pap", "vat"} <= query_terms(q)


# ---- eval ----
def test_retrieval_metrics_and_calibration():
    assert first_relevant_rank(["a", "b", "c"], ["c"]) == 3
    assert recall_at_k(["a", "b", "c"], ["c", "z"], 2) == 0
    rows = [{"relevant_urls": ["x"], "relevant_score": 0.8, "best_score": 0.8},
            {"relevant_urls": ["y"], "relevant_score": 0.5, "best_score": 0.6},
            {"relevant_urls": [], "relevant_score": None, "best_score": 0.2},
            {"relevant_urls": [], "relevant_score": None, "best_score": 0.45}]
    cal = calibrate_threshold(rows, current=0.3)
    assert cal["current_accuracy"] == 0.75 and cal["suggested_accuracy"] == 1.0
    assert 0.45 < cal["suggested_rerank_min"] <= 0.5
