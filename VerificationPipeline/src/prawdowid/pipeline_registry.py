from kedro.pipeline import Pipeline

from prawdowid.pipelines import eval as eval_pipe
from prawdowid.pipelines import ingestion, verification


def register_pipelines() -> dict[str, Pipeline]:
    pipes = {
        "verification": verification.create_pipeline(),
        "ingestion": ingestion.create_pipeline(),
        "eval": eval_pipe.create_pipeline(),
    }
    pipes["__default__"] = pipes["verification"]
    return pipes
