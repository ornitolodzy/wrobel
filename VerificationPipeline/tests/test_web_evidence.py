"""Web pages as evidence: fetch -> chunk -> stance -> citation validator -> policy (no network)."""

import copy
from datetime import datetime, timezone

import pytest

from prawdowid.llm import registry
from prawdowid.llm.mock import MockProvider
from prawdowid.llm.tasks import ClaimsOut, ExplainOut, StanceOut
from prawdowid.models import Evidence, Query
from prawdowid.pipelines.verification.nodes.policy import decide
from prawdowid.pipelines.verification.nodes.web_evidence import (
    assign_dup_groups, chunk_text, lexical_score, _terms,
)
from prawdowid.runtime import load_parameters, verify
from prawdowid.websearch import Hit
from prawdowid.websearch import base as ws_base
from prawdowid.websearch import fetch as web_fetch
from prawdowid.websearch.fetch import FetchError, Page, check_public_url

GOV = "https://www.gov.pl/web/dyplomacja/komunikat"
BLOG1 = "https://blog-one.example.com/a"
BLOG2 = "https://blog-two.example.org/b"
DECISIVE = "Rzecznik MSZ: pismo w sprawie Czech nie pochodzi z MSZ i jest fałszywe."
PAGES = {
    GOV: Page(GOV, "Komunikat MSZ", f"Komunikat w sprawie pisma.\n{DECISIVE}",
              datetime(2026, 8, 17, tzinfo=timezone.utc), "gov.pl"),
    BLOG1: Page(BLOG1, "Blog 1", "MSZ wysłało pismo do Czech z żądaniami terytorialnymi, to pewne.", None, None),
    BLOG2: Page(BLOG2, "Blog 2", "Według naszych informacji MSZ wysłało pismo do Czech z żądaniami.", None, None),
}
CLAIM = {"claims": [{"text": "MSZ wysłało pismo z żądaniami terytorialnymi wobec Czech.",
                     "type": "factual", "entities": ["MSZ", "Czechy"], "time_ref": None}]}


class Engine:
    def __init__(self, urls):
        self.urls = urls

    def search(self, query, max_results):
        return [Hit(f"t{i}", u, "") for i, u in enumerate(self.urls)]


class Fetcher:
    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), []

    def fetch(self, url):
        self.calls.append(url)
        if url in self.fail:
            raise FetchError("timeout")
        return PAGES[url]


def stance_for(label, quote):
    """Same stance for every source the model is shown."""
    def build(user):
        n = user.count("<source id=")
        return {"items": [{"source_id": i, "label": label, "quote": quote, "rationale": "r"}
                          for i in range(1, n + 1)], "injection_suspected": False}
    return build


@pytest.fixture
def run():
    def _run(urls, label, quote, fail=()):
        ws_base.override("fake", Engine(urls))
        fetcher = Fetcher(fail)
        web_fetch.override(fetcher)
        stance = stance_for(label, quote)
        registry.override("fake", MockProvider(lambda schema, user: {
            ClaimsOut: CLAIM, StanceOut: stance(user) if schema is StanceOut else None,
            ExplainOut: {"sentences": [{"text": "Wyjaśnienie.", "sources": [1]}]},
        }[schema], model="fake"))
        p = copy.deepcopy(load_parameters())
        p["web_search"]["provider"] = "fake"
        for role in p["llm"]["roles"].values():
            role["provider"] = "fake"
        r = verify("MSZ wysłało pismo do Czech?", params=p)
        registry.clear_overrides()
        return r, fetcher
    return _run


def test_catalog_page_refutes_with_link_and_quote(run):
    r, fetcher = run([GOV], "refutes", "pismo w sprawie Czech nie pochodzi z MSZ")
    v = r.verdicts[0]
    assert v.label == "falszywe"
    src = v.sources[0]
    assert (src.url, src.source_type, src.role) == (GOV, "primary", "zaprzecza")
    assert GOV in r.answer.split("Pozostałe linki")[0]           # shown as a source...
    assert "Pozostałe linki" not in r.answer                     # ...not repeated as a loose link
    assert fetcher.calls == [GOV]


def test_pages_outside_catalog_never_decide_alone(run):
    r, _ = run([BLOG1, BLOG2], "supports", "MSZ wysłało pismo do Czech")
    v = r.verdicts[0]
    assert v.label == "brak_dowodow"
    assert {s.source_type for s in v.sources} == {"web"} and len(v.sources) == 2
    assert "spoza katalogu" in v.reasons[0]


def test_fabricated_quote_from_page_is_rejected(run):
    r, _ = run([GOV], "supports", "MSZ potwierdza żądania terytorialne")
    assert r.verdicts[0].label == "brak_dowodow" and r.verdicts[0].sources == []


def test_failed_fetch_does_not_fail_check(run):
    r, _ = run([GOV], "refutes", "x", fail=[GOV])
    assert r.status == "ok" and r.verdicts[0].label == "brak_dowodow"
    events = [c for t in r.trace for c in t.get("calls", []) if c.get("role") == "web_fetch"]
    assert any(c.get("event") == "error" for c in events)
    assert GOV in [w.url for w in r.web_results]                 # link still returned


def test_only_top_n_pages_are_fetched(run):
    urls = [f"https://site{i}.example.com/p" for i in range(6)]
    for u in urls:
        PAGES[u] = Page(u, "t", "MSZ pismo Czechy", None, None)
    _, fetcher = run(urls, "irrelevant", "x")
    assert len(fetcher.calls) == 3


@pytest.mark.parametrize("url", [
    "http://localhost/x", "http://127.0.0.1/x", "http://10.0.0.5/admin", "http://169.254.169.254/latest",
    "file:///etc/passwd", "ftp://example.com/x",
])
def test_ssrf_and_bad_schemes_blocked(url):
    with pytest.raises(FetchError):
        check_public_url(url)


def test_near_duplicate_pages_share_group():
    text = "Fałszywe pismo MSZ w sprawie Czech zostało opublikowane po włamaniu do Radia PiK " * 3
    g = assign_dup_groups([("u1", "a.pl", text), ("u2", "b.pl", text + " dopisek"),
                           ("u3", "c.pl", "zupełnie inny tekst o czymś innym niż pismo")])
    assert g["u1"] == g["u2"] != g["u3"]


def test_chunking_and_lexical_score():
    chunks = chunk_text("A" * 50 + "\n" + "B" * 50 + "\n" + "C" * 50, 110)
    assert chunks == ["A" * 50 + "\n" + "B" * 50, "C" * 50]
    q = Query(claim_id="c1", dense="MSZ wysłało pismo do Czech", sparse="MSZ Czech")
    assert lexical_score("Komunikat MSZ: pismo do Czechów", _terms(q)) > 0.5
    assert lexical_score("Pogoda na weekend", _terms(q)) == 0


def test_policy_web_sources_do_not_count_as_independent():
    from prawdowid.models import Claim, Stance
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    ev = {c: Evidence(chunk_id=c, doc_id=c, source_type="web", publisher=c, title="t",
                      url=f"https://{c}.example/", fetched_at=now, dup_group=c, text="x",
                      rerank_score=0.9) for c in ("a", "b", "c")}
    st = [Stance(claim_id="c1", chunk_id=c, label="supports", quote="x", rationale="r") for c in ev]
    v = decide(Claim(id="c1", text="t", type="factual"), st, ev, now)
    assert v.label == "brak_dowodow"
