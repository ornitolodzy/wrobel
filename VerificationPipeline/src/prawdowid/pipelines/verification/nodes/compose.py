"""Step 6: answer composition. Template is authoritative (verdict, premises, numbered sources);
the LLM only adds 2-3 sentences, each tied to source numbers."""

from __future__ import annotations

import re

from prawdowid.llm import LLMError, is_mock
from prawdowid.llm import usage
from prawdowid.models import Claim, Verdict, WebResult

_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")

SOURCE_TYPE_PL = {
    "primary": "źródło pierwotne/urzędowe",
    "factcheck": "fact-checking",
    "secondary": "źródło wtórne (media)",
    "web": "strona spoza katalogu, niezweryfikowany wydawca",
}


def _numbers(text: str) -> set[str]:
    return set(_NUMBERS.findall(text))


def check_explanation(sentences, verdict: Verdict) -> str | None:
    """Accept an LLM explanation only if every sentence cites existing sources and every
    number/date in it appears in the sources it cites. Returns rejection reason or None."""
    refs = {r.n: r for r in verdict.sources}
    if not sentences:
        return "empty explanation"
    for s in sentences:
        if not s.sources:
            return f"sentence without source: {s.text!r}"
        if unknown := [n for n in s.sources if n not in refs]:
            return f"unknown source numbers {unknown}"
        allowed = set().union(*(_numbers(refs[n].quote) for n in s.sources))
        for n in s.sources:
            if refs[n].published_at:
                allowed |= _numbers(f"{refs[n].published_at:%Y-%m-%d %d.%m.%Y %Y}")
        if extra := _numbers(s.text) - allowed:
            return f"numbers not in cited sources: {sorted(extra)}"
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
        f"{s.text.rstrip()} " + "".join(f"[{n}]" for n in dict.fromkeys(s.sources))
        for s in sentences
    )


def render_sources(verdict: Verdict) -> list[str]:
    if verdict.label == "nie_podlega_weryfikacji":
        return []
    if not verdict.sources:
        return ["Źródła: nie znaleziono źródeł, które rozstrzygają to twierdzenie."]
    lines = ["Źródła:"]
    for r in verdict.sources:
        date = f"{r.published_at:%Y-%m-%d}, " if r.published_at else ""
        lines += [
            f"[{r.n}] {r.role.replace('_', ' ')} · {r.publisher}: „{r.title}” "
            f"({date}{SOURCE_TYPE_PL[r.source_type]})",
            f"    {r.url}",
            f"    cytat: „{r.quote}”",
        ]
    return lines


LABELS = {
    "potwierdzone": "Potwierdzone",
    "falszywe": "Fałszywe",
    "wprowadzajace_w_blad": "Wprowadzające w błąd",
    "brak_dowodow": "Brak wystarczających dowodów",
    "nie_podlega_weryfikacji": "Nie podlega weryfikacji",
}


def render_web_results(results: list[WebResult], limit: int = 5) -> list[str]:
    if not results:
        return []
    lines = ["Pozostałe linki z wyszukiwania (nieocenione, NIE są dowodem w werdykcie):"]
    for r in results[:limit]:
        tag = f"[katalog: {SOURCE_TYPE_PL[r.source_type]}] " if r.in_catalog else ""
        lines.append(f"- {tag}{r.title or r.domain} ({r.domain})")
        lines.append(f"    {r.url}")
    return lines


def compose_answer(
    claims: list[Claim], verdicts: list[Verdict], web_results: list[WebResult], llm_params: dict
) -> str:
    if not claims:
        return "Nie znaleziono twierdzenia do sprawdzenia. Doprecyzuj, proszę, co mamy zweryfikować."
    by_id = {c.id: c for c in claims}
    blocks = []
    for v in verdicts:
        lines = [
            f"Twierdzenie: {by_id[v.claim_id].text}",
            f"Werdykt: {LABELS[v.label]} (siła: {v.strength})",
            "Przesłanki: " + " ".join(v.reasons),
        ]
        if v.warnings:
            lines.append("Uwaga: " + " ".join(v.warnings))
        lines += render_sources(v)
        explanation = _explain(by_id[v.claim_id], v, llm_params)
        if explanation:
            lines.append("Interpretacja systemu (na podstawie źródeł powyżej): " + explanation)
        used = {r.url for r in v.sources}
        lines += render_web_results(
            [w for w in web_results if w.claim_id == v.claim_id and w.url not in used])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def assemble(
    norm_input, claims: list[Claim], verdicts: list[Verdict], web_results: list[WebResult],
    answer: str,
) -> dict:
    """Final node: collects free outputs (Kedro releases intermediates during a run)."""
    limitations = []
    if norm_input.injection_suspected:
        limitations.append("Wykryto możliwą próbę manipulacji poleceniami; treść potraktowano jako dane.")
    if norm_input.pii_masked:
        limitations.append("Dane osobowe zostały zamaskowane przed analizą.")
    return {"claims": claims, "verdicts": verdicts, "web_results": web_results,
            "answer": answer, "limitations": limitations}
