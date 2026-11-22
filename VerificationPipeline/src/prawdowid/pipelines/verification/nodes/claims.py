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


# Language that marks an opinion (evaluation, taste, norm) or a forecast. Without it, an LLM label
# "opinion"/"prediction" on a statement about the world is not trusted: the claim is checked as a fact
# (at worst the answer is "brak dowodów"), instead of refusing to check it (small models label
# controversial facts, e.g. "X causes Y", as opinions).
_OPINION_MARKERS = re.compile(
    r"\b(uważam|myślę|sądzę|wydaje mi się|moim zdaniem|według mnie|lubię|kocham|nienawidzę"
    r"|najlepsz|najgorsz|najładniejsz|najpiękniejsz|piękn|brzydk|okropn|wspaniał|fataln|beznadziejn"
    r"|skandal|hańb|zdraj|głup|idiot|żenując|powinien|powinna|powinno|powinni|należy|szkoda"
    r"|świetn|kiepsk|absurd|niesprawiedliw|haniebn|oburzając|żałosn)\w*"
    r"|\b(zł[yaeą]|złego|złej|dobr[yaeą]|dobrego|dobrej)\b",
    re.IGNORECASE,
)
_FUTURE_MARKERS = re.compile(
    r"\b(będzie|będą|wkrótce|kiedyś|w przyszłości|prognoz\w*|przewiduj\w*|za \w+ lat|za dekadę"
    r"|za rok|za miesiąc|do \d{4}|zostanie|zostaną)\b",
    re.IGNORECASE,
)


# Words that say nothing about the world on their own: a "claim" made only of them ("To jest pytanie.",
# "To prawda.") is a model artefact of an unclear message, so the user is asked to be specific instead.
_FILLER = set(
    "pytanie pytania prawda prawdą prawdziwe prawdziwy informacja informacje informację wiadomość "
    "twierdzenie treść tekst rzecz sprawa temat coś ktoś wszystko chodzi wiesz możesz sprawdź "
    "sprawdzić proszę cześć jest była było były będzie tego tych które który która".split()
)


def _has_substance(text: str) -> bool:
    words = re.findall(r"[^\W\d_]+", text.lower())
    return any(len(w) >= 4 and w not in _FILLER for w in words) or any(ch.isdigit() for ch in text)


def _check_type(claim: Claim) -> Claim:
    if claim.type == "opinion" and not _OPINION_MARKERS.search(claim.text):
        usage.record(role="claims", event="type_overridden", claim_id=claim.id, llm_type="opinion")
        return claim.model_copy(update={"type": "factual"})
    if claim.type == "prediction" and not (_FUTURE_MARKERS.search(claim.text)
                                           or _OPINION_MARKERS.search(claim.text)):
        usage.record(role="claims", event="type_overridden", claim_id=claim.id, llm_type="prediction")
        return claim.model_copy(update={"type": "factual"})
    return claim


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
        checked = [_check_type(c) for c in claims if _has_substance(c.text)]
        if len(checked) < len(claims):
            usage.record(role="claims", event="dropped_empty_claims", dropped=len(claims) - len(checked))
        return checked
    except (ApiLimitError, LLMRefusal):
        raise
    except LLMError as e:
        # Spec: on LLM failure the whole text becomes one claim, logged in the trace.
        usage.record(role="claims", event="fallback_rule_based", reason=str(e))
        return _rule_based(text)
