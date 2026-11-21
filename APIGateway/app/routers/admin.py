from typing import Any

from fastapi import APIRouter, Depends, Query

from app.db import Database
from app.deps import Client, get_db, get_runtime, require_admin
from app.runtime_config import RuntimeConfigStore
from app.schemas import RuntimeConfig, RuntimeConfigUpdate

router = APIRouter(prefix="/api/v1", tags=["config"])


@router.get("/config", response_model=RuntimeConfig, summary="Zwraca aktualne parametry konfiguracyjne")
async def get_config(
    runtime: RuntimeConfigStore = Depends(get_runtime),
) -> RuntimeConfig:
    return runtime.get()


@router.patch("/config", response_model=RuntimeConfig, summary="Zmienia parametry konfiguracyjne, np. model LLM (admin)")
async def update_config(
    patch: RuntimeConfigUpdate,
    client: Client = Depends(require_admin),
    runtime: RuntimeConfigStore = Depends(get_runtime),
    db: Database = Depends(get_db),
) -> RuntimeConfig:
    config, changes = await runtime.update(patch)
    if changes:
        await db.log_event("config_changed", client=client.name, detail=changes)
    return config


@router.get("/admin/events", summary="Zwraca ostatnie zdarzenia z audit logu (admin)")
async def list_events(
    limit: int = Query(default=100, ge=1, le=1000),
    event_type: str | None = Query(default=None, max_length=64),
    _: Client = Depends(require_admin),
    db: Database = Depends(get_db),
) -> list[dict[str, Any]]:
    events = await db.list_events(limit=limit, event_type=event_type)
    return [
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "event_type": e.event_type,
            "client": e.client,
            "check_id": e.check_id,
            "detail": e.detail,
        }
        for e in events
    ]
