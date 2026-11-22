from collections.abc import AsyncIterator

from app.db import DocumentIn
from app.sources.base import AdapterContext


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    """Źródło tylko do wyszukiwania na żywo (app/live_search.py): planowe scrapowanie nic z niego nie pobiera."""
    return
    yield  # pragma: no cover - generator bez elementów
