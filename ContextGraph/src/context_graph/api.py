from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from neo4j.exceptions import ServiceUnavailable

from context_graph import __version__
from context_graph.config import Settings, get_settings
from context_graph.context import entity_neighbourhood, read_context
from context_graph.db import Graph
from context_graph.embeddings import Embedder, make_embedder
from context_graph.events import EventExtractionResult, EventLLM, extract_events, make_event_llm
from context_graph.extract import EntityExtractor, RegexExtractor
from context_graph.ingest import (
    NotFoundError,
    QuoteError,
    add_relation,
    embed_pending,
    upsert_document,
    upsert_image,
)
from context_graph.models import (
    ContextQuery,
    ContextResponse,
    DocumentIn,
    EmbeddingBackfillResult,
    ImageIn,
    IngestResult,
    RelationIn,
)
from context_graph.schema import ensure_schema
from context_graph.stats import graph_stats


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    graph = Graph(settings)
    graph.wait_until_ready()
    ensure_schema(graph, settings.embeddings_dim)
    app.state.graph = graph
    app.state.extractor = RegexExtractor()
    app.state.embedder = make_embedder(settings)
    app.state.event_llm = make_event_llm(settings)
    try:
        yield
    finally:
        graph.close()


app = FastAPI(title="ContextGraph", version=__version__, lifespan=lifespan)


def get_graph(request: Request) -> Graph:
    return request.app.state.graph


def get_extractor(request: Request) -> EntityExtractor:
    return request.app.state.extractor


def get_embedder(request: Request) -> Embedder:
    return request.app.state.embedder


@app.exception_handler(ServiceUnavailable)
async def neo4j_down(_: Request, exc: ServiceUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"Neo4j unavailable: {exc}"})


@app.get("/health", summary="Ping Neo4j")
def health(graph: Graph = Depends(get_graph)) -> dict:
    ok = graph.ping()
    if not ok:
        raise HTTPException(503, "neo4j not reachable")
    return {"status": "ok", "neo4j": ok}


@app.post("/ingest/document", response_model=IngestResult, summary="Zapisuje dokument z PageScrappera")
def ingest_document(
    doc: DocumentIn,
    graph: Graph = Depends(get_graph),
    extractor: EntityExtractor = Depends(get_extractor),
    embedder: Embedder = Depends(get_embedder),
    settings: Settings = Depends(get_settings),
) -> IngestResult:
    return upsert_document(graph, doc, extractor, settings, embedder)


@app.post("/ingest/page", response_model=IngestResult, summary="Starsza nazwa /ingest/document")
def ingest_page(
    doc: DocumentIn,
    graph: Graph = Depends(get_graph),
    extractor: EntityExtractor = Depends(get_extractor),
    embedder: Embedder = Depends(get_embedder),
    settings: Settings = Depends(get_settings),
) -> IngestResult:
    return upsert_document(graph, doc, extractor, settings, embedder)


@app.post("/ingest/image", response_model=IngestResult, summary="Zapisuje tekst odczytany z obrazu (OCR)")
def ingest_image(
    image: ImageIn,
    graph: Graph = Depends(get_graph),
    extractor: EntityExtractor = Depends(get_extractor),
    embedder: Embedder = Depends(get_embedder),
    settings: Settings = Depends(get_settings),
) -> IngestResult:
    return upsert_image(graph, image, extractor, settings, embedder)


@app.post("/embeddings/backfill", response_model=EmbeddingBackfillResult, summary="Liczy brakujące embeddingi")
def embeddings_backfill(
    limit: int = Query(default=500, ge=1, le=5000),
    graph: Graph = Depends(get_graph),
    embedder: Embedder = Depends(get_embedder),
) -> EmbeddingBackfillResult:
    return embed_pending(graph, embedder, limit=limit)


def get_event_llm(request: Request) -> EventLLM:
    return request.app.state.event_llm


@app.post("/events/extract", response_model=EventExtractionResult, summary="Warstwa 2: wyciąga zdarzenia (LLM)")
def events_extract(
    limit: int = Query(default=50, ge=1, le=1000),
    graph: Graph = Depends(get_graph),
    llm: EventLLM = Depends(get_event_llm),
    extractor: EntityExtractor = Depends(get_extractor),
) -> EventExtractionResult:
    return extract_events(graph, llm, extractor, limit)


@app.post("/relations", summary="Dodaje relację SUPPORTS / CONTRADICTS / CORRECTS / CITES / REPRINTS z cytatem")
def post_relation(rel: RelationIn, graph: Graph = Depends(get_graph)) -> dict:
    try:
        return add_relation(graph, rel)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except QuoteError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/context", response_model=ContextResponse, summary="Odczytuje kontekst dla twierdzenia")
def context(
    q: ContextQuery,
    graph: Graph = Depends(get_graph),
    extractor: EntityExtractor = Depends(get_extractor),
    settings: Settings = Depends(get_settings),
) -> ContextResponse:
    return read_context(graph, q, extractor, settings.seed_limit)


@app.get("/entities/{name}", summary="Sąsiedztwo encji")
def entity(name: str, graph: Graph = Depends(get_graph), extractor: EntityExtractor = Depends(get_extractor)) -> dict:
    found = entity_neighbourhood(graph, name, extractor)
    if found is None:
        raise HTTPException(404, f"entity {name!r} not found")
    return found


@app.get("/stats", summary="Statystyki grafu: węzły, relacje, embeddingi, domeny")
def stats(graph: Graph = Depends(get_graph)) -> dict:
    return graph_stats(graph)
