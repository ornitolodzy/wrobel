from fastapi import APIRouter, Request

from app.live_search import LiveSearcher
from app.schemas import LiveSearchOut, LiveSearchRequest

router = APIRouter(tags=["live-search"])


@router.post(
    "/live-search",
    response_model=LiveSearchOut,
    summary="Szuka twierdzenia na żywo w wyszukiwarkach zaufanych witryn i dokłada znalezione strony do bazy",
)
async def live_search(body: LiveSearchRequest, request: Request) -> LiveSearchOut:
    state = request.app.state
    searcher = LiveSearcher(
        state.catalog, state.fetcher, state.renderer, state.settings, state.db, state.ingestor.rag
    )
    return await searcher.search(body.query.strip(), body.max_documents)
