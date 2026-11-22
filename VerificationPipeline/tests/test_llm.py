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


def explain(*sentences):
    """sentences: (text, [source numbers])"""
    return {"sentences": [{"text": t, "sources": src} for t, src in sentences]}


QUOTE = "pismo w sprawie Czech nie pochodzi z MSZ"


def stance(label, quote):
    return {"items": [{"source_id": 1, "label": label, "quote": quote, "rationale": "r"}]}


def run(params, **scripted):
    registry.override("fake", MockProvider(script(**scripted), model="fake-model"))
    return verify("MSZ wysłało pismo do Czech?", params=params)


def test_refuting_primary_source_gives_false_with_explanation(params):
    r = run(params, claims=CLAIMS, stance=stance("refutes", "pismo w sprawie Czech nie pochodzi z MSZ"),
            explain=explain(("Komunikat MSZ zaprzecza, że pismo pochodzi z MSZ.", [1])))
    assert r.status == "ok"
    v = r.verdicts[0]
    assert v.label == "falszywe" and v.evidence[0].chunk_id == "k1"
    src = v.sources[0]
    assert (src.n, src.role, src.publisher) == (1, "zaprzecza", "MSZ")
    assert src.url == "https://www.gov.pl/web/dyplomacja/x" and src.quote == QUOTE
    assert "https://www.gov.pl/web/dyplomacja/x" in r.answer and f"„{QUOTE}”" in r.answer
    assert r.answer.startswith("Fałsz. Komunikat MSZ zaprzecza, że pismo pochodzi z MSZ. [1]")


def test_fabricated_quote_is_dropped(params):
    r = run(params, claims=CLAIMS, stance=stance("supports", "MSZ potwierdza żądania wobec Czech"),
            explain=explain(("x", [1])))
    assert r.verdicts[0].label == "brak_dowodow"


def test_explanation_with_unsupported_number_falls_back_to_template(params):
    r = run(params, claims=CLAIMS, stance=stance("refutes", "pismo w sprawie Czech nie pochodzi z MSZ"),
            explain=explain(("W 2019 roku 5 ambasad to potwierdziło.", [1])))
    assert "2019" not in r.answer
    events = [c.get("event") for t in r.trace for c in t.get("calls", [])]
    assert "explanation_rejected" in events


def test_llm_calls_recorded_in_trace(params):
    r = run(params, claims=CLAIMS, stance=stance("irrelevant", "x"), explain=explain(("x", [1])))
    calls = {t["step"]: t["calls"] for t in r.trace if "calls" in t}
    assert calls["extract_claims"][0]["model"] == "fake-model"
    assert calls["extract_claims"][0]["prompt_version"] == "0.4"
    assert "judge_stance" in calls


def test_claims_llm_failure_falls_back_to_whole_text(params):
    r = run(params, claims=LLMError("timeout"), stance=stance("irrelevant", "x"))
    assert r.status == "ok" and len(r.claims) == 1
    events = [c.get("event") for t in r.trace for c in t.get("calls", [])]
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


def test_broken_stance_item_is_dropped_not_whole_answer():
    out = StanceOut.model_validate_json(
        '{"items": [{"source_id": 1, "label": "refutes", "quote": "Pismo nie pochodzi z MSZ", "rationale": "r"},'
        ' {"label": "supports", "quote": "x", "rationale": "brak source_id"},'
        ' {"source_id": 2, "label": "supports", "quote": null, "rationale": "quote null"}]}')
    assert [it.source_id for it in out.items] == [1]


def test_env_overrides_shrink_context(monkeypatch):
    monkeypatch.setenv("PRAWDOWID_RETRIEVAL_TOP_K", "5")
    monkeypatch.setenv("PRAWDOWID_MAX_CONTEXT_CHARS", "5500")
    load_parameters.cache_clear()
    try:
        r = load_parameters()["retrieval"]
        assert (r["top_k"], r["max_context_chars"]) == (5, 5500)
    finally:
        load_parameters.cache_clear()


def test_nli_ignores_inherited_llm_model_name():
    from prawdowid.llm.nli_provider import DEFAULT_MODEL, NLIStanceProvider

    p = NLIStanceProvider({"provider": "nli", "model": "anthropic/claude-opus-5-5"})
    assert p.model_name == DEFAULT_MODEL


@pytest.mark.parametrize("bad", [
    explain(("Pismo jest fałszywe.", [])),                       # sentence without a source
    explain(("Pismo jest fałszywe.", [7])),                      # source the model was not given
    explain(("Pismo jest fałszywe.", [1]), ("Tak twierdzi też PAP.", [])),
])
def test_explanation_must_cite_given_sources(params, bad):
    r = run(params, claims=CLAIMS, stance=stance("refutes", QUOTE), explain=bad)
    assert r.verdicts[0].label == "falszywe"
    assert r.answer.startswith("Fałsz. ")                   # template "why", LLM text rejected
    assert "Pismo jest fałszywe." not in r.answer
    assert "[1] MSZ: „Komunikat”" in r.answer and "zaprzecza" in r.answer
    events = [c.get("event") for t in r.trace for c in t.get("calls", [])]
    assert "explanation_rejected" in events


def test_publication_date_of_cited_source_is_allowed_in_explanation(params):
    r = run(params, claims=CLAIMS, stance=stance("refutes", QUOTE),
            explain=explain(("Komunikat MSZ z 2026-11-01 zaprzecza pismu.", [1])))
    assert r.answer.startswith("Fałsz. Komunikat MSZ z 2026-11-01 zaprzecza pismu. [1]")


def test_no_evidence_says_no_sources(params):
    r = run(params, claims=CLAIMS, stance=stance("irrelevant", "x"), explain=explain(("x", [1])))
    v = r.verdicts[0]
    assert v.label == "brak_dowodow" and v.sources == []
    assert v.lead == "Nie znaleziono informacji."
    assert r.answer.startswith("Nie znaleziono informacji.")


def test_question_gets_tak_nie_lead(params):
    q = {"claims": [{**CLAIMS["claims"][0], "is_question": True}]}
    r = run(params, claims=q, stance=stance("refutes", QUOTE),
            explain=explain(("Komunikat MSZ zaprzecza, że pismo pochodzi z MSZ.", [1])))
    assert r.verdicts[0].lead == "Nie." and r.answer.startswith("Nie. Komunikat MSZ")


def test_rating_claim_without_rating_quote_is_rejected(params):
    r = run(params, claims=CLAIMS, stance=stance("refutes", QUOTE),
            explain=explain(("MSZ ocenił to twierdzenie jako fałsz.", [1])))
    assert "ocenił" not in r.answer.splitlines()[0]
    events = [c.get("reason") for t in r.trace for c in t.get("calls", [])]
    assert "claims a rating without a cited rating label" in events


def test_opinion_lead():
    from prawdowid.runtime import verify
    r = verify("Moim zdaniem polityka MSZ jest najgorsza.")
    assert r.verdicts[0].lead == "Niemożliwe do weryfikacji."
    assert r.answer.startswith("Niemożliwe do weryfikacji.")


def test_no_claims_from_long_post_falls_back_to_whole_text(params):
    """Small models sometimes return no claims for an emotional post that does carry a fact."""
    from prawdowid.llm import usage
    from prawdowid.pipelines.verification.nodes.claims import extract_claims
    from prawdowid.pipelines.verification.nodes.normalize import normalize

    registry.override("fake", MockProvider(script(claims={"claims": []}), model="fake-model"))
    usage.start()
    post = "Wow! Wszyscy mówią, że policjanci pod Trybunałem dostają 3000 zł za każdą dobę służby!"
    claims = extract_claims(normalize(post, "web"), params["llm"])
    assert len(claims) == 1 and claims[0].type == "factual"
    assert any(c.get("event") == "fallback_no_claims" for c in usage.drain())
    # a greeting stays without claims (-> "doprecyzuj")
    assert extract_claims(normalize("Hej, co słychać?", "web"), params["llm"]) == []
    registry.clear_overrides()
