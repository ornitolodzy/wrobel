"""Write side: documents (PageScrapper), images (ImageRecognitionService) and external relations.

Schema follows the Verification Pipeline contract (docs/neo4j-kontrakt.md there):
(:Chunk)-[:PART_OF]->(:Document)-[:PUBLISHED_BY]->(:Source).

Every write uses MERGE and deterministic ids, so re-ingesting the same input is idempotent.
Every relationship carries `method` and `created_at`; interpretive ones (SUPPORTS/CONTRADICTS/...)
also carry a verbatim `quote` that must appear in the source text (ADR 0002 in VerificationPipeline).
Nothing from the scraper payload is dropped: full text lives in the chunks, every field on the Document.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from itertools import combinations
from urllib.parse import urlsplit, urlunsplit

from neo4j import ManagedTransaction

from context_graph.chunking import chunk_text
from context_graph.config import Settings
from context_graph.db import Graph
from context_graph.embeddings import Embedder, EmbeddingError
from context_graph.extract import EntityExtractor
from context_graph.models import DocumentIn, EmbeddingBackfillResult, ImageIn, IngestResult, RelationIn
from context_graph.textnorm import content_hash, normalize_quote, short_hash

logger = logging.getLogger("context_graph.ingest")

CHUNK_RELATIONS = {"SUPPORTS", "CONTRADICTS"}
DOCUMENT_RELATIONS = {"CORRECTS", "CITES", "REPRINTS"}
CORPUS_VERSION_ID = "current"


class NotFoundError(LookupError):
    pass


class QuoteError(ValueError):
    pass


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def document_id(url_or_id: str) -> str:
    return short_hash(canonical_url(url_or_id)) if "://" in url_or_id else url_or_id


page_id = document_id


def _utc(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day, tzinfo=UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def source_key(domain: str, publisher: str) -> str:
    return short_hash(f"{domain}|{publisher.strip().lower()}")


# --------------------------------------------------------------------------- chunks


def _build_chunks(
    owner_id: str,
    parts: list[tuple[str, str]],
    max_chars: int,
    extractor: EntityExtractor,
    ref_date: date | None,
) -> tuple[list[dict], list[list[str]], list[dict], list[list[str]]]:
    """parts: [(section, text)] -> chunk rows, NEXT pairs, MENTIONS rows, CO_OCCURS key pairs."""
    chunks: list[dict] = []
    position = 0
    for section, text in parts:
        if section in ("naglowek", "ocena"):
            if text.strip():
                suffix = "h" if section == "naglowek" else "r"
                chunks.append({"id": f"{owner_id}:{suffix}", "text": text.strip(), "section": section, "position": 0})
            continue
        for piece in chunk_text(text, max_chars):
            position += 1
            chunks.append({"id": f"{owner_id}:{position}", "text": piece, "section": section, "position": position})

    ordered = [c for c in chunks if c["section"] == "naglowek"] + sorted(
        (c for c in chunks if c["section"] in ("tresc", "ocr")), key=lambda c: c["position"]
    )
    next_pairs = [[a["id"], b["id"]] for a, b in zip(ordered, ordered[1:], strict=False)]

    mentions: list[dict] = []
    pairs: set[tuple[str, str]] = set()
    for c in chunks:
        ents = extractor.extract(c["text"], ref_date=ref_date)
        for e in ents:
            month_day = e.key[-5:] if e.type == "DATE" else None
            year = int(e.key[5:9]) if e.type == "DATE" and not e.key.startswith("date:--") else None
            mentions.append(
                {
                    "chunk": c["id"],
                    "key": e.key,
                    "name": e.name,
                    "type": e.type,
                    "month_day": month_day,
                    "year": year,
                    "year_inferred": e.year_inferred,
                }
            )
        keys = sorted({e.key for e in ents})
        pairs.update(combinations(keys, 2))
    return chunks, next_pairs, mentions, [list(p) for p in sorted(pairs)]


def _write_chunks(
    tx: ManagedTransaction,
    owner_label: str,
    owner_id: str,
    chunks: list[dict],
    next_pairs: list[list[str]],
    mentions: list[dict],
    pairs: list[list[str]],
    method: str,
) -> None:
    if owner_label == "Document":
        owned = "(c:Chunk)-[:PART_OF]->(o:Document {id: $owner})"
        link = "MERGE (c)-[r:PART_OF]->(o)"
    else:
        owned = "(o:Image {id: $owner})-[:HAS_TEXT]->(c:Chunk)"
        link = "MERGE (o)-[r:HAS_TEXT]->(c)"
    ids = [c["id"] for c in chunks]
    tx.run(f"MATCH {owned} WHERE c.owner = $owner AND NOT c.id IN $ids DETACH DELETE c", owner=owner_id, ids=ids)
    tx.run("UNWIND $ids AS id MATCH (c:Chunk {id: id})-[r:MENTIONS|NEXT]->() DELETE r", ids=ids)
    tx.run(
        "UNWIND $chunks AS ch MATCH (c:Chunk {id: ch.id}) WHERE c.text <> ch.text REMOVE c.embedding, c.embedded_at",
        chunks=chunks,
    )
    tx.run(
        f"""
        MATCH (o:{owner_label} {{id: $owner}})
        UNWIND $chunks AS ch
        MERGE (c:Chunk {{id: ch.id}})
          ON CREATE SET c.created_at = datetime()
        SET c.text = ch.text, c.section = ch.section, c.position = ch.position, c.owner = $owner
        {link}
          ON CREATE SET r.method = 'ingest', r.created_at = datetime()
        """,
        owner=owner_id,
        chunks=chunks,
    )
    tx.run(
        """
        UNWIND $pairs AS pr
        MATCH (a:Chunk {id: pr[0]}), (b:Chunk {id: pr[1]})
        MERGE (a)-[r:NEXT]->(b)
          ON CREATE SET r.method = 'ingest', r.created_at = datetime()
        """,
        pairs=next_pairs,
    )
    tx.run(
        """
        UNWIND $mentions AS m
        MATCH (c:Chunk {id: m.chunk})
        MERGE (e:Entity {key: m.key})
          ON CREATE SET e.name = m.name, e.type = m.type, e.created_at = datetime()
        SET e.month_day = coalesce(m.month_day, e.month_day), e.year = coalesce(m.year, e.year)
        MERGE (c)-[r:MENTIONS]->(e)
          ON CREATE SET r.method = $method, r.created_at = datetime()
        SET r.surface = m.name, r.year_inferred = m.year_inferred
        """,
        mentions=mentions,
        method=method,
    )
    tx.run(
        """
        UNWIND $pairs AS pr
        MATCH (a:Entity {key: pr[0]}), (b:Entity {key: pr[1]})
        MERGE (a)-[r:CO_OCCURS]->(b)
          ON CREATE SET r.owners = [], r.method = 'ingest', r.created_at = datetime()
        SET r.owners = CASE WHEN $owner IN r.owners THEN r.owners ELSE r.owners + $owner END
        SET r.weight = size(r.owners)
        """,
        pairs=pairs,
        owner=owner_id,
    )


# --------------------------------------------------------------------------- embeddings


PENDING = """
MATCH (c:Chunk) WHERE c.embedding IS NULL AND ($ids IS NULL OR c.id IN $ids)
RETURN c.id AS id, c.text AS text ORDER BY c.id LIMIT $limit
"""


def embed_pending(
    graph: Graph, embedder: Embedder, ids: list[str] | None = None, limit: int = 500
) -> EmbeddingBackfillResult:
    if embedder.model is None:
        remaining = graph.read("MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS n")[0]["n"]
        return EmbeddingBackfillResult(embedded=0, remaining=remaining, model=None, error="embeddings disabled")
    rows = graph.read(PENDING, ids=ids, limit=limit)
    error = None
    embedded = 0
    if rows:
        try:
            vectors = embedder.embed([r["text"] for r in rows])
        except EmbeddingError as exc:
            logger.warning("Embedding failed: %s", exc)
            vectors, error = [], str(exc)
        if vectors:
            graph.write(
                """
                UNWIND $rows AS row
                MATCH (c:Chunk {id: row.id})
                CALL db.create.setNodeVectorProperty(c, 'embedding', row.vec)
                SET c.embedded_at = datetime(), c.embedding_model = $model
                """,
                rows=[{"id": r["id"], "vec": v} for r, v in zip(rows, vectors, strict=True)],
                model=embedder.model,
            )
            graph.write(
                """
                MERGE (v:CorpusVersion {id: $id})
                  ON CREATE SET v.created_at = datetime()
                SET v.embedding_model = $model, v.updated_at = datetime()
                """,
                id=CORPUS_VERSION_ID,
                model=embedder.model,
            )
            embedded = len(vectors)
    remaining = graph.read("MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS n")[0]["n"]
    return EmbeddingBackfillResult(embedded=embedded, remaining=remaining, model=embedder.model, error=error)


# --------------------------------------------------------------------------- documents


def upsert_document(
    graph: Graph,
    doc: DocumentIn,
    extractor: EntityExtractor,
    settings: Settings,
    embedder: Embedder | None = None,
) -> IngestResult:
    did = document_id(doc.url)
    url = canonical_url(doc.url)
    domain = doc.domain or urlsplit(url).netloc
    publisher = (doc.publisher or "").strip() or domain
    published = _utc(doc.published_at)
    text_hash = content_hash(doc.text)
    parts: list[tuple[str, str]] = []
    if doc.title:
        parts.append(("naglowek", doc.title))
    if doc.rating:
        parts.append(("ocena", doc.rating))
    parts.append(("tresc", doc.text))
    ref_date = published.date() if published else None
    chunks, next_pairs, mentions, pairs = _build_chunks(did, parts, settings.chunk_max_chars, extractor, ref_date)

    def targets(urls: list[str]) -> list[dict]:
        return [{"id": document_id(u), "url": canonical_url(u)} for u in dict.fromkeys(urls) if document_id(u) != did]

    cites, corrects = targets(doc.links), targets(doc.corrects)

    def work(tx: ManagedTransaction) -> dict[str, int]:
        tx.run(
            """
            MERGE (s:Source {id: $sid})
              ON CREATE SET s.created_at = datetime(), s.catalog_ids = []
            SET s.publisher = $publisher, s.domain = $domain, s.type = $type, s.catalog_type = $catalog_type,
                s.license = coalesce($license, s.license),
                s.catalog_ids = CASE WHEN $catalog_id IS NULL OR $catalog_id IN s.catalog_ids THEN s.catalog_ids
                                     ELSE s.catalog_ids + $catalog_id END
            MERGE (d:Document {id: $id})
              ON CREATE SET d.created_at = datetime()
            SET d.url = $url, d.title = $title, d.published_at = $published_at,
                d.fetched_at = coalesce($fetched_at, datetime()), d.text_hash = $text_hash,
                d.scraper_hash = $scraper_hash, d.language = $language, d.rating = $rating,
                d.source_type = $type, d.catalog_type = $catalog_type, d.catalog_id = $catalog_id,
                d.license = $license, d.stub = false, d.updated_at = datetime()
            WITH s, d
            OPTIONAL MATCH (d)-[old:PUBLISHED_BY]->(other:Source) WHERE other <> s
            DELETE old
            MERGE (d)-[r:PUBLISHED_BY]->(s)
              ON CREATE SET r.method = 'ingest', r.created_at = datetime()
            """,
            sid=source_key(domain, publisher),
            publisher=publisher,
            domain=domain,
            type=settings.contract_type(doc.source_type),
            catalog_type=doc.source_type,
            catalog_id=doc.source_id,
            license=doc.license,
            id=did,
            url=url,
            title=doc.title or None,
            published_at=published,
            fetched_at=_utc(doc.fetched_at),
            text_hash=text_hash,
            scraper_hash=doc.content_hash,
            language=doc.language,
            rating=doc.rating,
        )
        _write_chunks(tx, "Document", did, chunks, next_pairs, mentions, pairs, method="regex")

        counts: dict[str, int] = {}
        for rel, rows, method in (("CITES", cites, "link"), ("CORRECTS", corrects, "declared")):
            res = tx.run(
                f"""
                MATCH (d:Document {{id: $id}})
                UNWIND $targets AS t
                MERGE (q:Document {{id: t.id}})
                  ON CREATE SET q.url = t.url, q.stub = true, q.created_at = datetime()
                MERGE (d)-[r:{rel}]->(q)
                  ON CREATE SET r.method = $method, r.created_at = datetime()
                RETURN count(r) AS n
                """,
                id=did,
                targets=rows,
                method=method,
            ).single()
            counts[rel] = res["n"] if res else 0

        tx.run(
            "MATCH (d:Document {id: $id})-[r:REPRINTS]-(o:Document) WHERE o.text_hash <> $hash DELETE r",
            id=did,
            hash=text_hash,
        )
        dups = tx.run(
            """
            MATCH (o:Document {text_hash: $hash}) WHERE NOT coalesce(o.stub, false)
            RETURN o.id AS id, coalesce(o.published_at, o.created_at) AS t
            ORDER BY t ASC, id ASC
            """,
            hash=text_hash,
        ).data()
        original = dups[0]["id"] if dups else did
        copies = [d["id"] for d in dups[1:]]
        tx.run(
            "MATCH (o:Document {text_hash: $hash}) WHERE NOT coalesce(o.stub, false) SET o.dup_group = $original",
            hash=text_hash,
            original=original,
        )
        counts["REPRINTS"] = 0
        if copies:
            tx.run(
                """
                MATCH (o:Document {id: $original})
                UNWIND $copies AS cid
                MATCH (c:Document {id: cid})
                OPTIONAL MATCH (c)-[old:REPRINTS]->(x:Document) WHERE x <> o
                DELETE old
                MERGE (c)-[r:REPRINTS]->(o)
                  ON CREATE SET r.method = 'content_hash', r.created_at = datetime()
                """,
                original=original,
                copies=copies,
            )
            counts["REPRINTS"] = len(copies)
        return counts

    counts = graph.transaction(work)
    embedded = 0
    if embedder is not None and embedder.model is not None:
        embedded = embed_pending(graph, embedder, ids=[c["id"] for c in chunks], limit=len(chunks)).embedded
    return IngestResult(
        id=did,
        chunks=len(chunks),
        entities=len({m["key"] for m in mentions}),
        embedded=embedded,
        relations=counts,
    )


upsert_page = upsert_document


# --------------------------------------------------------------------------- images


def upsert_image(
    graph: Graph,
    image: ImageIn,
    extractor: EntityExtractor,
    settings: Settings,
    embedder: Embedder | None = None,
) -> IngestResult:
    iid = f"img-{short_hash(image.image_id)}"
    chunks, next_pairs, mentions, pairs = _build_chunks(
        iid, [("ocr", image.ocr_text)], settings.chunk_max_chars, extractor, None
    )
    did = document_id(image.page_url) if image.page_url else None

    def work(tx: ManagedTransaction) -> dict[str, int]:
        tx.run(
            """
            MERGE (i:Image {id: $id})
              ON CREATE SET i.created_at = datetime()
            SET i.source_id = $source_id, i.path = $path, i.url = $url
            """,
            id=iid,
            source_id=image.image_id,
            path=image.path,
            url=image.url,
        )
        _write_chunks(tx, "Image", iid, chunks, next_pairs, mentions, pairs, method="regex+ocr")
        if not did:
            return {"HAS_IMAGE": 0}
        tx.run(
            """
            MATCH (i:Image {id: $id})
            MERGE (d:Document {id: $did})
              ON CREATE SET d.url = $page_url, d.stub = true, d.created_at = datetime()
            MERGE (d)-[r:HAS_IMAGE]->(i)
              ON CREATE SET r.method = 'ingest', r.created_at = datetime()
            WITH d, i
            MATCH (i)-[:HAS_TEXT]->(c:Chunk)
            MERGE (c)-[p:PART_OF]->(d)
              ON CREATE SET p.method = 'ocr', p.created_at = datetime()
            """,
            id=iid,
            did=did,
            page_url=canonical_url(image.page_url or ""),
        )
        return {"HAS_IMAGE": 1}

    counts = graph.transaction(work)
    embedded = 0
    if embedder is not None and embedder.model is not None:
        embedded = embed_pending(graph, embedder, ids=[c["id"] for c in chunks], limit=len(chunks)).embedded
    return IngestResult(
        id=iid,
        chunks=len(chunks),
        entities=len({m["key"] for m in mentions}),
        embedded=embedded,
        relations=counts,
    )


# --------------------------------------------------------------------------- relations


def add_relation(graph: Graph, rel: RelationIn) -> dict:
    quote = normalize_quote(rel.quote)
    if not quote:
        raise QuoteError("quote is required")

    if rel.type in CHUNK_RELATIONS:
        src, dst = rel.src, rel.dst
        rows = graph.read("MATCH (a:Chunk {id: $src}), (b:Chunk {id: $dst}) RETURN a.text AS text", src=src, dst=dst)
        label = "Chunk"
    else:
        src, dst = document_id(rel.src), document_id(rel.dst)
        rows = graph.read(
            """
            MATCH (a:Document {id: $src}), (b:Document {id: $dst})
            OPTIONAL MATCH (c:Chunk)-[:PART_OF]->(a)
            WITH a, c ORDER BY c.position
            WITH a, collect(c.text) AS texts
            RETURN coalesce(a.title, '') + ' ' + reduce(s = '', t IN texts | s + ' ' + t) AS text
            """,
            src=src,
            dst=dst,
        )
        label = "Document"
    if not rows:
        raise NotFoundError(f"{label} {rel.src!r} or {rel.dst!r} not found")
    if quote not in normalize_quote(rows[0]["text"] or ""):
        raise QuoteError("quote does not appear verbatim in the source node text")

    graph.write(
        f"""
        MATCH (a:{label} {{id: $src}}), (b:{label} {{id: $dst}})
        MERGE (a)-[r:{rel.type}]->(b)
          ON CREATE SET r.created_at = datetime()
        SET r.quote = $quote, r.method = $method, r.model = $model, r.confidence = $confidence
        """,
        src=src,
        dst=dst,
        quote=rel.quote,
        method=rel.method,
        model=rel.model,
        confidence=rel.confidence,
    )
    return {"type": rel.type, "src": src, "dst": dst}
