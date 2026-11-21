"""Web search node with a scripted engine (no network)."""

import copy

import pytest

from prawdowid.runtime import load_parameters, verify
from prawdowid.websearch import Hit, WebSearchError
from prawdowid.websearch import base as ws_base

HITS = [
    Hit("Ktoś na forum", "https://forum.example.com/t/1", "plotka"),
    Hit("Czeski MSZ dementuje", "https://www.rmf24.pl/fakty/x", "dementi"),
    Hit("Komunikat MSZ", "https://www.gov.pl/web/dyplomacja/komunikat", "oficjalnie"),
    Hit("Komunikat MSZ (duplikat)", "https://www.gov.pl/web/dyplomacja/komunikat", "x"),
    Hit("Demagog: fałsz", "https://demagog.org.pl/analizy/y", "fact-check"),
    Hit("Spam", "https://ads.spam.net/z", "kup"),
]


class Engine:
    def __init__(self, result):
        self.result, self.queries = result, []

    def search(self, query, max_results):
        self.queries.append(query)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result[:max_results]


@pytest.fixture
def run():
    def _run(text, result=HITS, **ws):
        engine = Engine(result)
        ws_base.override("fake", engine)
        p = copy.deepcopy(load_parameters())
        p["web_search"].update(provider="fake", blocked_domains=["spam.net"], site_queries=["gov.pl"])
        p["web_search"].update(**ws)
        return verify(text, params=p), engine
    return _run


def test_returns_links_catalog_first_deduped_and_filtered(run):
    r, engine = run("MSZ wysłało pismo z żądaniami wobec Czech.")
    urls = [w.url for w in r.web_results]
    assert urls == [
        "https://www.gov.pl/web/dyplomacja/komunikat",    # catalog: primary
        "https://demagog.org.pl/analizy/y",               # catalog: factcheck
        "https://forum.example.com/t/1",                  # outside catalog: engine order
        "https://www.rmf24.pl/fakty/x",
    ]
    gov = r.web_results[0]
    assert (gov.rank, gov.in_catalog, gov.source_type, gov.domain) == (1, True, "primary", "gov.pl")
    assert gov.publisher == "Ministerstwo Spraw Zagranicznych"
    assert not r.web_results[2].in_catalog and r.web_results[2].source_type is None
    assert engine.queries == ["MSZ wysłało pismo z żądaniami wobec Czech.",
                              "MSZ wysłało pismo z żądaniami wobec Czech. site:gov.pl"]


def test_links_are_in_answer_but_do_not_change_verdict(run):
    r, _ = run("MSZ wysłało pismo z żądaniami wobec Czech.")
    assert r.verdicts[0].label == "brak_dowodow" and r.verdicts[0].sources == []
    assert "NIE są dowodem" in r.answer
    assert "https://www.gov.pl/web/dyplomacja/komunikat" in r.answer
    assert "[katalog: źródło pierwotne/urzędowe]" in r.answer


def test_query_is_pii_masked(run):
    _, engine = run("Jan, PESEL 90010112345, mówi że MSZ wysłało pismo do Czech.")
    assert "90010112345" not in engine.queries[0]


def test_opinion_is_not_searched(run):
    r, engine = run("Moim zdaniem MSZ jest najgorsze.")
    assert engine.queries == [] and r.web_results == []


def test_search_failure_does_not_fail_the_check(run):
    r, _ = run("MSZ wysłało pismo do Czech.", result=WebSearchError("timeout"))
    assert r.status == "ok" and r.web_results == []
    events = [c for t in r.trace for c in t.get("calls", []) if c.get("role") == "web_search"]
    assert events and events[0]["event"] == "error"


def test_disabled(run):
    r, engine = run("MSZ wysłało pismo do Czech.", enabled=False)
    assert engine.queries == [] and r.web_results == []
