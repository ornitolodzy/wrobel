"""Panel moderatora (admin): sprawy zgłoszeń bez rozstrzygnięcia i eksport danych (CSV/JSON)."""

import csv
import io
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.cases import CaseDesk, as_dict
from app.config import Settings
from app.db import Check, Database
from app.deps import Client, get_db, get_settings, require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["moderation"])


class ForwardRequest(BaseModel):
    to: str = Field(default="moderator", min_length=1, max_length=128, description="komu przekazano sprawę")
    note: str | None = Field(default=None, max_length=2000)


class CloseRequest(BaseModel):
    resolution: str | None = Field(default=None, max_length=2000, description="np. link do sprostowania")


def _desk(request: Request, db: Database = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDesk:
    return CaseDesk(db, settings, transport=getattr(request.app.state, "transport", None))


@router.get("/cases", summary="Sprawy moderatora: powtarzające się pytania bez odpowiedzi (najczęstsze na górze)")
async def list_cases(
    case_status: Literal["open", "recommended", "forwarded", "closed"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
    _: Client = Depends(require_admin),
    desk: CaseDesk = Depends(_desk),
) -> list[dict[str, Any]]:
    return [as_dict(c) for c in await desk.list(case_status, limit)]


@router.post("/cases/{case_id}/forward", summary="Przekazuje sprawę moderatorowi (faktyczne przekazanie)")
async def forward_case(
    body: ForwardRequest,
    case_id: int = Path(..., ge=1),
    client: Client = Depends(require_admin),
    desk: CaseDesk = Depends(_desk),
) -> dict[str, Any]:
    case = await desk.forward(case_id, to=body.to, by=client.name, note=body.note)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono sprawy.")
    return as_dict(case)


@router.post("/cases/{case_id}/close", summary="Zamyka sprawę (np. po publikacji sprostowania)")
async def close_case(
    body: CloseRequest,
    case_id: int = Path(..., ge=1),
    client: Client = Depends(require_admin),
    desk: CaseDesk = Depends(_desk),
) -> dict[str, Any]:
    case = await desk.close(case_id, by=client.name, resolution=body.resolution)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono sprawy.")
    return as_dict(case)


@router.get("/export", summary="Eksport danych do narzędzi analitycznych: sprawy albo sprawdzenia, CSV lub JSON")
async def export(
    kind: Literal["cases", "checks"] = Query(default="cases"),
    fmt: Literal["json", "csv"] = Query(default="json", alias="format"),
    limit: int = Query(default=1000, ge=1, le=10000),
    _: Client = Depends(require_admin),
    db: Database = Depends(get_db),
    desk: CaseDesk = Depends(_desk),
) -> Response:
    if kind == "cases":
        rows = [as_dict(c) for c in await desk.list(None, limit)]
    else:
        async with db.session() as s:
            checks = (await s.execute(select(Check).order_by(Check.created_at.desc()).limit(limit))).scalars()
            rows = [_check_row(c) for c in checks]
    if fmt == "json":
        import json

        return Response(json.dumps(rows, ensure_ascii=False, indent=1), media_type="application/json")
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: (";".join(map(str, v)) if isinstance(v, list) else v) for k, v in r.items()})
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="prawdowid-{kind}.csv"'})


def _check_row(c: Check) -> dict[str, Any]:
    resp = c.response or {}
    claims = resp.get("claims") or []
    return {
        "check_id": c.id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "channel": c.channel,
        "input_type": c.input_type,
        "input_masked": c.input_masked,
        "status": c.status,
        "duration_ms": c.duration_ms,
        "verdicts": [cl.get("verdict") for cl in claims],
        "claims": [cl.get("claim") for cl in claims],
        "sources": [s.get("url") for cl in claims for s in cl.get("sources") or []],
    }
