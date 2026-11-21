from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from app.db import ScrapeRun
from app.ingest import Ingestor, RunAlreadyActive, UnknownSource
from app.schemas import RunOut, ScrapAllRequest

router = APIRouter(prefix="/scrap", tags=["ingest"])


def _to_out(run: ScrapeRun) -> RunOut:
    return RunOut(
        run_id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        request=run.request,
        results=run.results,
    )


@router.post("/all", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED, summary="Uruchamia w tle scrapowanie źródeł z katalogu")
async def scrap_all(request: Request, body: ScrapAllRequest = Body(default_factory=ScrapAllRequest)) -> RunOut:
    ingestor: Ingestor = request.app.state.ingestor
    try:
        run = await ingestor.start(body.source_ids, body.max_items_per_source)
    except RunAlreadyActive:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Scrapowanie już trwa.") from None
    except UnknownSource as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Nieznane źródło: {exc}") from None
    return _to_out(run)


@router.get("/runs", response_model=list[RunOut], summary="Zwraca historię uruchomień scrapowania")
async def list_runs(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> list[RunOut]:
    return [_to_out(r) for r in await request.app.state.db.list_runs(limit)]


@router.get("/runs/{run_id}", response_model=RunOut, summary="Zwraca stan jednego uruchomienia")
async def get_run(run_id: str, request: Request) -> RunOut:
    run = await request.app.state.db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono uruchomienia.")
    return _to_out(run)
