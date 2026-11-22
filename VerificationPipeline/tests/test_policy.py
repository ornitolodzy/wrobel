"""Verdict policy per scenario, without any LLM call."""

from datetime import datetime, timedelta, timezone

from prawdowid.models import Claim, Evidence, Stance
from prawdowid.pipelines.verification.nodes.policy import decide

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)
CLAIM = Claim(id="c1", text="MSZ wysłało pismo do Czech.", type="factual")


def ev(cid, source_type="primary", group=None, days_old=10, publisher="gov.pl"):
    return Evidence(
        chunk_id=cid, doc_id=f"d-{cid}", source_type=source_type, publisher=publisher,
        title="t", url=f"https://example.org/{cid}", fetched_at=NOW,
        published_at=NOW - timedelta(days=days_old), dup_group=group or f"g-{cid}",
        text="tekst", rerank_score=0.9,
    )


def st(cid, label):
    return Stance(claim_id="c1", chunk_id=cid, label=label, quote="tekst", rationale="r")


def test_opinion_not_verifiable():
    v = decide(Claim(id="c1", text="x", type="opinion"), [], {}, NOW)
    assert v.label == "nie_podlega_weryfikacji"


def test_no_evidence_is_not_false():
    assert decide(CLAIM, [], {}, NOW).label == "brak_dowodow"


def test_primary_source_confirms():
    e = {"a": ev("a", "primary")}
    v = decide(CLAIM, [st("a", "supports")], e, NOW)
    assert v.label == "potwierdzone" and v.strength == "średnia"


def test_two_independent_plus_primary_is_strong():
    e = {"a": ev("a", "primary"), "b": ev("b", "secondary")}
    v = decide(CLAIM, [st("a", "supports"), st("b", "supports")], e, NOW)
    assert v.label == "potwierdzone" and v.strength == "wysoka"


def test_reprints_are_not_independent():
    e = {"a": ev("a", "secondary", group="pik"), "b": ev("b", "secondary", group="pik")}
    v = decide(CLAIM, [st("a", "supports"), st("b", "supports")], e, NOW)
    assert v.label == "brak_dowodow"


def test_factcheck_rating_refutes():
    rating = ev("a", "factcheck").model_copy(update={"section": "ocena"})
    assert decide(CLAIM, [st("a", "refutes")], {"a": rating}, NOW).label == "falszywe"


def test_single_factcheck_prose_is_not_a_ruling():
    # One fact-check's text about the same person/topic is not its verdict on this claim
    e = {"a": ev("a", "factcheck")}
    v = decide(CLAIM, [st("a", "refutes")], e, NOW)
    assert v.label == "brak_dowodow" and v.sources  # shown, but not decisive


def test_two_independent_factchecks_refute():
    e = {"a": ev("a", "factcheck"), "b": ev("b", "factcheck")}
    assert decide(CLAIM, [st("a", "refutes"), st("b", "refutes")], e, NOW).label == "falszywe"


def test_factcheck_rating_confirms():
    rating = ev("a", "factcheck").model_copy(update={"section": "ocena"})
    assert decide(CLAIM, [st("a", "supports")], {"a": rating}, NOW).label == "potwierdzone"


def test_announcement_is_not_proof_it_happened():
    e = {"a": ev("a", "primary")}
    v = decide(CLAIM, [st("a", "announced")], e, NOW)
    assert v.label == "brak_dowodow"
    assert v.lead == "Nie da się tego jednoznacznie potwierdzić."
    assert v.sources[0].role == "zapowiedz"
    assert "zapowiedzi" in v.reasons[0]


def test_missing_context_is_misleading():
    e = {"a": ev("a", "primary")}
    assert decide(CLAIM, [st("a", "missing_context")], e, NOW).label == "wprowadzajace_w_blad"


def test_conflicting_strong_sources_withhold():
    e = {"a": ev("a", "primary"), "b": ev("b", "factcheck")}
    v = decide(CLAIM, [st("a", "supports"), st("b", "refutes")], e, NOW)
    assert v.label == "brak_dowodow" and v.warnings
    # both sides are shown, numbered: confirming first, then refuting
    assert [(s.n, s.role, s.chunk_id) for s in v.sources] == [(1, "potwierdza", "a"), (2, "zaprzecza", "b")]


def test_sources_carry_url_and_quote():
    e = {"a": ev("a", "primary")}
    v = decide(CLAIM, [st("a", "supports")], e, NOW)
    assert v.sources[0].url == "https://example.org/a" and v.sources[0].quote == "tekst"


def test_archive_warning():
    e = {"a": ev("a", "primary", days_old=800)}
    v = decide(CLAIM, [st("a", "supports")], e, NOW)
    assert any("archiwalny" in w for w in v.warnings)


def test_irrelevant_ignored():
    e = {"a": ev("a", "primary")}
    assert decide(CLAIM, [st("a", "irrelevant")], e, NOW).label == "brak_dowodow"


def test_nothing_found_names_the_needed_source():
    from prawdowid.pipelines.verification.nodes.compose import template_why

    claim = CLAIM.model_copy(update={"needed_source": "Komunikat MSZ z datą wysłania pisma"})
    why = template_why(decide(claim, [], {}, NOW), claim)
    assert "Potrzebne byłoby źródło takie jak: komunikat MSZ z datą wysłania pisma." in why
    assert "Brak wyniku w bazie nie oznacza, że to nieprawda." in why
