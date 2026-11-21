"""Step 5: verdict policy (pure code). The LLM never decides the verdict.

Unit-tested per scenario (tests/test_policy.py) without any LLM call.
"""

from __future__ import annotations

from datetime import datetime, timezone

from prawdowid.models import Claim, ClaimEvidence, Evidence, Stance, Verdict

DEFAULT_POLICY = {
    "source_weights": {"primary": 1.0, "factcheck": 0.9, "secondary": 0.6},
    "strong_source": 0.9,
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
    weights, strong = p["source_weights"], p["strong_source"]
    now = now or datetime.now(timezone.utc)

    def verdict(label, reasons, used=(), warnings=()):
        return Verdict(
            claim_id=claim.id,
            label=label,
            strength=_strength(list(used), evidence_by_id),
            reasons=list(reasons),
            evidence=list(used),
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

    def groups(label):  # independent confirmations = distinct reprint groups
        return {evidence_by_id[s.chunk_id].dup_group for s in of(label)}

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
    return verdict(
        "brak_dowodow",
        ["Dowody zbyt słabe lub pojedyncze źródło wtórne."],
        rel,
    )


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
