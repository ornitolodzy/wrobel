# PRAWDOWID — lokalny stack (docker compose)

System RAG do weryfikacji twierdzeń. Ten katalog spina wszystkie serwisy w jeden stack:
**jeden Postgres**, kanały (web/Discord) oddzielone od logiki weryfikacji, a werdykt powstaje z jawnych reguł.

```
                       Obywatel
                          │
             ┌────────────┴────────────┐
        Web UI (Angular)          Discord bot
             │  :4200 (nginx proxy)     │
             └────────────┬─────────────┘
                          ▼
                  API Gateway :8000  ── walidacja, rate limit, maskowanie PII, audit log
     ┌───── URL ──────────┼──────── obraz ──────┐
     ▼                    │ tekst / twierdzenie  ▼
 PageScrapper :8002       │               OCR (PaddleOCR) :8001
 (URL fetcher, dane       ▼
  niezaufane)      VerificationPipeline :8003
     │             1. ekstrakcja twierdzeń  2. zapytania  3. hybrid retrieval (tsvector + wektor → RRF)
     │             4. stance  5. polityka werdyktu (REGUŁY, nie LLM)  6. odpowiedź  7. walidator cytatów
     │                    │  ▲                        │
     │ ingestion worker   │  │ hybrid search          └─► LLM przez LiteLLM (Claude / Mistral / Bielik)
     │ (cron 6h / ręcznie)▼  │
     └──── /ingest ────► Postgres 16 + pgvector + tsvector (jeden dla wszystkich)
                          gateway: checks, audit_events · scrapper: documents, scrape_runs
                          pipeline: source, document, chunk, corpus_version, check, case
```

Przepływ korpusu: PageScrapper pobiera źródła z jawnego katalogu ([sources.yaml](../../PageScrapper/sources.yaml)) i każdy
nowy lub zmieniony dokument wysyła do pipeline'u (`RAG_INGEST_URL` → `POST /ingest`). Pipeline tnie go na fragmenty,
liczy embedding i zapisuje do tabel `document` / `chunk`. Przy starcie pipeline dociąga też wszystko, co scrapper
zebrał wcześniej (tabela `documents`).

## Układ katalogów

Wszystkie serwisy są w jednym monorepo; pliki stacku są w `Infra/docker/`:

```
wrobel/                              <- katalog główny monorepo
├── docker-compose.yml               include: Infra/docker/docker-compose.yml (start z katalogu głównego)
├── Makefile                         make up / down / logs / test / dev-frontend
├── .github/workflows/               CI wszystkich serwisów (filtrowane po ścieżkach)
├── Infra/docker/
│   ├── docker-compose.yml           cały stack (projekt "wrobel")
│   ├── start.sh                     pełny start ze sprawdzeniem połączeń
│   ├── .env.example                 klucze LLM i źródeł (kopiuj do .env w tym katalogu)
│   ├── proxy/nginx.conf             reverse proxy :4200
│   └── verification-pipeline/       Dockerfile (+ .dockerignore) obrazu pipeline'u
│                                    i samodzielny compose do developmentu pipeline'u
└── APIGateway/  Frontend/  PageScrapper/  ImageRecognitionServcie/  VerificationPipeline/
```

## Serwisy

| Serwis | Katalog | Port na hoście | Adres w sieci Docker | Rola |
|---|---|---|---|---|
| `proxy` (nginx) | [proxy/](proxy/) | `4200` (publiczny) | — | wejście do aplikacji: `/` → frontend, `/api/*` → gateway |
| `frontend` | [Frontend/](../../Frontend/) | — | `frontend:4000` | Web UI (Angular SSR) |
| `api-gateway` | [APIGateway/](../../APIGateway/) | `8000` (publiczny) | `api-gateway:8000` | brzeg systemu, jedyny publiczny punkt API |
| `page-scrapper` | [PageScrapper/](../../PageScrapper/) | `127.0.0.1:8002` | `page-scrapper:8002` | pobieranie stron z linków + budowa bazy wiedzy |
| `ocr` | [ImageRecognitionServcie/](../../ImageRecognitionServcie/) | `127.0.0.1:8001` | `ocr:8000` | odczyt tekstu z obrazów |
| `verification-pipeline` | [VerificationPipeline/](../../VerificationPipeline/) | `127.0.0.1:8003` | `verification-pipeline:8000` | weryfikacja twierdzeń (RAG + reguły werdyktu), indeks korpusu |
| `postgres` (pgvector/pg16) | — | — | `postgres:5432` | jedna baza danych dla wszystkich serwisów |

Scrapper, OCR i pipeline nie mają autoryzacji, dlatego ich porty są dostępne tylko z Twojego komputera (`127.0.0.1`).
Z zewnątrz wszystko powinno iść przez gateway.

---

## Szybki start: jeden skrypt

Sklonuj monorepo i uruchom [start.sh](start.sh) (Git Bash na Windows, Linux albo macOS):

```bash
git clone <adres-monorepo> wrobel && cd wrobel
make start            # to samo co ./Infra/docker/start.sh
```

Skrypt:

1. sprawdza wymagania: Docker, Compose v2 (`docker compose` albo samodzielne `docker-compose`), działający silnik;
2. sprawdza, czy w monorepo są katalogi wszystkich serwisów i integracja pipeline'u z gatewayem;
3. tworzy `.env` z `.env.example` i ostrzega, jeśli nie ma klucza LLM;
4. buduje obrazy i uruchamia stack, czekając, aż wszystkie healthchecki będą zielone (przy błędzie wypisuje logi);
5. sprawdza połączenia gateway → pipeline / scrapper / OCR oraz proxy :4200;
6. jeśli baza wiedzy jest pusta, uruchamia pierwsze pobranie źródeł (scrapper → pipeline).

| Opcja | Działanie |
|---|---|
| `--no-build` | start bez przebudowy obrazów |
| `--no-scrape` | bez pierwszego pobrania źródeł |

Zmienne: `ADMIN_API_KEY`, `WAIT_TIMEOUT` (sekundy, domyślnie 900).

Bez skryptu: `docker compose up -d --build` działa zarówno z katalogu głównego monorepo, jak i z `Infra/docker`.

Poniżej to samo krok po kroku.

---

## Tutorial: uruchomienie

### 1. Wymagania

- Docker Desktop (z włączonym silnikiem). Sprawdź: `docker info`
- ok. 6–8 GB wolnego miejsca na obrazy (PaddleOCR i Chromium dla scrappera)

### 2. (Opcjonalnie) klucze: model LLM i dodatkowe źródła

```powershell
Copy-Item .env.example .env
```

Wszystkie wpisy w [.env.example](.env.example) są opcjonalne:

- **Bez klucza LLM** pipeline działa na regułach (`provider: mock`). Wyszukuje dowody w korpusie, ale nie ocenia
  stanowiska źródeł, więc każde twierdzenie kończy się werdyktem `insufficient_evidence` („brak dowodów”).
  Celowo nie zgaduje werdyktu.
- **Prawdziwe werdykty:** ustaw na przykład

  ```env
  PRAWDOWID_LLM_PROVIDER=litellm
  PRAWDOWID_LLM_MODEL=anthropic/claude-opus-5-5
  ANTHROPIC_API_KEY=sk-ant-...
  ```

  albo `mistral/mistral-large-latest` z `MISTRAL_API_KEY`. Po zmianie `.env` wystarczy
  `docker compose up -d verification-pipeline`, bez przebudowy.
- `GOOGLE_FACTCHECK_API_KEY`, `EUVSDISINFO_TOKEN`: bez nich te źródła scrappera są pomijane.

### 3. Zbuduj i uruchom

```powershell
cd wrobel\Infra\docker
docker compose up -d --build
```

Pierwszy build trwa kilka minut. Kolejne starty bez `--build` trwają kilka sekund.

### 4. Sprawdź, czy wszystko wstało

```powershell
docker compose ps
```

Projekt compose nazywa się **`wrobel`** (`name:` w `docker-compose.yml`), więc kontenery to `wrobel-api-gateway-1`,
`wrobel-page-scrapper-1` itd., a wolumen bazy to `wrobel_postgres-data`.

`postgres`, `ocr`, `verification-pipeline`, `page-scrapper` i `api-gateway` powinny mieć status `(healthy)`.
Kolejność startu: postgres → pipeline → scrapper → gateway.
Model OCR ładuje się w tle; gotowość sprawdzisz pod http://127.0.0.1:8001/readyz.

```powershell
curl.exe http://localhost:8000/health/services
```

Oczekiwany wynik: `llm` (czyli VerificationPipeline), `page_scrapper` i `image_recognition` mają `"status":"ok"`.

### 5. Otwórz aplikację

http://localhost:4200

### 6. Przetestuj weryfikację z terminala

Najprościej przez Swagger: http://localhost:8000/docs (przycisk **Try it out** przy każdym endpoincie).

**PowerShell** (Windows):

```powershell
# twierdzenie tekstowe (UTF-8, żeby polskie znaki doszły poprawnie)
$body = @{ text = "RCB wysłało dziś alert o ataku z powietrza" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/verify/claim `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))

# link: gateway -> scrapper pobiera stronę -> pipeline weryfikacji
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/verify/url `
  -ContentType "application/json" -Body (@{ url = "https://www.gov.pl/web/rcb" } | ConvertTo-Json)

# obraz: gateway -> OCR -> pipeline weryfikacji
curl.exe -X POST http://localhost:8000/api/v1/verify/image -F "file=@screenshot.png"
```

> W PowerShellu 5.1 samo `curl` to alias `Invoke-WebRequest`. Do prawdziwego curla pisz `curl.exe`,
> ale JSON w argumentach lepiej wysyłać przez `Invoke-RestMethod` jak wyżej.
> Krzaki zamiast polskich znaków w odpowiedzi to wyłącznie kwestia wyświetlania w konsoli.

**Git Bash / Linux / macOS:**

```bash
curl -X POST http://localhost:8000/api/v1/verify/claim -H 'Content-Type: application/json'   -d '{"text":"RCB wysłało dziś alert o ataku z powietrza"}'
curl -X POST http://localhost:8000/api/v1/verify/url -H 'Content-Type: application/json'   -d '{"url":"https://www.gov.pl/web/rcb"}'
curl -X POST http://localhost:8000/api/v1/verify/image -F 'file=@screenshot.png'
```

Bez klucza LLM (krok 2) werdykt będzie `insufficient_evidence`, ale w odpowiedzi zobaczysz prawdziwe wyodrębnione
twierdzenia. Jeśli pipeline zgłosi problem techniczny (np. zły klucz), status to `error` lub `api_limit` z dopiskiem
„to nie jest ocena prawdziwości treści”. Nigdy nie jest to fałszywy werdykt.

**Zmiana modelu w locie (bez przebudowy)** — gateway przekazuje swój model do pipeline'u przy każdym zapytaniu:

```powershell
$admin = @{ "X-API-Key" = "change-me-admin" }
# przełącz wszystkie role pipeline'u na Claude (wymaga ANTHROPIC_API_KEY w .env)
Invoke-RestMethod -Method Patch -Uri http://localhost:8000/api/v1/config -Headers $admin -ContentType "application/json" `
  -Body '{"llm_provider":"anthropic","llm_model":"claude-opus-5-5"}'
# powrót do konfiguracji pipeline'u (.env / conf)
Invoke-RestMethod -Method Patch -Uri http://localhost:8000/api/v1/config -Headers $admin -ContentType "application/json" `
  -Body '{"llm_provider":"pipeline","llm_model":"conf"}'
```

### 7. Zapełnij bazę wiedzy (korpus)

Po starcie korpus jest pusty. Scrapper pobiera źródła automatycznie co 6 godzin (`SCRAPE_INTERVAL_MINUTES=360`).
Żeby pobrać je od razu, użyj klucza admina (domyślnie `change-me-admin`, ustawiony w `docker-compose.yml`):

```powershell
$admin = @{ "X-API-Key" = "change-me-admin" }
# wszystkie źródła (albo np. -Body '{"source_ids":["rcb-komunikaty"],"max_items_per_source":5}')
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/scrap/all -Headers $admin -ContentType "application/json" -Body '{}'
Invoke-RestMethod http://localhost:8000/api/v1/scrap/runs -Headers $admin   # postęp
Invoke-RestMethod http://localhost:8000/api/v1/corpus/stats                 # ile dokumentów / domen
```

```bash
curl -X POST http://localhost:8000/api/v1/scrap/all -H 'X-API-Key: change-me-admin' -H 'Content-Type: application/json' -d '{}'
curl http://localhost:8000/api/v1/scrap/runs -H 'X-API-Key: change-me-admin'
curl http://localhost:8000/api/v1/corpus/stats
```

Pełne pobranie bez dodatkowych kluczy to około 280 dokumentów z 10 domen. Każdy dokument od razu trafia do indeksu
pipeline'u. Stan indeksu sprawdzisz pod http://127.0.0.1:8003/corpus/stats (dokumenty, fragmenty, wersja korpusu).
Ręczne przeindeksowanie wszystkiego, co zebrał scrapper: `POST http://127.0.0.1:8003/ingest/sync`.

### 8. Logi, restart, zatrzymanie

```powershell
docker compose logs -f api-gateway verification-pipeline   # logi wybranych serwisów
docker compose up -d --build page-scrapper         # przebudowa jednego serwisu po zmianach w kodzie
docker compose down                                # zatrzymanie (dane w bazie zostają)
docker compose down -v                             # zatrzymanie + usunięcie bazy (wolumen postgres-data)
```

---

## Wszystkie routy

Legenda: 🔑 wymaga nagłówka `X-API-Key: <ADMIN_API_KEY>`. ⏱ objęte limitem zapytań (domyślnie 30/min na klienta).
Opcjonalny nagłówek `X-Channel: web|discord` oznacza kanał, z którego przyszło zapytanie.

### Interfejsy dokumentacji (Swagger)

| Serwis | URL |
|---|---|
| API Gateway | http://localhost:8000/docs |
| PageScrapper | http://127.0.0.1:8002/docs |
| OCR | http://127.0.0.1:8001/docs |
| VerificationPipeline | http://127.0.0.1:8003/docs |

### Frontend / proxy — `http://localhost:4200`

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/` | aplikacja webowa |
| * | `/api/*` | przekierowanie do API Gateway |
| GET | `/health`, `/health/services` | przekierowanie do health gatewaya |
| WS | `/ws` | WebSocket do gatewaya |

### API Gateway — `http://localhost:8000`

**Health**

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/health` | czy gateway działa |
| GET | `/health/services` | stan serwisów docelowych: LLM, OCR, scrapper |

**Weryfikacja** ⏱

| Metoda | Ścieżka | Body | Opis |
|---|---|---|---|
| POST | `/api/v1/verify/claim` | `{"text": "..."}` (maks. 5000 znaków) | weryfikacja twierdzenia tekstowego |
| POST | `/api/v1/verify/url` | `{"url": "https://..."}` | scrapper pobiera stronę, potem weryfikacja jej treści |
| POST | `/api/v1/verify/image` | multipart `file` (maks. 10 MB) | OCR, potem weryfikacja odczytanego tekstu |
| POST | `/api/v1/text-recognition-from-img` | multipart `file` | sam OCR, bez weryfikacji |

Odpowiedź (`CheckResponse`) zawiera `check_id`, `status` (`completed` / `needs_clarification` / `error` / `api_limit`), listę `claims` (każde z werdyktem `confirmed` / `false` / `misleading` / `insufficient_evidence` / `not_verifiable`, źródłami i ograniczeniami) oraz pola `ocr_text`, `page` i `mock`.

**Zapisy sprawdzeń**

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/v1/checks/{check_id}` | zapis przebiegu sprawdzenia |
| DELETE | `/api/v1/checks/{check_id}` | usunięcie zapisu |

**Baza wiedzy** (przekazywane do scrappera)

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/v1/sources` ⏱ | jawny katalog źródeł z liczbą dokumentów |
| GET | `/api/v1/corpus/stats` ⏱ | statystyki korpusu: dokumenty, domeny, typy źródeł |
| POST | `/api/v1/scrap/all` 🔑 | uruchamia pobieranie źródeł; body opcjonalne: `{"source_ids": [...], "max_items_per_source": 10}` |
| GET | `/api/v1/scrap/runs?limit=` 🔑 | historia uruchomień pobierania |
| GET | `/api/v1/scrap/runs/{run_id}` 🔑 | stan jednego uruchomienia |
| GET | `/api/v1/documents?source_id=&limit=&offset=` 🔑 | lista dokumentów (podgląd treści) |
| GET | `/api/v1/documents/{doc_id}` 🔑 | dokument z pełną treścią |

**Konfiguracja / administracja**

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/v1/config` | aktualne parametry (model LLM, limity, `mock_mode`) |
| PATCH | `/api/v1/config` 🔑 | zmiana parametrów bez przebudowy, np. `{"llm_model": "...", "mock_mode": false}` |
| GET | `/api/v1/admin/events` 🔑 | ostatnie zdarzenia z audit logu |

### PageScrapper — `http://127.0.0.1:8002` (tylko lokalnie)

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/health` | czy działa |
| POST | `/scrape` | pobiera jedną stronę: `{"url": "...", "render": "auto"\|"never"\|"always"}` |
| POST | `/scrap/all` | pobieranie źródeł z [sources.yaml](../../PageScrapper/sources.yaml) w tle (202) |
| GET | `/scrap/runs` | historia uruchomień |
| GET | `/scrap/runs/{run_id}` | stan uruchomienia (new / updated / unchanged / errors per źródło) |
| GET | `/sources` | katalog źródeł do audytu |
| GET | `/stats` | statystyki korpusu |
| GET | `/documents` | dokumenty (podgląd treści) |
| GET | `/documents/{id}` | dokument z pełną treścią |

Błędy `/scrape` mają postać `{"detail": {"code": "...", "message": "..."}}`:

- 422 — `invalid_url`, `blocked_url`, `dns_error`, `no_text`
- 415 — `unsupported_content`
- 502 / 504 — `http_error`, `fetch_error`, `timeout`

Adresy prywatne i wewnętrzne (np. `postgres`, `localhost`) są blokowane, co chroni przed SSRF.

### VerificationPipeline — `http://127.0.0.1:8003` (tylko lokalnie)

| Metoda | Ścieżka | Kto woła | Opis |
|---|---|---|---|
| GET | `/health` | gateway | czy działa |
| GET | `/config` | ręcznie | aktywna konfiguracja: model per rola, backend retrieval, embeddingi, progi |
| POST | `/verify` | gateway (`LLM_SERVICE_URL`) | weryfikacja w kontrakcie gatewaya: `{check_id, input_type, content, options, model}` |
| POST | `/check` | ręcznie / testy | natywne API pipeline'u: `{"text": "..."}`; pełny wynik z `trace` (każdy krok, czasy, kandydaci, odrzucone fragmenty) |
| POST | `/ingest` | PageScrapper (`RAG_INGEST_URL`) | przyjmuje dokument: chunk, embedding, zapis do `document` / `chunk` |
| POST | `/ingest/sync` | ręcznie | indeksuje wszystkie dokumenty z tabeli scrappera (idempotentne) |
| GET | `/corpus/stats` | ręcznie | dokumenty, fragmenty, typy źródeł, wersja korpusu, model embeddingów |

Mapowanie werdyktów pipeline'u na kontrakt gatewaya: `potwierdzone` → `confirmed`, `falszywe` → `false`,
`wprowadzajace_w_blad` → `misleading`, `brak_dowodow` → `insufficient_evidence`, `nie_podlega_weryfikacji` → `not_verifiable`.
Źródła w odpowiedzi mają dosłowny cytat (`fragment`), który przeszedł walidator cytatów.

### OCR — `http://127.0.0.1:8001` (tylko lokalnie)

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/healthz` | czy proces działa |
| GET | `/readyz` | czy model jest załadowany (503, dopóki się ładuje) |
| GET | `/metrics` | metryki Prometheus |
| POST | `/v1/ocr` | multipart `file` (+ opcjonalnie `langs=pl,en`, `output=text\|blocks`) |

---

## Szybki test: czy wszystko działa

| Co sprawdzasz | URL | Oczekiwany wynik |
|---|---|---|
| Aplikacja | http://localhost:4200 | strona się ładuje |
| Gateway | http://localhost:8000/health | `{"status":"ok"}` |
| Połączenia gatewaya | http://localhost:8000/health/services | scrapper i OCR mają `ok` |
| Scrapper | http://127.0.0.1:8002/health | `{"status":"ok"}` |
| OCR | http://127.0.0.1:8001/readyz | `"status":"ready"` |
| Pipeline | http://127.0.0.1:8003/health | `{"status":"ok"}` |
| Indeks korpusu | http://127.0.0.1:8003/corpus/stats | dokumenty i fragmenty > 0 po pobraniu źródeł |
| Konfiguracja pipeline'u | http://127.0.0.1:8003/config | model per rola, `retrieval_backend: postgres` |
| Katalog źródeł | http://localhost:8000/api/v1/sources | lista źródeł JSON |

## Najważniejsza konfiguracja (`docker-compose.yml`)

| Zmienna | Serwis | Domyślnie | Znaczenie |
|---|---|---|---|
| `ADMIN_API_KEY` | api-gateway | `change-me-admin` | klucz do endpointów 🔑; **zmień poza środowiskiem lokalnym** |
| `LLM_SERVICE_URL` | api-gateway | `http://verification-pipeline:8000` | adres pipeline'u weryfikacji; pusty = odpowiedzi MOCK |
| `LLM_PROVIDER` / `LLM_MODEL` | api-gateway | `pipeline` / `conf` | model wysyłany do pipeline'u; `pipeline` = użyj konfiguracji pipeline'u; zmiana w locie przez `PATCH /api/v1/config` |
| `DOWNSTREAM_TIMEOUT_SECONDS` | api-gateway | `90` | limit czasu na odpowiedź pipeline'u (do 3 wywołań LLM) |
| `PAGE_SCRAPPER_URL` | api-gateway | `http://page-scrapper:8002` | adres scrappera |
| `IMAGE_RECOGNITION_URL` | api-gateway | `http://ocr:8000` | adres OCR |
| `CORS_ORIGINS` | api-gateway | `http://localhost:4200` | dozwolone originy |
| `SCRAPE_INTERVAL_MINUTES` | page-scrapper | `360` | co ile minut automatyczne pobieranie (0 = tylko ręcznie) |
| `SCRAPE_ON_STARTUP` | page-scrapper | `false` | czy pobierać źródła zaraz po starcie |
| `RAG_INGEST_URL` | page-scrapper | `http://verification-pipeline:8000/ingest` | gdzie wysyłać nowe dokumenty do indeksu |
| `PRAWDOWID_LLM_PROVIDER` / `PRAWDOWID_LLM_MODEL` | verification-pipeline | z `.env`; puste = `mock` | model LLM wszystkich ról (LiteLLM: `anthropic/...`, `mistral/...`, `openai/...` + `PRAWDOWID_LLM_API_BASE`) |
| `PRAWDOWID_ENV` | verification-pipeline | `docker` | nakładka [conf/docker/parameters.yml](../../VerificationPipeline/conf/docker/parameters.yml): retrieval z Postgresa, embeddingi `mock` |
| `PRAWDOWID_SYNC_ON_STARTUP` | verification-pipeline | `true` | przy starcie indeksuje dokumenty zebrane wcześniej przez scrapper |
| `OCR_LANGS` | ocr | `pl,en` | języki OCR |

## Rozwiązywanie problemów

- **`Conflict. The container name ... is already in use`** — został stary kontener po przerwanym starcie.
  Sprawdź `docker ps -a`, usuń kontener ze statusem `Created` (`docker rm <id>`) i uruchom `docker compose up -d` jeszcze raz.
- **`/health/services` pokazuje scrapper lub OCR jako niedostępny** — sprawdź `docker compose logs page-scrapper` / `docker compose logs ocr`.
  OCR po starcie potrzebuje chwili na załadowanie modelu.
- **Każdy werdykt to `insufficient_evidence`** — brak klucza LLM (krok 2) albo pusty korpus (krok 7).
  Szczegóły pokaże `POST http://127.0.0.1:8003/check` z `{"text": "..."}`: w `trace` widać, ile fragmentów znalazł
  retrieval i na którym kroku zabrakło dowodów.
- **Odpowiedź `error` z `upstream_error 502` z serwisu `llm`** — pipeline nie mógł wywołać modelu (np. brak lub zły klucz).
  Sprawdź `docker compose logs verification-pipeline` (wpis `pipeline error in step ...`).
- **`/api/v1/scrap/all` zwraca 403** — brakuje nagłówka `X-API-Key` albo `ADMIN_API_KEY` jest pusty.
- **Zajęty port 4200 / 8000** — zmień mapowanie `ports` w `Infra/docker/docker-compose.yml`.

Szczegóły poszczególnych serwisów są w ich README:
[APIGateway](../../APIGateway/README.md) · [VerificationPipeline](../../VerificationPipeline/README.md) · [PageScrapper](../../PageScrapper/README.md) · [SOURCES.md](../../PageScrapper/SOURCES.md) · [OCR](../../ImageRecognitionServcie/README.md) · [Frontend](../../Frontend/README.md)
