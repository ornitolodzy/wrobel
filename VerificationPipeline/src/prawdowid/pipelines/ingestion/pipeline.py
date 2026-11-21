from kedro.pipeline import Pipeline, node, pipeline

from .nodes import chunk_documents, crawl, dedup, embed_and_store, load_source_catalog, report


def create_pipeline(**kwargs) -> Pipeline:
    return pipeline(
        [
            node(load_source_catalog, "params:ingestion", "seeds", name="load_sources"),
            node(crawl, ["seeds", "params:ingestion"], "crawled", name="crawl"),
            node(dedup, "crawled", "documents", name="dedup"),
            node(chunk_documents, ["documents", "params:ingestion"], "chunks", name="chunk"),
            node(embed_and_store, ["documents", "chunks", "params:ingestion", "params:embeddings"],
                 "store_stats", name="embed_store"),
            node(report, ["crawled", "documents", "store_stats", "params:ingestion"],
                 "ingestion_report", name="report"),
        ]
    )
