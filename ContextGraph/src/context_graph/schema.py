"""Constraints and indexes. Idempotent: safe to run on every start.

Index names `chunk_text` and `chunk_embedding` are the defaults of the Verification Pipeline
(`retrieval.neo4j.fulltext_index` / `vector_index`, docs/neo4j-kontrakt.md).
"""

from __future__ import annotations

from context_graph.db import Graph

LEGACY = [
    "DROP CONSTRAINT source_domain IF EXISTS",
    "DROP CONSTRAINT page_id IF EXISTS",
    "DROP INDEX page_url IF EXISTS",
    "DROP INDEX page_hash IF EXISTS",
]


def statements(embedding_dim: int) -> list[str]:
    return LEGACY + [
        "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT image_id IF NOT EXISTS FOR (n:Image) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (n:Entity) REQUIRE n.key IS UNIQUE",
        "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT corpus_version_id IF NOT EXISTS FOR (n:CorpusVersion) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT checked_claim_id IF NOT EXISTS FOR (n:CheckedClaim) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT person_key IF NOT EXISTS FOR (n:Person) REQUIRE n.key IS UNIQUE",
        "CREATE CONSTRAINT appearance_id IF NOT EXISTS FOR (n:Appearance) REQUIRE n.id IS UNIQUE",
        "CREATE INDEX document_url IF NOT EXISTS FOR (n:Document) ON (n.url)",
        "CREATE INDEX document_hash IF NOT EXISTS FOR (n:Document) ON (n.text_hash)",
        "CREATE INDEX entity_month_day IF NOT EXISTS FOR (n:Entity) ON (n.month_day)",
        # No Polish analyzer in Neo4j: index raw lowercase tokens, the query side uses prefix stems.
        "CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (n:Chunk) ON EACH [n.text] "
        "OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}",
        "CREATE FULLTEXT INDEX entity_name IF NOT EXISTS FOR (n:Entity) ON EACH [n.name] "
        "OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}",
        f"CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (n:Chunk) ON (n.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {int(embedding_dim)}, "
        f"`vector.similarity_function`: 'cosine'}}}}",
        # Claim text alone (same model as chunks): a short post matches a short claim.
        f"CREATE VECTOR INDEX checked_claim_embedding IF NOT EXISTS FOR (n:CheckedClaim) ON (n.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {int(embedding_dim)}, "
        f"`vector.similarity_function`: 'cosine'}}}}",
    ]


def ensure_schema(graph: Graph, embedding_dim: int = 1024) -> None:
    for stmt in statements(embedding_dim):
        graph.write(stmt)
    graph.write("CALL db.awaitIndexes(60)")
