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


def st(cid, label, quote="MSZ wysłało pismo do Czech"):
    # A real stance quotes a fragment about the claim (the policy ignores off-topic quotes).
    return Stance(claim_id="c1", chunk_id=cid, label=label, quote=quote, rationale="r")


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
    assert v.sources[0].url == "https://example.org/a" and v.sources[0].quote == "MSZ wysłało pismo do Czech"


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


def test_single_factcheck_with_explicit_ruling_refutes():
    # The fact-checker states its ruling in its own text ("to fałsz", "zdementowano"): that is a ruling
    e = {"a": ev("a", "factcheck").model_copy(update={"text": "Ta informacja jest nieprawdziwa. Sprawdziliśmy."})}
    assert decide(CLAIM, [st("a", "refutes")], e, NOW).label == "falszywe"
    titled = {"a": ev("a", "factcheck").model_copy(update={"title": "Krąży nagranie. Uwaga - to fałsz"})}
    assert decide(CLAIM, [st("a", "refutes")], titled, NOW).label == "falszywe"


def test_explicit_ruling_does_not_make_a_fact_check_confirm():
    e = {"a": ev("a", "factcheck").model_copy(update={"text": "To manipulacja."})}
    assert decide(CLAIM, [st("a", "supports")], e, NOW).label == "brak_dowodow"


def test_claimed_year_not_in_confirming_sources_is_misleading():
    claim = Claim(id="c1", text="Alert o skażonej wodzie w Złotym Stoku wysłano w 2025 roku.", type="factual")
    e = {"a": ev("a", "primary")}  # published 2026, text without 2025
    q = "Alert: woda w Złotym Stoku niezdatna do picia"
    v = decide(claim, [st("a", "supports", q)], e, NOW)
    assert v.label == "wprowadzajace_w_blad" and "innego czasu" in v.reasons[0]
    # the year in the source text or publication date keeps the confirmation
    e2 = {"a": ev("a", "primary").model_copy(update={"text": "Komunikat z 2025 r."})}
    assert decide(claim, [st("a", "supports", q)], e2, NOW).label == "potwierdzone"
    claim26 = claim.model_copy(update={"text": "Alert o wodzie w Złotym Stoku wysłano w listopadzie 2026 roku."})
    assert decide(claim26, [st("a", "supports", q)], e, NOW).label == "potwierdzone"


def test_claimed_number_not_in_confirming_source_is_misleading():
    claim = Claim(id="c1", text="Opłata za brytyjską autoryzację ETA wynosi 50 funtów za osobę.", type="factual")
    q = "Opłata za wydanie ETA wynosi 20 GBP za każdą podróżującą osobę (także dzieci)."
    e = {"a": ev("a", "primary").model_copy(update={"text": q})}
    v = decide(claim, [st("a", "supports", q)], e, NOW)
    assert v.label == "wprowadzajace_w_blad" and "50" in v.reasons[0] and "20" in v.reasons[0]
    right = claim.model_copy(update={"text": "Opłata za ETA wynosi 20 funtów za osobę."})
    assert decide(right, [st("a", "supports", q)], e, NOW).label == "potwierdzone"


def test_number_formats_match():
    claim = Claim(id="c1", text="W Anglii lek na receptę kosztuje 9,90 funta, a pula wynosi 20 tys. zł.", type="factual")
    q = "Opłata ryczałtowa 9.9 GBP za lek; pula nagród 20 000 zł."
    e = {"a": ev("a", "primary").model_copy(update={"text": q})}
    assert decide(claim, [st("a", "supports", q)], e, NOW).label == "potwierdzone"


def test_source_without_numbers_is_not_compared():
    claim = Claim(id="c1", text="CERT Polska ogłosił program z pulą 1 mln zł.", type="factual")
    q = "CERT Polska ogłosił program. Pula nagród to milion złotych."
    e = {"a": ev("a", "primary").model_copy(update={"text": q})}
    assert decide(claim, [st("a", "supports", q)], e, NOW).label == "potwierdzone"


def test_day_of_month_matches_publication_date():
    claim = Claim(id="c1", text="21 listopada kierowcy mogli bezpłatnie sprawdzić światła.", type="factual")
    q = "W piątek kierowcy sprawdzą światła za darmo na 5 stacjach."
    e = {"a": ev("a", "primary", days_old=0).model_copy(update={"text": q})}  # published 21.11
    assert decide(claim, [st("a", "supports", q)], e, NOW).label == "potwierdzone"


def test_dotted_date_in_source_matches_day():
    claim = Claim(id="c1", text="8 listopada od godziny 7:00 nastąpi neutralizacja niewybuchu w Szczecinie.", type="factual")
    q = "W dniu 8.11 od godz. 7:00 neutralizacja niewybuchu w Szczecinie."
    e = {"a": ev("a", "primary", days_old=20).model_copy(update={"text": q})}
    assert decide(claim, [st("a", "supports", q)], e, NOW).label == "potwierdzone"
