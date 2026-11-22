"""Claims checked by fact-checkers: (:Document)-[:CHECKS]->(:CheckedClaim).

A separate layer from `Claim` (user questions saved by /context): these are claims a fact-checker
reviewed, with its rating, so a new post can be matched to an existing check by the claim text
(embedded on its own, not diluted in a 1200-character chunk).

Attribution rule: (:CheckedClaim)-[:ATTRIBUTED_TO]->(:Person) is written only when the
fact-checker declared the author in ClaimReview (origin claimreview / google_factcheck), never for
claims extracted by an LLM, and never for generic authors ("Użytkownicy Facebooka", "Wiele źródeł"),
which stay as `claimant_text` on the claim.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from neo4j import ManagedTransaction

from context_graph.models import ClaimIn
from context_graph.textnorm import normalize_space, short_hash

ATTRIBUTABLE_ORIGINS = {"claimreview", "google_factcheck"}

# Order matters: "nieprawda" contains "prawda", "częściowa prawda" is not "prawda".
_RATINGS = [
    ("czesciowa_prawda", r"częściow\w* prawd|partly true|half true|mixture"),
    ("nieweryfikowalne", r"nieweryfikow|unverif|unproven"),
    ("manipulacja", r"manipul|wprowadza\w* w błąd|misleading|kontekst|wyrwan|context"),
    ("falsz", r"fałsz|falsz|nieprawd|fake|false|untrue|przerobion|wygenerowan|zmyślon|fabryk"),
    ("satyra", r"satyr|satire"),
    ("prawda", r"prawd|true|correct"),
]

_GENERIC_CLAIMANT = re.compile(
    r"użytkowni|internaut|wiel[ea]\b|różn|anonim|nieznan|media społeczn|mediach społeczn|"
    r"\bsieci\b|internet|\bkont[oa]\b|profil|wpis|post[yó]|autorzy|źródł|social media|users",
    re.IGNORECASE,
)


def normalize_rating(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip().lower()
    for label, pattern in _RATINGS:
        if re.search(pattern, text):
            return label
    return "inne"


def claimant_kind(name: str | None, schema_type: str | None) -> str | None:
    """person / account / organization / generic (generic never becomes a Person node)."""
    if not name or not name.strip():
        return None
    name = name.strip()
    if name.startswith("@"):
        return "account"
    if _GENERIC_CLAIMANT.search(name):
        return "generic"
    return "organization" if (schema_type or "").lower() == "organization" else "person"


def claim_id(document_id: str, text: str) -> str:
    return short_hash(f"{document_id}|{normalize_space(text).lower()}")


def person_key(name: str) -> str:
    return short_hash(normalize_space(name).lower())


def claim_rows(document_id: str, claims: list[ClaimIn]) -> tuple[list[dict], list[dict], list[dict]]:
    """-> claim rows, APPEARED_IN rows, ATTRIBUTED_TO rows (duplicates in the input are merged)."""
    rows: dict[str, dict] = {}
    appearances: list[dict] = []
    attributions: list[dict] = []
    for claim in claims:
        text = normalize_space(claim.text)
        if not text:
            continue
        cid = claim_id(document_id, text)
        if cid in rows:
            continue
        kind = claimant_kind(claim.claimant, claim.claimant_type)
        rows[cid] = {
            "id": cid,
            "text": text,
            "rating_raw": claim.rating,
            "rating": normalize_rating(claim.rating),
            "claim_date": claim.claim_date,
            "review_date": claim.review_date,
            "origin": claim.origin,
            "claimant_text": normalize_space(claim.claimant) if claim.claimant else None,
            "claimant_kind": kind,
        }
        for url in dict.fromkeys(u.strip() for u in claim.appearances if u and u.strip()):
            appearances.append(
                {"claim": cid, "id": short_hash(url), "url": url, "domain": urlsplit(url).netloc.lower()}
            )
        if claim.origin in ATTRIBUTABLE_ORIGINS and kind in {"person", "account", "organization"}:
            name = normalize_space(claim.claimant or "")
            attributions.append(
                {"claim": cid, "key": person_key(name), "name": name, "kind": kind, "method": claim.origin}
            )
    return list(rows.values()), appearances, attributions


def write_claims(tx: ManagedTransaction, document_id: str, claims: list[ClaimIn]) -> dict[str, int]:
    """Replace the document's claims with `claims` (idempotent: same input, same graph)."""
    rows, appearances, attributions = claim_rows(document_id, claims)
    ids = [r["id"] for r in rows]
    tx.run(
        """
        MATCH (:Document {id: $did})-[:CHECKS]->(old:CheckedClaim) WHERE NOT old.id IN $ids
        DETACH DELETE old
        """,
        did=document_id,
        ids=ids,
    )
    tx.run(
        """
        MATCH (d:Document {id: $did})
        UNWIND $rows AS row
        MERGE (cc:CheckedClaim {id: row.id})
          ON CREATE SET cc.created_at = datetime()
        SET cc.text = row.text, cc.rating_raw = row.rating_raw, cc.rating = row.rating,
            cc.claim_date = row.claim_date, cc.review_date = row.review_date, cc.origin = row.origin,
            cc.claimant_text = row.claimant_text, cc.claimant_kind = row.claimant_kind,
            cc.updated_at = datetime()
        MERGE (d)-[r:CHECKS]->(cc)
          ON CREATE SET r.method = row.origin, r.created_at = datetime()
        """,
        did=document_id,
        rows=rows,
    )
    # Links are rebuilt from the input, so a corrected ClaimReview drops stale authors / appearances.
    tx.run(
        "MATCH (cc:CheckedClaim)-[r:APPEARED_IN|ATTRIBUTED_TO]->() WHERE cc.id IN $ids DELETE r",
        ids=ids,
    )
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (cc:CheckedClaim {id: row.claim})
        MERGE (a:Appearance {id: row.id})
          ON CREATE SET a.created_at = datetime()
        SET a.url = row.url, a.domain = row.domain
        MERGE (cc)-[r:APPEARED_IN]->(a)
          ON CREATE SET r.method = 'claimreview', r.created_at = datetime()
        """,
        rows=appearances,
    )
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (cc:CheckedClaim {id: row.claim})
        MERGE (p:Person {key: row.key})
          ON CREATE SET p.created_at = datetime()
        SET p.name = row.name, p.kind = row.kind
        MERGE (cc)-[r:ATTRIBUTED_TO]->(p)
          ON CREATE SET r.method = row.method, r.declared_by = 'fact_checker', r.created_at = datetime()
        """,
        rows=attributions,
    )
    tx.run("MATCH (p:Person) WHERE NOT (p)<-[:ATTRIBUTED_TO]-() DELETE p")
    tx.run("MATCH (a:Appearance) WHERE NOT (a)<-[:APPEARED_IN]-() DELETE a")
    return {"CHECKS": len(rows), "APPEARED_IN": len(appearances), "ATTRIBUTED_TO": len(attributions)}
