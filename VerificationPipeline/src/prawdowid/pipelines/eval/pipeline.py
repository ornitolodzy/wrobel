from kedro.pipeline import Pipeline, node, pipeline

from .nodes import load_cases, report, run_cases


def create_pipeline(**kwargs) -> Pipeline:
    return pipeline(
        [
            node(load_cases, "params:eval", "eval_cases", name="load_cases"),
            node(run_cases, ["eval_cases", "parameters"], "eval_rows", name="run_cases"),
            node(report, ["eval_rows", "params:eval"], "eval_report", name="report"),
        ]
    )
