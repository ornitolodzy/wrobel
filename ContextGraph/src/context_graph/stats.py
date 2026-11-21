"""Graph summary for audit and the demo: what is stored and how much of it is searchable."""

from __future__ import annotations

from context_graph.db import Graph


def graph_stats(graph: Graph) -> dict:
    nodes = {r["label"]: r["n"] for r in graph.read("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n")}
    rels = {r["type"]: r["n"] for r in graph.read("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n")}
    docs = graph.read(
        """
        MATCH (d:Document) WHERE NOT coalesce(d.stub, false)
        RETURN count(d) AS documents,
               count(DISTINCT d.dup_group) AS independent_documents,
               sum(CASE WHEN d.published_at IS NULL THEN 1 ELSE 0 END) AS without_published_at,
               min(d.published_at) AS oldest, max(d.published_at) AS newest
        """
    )[0]
    domains = graph.read(
        """
        MATCH (d:Document)-[:PUBLISHED_BY]->(s:Source) WHERE NOT coalesce(d.stub, false)
        RETURN s.domain AS domain, count(d) AS n ORDER BY n DESC
        """
    )
    types = graph.read(
        """
        MATCH (d:Document)-[:PUBLISHED_BY]->(s:Source) WHERE NOT coalesce(d.stub, false)
        RETURN s.type AS type, count(d) AS n ORDER BY n DESC
        """
    )
    chunks = graph.read(
        """
        MATCH (c:Chunk)
        RETURN count(c) AS chunks, sum(CASE WHEN c.embedding IS NULL THEN 0 ELSE 1 END) AS embedded
        """
    )[0]
    version = graph.read("MATCH (v:CorpusVersion) RETURN v.id AS id, v.embedding_model AS embedding_model LIMIT 1")
    return {
        "nodes": nodes,
        "relationships": rels,
        "documents": docs["documents"],
        "independent_documents": docs["independent_documents"],
        "documents_without_published_at": docs["without_published_at"],
        "oldest_published_at": docs["oldest"],
        "newest_published_at": docs["newest"],
        "domain_count": len(domains),
        "domains": {r["domain"]: r["n"] for r in domains},
        "by_source_type": {r["type"]: r["n"] for r in types},
        "chunks": chunks["chunks"],
        "chunks_embedded": chunks["embedded"],
        "corpus_version": version[0] if version else None,
    }
