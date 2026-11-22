"""Read side: context for a claim/question.

1. seed     fulltext (prefix stems) + chunks mentioning the claim's entities
2. expand   up to `hops` steps over NEXT, shared MENTIONS, CO_OCCURS, CITES, image OCR,
            SUPPORTS/CONTRADICTS and CORRECTS (the last two are always kept: they change a verdict)
3. rank     seed score decayed by relation weight per hop
4. explain  every item carries the relation path that led to it; REPRINTS collapse into dup_group
"""

from __future__ import annotations

from dataclasses import dataclass, field

from context_graph.db import Graph
from context_graph.events import events_for_chunks
from context_graph.extract import Entity, EntityExtractor
from context_graph.models import ContextItem, ContextQuery, ContextResponse, EntityRef, EventOut, RelationOut
from context_graph.textnorm import lucene_query, normalize_space, short_hash, stem

DECAY = 0.85
W_ENTITY_SEED = 0.6
W_COOCCUR = 0.35
FRONTIER = 25
# Items with these flags are returned even below the score cut (bounded extra slots).
ALWAYS_KEEP = {"corrected", "correction", "contradicted", "contradicts"}

# (relation, direction as seen from the parent chunk) -> (flag on the new chunk, flag on the parent)
FLAGS = {
    ("CONTRADICTS", "out"): ("contradicted", "contradicts"),
    ("CONTRADICTS", "in"): ("contradicts", "contradicted"),
    ("SUPPORTS", "out"): ("supported", "supports"),
    ("SUPPORTS", "in"): ("supports", "supported"),
    ("CORRECTS", "in"): ("correction", "corrected"),
    ("CORRECTS", "out"): ("corrected", "correction"),
}

SEED_FULLTEXT = """
CALL db.index.fulltext.queryNodes('chunk_text', $q, {limit: $limit}) YIELD node, score
RETURN node.id AS id, score
"""

SEED_ENTITIES = """
UNWIND $keys AS k
MATCH (e:Entity {key: k})<-[:MENTIONS]-(c:Chunk)
WITH c, collect(DISTINCT e.name) AS names
RETURN c.id AS id, names
ORDER BY size(names) DESC
LIMIT $limit
"""

SEED_COOCCUR = """
UNWIND $keys AS k
MATCH (e:Entity {key: k})-[r:CO_OCCURS]-(e2:Entity)<-[:MENTIONS]-(c:Chunk)
WHERE NOT e2.key IN $keys
RETURN c.id AS id, e.name AS via, e2.name AS other, max(r.weight) AS weight
ORDER BY weight DESC
LIMIT $limit
"""

# One hop from each frontier chunk. Each UNION branch returns (n, rel, dir, via, w).
EXPAND = """
UNWIND $ids AS cid
MATCH (c:Chunk {id: cid})
CALL (c) {
  MATCH (c)-[:NEXT]-(n:Chunk)
  RETURN n, 'NEXT' AS rel, 'none' AS dir, null AS via, 0.6 AS w
  UNION
  MATCH (c)-[r:SUPPORTS|CONTRADICTS]->(n:Chunk)
  RETURN n, type(r) AS rel, 'out' AS dir, r.quote AS via, 0.9 AS w
  UNION
  MATCH (c)<-[r:SUPPORTS|CONTRADICTS]-(n:Chunk)
  RETURN n, type(r) AS rel, 'in' AS dir, r.quote AS via, 0.9 AS w
  UNION
  MATCH (c)-[:PART_OF]->(:Document)<-[:CORRECTS]-(cp:Document)<-[:PART_OF]-(n:Chunk)
  WHERE n.position <= 1
  RETURN n, 'CORRECTS' AS rel, 'in' AS dir, cp.url AS via, 0.9 AS w
  UNION
  MATCH (c)-[:PART_OF]->(:Document)-[:CORRECTS]->(op:Document)<-[:PART_OF]-(n:Chunk)
  WHERE n.position <= 1
  RETURN n, 'CORRECTS' AS rel, 'out' AS dir, op.url AS via, 0.7 AS w
  UNION
  MATCH (c)-[:PART_OF]->(:Document)-[:CITES]->(op:Document)<-[:PART_OF]-(n:Chunk)
  WHERE n.position <= 1
  RETURN n, 'CITES' AS rel, 'out' AS dir, op.url AS via, 0.4 AS w
  LIMIT 10
  UNION
  MATCH (c)-[:PART_OF]->(:Document)-[:HAS_IMAGE]->(i:Image)-[:HAS_TEXT]->(n:Chunk)
  WHERE n <> c
  RETURN n, 'HAS_IMAGE' AS rel, 'out' AS dir, i.path AS via, 0.5 AS w
  UNION
  MATCH (c)<-[:HAS_TEXT]-(i:Image)<-[:HAS_IMAGE]-(:Document)<-[:PART_OF]-(n:Chunk)
  WHERE n.position <= 1
  RETURN n, 'HAS_IMAGE' AS rel, 'in' AS dir, i.path AS via, 0.5 AS w
  UNION
  MATCH (c)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(n:Chunk)
  WHERE n <> c
  RETURN n, 'MENTIONS' AS rel, 'none' AS dir, e.name AS via, 0.5 AS w
  LIMIT 25
}
RETURN cid AS src, n.id AS id, rel, dir, via, w
"""

DETAILS = """
UNWIND $ids AS id
MATCH (c:Chunk {id: id})
OPTIONAL MATCH (c)-[:PART_OF]->(p1:Document)
OPTIONAL MATCH (p2:Document)-[:HAS_IMAGE]->(:Image)-[:HAS_TEXT]->(c)
WITH c, coalesce(p1, p2) AS p
OPTIONAL MATCH (p)-[:PUBLISHED_BY]->(s:Source)
RETURN c.id AS id, c.text AS text, c.section AS section,
       p.id AS doc_id, p.url AS url, p.title AS title, p.published_at AS published_at,
       p.fetched_at AS fetched_at, s.publisher AS publisher, s.type AS source_type,
       coalesce(p.dup_group, p.id) AS dup_group
"""

RELATIONS_BETWEEN = """
MATCH (a:Chunk)-[r:SUPPORTS|CONTRADICTS]->(b:Chunk)
WHERE a.id IN $ids AND b.id IN $ids
RETURN type(r) AS type, a.id AS src, b.id AS dst, r.quote AS quote, r.method AS method
UNION
MATCH (a:Document)-[r:CORRECTS|REPRINTS|CITES]->(b:Document)
WHERE a.id IN $pids AND b.id IN $pids
RETURN type(r) AS type, a.url AS src, b.url AS dst, r.quote AS quote, r.method AS method
"""


@dataclass
class Candidate:
    id: str
    score: float
    hops: int
    path: list[str]
    flags: set[str] = field(default_factory=set)


def claim_entities(q: ContextQuery, extractor: EntityExtractor) -> list[Entity]:
    found: dict[str, Entity] = {e.key: e for e in extractor.extract(q.text)}
    for name in q.entities:
        ents = extractor.extract(name) or [Entity(key=" ".join(stem(w) for w in name.split()), name=name, type="MISC")]
        for e in ents:
            found.setdefault(e.key, e)
    return list(found.values())


RESOLVE_KEYS = """
UNWIND $keys AS k
OPTIONAL MATCH (e:Entity)
WHERE e.key = k OR (k STARTS WITH 'date:--' AND e.month_day = substring(k, 7))
RETURN k AS key, [x IN collect(e.key) WHERE x IS NOT NULL] AS found
"""


def resolve_keys(graph: Graph, keys: list[str]) -> dict[str, list[str]]:
    """Claim entity keys -> graph entity keys. A date without a year (date:--MM-DD) matches that day
    in every year present in the graph, so the context shows which year each source talks about."""
    if not keys:
        return {}
    return {r["key"]: r["found"] for r in graph.read(RESOLVE_KEYS, keys=keys)}


def read_context(graph: Graph, q: ContextQuery, extractor: EntityExtractor, seed_limit: int = 20) -> ContextResponse:
    ents = claim_entities(q, extractor)
    resolved = resolve_keys(graph, [e.key for e in ents])
    existing = {k for found in resolved.values() for k in found}
    entity_refs = [EntityRef(key=e.key, name=e.name, type=e.type, found=bool(resolved.get(e.key))) for e in ents]

    cands: dict[str, Candidate] = {}

    def offer(cid: str, score: float, hops: int, path: list[str], flags: set[str] | None = None) -> bool:
        cur = cands.get(cid)
        if cur is None:
            cands[cid] = Candidate(cid, score, hops, path, set(flags or ()))
            return True
        cur.flags |= flags or set()
        if score > cur.score:
            cur.score, cur.hops, cur.path = score, hops, path
            return True
        return False

    # 1. seeds ------------------------------------------------------------------------------
    seed: dict[str, tuple[float, list[str]]] = {}
    lq = lucene_query(q.text)
    if lq:
        rows = graph.read(SEED_FULLTEXT, q=lq, limit=seed_limit)
        top = max((r["score"] for r in rows), default=1.0) or 1.0
        for r in rows:
            seed[r["id"]] = (r["score"] / top, ["fulltext"])
    if existing:
        for r in graph.read(SEED_ENTITIES, keys=sorted(existing), limit=seed_limit):
            bonus = W_ENTITY_SEED * len(r["names"]) / len(existing)
            base, path = seed.get(r["id"], (0.0, []))
            seed[r["id"]] = (base + bonus, path + [f"ABOUT Entity({n}) <-MENTIONS-" for n in r["names"]])
    for cid, (score, path) in seed.items():
        offer(cid, score, 0, path)

    if existing and q.hops >= 2:
        for r in graph.read(SEED_COOCCUR, keys=sorted(existing), limit=seed_limit):
            score = W_COOCCUR * min(1.0, r["weight"] / 3)
            offer(r["id"], score, 2, [f"ABOUT Entity({r['via']}) -CO_OCCURS- Entity({r['other']}) <-MENTIONS-"])

    # 2. expansion --------------------------------------------------------------------------
    frontier = sorted(cands.values(), key=lambda c: -c.score)[:FRONTIER]
    for _ in range(q.hops):
        if not frontier:
            break
        rows = graph.read(EXPAND, ids=[c.id for c in frontier])
        changed: set[str] = set()
        for r in rows:
            parent = cands[r["src"]]
            if r["id"] == parent.id:
                continue
            new_flag, parent_flag = FLAGS.get((r["rel"], r["dir"]), (None, None))
            if parent_flag:
                parent.flags.add(parent_flag)
            step = r["rel"] if r["dir"] == "none" else (f"-{r['rel']}->" if r["dir"] == "out" else f"<-{r['rel']}-")
            if r["via"]:
                step += f" ({str(r['via'])[:80]})"
            score = parent.score * r["w"] * DECAY
            if offer(r["id"], score, parent.hops + 1, parent.path + [step], {new_flag} if new_flag else None):
                changed.add(r["id"])
        frontier = sorted((cands[i] for i in changed), key=lambda c: -c.score)[:FRONTIER]

    # 3. rank + always-keep -----------------------------------------------------------------
    ranked = sorted(cands.values(), key=lambda c: (-c.score, c.id))
    chosen = ranked[: q.limit]
    extra = [c for c in ranked[q.limit :] if c.flags & ALWAYS_KEEP][: max(1, q.limit // 2)]
    chosen += extra

    # 4. details, reprint collapse, relations ------------------------------------------------
    details = {r["id"]: r for r in graph.read(DETAILS, ids=[c.id for c in chosen])}
    items: list[ContextItem] = []
    seen: dict[tuple[str | None, str], ContextItem] = {}
    for c in chosen:
        d = details.get(c.id)
        if d is None:
            continue
        dedup_key = (d["dup_group"], normalize_space(d["text"]).lower())
        if dedup_key in seen:  # same text in a reprint: one item, not two confirmations
            seen[dedup_key].flags.append(f"reprinted_at:{d['url']}")
            continue
        item = ContextItem(
            chunk_id=c.id,
            text=d["text"],
            section=d["section"] or "tresc",
            doc_id=d["doc_id"],
            url=d["url"],
            title=d["title"],
            publisher=d["publisher"],
            source_type=d["source_type"],
            published_at=d["published_at"],
            fetched_at=d["fetched_at"],
            dup_group=d["dup_group"],
            score=round(c.score, 4),
            hops=c.hops,
            path=c.path,
            flags=sorted(c.flags),
        )
        seen[dedup_key] = item
        items.append(item)

    ids = [i.chunk_id for i in items]
    pids = sorted({i.doc_id for i in items if i.doc_id})
    relations = [RelationOut(**r) for r in graph.read(RELATIONS_BETWEEN, ids=ids, pids=pids)]

    claim_id = short_hash(normalize_space(q.text).lower())
    if q.save:
        save_claim(graph, claim_id, q.text, sorted(existing), items)
    events = [EventOut(**e) for e in events_for_chunks(graph, ids)]
    return ContextResponse(claim_id=claim_id, entities=entity_refs, items=items, relations=relations, events=events)


def save_claim(graph: Graph, claim_id: str, text: str, keys: list[str], items: list[ContextItem]) -> None:
    rows = [{"id": i.chunk_id, "score": i.score, "rank": n, "path": i.path} for n, i in enumerate(items)]

    def work(tx):
        tx.run(
            """
            MERGE (cl:Claim {id: $id})
              ON CREATE SET cl.created_at = datetime()
            SET cl.text = $text, cl.updated_at = datetime()
            WITH cl
            OPTIONAL MATCH (cl)-[old:ABOUT|CONTEXT]->()
            DELETE old
            """,
            id=claim_id,
            text=text,
        )
        tx.run(
            """
            MATCH (cl:Claim {id: $id})
            UNWIND $keys AS k
            MATCH (e:Entity {key: k})
            MERGE (cl)-[r:ABOUT]->(e)
              ON CREATE SET r.method = 'extract', r.created_at = datetime()
            """,
            id=claim_id,
            keys=keys,
        )
        tx.run(
            """
            MATCH (cl:Claim {id: $id})
            UNWIND $rows AS it
            MATCH (c:Chunk {id: it.id})
            CREATE (cl)-[:CONTEXT {score: it.score, rank: it.rank, path: it.path,
                                   method: 'read_context', created_at: datetime()}]->(c)
            """,
            id=claim_id,
            rows=rows,
        )

    graph.transaction(work)


ENTITY = """
MATCH (e:Entity)
WHERE e.key IN $keys OR toLower(e.name) = toLower($name)
WITH e LIMIT 1
OPTIONAL MATCH (e)-[r:CO_OCCURS]-(o:Entity)
WITH e, o, r ORDER BY r.weight DESC
WITH e, [x IN collect({key: o.key, name: o.name, type: o.type, weight: r.weight}) WHERE x.key IS NOT NULL][..20]
     AS related
OPTIONAL MATCH (e)<-[:MENTIONS]-(c:Chunk)
OPTIONAL MATCH (c)-[:PART_OF]->(p:Document)
WITH e, related, [x IN collect({chunk_id: c.id, text: c.text, url: p.url}) WHERE x.chunk_id IS NOT NULL][..20]
     AS chunks
RETURN e.key AS key, e.name AS name, e.type AS type, related, chunks
"""


def entity_neighbourhood(graph: Graph, name: str, extractor: EntityExtractor) -> dict | None:
    extracted = [e.key for e in extractor.extract(name)]
    resolved = [k for found in resolve_keys(graph, extracted).values() for k in found]
    keys = extracted + resolved + [" ".join(stem(w) for w in name.split()), name.lower()]
    rows = graph.read(ENTITY, keys=keys, name=name)
    return rows[0] if rows else None
