"""Step 5: verdict policy (pure code). The LLM never decides the verdict.

Unit-tested per scenario (tests/test_policy.py) without any LLM call.
"""

from __future__ import annotations

from datetime import datetime, timezone

from prawdowid.models import (
    ROLE_OF_STANCE,
    Claim,
    ClaimEvidence,
    Evidence,
    SourceRef,
    Stance,
    Verdict,
)

DEFAULT_POLICY = {
    "source_weights": {"primary": 1.0, "factcheck": 0.9, "secondary": 0.6, "web": 0.3},
    "strong_source": 0.9,
    # Only sources at least this strong count as an independent confirmation/refutation.
    # "web" (outside the catalog) stays below it: shown as a source, never decisive alone.
    "min_weight_to_count": 0.6,
    "archive_after_days": 365,
}


def decide(
    claim: Claim,
    stances: list[Stance],
    evidence_by_id: dict[str, Evidence],
    now: datetime | None = None,
    params: dict | None = None,
) -> Verdict:
    p = {**DEFAULT_POLICY, **(params or {})}
    weights = {**DEFAULT_POLICY["source_weights"], **p["source_weights"]}
    strong, min_count = p["strong_source"], p["min_weight_to_count"]
    now = now or datetime.now(timezone.utc)

    def verdict(label, reasons, used=(), warnings=()):
        return Verdict(
            claim_id=claim.id,
            label=label,
            strength=_strength(list(used), evidence_by_id),
            reasons=list(reasons),
            evidence=list(used),
            sources=source_refs(list(used), evidence_by_id),
            warnings=list(warnings) + _archive_warning(list(used), evidence_by_id, now, p),
        )

    if claim.type in ("opinion", "prediction"):
        return Verdict(
            claim_id=claim.id,
            label="nie_podlega_weryfikacji",
            strength="niska",
            reasons=["To opinia lub prognoza, a nie twierdzenie o fakcie."],
        )

    rel = [s for s in stances if s.label != "irrelevant" and s.chunk_id in evidence_by_id]
    if not rel:
        return verdict("brak_dowodow", ["Brak źródeł odnoszących się do twierdzenia."])

    def of(label):
        return [s for s in rel if s.label == label]

    def groups(label):  # independent confirmations = distinct reprint groups of countable sources
        return {evidence_by_id[s.chunk_id].dup_group for s in of(label)
                if weights[evidence_by_id[s.chunk_id].source_type] >= min_count}

    def best(label):
        return max((weights[evidence_by_id[s.chunk_id].source_type] for s in of(label)), default=0)

    sup, ref, ctx = groups("supports"), groups("refutes"), groups("missing_context")

    if sup and ref and best("supports") >= strong and best("refutes") >= strong:
        return verdict(
            "brak_dowodow",
            ["Wiarygodne źródła są ze sobą sprzeczne."],
            of("supports") + of("refutes"),
            ["Sprzeczność między wiarygodnymi źródłami."],
        )
    if ctx and best("missing_context") >= strong:
        return verdict(
            "wprowadzajace_w_blad",
            [_describe("Materiał prawdziwy, ale wyrwany z kontekstu", of("missing_context"), evidence_by_id)],
            of("missing_context"),
        )
    if ref and (best("refutes") >= strong or len(ref) >= 2) and not sup:
        return verdict(
            "falszywe",
            [_describe(f"{len(ref)} niezależne źródło(a) przeczą", of("refutes"), evidence_by_id)],
            of("refutes"),
        )
    if sup and (best("supports") >= 1.0 or len(sup) >= 2) and not ref:
        return verdict(
            "potwierdzone",
            [_describe(f"{len(sup)} niezależne źródło(a) potwierdzają", of("supports"), evidence_by_id)],
            of("supports"),
        )
    if all(evidence_by_id[s.chunk_id].source_type == "web" for s in rel):
        reason = ("Znaleziono tylko strony spoza katalogu źródeł (niezweryfikowani wydawcy); "
                  "same nie wystarczają do werdyktu.")
    else:
        reason = "Dowody zbyt słabe lub pojedyncze źródło wtórne."
    return verdict("brak_dowodow", [reason], rel)


def decide_all(
    claims: list[Claim],
    stances: list[Stance],
    evidence: list[ClaimEvidence],
    policy_params: dict,
) -> list[Verdict]:
    by_id = {e.chunk_id: e for ce in evidence for e in ce.evidence}
    now = datetime.now(timezone.utc)
    return [
        decide(c, [s for s in stances if s.claim_id == c.id], by_id, now, policy_params)
        for c in claims
    ]


def source_refs(used: list[Stance], by_id: dict[str, Evidence]) -> list[SourceRef]:
    """Numbered sources behind a verdict: confirming first, then refuting, then context."""
    order = {"supports": 0, "refutes": 1, "missing_context": 2}
    seen, refs = set(), []
    for s in sorted(used, key=lambda s: order.get(s.label, 9)):
        if s.label not in ROLE_OF_STANCE or s.chunk_id in seen:
            continue
        seen.add(s.chunk_id)
        e = by_id[s.chunk_id]
        refs.append(SourceRef(
            n=len(refs) + 1, role=ROLE_OF_STANCE[s.label], publisher=e.publisher, title=e.title,
            url=e.url, source_type=e.source_type, published_at=e.published_at,
            fetched_at=e.fetched_at, doc_id=e.doc_id, chunk_id=e.chunk_id, quote=s.quote,
            rationale=s.rationale,
        ))
    return refs


def _strength(used: list[Stance], by_id: dict[str, Evidence]) -> str:
    if not used:
        return "niska"
    ev = [by_id[s.chunk_id] for s in used]
    has_primary = any(e.source_type == "primary" for e in ev)
    independent = len({e.dup_group for e in ev}) >= 2
    if has_primary and independent:
        return "wysoka"
    if has_primary or independent:
        return "średnia"
    return "niska"


def _archive_warning(used, by_id, now, p) -> list[str]:
    dates = [by_id[s.chunk_id].published_at for s in used if by_id[s.chunk_id].published_at]
    if dates and (now - max(dates)).days > p["archive_after_days"]:
        return [f"Materiał archiwalny: najnowsze źródło z {max(dates):%Y-%m-%d}."]
    return []


def _describe(prefix: str, used: list[Stance], by_id: dict[str, Evidence]) -> str:
    parts = []
    for s in used:
        e = by_id[s.chunk_id]
        date = f", {e.published_at:%Y-%m-%d}" if e.published_at else ""
        parts.append(f"{e.publisher} ({e.source_type}{date})")
    return f"{prefix}: " + "; ".join(dict.fromkeys(parts)) + "."
