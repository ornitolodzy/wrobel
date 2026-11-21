from fastapi import APIRouter, Request

from app.schemas import Page, ScrapeRequest
from app.scrape import scrape_url

router = APIRouter(tags=["scrape"])


@router.post("/scrape", response_model=Page, summary="Pobiera dowolną stronę i zwraca jej treść (link od użytkownika)")
async def scrape(body: ScrapeRequest, request: Request) -> Page:
    state = request.app.state
    return await scrape_url(body.url.strip(), body.render, state.fetcher, state.renderer, state.settings)
