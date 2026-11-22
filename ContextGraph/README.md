# ContextGraph

Baza grafowa (Neo4j) z wiedzą dla systemu Prawdowid. Jedyne źródło kontekstu dla VerificationPipeline
(ADR 0005 tam: weryfikacja bez dostępu do sieci, kontekst tylko z grafu).

```
PageScrapper ──POST /ingest/document──► ContextGraph ──► Neo4j ◄── VerificationPipeline (tylko odczyt)
ImageRecognitionService ──POST /ingest/image──┘          │
Bielik (offline) ──► POST /events/extract (warstwa 2) ───┘
```

Graf ma dwie warstwy ([docs/graph-model.md](docs/graph-model.md)):

1. **Dokumenty (nic nie ginie).** Źródło → dokument → fragmenty z dosłownym tekstem, data publikacji i pobrania,
   wydawca, typ źródła, licencja, ocena fact-checkera, embeddingi `bge-m3`, przedruki / cytowania / sprostowania.
   Schemat zgodny z kontraktem pipeline'u (`Document`, `PART_OF`, `PUBLISHED_BY`, `dup_group`, `fetched_at`, ...).
2. **Zdarzenia („ktoś zrobił / coś się wydarzyło”).** Bielik wyciąga z fragmentów: kto, co, wobec kogo, kiedy, gdzie.
   Każde zdarzenie ma dosłowny cytat z fragmentu, inaczej jest odrzucane. Daty zawsze z rokiem.

## Start

```bash
docker compose up -d --build                 # Neo4j 5.26 + API na 127.0.0.1:8100, Browser: http://localhost:7474
python scripts/sync_from_scraper.py \
  --scraper http://127.0.0.1:8002 --graph http://127.0.0.1:8100   # jednorazowo: wszystko, co scraper już ma
```

Nowe dokumenty scraper wysyła sam: w jego `.env` `RAG_INGEST_URL=http://host.docker.internal:8100/ingest/document`.

Bez Dockera:

```bash
docker compose up -d neo4j
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn context_graph.api:app --port 8100
.venv/bin/python scripts/seed_example.py    # fikcyjne dane demo (domeny *.example)
```

Porty Neo4j i API są dostępne tylko z `127.0.0.1`. Na serwerze zmień `NEO4J_PASSWORD` (domyślne hasło jest w repo).

## Embeddingi i Bielik (gdy jest dostęp do serwera)

Tunel do Ollamy na serwerze z GPU (Tailscale), okno zostaw otwarte:

```bash
ssh -N -L 127.0.0.1:21434:127.0.0.1:11434 <login>@<serwer>
curl http://127.0.0.1:21434/api/tags        # lista modeli: bielik, bge-m3
```

Na Windowsie z Dockerem port 11435 bywa zarezerwowany przez Hyper-V (`bind ... Permission denied`,
sprawdź `netsh interface ipv4 show excludedportrange protocol=tcp`), dlatego lokalnie używamy 21434.

W `.env` (wzór: `.env.example`), z portem tunelu:

```bash
EMBEDDINGS_PROVIDER=ollama                   # bge-m3 przez tunel SSH, ten sam model co pipeline
EMBEDDINGS_API_BASE=http://host.docker.internal:21434
EVENTS_PROVIDER=openai                       # Bielik przez endpoint zgodny z OpenAI (vLLM albo Ollama /v1)
EVENTS_API_BASE=http://host.docker.internal:21434/v1
EVENTS_MODEL=SpeakLeash/bielik-11b-v2.3-instruct:Q4_K_M
```

Potem `docker compose up -d api` i:

```bash
curl -X POST "localhost:8100/embeddings/backfill?limit=5000"   # wektory dla fragmentów zapisanych bez nich
curl -X POST "localhost:8100/events/extract?limit=200"         # warstwa 2, wsadowo (można powtarzać)
```

Bez embeddingów pipeline i tak działa na wyszukiwaniu pełnotekstowym. Węzeł `CorpusVersion` (nazwa modelu)
powstaje dopiero przy pierwszym embeddingu, bo pipeline odrzuca graf z innym modelem niż swój.

## API

| Endpoint | Opis |
|---|---|
| `POST /ingest/document` | dokument z PageScrappera: `{url, text, title, publisher, source_id, source_type, published_at, retrieved_at, language, content_hash, links[], corrects[], rating, license}` |
| `POST /ingest/page` | starsza nazwa tego samego |
| `POST /ingest/image` | wynik OCR: `{image_id, path, ocr_text, page_url}` |
| `POST /embeddings/backfill` | liczy brakujące embeddingi (`?limit=`) |
| `POST /events/extract` | warstwa 2: zdarzenia z kolejnych fragmentów (`?limit=`) |
| `POST /relations` | `SUPPORTS` / `CONTRADICTS` (fragment→fragment) lub `CORRECTS` / `CITES` / `REPRINTS` (dokument→dokument), z dosłownym `quote` |
| `POST /context` | odczyt kontekstu: `{text, entities[], hops: 1-3, limit, save}` → fragmenty ze ścieżkami + zdarzenia |
| `GET /entities/{name}` | sąsiedztwo encji |
| `GET /stats` | dokumenty, domeny, typy źródeł, ile fragmentów ma embeddingi |
| `GET /health` | ping Neo4j |

```bash
curl -s -X POST localhost:8100/context -H 'content-type: application/json' \
  -d '{"text": "Naruszono przestrzeń powietrzną dnia 20.11", "hops": 2, "limit": 8}'
```

Każdy element odpowiedzi ma `path` (relacje, po których go znaleziono) i `flags`
(`corrected`, `correction`, `contradicted`, `reprinted_at:<url>`, ...), a `events` to zdarzenia z tych fragmentów.

## Testy

```bash
.venv/bin/pytest                                              # testy jednostkowe (bez bazy)
NEO4J_TEST_URI=bolt://localhost:7687 .venv/bin/pytest         # + integracja; UWAGA: czyści bazę
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`tests/test_contract.py` uruchamia na grafie dokładnie te zapytania Cypher, których używa VerificationPipeline
(`src/prawdowid/retrieval/neo4j.py`). Po integracji załaduj dane ponownie (`scripts/sync_from_scraper.py`).

## Struktura

```
src/context_graph/
  api.py          FastAPI
  ingest.py       zapis dokumentów, obrazów, relacji i embeddingów (MERGE, idempotentnie)
  events.py       warstwa 2: zdarzenia z LLM, tylko z dosłownym cytatem
  context.py      odczyt kontekstu: seed → ekspansja → ranking → ścieżki → zdarzenia
  embeddings.py   bge-m3 przez Ollamę (ten sam call co pipeline)
  extract.py      encje (RegexExtractor), daty z rokiem
  stats.py        GET /stats
  chunking.py, textnorm.py, schema.py, db.py, config.py, models.py, example_data.py
scripts/
  sync_from_scraper.py   jednorazowe przeniesienie dokumentów z PageScrappera
  seed_example.py        fikcyjne dane demo
```

## Ograniczenia

- Brak polskiego analizatora w Neo4j: odmianę obsługują prefiksy przy zapytaniu i rdzenie kluczy encji.
  Ekstraktor regułowy jest prosty; spaCy `pl_core_news` można podpiąć przez `EntityExtractor`.
- `SUPPORTS` / `CONTRADICTS` nie powstają automatycznie, dodaje je zewnętrzny proces przez `/relations`.
- Warstwa 2 zależy od jakości Bielika; zdarzenia bez dosłownego cytatu są odrzucane, więc gorszy model daje
  mniej zdarzeń, a nie zmyślone.
- Testy integracyjne czyszczą bazę, w której pracują.
