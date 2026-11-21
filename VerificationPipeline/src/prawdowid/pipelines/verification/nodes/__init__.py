"""Verification steps 0-7. Pure functions: no Kedro imports here."""

from .citations import validate_citations
from .claims import extract_claims
from .compose import assemble, compose_answer
from .normalize import normalize
from .policy import decide, decide_all
from .queries import build_queries
from .retrieval import retrieve_rerank
from .stance import judge_stance
from .web_evidence import fetch_web_evidence, merge_evidence
from .web_search import web_search

__all__ = [
    "normalize",
    "extract_claims",
    "build_queries",
    "retrieve_rerank",
    "judge_stance",
    "validate_citations",
    "decide",
    "decide_all",
    "compose_answer",
    "assemble",
    "web_search",
    "fetch_web_evidence",
    "merge_evidence",
]
