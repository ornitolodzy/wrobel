from kedro.pipeline import Pipeline, node, pipeline

from .nodes import (
    load_cases,
    load_retrieval_cases,
    report,
    retrieval_report,
    run_cases,
    run_retrieval_cases,
)


def create_pipeline(**kwargs) -> Pipeline:
    return pipeline(
        [
            node(load_cases, "params:eval", "eval_cases", name="load_cases"),
            node(run_cases, ["eval_cases", "parameters"], "eval_rows", name="run_cases"),
            node(load_retrieval_cases, "params:eval", "retrieval_cases", name="load_retrieval_cases"),
            node(run_retrieval_cases, ["retrieval_cases", "parameters"], "retrieval_rows",
                 name="run_retrieval_cases"),
            node(retrieval_report, ["retrieval_rows", "parameters"], "retrieval_metrics",
                 name="retrieval_metrics"),
            node(report, ["eval_rows", "retrieval_metrics", "params:eval"], "eval_report", name="report"),
        ]
    )
