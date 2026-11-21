"""Step 6: answer composition. Template is authoritative; LLM only adds 2-3 sentences."""

from __future__ import annotations

import re

from prawdowid.llm import LLMError, is_mock
from prawdowid.llm import usage
from prawdowid.models import Claim, Verdict

_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_supported(explanation: str, verdict: Verdict) -> bool:
    """Every number/date in the LLM explanation must appear in the cited quotes or premises."""
    allowed = " ".join([*(s.quote for s in verdict.evidence), *verdict.reasons])
    return set(_NUMBERS.findall(explanation)) <= set(_NUMBERS.findall(allowed))


def _explain(claim: Claim, verdict: Verdict, llm_params: dict) -> str | None:
    if is_mock(llm_params["roles"]["compose"]) or not verdict.evidence:
        return None
    from prawdowid.llm.tasks import explain_llm

    try:
        text = explain_llm(claim, verdict, llm_params)
    except LLMError as e:
        usage.record(role="compose", event="fallback_template", reason=str(e))
        return None
    if not numbers_supported(text, verdict):
        usage.record(role="compose", event="explanation_rejected", reason="unsupported numbers")
        return None
    return text

LABELS = {
    "potwierdzone": "Potwierdzone",
    "falszywe": "Fałszywe",
    "wprowadzajace_w_blad": "Wprowadzające w błąd",
    "brak_dowodow": "Brak wystarczających dowodów",
    "nie_podlega_weryfikacji": "Nie podlega weryfikacji",
}


def compose_answer(claims: list[Claim], verdicts: list[Verdict], llm_params: dict) -> str:
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
        explanation = _explain(by_id[v.claim_id], v, llm_params)
        if explanation:
            lines.append("Interpretacja systemu: " + explanation)
        if v.warnings:
            lines.append("Uwaga: " + " ".join(v.warnings))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def assemble(norm_input, claims: list[Claim], verdicts: list[Verdict], answer: str) -> dict:
    """Final node: collects free outputs (Kedro releases intermediates during a run)."""
    limitations = []
    if norm_input.injection_suspected:
        limitations.append("Wykryto możliwą próbę manipulacji poleceniami; treść potraktowano jako dane.")
    if norm_input.pii_masked:
        limitations.append("Dane osobowe zostały zamaskowane przed analizą.")
    return {"claims": claims, "verdicts": verdicts, "answer": answer, "limitations": limitations}
