"""Step 1: claim extraction (LLM -> JSON). Rule-based fallback when mocked or the LLM fails."""

from __future__ import annotations

import re

from prawdowid.llm import ApiLimitError, LLMError, LLMRefusal, is_mock
from prawdowid.llm import usage
from prawdowid.models import Claim, NormalizedInput

_OPINION = re.compile(r"\b(uważam|myślę|moim zdaniem|sądzę|najlepsz|najgorsz)\w*", re.I)
_PREDICTION = re.compile(r"\b(będzie|wkrótce|w przyszłym|za rok)\b", re.I)


_QUESTION = re.compile(r"^\s*czy\s+", re.I)


def question_to_claim(text: str) -> tuple[str, bool]:
    """'Czy PAP usunęła depeszę?' -> ('PAP usunęła depeszę.', True). Negation is kept as asked,
    so the verdict on the claim answers the question directly (Tak/Nie)."""
    t = text.strip()
    if not (t.endswith("?") or _QUESTION.match(t)):
        return t, False
    t = _QUESTION.sub("", t).rstrip("?").strip()
    return (t[:1].upper() + t[1:] + "." if t else text.strip()), True


def _rule_based(text: str) -> list[Claim]:
    text, is_question = question_to_claim(text)
    if _OPINION.search(text):
        ctype = "opinion"
    elif _PREDICTION.search(text):
        ctype = "prediction"
    else:
        ctype = "factual"
    return [Claim(id="c1", text=text, type=ctype, is_question=is_question)]


def extract_claims(norm_input: NormalizedInput, llm_params: dict) -> list[Claim]:
    text = norm_input.text.strip()
    if not text:
        return []
    if is_mock(llm_params["roles"]["claims"]):
        return _rule_based(text)

    from prawdowid.llm.tasks import extract_claims_llm

    try:
        claims = extract_claims_llm(text, llm_params)
        # Small models sometimes return nothing for an emotional post that does carry a fact
        # (eval: 26 of 88 realistic posts). A longer text then becomes one claim, classified by the
        # rules (an opinion stays an opinion); greetings and short texts still get "doprecyzuj".
        min_words = llm_params.get("fallback_min_words", 8)
        if not claims and len(text.split()) >= min_words:
            usage.record(role="claims", event="fallback_no_claims", words=len(text.split()))
            return _rule_based(text)
        return claims
    except (ApiLimitError, LLMRefusal):
        raise
    except LLMError as e:
        # Spec: on LLM failure the whole text becomes one claim, logged in the trace.
        usage.record(role="claims", event="fallback_rule_based", reason=str(e))
        return _rule_based(text)
