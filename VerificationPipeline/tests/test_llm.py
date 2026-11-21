"""LLM layer end-to-end with a scripted provider and an in-memory retriever (no network)."""

import copy
from datetime import datetime, timezone

import pytest

from prawdowid.llm import ApiLimitError, LLMError, LLMRefusal, registry
from prawdowid.llm.mock import MockProvider
from prawdowid.llm.tasks import ClaimsOut, ExplainOut, StanceOut
from prawdowid.models import Evidence
from prawdowid.retrieval import base as retrieval_base
from prawdowid.runtime import load_parameters, verify

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
SOURCE_TEXT = "Rzecznik MSZ: pismo w sprawie Czech nie pochodzi z MSZ i jest fałszywe."


class FakeRetriever:
    def search(self, query, params):
        return [Evidence(
            chunk_id="k1", doc_id="d1", source_type="primary", publisher="MSZ", title="Komunikat",
            url="https://www.gov.pl/web/dyplomacja/x", published_at=NOW, fetched_at=NOW,
            dup_group="g1", text=SOURCE_TEXT, rerank_score=0.9,
        )]


def script(claims=None, stance=None, explain=None):
    """Responder per output schema; a value may be an exception to raise."""
    def responder(schema, user):
        return {ClaimsOut: claims, StanceOut: stance, ExplainOut: explain}[schema]
    return responder


@pytest.fixture
def params():
    p = copy.deepcopy(load_parameters())
    for role in p["llm"]["roles"].values():
        role["provider"] = "fake"
    p["retrieval"]["backend"] = "fake"
    retrieval_base.override("fake", FakeRetriever())
    yield p
    registry.clear_overrides()


CLAIMS = {"claims": [{"text": "MSZ wysłało pismo z żądaniami wobec Czech.", "type": "factual",
                      "entities": ["MSZ", "Czechy"]}]}


def stance(label, quote):
    return {"items": [{"source_id": 1, "label": label, "quote": quote, "rationale": "r"}]}


def run(params, **scripted):
    registry.override("fake", MockProvider(script(**scripted), model="fake-model"))
    return verify("MSZ wysłało pismo do Czech?", params=params)


def test_refuting_primary_source_gives_false_with_explanation(params):
    r = run(params, claims=CLAIMS, stance=stance("refutes", "pismo w sprawie Czech nie pochodzi z MSZ"),
            explain={"explanation": "Komunikat MSZ wskazuje, że pismo jest fałszywe."})
    assert r.status == "ok"
    v = r.verdicts[0]
    assert v.label == "falszywe" and v.evidence[0].chunk_id == "k1"
    assert "Interpretacja systemu" in r.answer


def test_fabricated_quote_is_dropped(params):
    r = run(params, claims=CLAIMS, stance=stance("supports", "MSZ potwierdza żądania wobec Czech"),
            explain={"explanation": "x"})
    assert r.verdicts[0].label == "brak_dowodow"


def test_explanation_with_unsupported_number_falls_back_to_template(params):
    r = run(params, claims=CLAIMS, stance=stance("refutes", "pismo w sprawie Czech nie pochodzi z MSZ"),
            explain={"explanation": "W 2019 roku 5 ambasad to potwierdziło."})
    assert "Interpretacja systemu" not in r.answer
    events = [c.get("event") for t in r.trace for c in t.get("llm", [])]
    assert "explanation_rejected" in events


def test_llm_calls_recorded_in_trace(params):
    r = run(params, claims=CLAIMS, stance=stance("irrelevant", "x"), explain={"explanation": "x"})
    calls = {t["step"]: t["llm"] for t in r.trace if "llm" in t}
    assert calls["extract_claims"][0]["model"] == "fake-model"
    assert calls["extract_claims"][0]["prompt_version"] == "0.2"
    assert "judge_stance" in calls


def test_claims_llm_failure_falls_back_to_whole_text(params):
    r = run(params, claims=LLMError("timeout"), stance=stance("irrelevant", "x"))
    assert r.status == "ok" and len(r.claims) == 1
    events = [c.get("event") for t in r.trace for c in t.get("llm", [])]
    assert "fallback_rule_based" in events


def test_rate_limit_is_api_limit_status_not_verdict(params):
    r = run(params, claims=CLAIMS, stance=ApiLimitError("429"))
    assert r.status == "api_limit" and r.verdicts == []


def test_refusal_is_error_status_not_verdict(params):
    r = run(params, claims=LLMRefusal("declined"))
    assert r.status == "error" and r.verdicts == []


def test_opinion_from_llm_skips_stance(params):
    r = run(params, claims={"claims": [{"text": "Polityka MSZ jest zła.", "type": "opinion"}]},
            stance=AssertionError("stance must not be called"))
    assert r.verdicts[0].label == "nie_podlega_weryfikacji"


def test_nli_ignores_inherited_llm_model_name():
    from prawdowid.llm.nli_provider import DEFAULT_MODEL, NLIStanceProvider

    p = NLIStanceProvider({"provider": "nli", "model": "anthropic/claude-opus-5-5"})
    assert p.model_name == DEFAULT_MODEL
