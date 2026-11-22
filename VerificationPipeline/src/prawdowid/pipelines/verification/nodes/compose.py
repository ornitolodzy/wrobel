"""Step 6: answer composition.

Format (per claim):
    <Lead> <1-2 sentences why, each with [n]>
    [n] <publisher>: „<title>” (date, source type), what it says / its rating
        <url>
    Źródło pierwotne: <publisher> „<headline>” [n], w bazie źródeł od <date>. ...
    Przesłanki / siła / uwagi

The template is authoritative (lead, sources, premises). The LLM only writes the "why" sentences,
each tied to source numbers, and the quote kinds decide what it may claim (ADR 0003).
"""

from __future__ import annotations

import re

from prawdowid.llm import LLMError, is_mock
from prawdowid.llm import usage
from prawdowid.models import Claim, SourceRef, Verdict

_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")
_RATED = re.compile(r"\bocen", re.I)  # "ocenił", "ocena", "oceniła"...
_LEAD_WORDS = re.compile(r"^\s*(tak|nie|prawda|fałsz)\s*[.,:!-]+\s*", re.I)

SOURCE_TYPE_PL = {
    "primary": "źródło pierwotne/urzędowe",
    "factcheck": "fact-checking",
    "secondary": "źródło wtórne (media)",
    "web": "strona spoza katalogu, niezweryfikowany wydawca",
}
ROLE_PL = {
    "potwierdza": "potwierdza",
    "zaprzecza": "zaprzecza",
    "brak_kontekstu": "brak kontekstu",
    "zapowiedz": "tylko zapowiedź, nie potwierdza, że to się wydarzyło",
    "podlinkowane": "materiał, do którego odsyła",
}


def _numbers(text: str) -> set[str]:
    return set(_NUMBERS.findall(text))


def check_explanation(sentences, verdict: Verdict) -> str | None:
    """Accept the LLM's sentences only if each cites sources it was given, its numbers/dates come
    from those sources, and a claimed rating ("ocenił") rests on a rating quote. Returns the
    rejection reason or None."""
    refs = {r.n: r for r in verdict.sources}
    if not sentences:
        return "empty explanation"
    for s in sentences:
        if not s.sources:
            return f"sentence without source: {s.text!r}"
        if unknown := [n for n in s.sources if n not in refs]:
            return f"unknown source numbers {unknown}"
        cited = [refs[n] for n in s.sources]
        allowed = set().union(*(_numbers(q.text) for r in cited for q in r.quotes))
        for r in cited:
            if r.published_at:
                allowed |= _numbers(f"{r.published_at:%Y-%m-%d %d.%m.%Y %Y}")
        if extra := _numbers(s.text) - allowed:
            return f"numbers not in cited sources: {sorted(extra)}"
        if _RATED.search(s.text) and not any(q.kind == "ocena" for r in cited for q in r.quotes):
            return "claims a rating without a cited rating label"
    return None


def _explain(claim: Claim, verdict: Verdict, llm_params: dict) -> str | None:
    if is_mock(llm_params["roles"]["compose"]) or not verdict.sources:
        return None
    from prawdowid.llm.tasks import explain_llm

    try:
        sentences = explain_llm(claim, verdict, llm_params)
    except LLMError as e:
        usage.record(role="compose", event="fallback_template", reason=str(e))
        return None
    if reason := check_explanation(sentences, verdict):
        usage.record(role="compose", event="explanation_rejected", reason=reason)
        return None
    return " ".join(
        _LEAD_WORDS.sub("", s.text).rstrip() + " " + "".join(f"[{n}]" for n in dict.fromkeys(s.sources))
        for s in sentences
    )


NOT_FOUND_IS_NOT_PROOF = "Brak wyniku w bazie nie oznacza, że to nieprawda."


def template_why(verdict: Verdict, claim: Claim | None = None) -> str:
    """Deterministic 'why' when there is no (accepted) LLM explanation."""
    if verdict.label == "nie_podlega_weryfikacji":
        return verdict.reasons[0]
    if not verdict.sources:
        needed = (claim.needed_source or "").strip().rstrip(".") if claim else ""
        parts = ["Nie udało mi się znaleźć źródeł, które to rozstrzygają."]
        if needed:
            first = needed.split()[0]
            if not first.isupper():  # skrót (MSZ, GIS) zostaje wielką literą
                needed = needed[0].lower() + needed[1:]
            parts.append(f"Potrzebne byłoby źródło takie jak: {needed}.")
        parts.append(NOT_FOUND_IS_NOT_PROOF)
        return " ".join(parts)
    for r in verdict.sources:
        rating = next((q for q in r.quotes if q.kind == "ocena"), None)
        if rating:
            text = next((q for q in r.quotes if q.kind == "tresc"), None)
            why = f"{r.publisher} ocenił to twierdzenie jako „{rating.text}”"
            return why + (f": „{text.text}” [{r.n}]" if text else f" [{r.n}].")
    return " ".join(verdict.reasons)


def render_source(r: SourceRef) -> list[str]:
    date = f"{r.published_at:%Y-%m-%d}, " if r.published_at else ""
    via = f", podlinkowane przez [{r.via}]" if r.via else ""
    lines = [f"[{r.n}] {r.publisher}: „{r.title}” ({date}{SOURCE_TYPE_PL[r.source_type]}{via})"
             f" · {ROLE_PL[r.role]}", f"    {r.url}"]
    for q in r.quotes:
        label = {"ocena": "ocena", "tresc": "cytat", "naglowek": "nagłówek"}[q.kind]
        lines.append(f"    {label}: „{q.text}”")
    return lines


def render_primary(r: SourceRef) -> list[str]:
    """Primary material a cited source links to (graph relation): existence, not content.
    We do not fetch anything during a check, so availability is as of the knowledge base."""
    when = f"{r.fetched_at:%Y-%m-%d}"
    return [
        f"Źródło pierwotne: {r.publisher} „{r.quote}” [{r.n}], w bazie źródeł od {when}. "
        f"To potwierdza istnienie materiału w chwili pobrania do bazy, a nie prawdziwość jego treści.",
        f"    {r.url}",
    ]


def compose_answer(claims: list[Claim], verdicts: list[Verdict], llm_params: dict) -> str:
    if not claims:
        return "Nie znaleziono twierdzenia do sprawdzenia. Doprecyzuj, proszę, co mamy zweryfikować."
    by_id = {c.id: c for c in claims}
    blocks = []
    for v in verdicts:
        claim = by_id[v.claim_id]
        why = _explain(claim, v, llm_params) or template_why(v, claim)
        v.explanation = why
        lines = [f"Twierdzenie: {claim.text}"] if len(claims) > 1 else []
        lines.append(f"{v.lead} {why}")
        judged = [r for r in v.sources if r.role != "podlinkowane"]
        for r in judged:
            lines += render_source(r)
        for r in v.sources:
            if r.role == "podlinkowane":
                lines += render_primary(r)
        if v.label != "nie_podlega_weryfikacji":
            lines.append(f"Przesłanki (siła: {v.strength}): " + " ".join(v.reasons))
        if v.warnings:
            lines.append("Uwaga: " + " ".join(v.warnings))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def compose(claims: list[Claim], verdicts: list[Verdict], llm_params: dict) -> tuple[str, list[Verdict]]:
    """Node: the answer text and the verdicts with their "why" (explanation) for the API."""
    answer = compose_answer(claims, verdicts, llm_params)
    return answer, verdicts


def assemble(norm_input, claims: list[Claim], verdicts: list[Verdict], answer: str) -> dict:
    """Final node: collects free outputs (Kedro releases intermediates during a run)."""
    limitations = []
    if norm_input.injection_suspected:
        limitations.append("Wykryto możliwą próbę manipulacji poleceniami; treść potraktowano jako dane.")
    if norm_input.pii_masked:
        limitations.append("Dane osobowe zostały zamaskowane przed analizą.")
    return {"claims": claims, "verdicts": verdicts, "answer": answer, "limitations": limitations}
