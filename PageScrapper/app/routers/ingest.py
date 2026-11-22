from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from app.db import ScrapeRun
from app.ingest import Ingestor, PageNotInCatalog, RunAlreadyActive, UnknownSource
from app.schemas import RunOut, ScrapAllRequest, ScrapPageOut, ScrapPageRequest

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


@router.post(
    "/page",
    response_model=ScrapPageOut,
    summary="Pobiera jedną stronę z witryny z katalogu i zapisuje ją w bazie (jak /scrap/all)",
)
async def scrap_page(body: ScrapPageRequest, request: Request) -> ScrapPageOut:
    ingestor: Ingestor = request.app.state.ingestor
    try:
        result = await ingestor.scrape_page(body.url, body.source_id, body.render)
    except UnknownSource as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Nieznane źródło: {exc}") from None
    except PageNotInCatalog as exc:
        detail = {"code": "not_in_catalog", "message": str(exc), "allowed_sites": request.app.state.catalog.page_sites()}
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail) from None
    doc, details = result.document, result.details
    return ScrapPageOut(
        run_id=result.run.id,
        url=details["url"],
        final_url=doc.url,
        source_id=result.source.id,
        source_name=result.source.name,
        source_type=result.source.source_type,
        outcome=details["outcome"],
        document_id=doc.id,
        revision=doc.revision or 1,
        title=doc.title,
        publisher=doc.publisher,
        published_at=doc.published_at,
        language=doc.language,
        text_chars=len(doc.text),
        claim_reviews=details["claim_reviews"],
        rendered=details["rendered"],
        rag=details["rag"],
    )


@router.get("/runs", response_model=list[RunOut], summary="Zwraca historię uruchomień scrapowania")
async def list_runs(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> list[RunOut]:
    return [_to_out(r) for r in await request.app.state.db.list_runs(limit)]


@router.get("/runs/{run_id}", response_model=RunOut, summary="Zwraca stan jednego uruchomienia")
async def get_run(run_id: str, request: Request) -> RunOut:
    run = await request.app.state.db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono uruchomienia.")
    return _to_out(run)
