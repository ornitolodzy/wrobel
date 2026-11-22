"""Step 5: verdict policy (pure code). The LLM never decides the verdict.

Unit-tested per scenario (tests/test_policy.py) without any LLM call.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from prawdowid.models import (
    ROLE_OF_STANCE,
    CitedQuote,
    Claim,
    ClaimEvidence,
    Evidence,
    SourceRef,
    Stance,
    Verdict,
)

# A fact-checker's explicit ruling in its own text (IFCN/EFCSN rating vocabulary), see decide().
_RULING = re.compile(
    r"(\bfałsz\w*|\bnieprawd\w*|\bfake\b|\bfejk\w*|\bmanipul\w*|\bzmanipul\w*|\bdezinformac\w*"
    r"|\bwprowadz\w* w błąd|\bwyrwan\w* z kontekstu|\bprzeróbk\w*|\bzdementow\w*|\bdementuj\w*|\bdementi\b"
    r"|\bzaprzecz\w*|\bzainscenizow\w*|\bbezpodstawn\w*|\bnieprawdziw\w*)",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

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
        sources = source_refs(list(used), evidence_by_id)
        return Verdict(
            claim_id=claim.id,
            label=label,
            lead=lead_for(label, claim.is_question, bool(sources)),
            strength=_strength(list(used), evidence_by_id),
            reasons=list(reasons),
            evidence=list(used),
            sources=sources,
            warnings=list(warnings) + _archive_warning(list(used), evidence_by_id, now, p),
        )

    if claim.type in ("opinion", "prediction"):
        return Verdict(
            claim_id=claim.id,
            label="nie_podlega_weryfikacji",
            lead=lead_for("nie_podlega_weryfikacji", claim.is_question, False),
            strength="niska",
            reasons=["To opinia lub prognoza, a nie twierdzenie o fakcie."],
        )

    # A quote can be evidence only if it is about the claim: it must share at least one content word
    # (stem) with the claim, counting the source title. Otherwise a verbatim but unrelated fragment
    # (e.g. an act's "entry into force" date) could "confirm" anything (citation correctness, §6).
    rel = [s for s in stances if s.label != "irrelevant" and s.chunk_id in evidence_by_id
           and _on_topic(claim.text, s.quote, evidence_by_id[s.chunk_id].title)]
    if not rel:
        return verdict("brak_dowodow", ["Brak źródeł odnoszących się do twierdzenia."])

    def of(label):
        return [s for s in rel if s.label == label]

    def groups(label):  # independent confirmations = distinct reprint groups of countable sources
        return {evidence_by_id[s.chunk_id].dup_group for s in of(label)
                if weights[evidence_by_id[s.chunk_id].source_type] >= min_count}

    def best(label):
        return max((weights[evidence_by_id[s.chunk_id].source_type] for s in of(label)), default=0)

    def decisive(label):
        """One source settles the claim on its own only if it is primary material, or if it is a
        strong fact-checker's own rating of the claim: the rating record (section "ocena"), or a
        refuting fact-check whose text states the ruling explicitly (IFCN-style wording: „fałsz”,
        „manipulacja”, „zdementowano”...). A fact-check's prose about the same person or topic
        without such a ruling is not a ruling on this claim: then two independent sources are needed."""
        for s in of(label):
            e = evidence_by_id[s.chunk_id]
            w = weights[e.source_type]
            if w >= 1.0 or (e.section == "ocena" and w >= strong):
                return True
            if (label == "refutes" and e.source_type == "factcheck" and w >= strong
                    and _RULING.search(f"{e.title or ''}\n{e.text}\n{s.quote}")):
                return True
        return False

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
    if ref and (decisive("refutes") or len(ref) >= 2) and not sup:
        return verdict(
            "falszywe",
            [_describe(f"{len(ref)} niezależne źródło(a) przeczą", of("refutes"), evidence_by_id)],
            of("refutes"),
        )
    if sup and (decisive("supports") or len(sup) >= 2) and not ref:
        other_time = _time_mismatch(claim.text, of("supports"), evidence_by_id)
        if other_time:
            # Scenario B: real material, wrong time. The sources confirm the event, not the claimed date.
            return verdict("wprowadzajace_w_blad", [other_time], of("supports"))
        return verdict(
            "potwierdzone",
            [_describe(f"{len(sup)} niezależne źródło(a) potwierdzają", of("supports"), evidence_by_id)],
            of("supports"),
        )
    if of("announced"):
        return verdict(
            "brak_dowodow",
            [_describe("Źródła mówią o zapowiedzi lub planie, ale nie potwierdzają, że to się wydarzyło",
                       of("announced"), evidence_by_id)],
            of("announced") + of("supports") + of("refutes"),
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


_KIND_ORDER = {"ocena": 0, "tresc": 1, "naglowek": 2}


def source_refs(used: list[Stance], by_id: dict[str, Evidence]) -> list[SourceRef]:
    """Numbered sources behind a verdict, one per URL: confirming first, then refuting, then
    context; then primary material that a cited fact-checker links to (chain of sources)."""
    order = {"supports": 0, "refutes": 1, "missing_context": 2, "announced": 3}
    refs: list[SourceRef] = []
    by_url: dict[str, SourceRef] = {}
    ranked = sorted(
        (s for s in used if s.label in ROLE_OF_STANCE),
        key=lambda s: (order[s.label], _KIND_ORDER[by_id[s.chunk_id].section]),
    )
    for s in ranked:
        e = by_id[s.chunk_id]
        quote = CitedQuote(kind=e.section, text=s.quote, rationale=s.rationale)
        if e.url in by_url:
            if quote.text not in {q.text for q in by_url[e.url].quotes}:
                by_url[e.url].quotes.append(quote)
            continue
        ref = _ref(len(refs) + 1, ROLE_OF_STANCE[s.label], e, quote)
        refs.append(ref)
        by_url[e.url] = ref

    # Primary material linked by a cited source (e.g. Demagog -> PAP dispatch): its headline is
    # taken verbatim from the fetched page, so it is shown even if the model did not judge it.
    for e in sorted(by_id.values(), key=lambda e: e.chunk_id):
        if e.section == "naglowek" and e.via_url in by_url and e.url not in by_url:
            ref = _ref(len(refs) + 1, "podlinkowane", e, CitedQuote(kind="naglowek", text=e.text))
            refs.append(ref)
            by_url[e.url] = ref
    for ref in refs:
        if ref.via_url in by_url:
            ref.via = by_url[ref.via_url].n
    return refs


def _ref(n: int, role: str, e: Evidence, quote: CitedQuote) -> SourceRef:
    return SourceRef(
        n=n, role=role, publisher=e.publisher, title=e.title, url=e.url,
        source_type=e.source_type, published_at=e.published_at, fetched_at=e.fetched_at,
        doc_id=e.doc_id, chunk_id=e.chunk_id, quote=quote.text, rationale=quote.rationale,
        kind=quote.kind, quotes=[quote], http_status=e.http_status, via_url=e.via_url,
    )


def lead_for(label: str, is_question: bool, has_sources: bool) -> str:
    """Short answer shown first."""
    if label == "potwierdzone":
        return "Tak." if is_question else "Prawda."
    if label == "falszywe":
        return "Nie." if is_question else "Fałsz."
    if label == "wprowadzajace_w_blad":
        return "Wprowadzające w błąd."
    if label == "nie_podlega_weryfikacji":
        return "Niemożliwe do weryfikacji."
    return "Nie da się tego jednoznacznie potwierdzić." if has_sources else "Nie znaleziono informacji."


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


_STOP = set(
    "albo będzie będą była było były gdzie jako jest jeszcze które który która może można także tego "
    "tych tylko przez przy oraz roku dnia więc wszystko został została zostało zostanie".split()
)


def _stems(text: str) -> set[str]:
    """Content-word stems: words of 4+ letters (5-letter prefix, which joins most Polish inflected
    forms) and numbers other than years (years are matched separately by _time_mismatch)."""
    out = set()
    for w in re.findall(r"[^\W_]+", (text or "").lower()):
        if w.isdigit():
            if len(w) >= 2 and not _YEAR.fullmatch(w):
                out.add(w)
        elif len(w) >= 4 and w not in _STOP:
            out.add(w[:5])
    return out


def _on_topic(claim_text: str, quote: str, title: str | None) -> bool:
    claim = _stems(claim_text)
    return not claim or bool(claim & _stems(f"{quote} {title or ''}"))


def _time_mismatch(claim_text: str, used: list[Stance], by_id: dict[str, Evidence]) -> str | None:
    """The claim names a year that none of the confirming sources mentions or was published in:
    the sources confirm the event, but from another time (scenario B, „zły czas”)."""
    years = set(_YEAR.findall(claim_text or ""))
    if not years or not used:
        return None
    dates = []
    for s in used:
        e = by_id[s.chunk_id]
        if e.published_at and str(e.published_at.year) in years:
            return None
        if any(y in f"{e.title or ''} {e.text}" for y in years):
            return None
        if e.published_at:
            dates.append(f"{e.published_at:%Y-%m-%d}")
    when = ", ".join(sorted(set(dates))) or "innego okresu"
    return (f"Źródła potwierdzają opisane zdarzenie, ale z innego czasu: twierdzenie mówi o roku "
            f"{', '.join(sorted(years))}, a źródła pochodzą z {when}.")


def _describe(prefix: str, used: list[Stance], by_id: dict[str, Evidence]) -> str:
    parts = []
    for s in used:
        e = by_id[s.chunk_id]
        date = f", {e.published_at:%Y-%m-%d}" if e.published_at else ""
        parts.append(f"{e.publisher} ({e.source_type}{date})")
    return f"{prefix}: " + "; ".join(dict.fromkeys(parts)) + "."
