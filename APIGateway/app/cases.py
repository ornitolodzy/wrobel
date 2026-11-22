"""Pętla informacji (zadanie §2, §3 funkcje premiowane, §5 „Pętla informacji”, §6 scenariusz H).

Twierdzenie bez rozstrzygnięcia (brak wystarczających dowodów) trafia do sprawy dla moderatora:
- podobne zgłoszenia, z WWW i z Discorda, łączą się w JEDNĄ sprawę (deduplikacja po rdzeniach słów),
- po `case_escalate_after` podobnych zgłoszeniach, albo od razu w sprawach wysokiego ryzyka
  (alarm, atak, ewakuacja, skażenie...), system REKOMENDUJE przekazanie sprawy moderatorowi,
- sprawa jest PRZEKAZANA dopiero wtedy, gdy to się faktycznie stało: webhook moderatora odpowiedział
  poprawnie albo administrator przekazał ją ręcznie. Użytkownik nigdy nie dostaje fałszywego
  komunikatu o przekazaniu.

Metoda podobieństwa jest jawna i tania: Jaccard zbiorów rdzeni słów (4 pierwsze litery, co
w polskiej odmianie łączy np. „grudzień” i „grudniu”), bez słów funkcyjnych.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from sqlalchemy import select

from app.config import Settings
from app.db import Database, ModerationCase, utcnow
from app.schemas import CaseInfo, CheckResponse, CheckStatus, Verdict

logger = logging.getLogger("gateway.cases")

_STOP = set(
    "aby ale bez był była było były być będzie będą czy dla dlatego dziś gdy jak jaki jest jeszcze już kiedy "
    "który która które lub ma mają może można nad nie nich niż oraz po pod przez przy się są tak też tego tej "
    "ten to tym tylko w we wczoraj z za ze że od do na o i a u co cały cała całe całym zostanie zostały został "
    "została prawda podobno słyszałem słyszałam mówią ponoć".split()
)
_RISK = re.compile(
    r"\b(alarm|alert|atak|ewakuac|skaż|skaz|wojn|wybuch|zamach|epidem|pandem|bomb|terror|mobiliz|"
    r"stan wojenn|blackout|radiac|promieniow|katastrof|zatru|porwan|strzelanin)",
    re.IGNORECASE,
)


def claim_key(text: str) -> str:
    """Zbiór rdzeni słów: 4 pierwsze litery, co w polskiej odmianie łączy np. „grudzień” i „grudniu”."""
    words = re.findall(r"[^\W\d_]+|\d+", (text or "").lower())
    stems = sorted({w if w.isdigit() else w[:4] for w in words if w not in _STOP and (len(w) > 2 or w.isdigit())})
    return " ".join(stems)


def similarity(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def is_risky(text: str) -> bool:
    return bool(_RISK.search(text or ""))


def message_for(case: ModerationCase, escalate_after: int) -> str:
    if case.status == "forwarded":
        return (f"Sprawę #{case.id} przekazano moderatorowi do weryfikacji "
                f"(podobnych zgłoszeń: {case.reports}).")
    if case.status == "recommended":
        why = "temat wysokiego ryzyka" if case.risk and case.reports < escalate_after else \
            f"{case.reports} podobne zgłoszenia bez rozstrzygnięcia"
        return (f"Rekomendujemy przekazanie sprawy #{case.id} moderatorowi ({why}). "
                "Sprawa czeka na decyzję i nie została jeszcze przekazana.")
    if case.status == "closed":
        return f"Sprawa #{case.id} została już zamknięta przez moderatora."
    return (f"Zgłoszenie zapisano w sprawie #{case.id}. Jeśli podobne pytanie się powtórzy, "
            "system zarekomenduje przekazanie go moderatorowi.")


def info(case: ModerationCase, escalate_after: int) -> CaseInfo:
    return CaseInfo(case_id=case.id, status=case.status, reports=case.reports, risk=case.risk,
                    message=message_for(case, escalate_after))


def as_dict(case: ModerationCase) -> dict[str, Any]:
    return {
        "case_id": case.id, "status": case.status, "risk": case.risk, "reports": case.reports,
        "claim": case.claim, "channels": case.channels or {}, "check_ids": case.check_ids or [],
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        "forwarded_at": case.forwarded_at.isoformat() if case.forwarded_at else None,
        "forwarded_to": case.forwarded_to, "resolution": case.resolution,
    }


class CaseDesk:
    def __init__(self, db: Database, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.db = db
        self.settings = settings
        self.transport = transport

    async def register(self, response: CheckResponse, previous_check_id: str | None = None) -> list[CaseInfo]:
        """Dołącza twierdzenia bez rozstrzygnięcia z tego sprawdzenia do spraw moderatora."""
        if not self.settings.cases_enabled or response.mock or response.status != CheckStatus.completed:
            return []
        claims = [c.claim for c in response.claims if c.verdict == Verdict.insufficient_evidence]
        out: list[CaseInfo] = []
        for claim in claims:
            key = claim_key(claim)
            if not key:
                continue
            case, newly_recommended = await self._upsert(claim, key, response, previous_check_id)
            if newly_recommended:
                await self.db.log_event("case_recommended", client=response.channel, check_id=response.check_id,
                                        detail={"case_id": case.id, "reports": case.reports, "risk": case.risk})
                if self.settings.moderator_webhook_url:
                    case = await self.forward(case.id, to="webhook", by=response.channel) or case
            out.append(info(case, self.settings.case_escalate_after))
        return out

    async def _upsert(self, claim: str, key: str, response: CheckResponse,
                      previous_check_id: str | None) -> tuple[ModerationCase, bool]:
        async with self.db.session() as s:
            candidates = (await s.execute(
                select(ModerationCase).where(ModerationCase.status != "closed")
                .order_by(ModerationCase.updated_at.desc()).limit(500))).scalars().all()
            best, score = None, 0.0
            for c in candidates:
                sim = similarity(key, c.claim_key)
                if sim > score:
                    best, score = c, sim
            risky = is_risky(claim)
            if best is not None and score >= self.settings.case_similarity:
                case = best
                ids = list(case.check_ids or [])
                # Wyszukiwanie na żywo to ponowne sprawdzenie TEGO SAMEGO zgłoszenia, nie nowe zgłoszenie.
                repeat = previous_check_id is not None and previous_check_id in ids
                if response.check_id not in ids:
                    ids.append(response.check_id)
                case.check_ids = ids
                if not repeat:
                    case.reports += 1
                    channels = dict(case.channels or {})
                    channels[response.channel] = channels.get(response.channel, 0) + 1
                    case.channels = channels
                case.risk = case.risk or risky
                case.updated_at = utcnow()
            else:
                case = ModerationCase(claim=claim[:1000], claim_key=key, status="open", risk=risky, reports=1,
                                      check_ids=[response.check_id], channels={response.channel: 1})
                s.add(case)
            newly = False
            if case.status == "open" and (case.reports >= self.settings.case_escalate_after or case.risk):
                case.status = "recommended"
                newly = True
            await s.commit()
            await s.refresh(case)
            return case, newly

    async def forward(self, case_id: int, *, to: str, by: str, note: str | None = None) -> ModerationCase | None:
        """Faktyczne przekazanie: webhook moderatora (musi odpowiedzieć 2xx) albo decyzja administratora."""
        async with self.db.session() as s:
            case = await s.get(ModerationCase, case_id)
            if case is None:
                return None
            if to == "webhook":
                ok = await self._send_webhook(case)
                if not ok:
                    await self.db.log_event("case_forward_failed", client=by, detail={"case_id": case.id})
                    return case  # zostaje „recommended”: bez fałszywego komunikatu o przekazaniu
            case.status = "forwarded"
            case.forwarded_at = utcnow()
            case.forwarded_to = to[:128]
            if note:
                case.resolution = note[:2000]
            case.updated_at = utcnow()
            await s.commit()
            await s.refresh(case)
        await self.db.log_event("case_forwarded", client=by, detail={"case_id": case_id, "to": to})
        return case

    async def close(self, case_id: int, *, by: str, resolution: str | None) -> ModerationCase | None:
        async with self.db.session() as s:
            case = await s.get(ModerationCase, case_id)
            if case is None:
                return None
            case.status = "closed"
            case.resolution = (resolution or "")[:2000] or None
            case.updated_at = utcnow()
            await s.commit()
            await s.refresh(case)
        await self.db.log_event("case_closed", client=by, detail={"case_id": case_id})
        return case

    async def list(self, status: str | None = None, limit: int = 200) -> list[ModerationCase]:
        async with self.db.session() as s:
            q = select(ModerationCase).order_by(ModerationCase.reports.desc(), ModerationCase.updated_at.desc())
            if status:
                q = q.where(ModerationCase.status == status)
            return list((await s.execute(q.limit(limit))).scalars())

    async def _send_webhook(self, case: ModerationCase) -> bool:
        text = (f"🐦 Wróbel: sprawa #{case.id} do weryfikacji ({case.reports} zgłoszeń"
                f"{', wysokie ryzyko' if case.risk else ''}).\nTwierdzenie: {case.claim[:1500]}")
        try:
            async with httpx.AsyncClient(timeout=5, transport=self.transport) as client:
                r = await client.post(self.settings.moderator_webhook_url, json={"content": text})
            return 200 <= r.status_code < 300
        except httpx.HTTPError:
            logger.warning("Webhook moderatora niedostępny (sprawa %s)", case.id)
            return False
