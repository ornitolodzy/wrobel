"""Verification DAG. Context comes only from the knowledge base (no web access, ADR 0005).
Branching (opinion skip, early stop) lives in data, not topology."""

from kedro.pipeline import Pipeline, node, pipeline

from .nodes import (
    assemble,
    build_queries,
    compose_answer,
    decide_all,
    extract_claims,
    judge_stance,
    select_context,
    normalize,
    retrieve_rerank,
    validate_citations,
)


def create_pipeline(**kwargs) -> Pipeline:
    return pipeline(
        [
            node(normalize, ["raw_input", "channel"], "norm_input", name="normalize"),
            node(extract_claims, ["norm_input", "params:llm"], "claims", name="extract_claims"),
            node(build_queries, "claims", "queries", name="build_queries"),
            node(retrieve_rerank, ["queries", "params:retrieval", "params:embeddings"],
                 "candidates", name="retrieve"),
            node(select_context, ["candidates", "queries", "params:retrieval", "params:reranker"],
                 "evidence", name="select_context"),
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
                ["claims", "verdicts", "params:llm"],
                "answer",
                name="compose",
            ),
            node(
                assemble,
                ["norm_input", "claims", "verdicts", "answer"],
                "result",
                name="assemble",
            ),
        ]
    )
