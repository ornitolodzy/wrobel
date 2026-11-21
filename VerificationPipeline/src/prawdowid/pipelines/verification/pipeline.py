"""Verification DAG. Branching (opinion skip, early stop) lives in data, not topology."""

from kedro.pipeline import Pipeline, node, pipeline

from .nodes import (
    assemble,
    build_queries,
    compose_answer,
    decide_all,
    extract_claims,
    fetch_web_evidence,
    judge_stance,
    merge_evidence,
    normalize,
    retrieve_rerank,
    validate_citations,
    web_search,
)


def create_pipeline(**kwargs) -> Pipeline:
    return pipeline(
        [
            node(normalize, ["raw_input", "channel"], "norm_input", name="normalize"),
            node(extract_claims, ["norm_input", "params:llm"], "claims", name="extract_claims"),
            node(build_queries, "claims", "queries", name="build_queries"),
            node(retrieve_rerank, ["queries", "params:retrieval"], "evidence_corpus",
                 name="retrieve_rerank"),
            node(web_search, ["queries", "params:web_search"], "web_results", name="web_search"),
            node(fetch_web_evidence, ["web_results", "queries", "params:web_fetch"], "web_evidence",
                 name="fetch_web_evidence"),
            node(merge_evidence, ["evidence_corpus", "web_evidence", "params:retrieval"], "evidence",
                 name="merge_evidence"),
            node(judge_stance, ["claims", "evidence", "params:llm"], "stances_raw", name="judge_stance"),
            node(
                validate_citations,
                ["stances_raw", "evidence", "params:citations"],
                "stances",
                name="validate_citations",
            ),
            node(
                decide_all,
                ["claims", "stances", "evidence", "params:policy"],
                "verdicts",
                name="decide",
            ),
            node(
                compose_answer,
                ["claims", "verdicts", "web_results", "params:llm"],
                "answer",
                name="compose",
            ),
            node(
                assemble,
                ["norm_input", "claims", "verdicts", "web_results", "answer"],
                "result",
                name="assemble",
            ),
        ]
    )
