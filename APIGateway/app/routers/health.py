from fastapi import APIRouter, Depends

from app.deps import get_downstream
from app.schemas import ServiceHealth
from app.services import Downstream

router = APIRouter(tags=["health"])


@router.get("/health", summary="Sprawdza, czy gateway działa")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/services", response_model=list[ServiceHealth], summary="Sprawdza stan serwisów docelowych (LLM, OCR, Scrapper)")
async def health_services(
    downstream: Downstream = Depends(get_downstream),
) -> list[ServiceHealth]:
    return await downstream.health()
