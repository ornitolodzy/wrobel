from kedro.pipeline import Pipeline, node, pipeline

from .nodes import (
    assign_dup_groups,
    chunk_documents,
    embed_and_store,
    fetch_documents,
    load_source_catalog,
)


def create_pipeline(**kwargs) -> Pipeline:
    return pipeline(
        [
            node(load_source_catalog, "params:ingestion", "sources", name="load_sources"),
            node(fetch_documents, "sources", "documents", name="fetch"),
            node(chunk_documents, ["documents", "params:ingestion"], "chunks_raw", name="chunk"),
            node(assign_dup_groups, "chunks_raw", "chunks", name="dedup"),
            node(embed_and_store, ["chunks", "params:retrieval"], "stored_count", name="embed_store"),
        ]
    )
