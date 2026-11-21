# PRAWDOWID — monorepo

System RAG do weryfikacji twierdzeń (tekst, link, zrzut ekranu). Wszystkie serwisy w jednym repozytorium,
spięte jednym stackiem Docker Compose i jedną bazą Postgres.

```
 Przeglądarka ──► proxy :4200 (nginx) ──► Frontend (Angular SSR)
                       │ /api, /ws, /health
                       ▼
                 APIGateway :8000 ──────────► ImageRecognitionServcie (OCR) :8001
                   │        │
                   │        └──── URL ──────► PageScrapper :8002 ──/ingest──┐
                   ▼                                                        ▼
         VerificationPipeline :8003 ◄──────── hybrid retrieval ──── Postgres 16 + pgvector
```

| Katalog | Serwis | Technologia |
|---|---|---|
| [APIGateway/](APIGateway/) | brzeg systemu: walidacja, rate limit, PII, audit log | FastAPI |
| [VerificationPipeline/](VerificationPipeline/) | twierdzenia → dowody → stanowisko → werdykt (reguły) | Kedro, FastAPI, LiteLLM |
| [PageScrapper/](PageScrapper/) | pobieranie stron + budowa korpusu ze źródeł | FastAPI, Playwright |
| [ContextGraph/](ContextGraph/) | graf wiedzy (dokumenty + zdarzenia z Bielika), profil `graph` | FastAPI, Neo4j |
| [ImageRecognitionServcie/](ImageRecognitionServcie/) | OCR obrazów | FastAPI, PaddleOCR |
| [Frontend/](Frontend/) | interfejs webowy | Angular 21 SSR |
| [Infra/](Infra/) | docker compose, proxy, start.sh, Dockerfile pipeline'u | Docker |

## Jak serwisy są połączone

Połączenia są zdefiniowane w jednym miejscu: [Infra/docker/docker-compose.yml](Infra/docker/docker-compose.yml).

| Skąd | Dokąd | Zmienna |
|---|---|---|
| proxy | frontend, gateway | [Infra/docker/proxy/nginx.conf](Infra/docker/proxy/nginx.conf) |
| APIGateway | VerificationPipeline `/verify` | `LLM_SERVICE_URL` |
| APIGateway | PageScrapper | `PAGE_SCRAPPER_URL` |
| APIGateway | OCR | `IMAGE_RECOGNITION_URL` |
| PageScrapper | VerificationPipeline `/ingest` | `RAG_INGEST_URL` |
| PageScrapper | ContextGraph `/ingest/document` | `make graph-sync` ([ContextGraph/README.md](ContextGraph/README.md)) |
| wszystkie | Postgres | `DATABASE_URL` |
| Frontend (`ng serve`) | APIGateway `:8000` | [Frontend/proxy.conf.json](Frontend/proxy.conf.json) |

## Start

```bash
make start      # pełny start: build, healthchecki, test połączeń, pierwsze pobranie źródeł
# albo
docker compose up -d --build     # z katalogu głównego (include Infra/docker/docker-compose.yml)
```

Aplikacja: http://localhost:4200 · Gateway (Swagger): http://localhost:8000/docs

Klucze LLM i źródeł: `Infra/docker/.env` (wzór w [.env.example](Infra/docker/.env.example)).
Szczegóły, wszystkie routy i rozwiązywanie problemów: [Infra/docker/README.md](Infra/docker/README.md).

## Praca na co dzień

```bash
make help                        # wszystkie cele
make logs S=api-gateway          # logi jednego serwisu
make build S=page-scrapper && make up
make dev-backend                 # backend w Dockerze...
make dev-frontend                # ...i Angular z hot reload na :4200 (proxy do gatewaya)
make test                        # testy serwisów Pythona (lokalnie)
make graph-up && make graph-sync # ContextGraph (Neo4j) obok stacku + dokumenty ze scrapera
```

CI ([.github/workflows/](.github/workflows/)) uruchamia testy tylko dla serwisów zmienionych w danym commicie
oraz waliduje i buduje stack przy zmianach w `Infra/` lub Dockerfile'ach.

## Historia

Monorepo powstało z połączenia repozytoriów `github.com/ornitolodzy/{APIGateway, Frontend,
ImageRecognitionServcie (develop), Infra, PageScrapper, VerificationPipeline}` z zachowaną pełną historią —
każde repo w swoim podkatalogu. Historia pliku: `git log --follow -- APIGateway/app/main.py`.
