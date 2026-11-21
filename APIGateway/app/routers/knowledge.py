from typing import Any

from fastapi import APIRouter, Depends, Path, Query

from app.deps import Client, get_downstream, rate_limit, require_admin
from app.routers.services import to_http
from app.services import Downstream, DownstreamError

router = APIRouter(prefix="/api/v1", tags=["knowledge base"])


async def _get(downstream: Downstream, path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        return await downstream.scraper_get(path, params)
    except DownstreamError as exc:
        raise to_http(exc) from exc


@router.get("/sources", summary="Zwraca jawny katalog źródeł z liczbą zebranych dokumentów")
async def list_sources(
    _: Client = Depends(rate_limit),
    downstream: Downstream = Depends(get_downstream),
) -> Any:
    return await _get(downstream, "/sources")


@router.get("/corpus/stats", summary="Zwraca statystyki bazy wiedzy: dokumenty, domeny, typy źródeł")
async def corpus_stats(
    _: Client = Depends(rate_limit),
    downstream: Downstream = Depends(get_downstream),
) -> Any:
    return await _get(downstream, "/stats")


@router.get("/scrap/runs", summary="Zwraca historię scrapowania źródeł (admin)")
async def list_scrape_runs(
    limit: int = Query(default=20, ge=1, le=200),
    _: Client = Depends(require_admin),
    downstream: Downstream = Depends(get_downstream),
) -> Any:
    return await _get(downstream, "/scrap/runs", {"limit": limit})


@router.get("/scrap/runs/{run_id}", summary="Zwraca stan jednego scrapowania (admin)")
async def get_scrape_run(
    run_id: str = Path(..., pattern=r"^[0-9a-f-]{36}$"),
    _: Client = Depends(require_admin),
    downstream: Downstream = Depends(get_downstream),
) -> Any:
    return await _get(downstream, f"/scrap/runs/{run_id}")


@router.get("/documents", summary="Zwraca zebrane dokumenty z bazy wiedzy (admin)")
async def list_documents(
    source_id: str | None = Query(default=None, pattern=r"^[a-z0-9-]{3,64}$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: Client = Depends(require_admin),
    downstream: Downstream = Depends(get_downstream),
) -> Any:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if source_id:
        params["source_id"] = source_id
    return await _get(downstream, "/documents", params)


@router.get("/documents/{doc_id}", summary="Zwraca jeden dokument z pełną treścią (admin)")
async def get_document(
    doc_id: int = Path(..., ge=1),
    _: Client = Depends(require_admin),
    downstream: Downstream = Depends(get_downstream),
) -> Any:
    return await _get(downstream, f"/documents/{doc_id}")
