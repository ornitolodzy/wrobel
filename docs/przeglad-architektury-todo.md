# Przegląd architektury — lista zadań (TODO)

Status: **odłożone do realizacji**. Źródło: przegląd kodu gałęzi `feat/upgraded-rag` (HEAD `1b51c35`, 2026-11-22).
Ścieżki są względem katalogu głównego monorepo. ✔ = potwierdzone bezpośrednio w kodzie, [?] = do potwierdzenia.

Priorytety:
- **P0** — granica zaufania: możliwe fałszywe werdykty lub przejęcie konfiguracji z internetu.
- **P1** — awarie techniczne udające werdykt, błędy poprawności werdyktu, dostępność.
- **P2** — średnie.
- **P3** — higiena.

Najgroźniejszy łańcuch to P0-1 + P0-2 + P0-3: publiczny gateway → SSRF w scrapperze → nieuwierzytelniony `/ingest`
ufający źródłu po samym URL → zatruty korpus → `potwierdzone`/`falszywe` na podstawie podrzuconego tekstu.

---

## P0 — granica zaufania

- [ ] **P0-1. Gateway publiczny + jawny klucz admina** ✔
  - Problem:
    - `docker-compose.yml:47` wystawia `"8000:8000"` na wszystkich interfejsach.
    - `docker-compose.yml:51` ma literał `ADMIN_API_KEY: change-me-admin`, którego `.env` nie nadpisuje.
    - Ten sam klucz jest w `README.md` i `start.sh:23`.
    - Endpointy admina są dostępne też przez nginx `:4200`.
    - `require_admin` nie ma limitu prób.
  - Skutek dla każdego, kto zna klucz:
    - `PATCH /api/v1/config` włącza `mock_mode` dla wszystkich albo ustawia dowolny `llm_model` na naszych kluczach API (`VerificationPipeline/src/prawdowid/api/gateway.py:89-94`);
    - odczyt `/admin/events` (z IP);
    - uruchamianie `/scrap/all`.
  - Do zrobienia:
    - [ ] port gatewaya `127.0.0.1:8000:8000`; ruch publiczny tylko przez proxy;
    - [ ] `ADMIN_API_KEY: ${ADMIN_API_KEY:?}` w compose, wpis w `.env.example`;
    - [ ] `start.sh` ma wczytywać `.env` i nie wypisywać klucza;
    - [ ] limit prób dla `require_admin`;
    - [ ] usunąć klucz z README.

- [ ] **P0-2. SSRF w PageScrapper (DNS rebinding + Chromium)** ✔ (fetch) / [?] (render)
  - Problem: `PageScrapper/app/fetch.py:92-100` sprawdza IP po rozwiązaniu nazwy, a httpx przy połączeniu (`:198`) rozwiązuje DNS ponownie. Domena z TTL 0 omija blokadę.
  - Kto: każdy użytkownik internetu przez `POST /api/v1/verify/url`. Treść odpowiedzi wraca do niego.
  - Chromium (`render.py`):
    - guard nie sprawdza przekierowań subzasobów [?];
    - werdykt hosta jest cache'owany na zawsze (`:117-125`);
    - WebSockety nie są przechwytywane;
    - działa z `--no-sandbox` (`:46`).
  - Sieć Dockera jest płaska, więc osiągalne są `verification-pipeline:8000/ingest`, `context-graph:8100` i Neo4j.
  - Do zrobienia:
    - [ ] przypiąć połączenie do sprawdzonego IP (własny transport/resolver httpx);
    - [ ] Chromium przez egress proxy z kontrolą IP na każdym połączeniu albo w osobnej sieci Dockera;
    - [ ] usunąć `--no-sandbox` (albo uzasadnić i odizolować);
    - [ ] cache `_host_ok` z TTL;
    - [ ] podział compose na sieci: `frontend-net` / `backend-net` / `scraper-egress`;
    - [ ] test jednostkowy rebinding (resolver: publiczne → prywatne IP).

- [ ] **P0-3. `/ingest` bez autoryzacji, zaufanie wyliczane z URL** ✔
  - Problem:
    - `VerificationPipeline/src/prawdowid/api/gateway.py:174-179` nie ma auth ani limitów rozmiaru.
    - `corpus.py:79-81` (`match_source(doc["url"])`) sprawia, że tekst z `url=https://www.gov.pl/web/rcb/x` staje się źródłem pierwotnym RCB (waga 1.0).
    - `policy.py:100`: jedno źródło pierwotne wystarcza do `potwierdzone`.
  - Wzmacniacze:
    - live search zapisuje `final_url` z obcej domeny pod zaufanym `source_type` (`PageScrapper/app/live_search.py:333`);
    - `extra.live_search` nie jest wysyłane do pipeline'u (`PageScrapper/app/ingest.py:50-64`).
  - Do zrobienia:
    - [ ] wspólny sekret (nagłówek) dla `/ingest`, `/ingest/sync`, endpointów zapisu ContextGraph;
    - [ ] limity rozmiaru `url`/`text`;
    - [ ] jawne pochodzenie dokumentu (`catalog` / `live` / `push`) zapisywane w bazie; dokumenty `live` z niższą wagą;
    - [ ] live search: odrzucać `final_url` spoza domeny źródła;
    - [ ] przekazywać `extra` do pipeline'u.

---

## P1 — awarie ≠ werdykt, poprawność werdyktu, dostępność

- [ ] **P1-1. Awaria retrieval zamienia się w „brak dowodów” ze statusem ok** ✔
  - Problem: `VerificationPipeline/src/prawdowid/pipelines/verification/nodes/retrieval.py:59-64` łapie `except Exception` i ustawia `found = []`. Łamie to zasadę „status techniczny ≠ werdykt” (`models.py:31`).
  - Wyzwalacze:
    - Postgres down;
    - Neo4j nieuruchomiony przy `backend=neo4j`;
    - `ValidationError` dla chunków `section="ocr"` z ContextGraph (`retrieval/neo4j.py:169`).
  - Do zrobienia:
    - [ ] błąd retrieval ma dawać status `error`;
    - [ ] obsłużyć `section="ocr"` (mapowanie albo pominięcie);
    - [ ] test regresyjny.

- [ ] **P1-2. Cache providerów LLM miesza ustawienia ról** ✔
  - Problem: `VerificationPipeline/src/prawdowid/llm/registry.py:29` używa klucza `(provider, model, api_base)`, więc `stance`/`compose` dziedziczą `max_tokens`, `timeout`, `temperature` od `claims`. Długie odpowiedzi stance są ucinane, potem `LLMError` i 502. Cache rośnie bez limitu.
  - Do zrobienia:
    - [ ] klucz cache = rola + pełny `role_cfg` (albo przekazywać ustawienia per wywołanie);
    - [ ] limit rozmiaru cache;
    - [ ] test z jednym modelem dla wszystkich ról.

- [ ] **P1-3. Domyślny start nadpisuje seed bge-m3 wektorami mock** ✔
  - Problem:
    - Seed ma `bge-m3`, a `VerificationPipeline/conf/docker/parameters.yml:19` ustawia `provider: mock`, przy `PRAWDOWID_SYNC_ON_STARTUP=true`.
    - `corpus.py:117,163` usuwa chunki i przelicza embeddingi.
    - W trakcie `/health` zwraca ok, a retrieval daje `[]`.
    - CTE wektorowe nie filtruje po `corpus_version` (`retrieval/postgres.py:23-27`).
  - Do zrobienia:
    - [ ] spójny domyślny model z seedem albo blokada przeliczenia bez jawnej flagi;
    - [ ] filtr `corpus_version` w zapytaniu wektorowym;
    - [ ] readiness po zakończeniu syncu;
    - [ ] test: czysty wolumen → `corpus/stats` nadal `bge-m3`.

- [ ] **P1-4. Walidator cytatów przepuszcza zlepki słów i nagłówki** ✔
  - Problem:
    - `VerificationPipeline/src/prawdowid/pipelines/verification/nodes/citations.py:44-66` dzieli cytat po „…” i każdy segment szuka osobno, więc pojedyncze rozrzucone słowa przechodzą.
    - Przechodzi cytat z pominiętym przeczeniem.
    - `naglowek` nie ma minimum, a polityka ignoruje `section`.
  - Do zrobienia:
    - [ ] maks. liczba segmentów i min. długość każdego segmentu;
    - [ ] sprawdzanie przeczenia tuż przed cytatem;
    - [ ] nagłówek nie może samodzielnie dawać `potwierdzone`;
    - [ ] testy.

- [ ] **P1-5. Przedruki liczone jako niezależne potwierdzenia**
  - Problem: `corpus.py:161` grupuje duplikaty tylko po identycznym hashu. `assign_dup_groups` (`text.py:67-79`) działa tylko offline. `policy.py:94,100` przy dwóch grupach wydaje werdykt.
  - Do zrobienia:
    - [ ] near-dup na ścieżce `/ingest` (reużyć `assign_dup_groups`).

- [ ] **P1-6. Maskowanie PII psuje liczby w twierdzeniach** ✔
  - Problem:
    - Gateway (`APIGateway/app/pii.py:7-8`): każda liczba 9-cyfrowa staje się `[TELEFON]`, 11-cyfrowa `[PESEL]`, 16-cyfrowa `[KARTA]`.
    - Pipeline (`nodes/normalize.py:15`): „500 000 000 zł” staje się „[TEL] zł”.
  - Do zrobienia:
    - [ ] kontekstowe wzorce (walidacja sumy kontrolnej PESEL / Luhn, wykluczenie kwot i jednostek);
    - [ ] testy z kwotami.

- [ ] **P1-7. „Weak-evidence guard” nigdy nie blokuje `potwierdzone`**
  - Problem: `APIGateway/app/verification.py:353-371`, a każde `potwierdzone` ma siłę co najmniej `średnia` (`policy.py:191-196`).
  - Do zrobienia:
    - [ ] strażnik oparty na pochodzeniu dowodu (live vs katalog), nie tylko na sile.

- [ ] **P1-8. Ocena fact-checkera bez twierdzenia, którego dotyczy**
  - Problem: chunk oceny zawiera samą etykietę (`retrieval/neo4j.py:193-197`). CheckedClaim (commit `b510c64`) nie jest czytany przez pipeline. Na ścieżce Postgres `claims` są gubione przez `IngestDocument`.
  - Do zrobienia:
    - [ ] dołączać tekst ocenianego twierdzenia do dowodu;
    - [ ] przyjmować `claims` w `/ingest`.

- [ ] **P1-9. Jeden wspólny limit 30/min dla całego serwisu** ✔
  - Problem:
    - `TRUSTED_PROXIES: 127.0.0.1` (`docker-compose.yml:53`), a ruch idzie z kontenera nginx, więc `_client_ip` (`APIGateway/app/deps.py:39`) zwraca IP proxy dla wszystkich.
    - Discord: jedno IP bota dla wszystkich użytkowników.
    - Frontend automatycznie woła `/verify/live` (`Frontend/src/app/pages/home/home.ts:214`).
  - Do zrobienia:
    - [ ] `TRUSTED_PROXIES` i `FORWARDED_ALLOW_IPS` = podsieć proxy (stała podsieć w compose);
    - [ ] klucz i osobna kwota dla bota Discord;
    - [ ] test z konfiguracją jak w compose.

- [ ] **P1-10. Łańcuch timeoutów się nie domyka**
  - Problem:
    - `APIGateway/app/services.py:44` ustawia timeout per faza httpx i per wywołanie, więc sumy (URL: 180 + 180 s, live: 90 + 180 + 180 s) przekraczają nginx 200/300 s.
    - Discord 120 s < gateway 180 s.
    - Frontend nie ma abortu.
    - Pipeline: `run_in_threadpool` nie da się anulować, a `compose` liczy sekwencyjnie.
    - README (90 s), kod (30 s) i compose (180 s) się różnią.
  - Do zrobienia:
    - [ ] całkowity budżet czasu na żądanie w gatewayu (< nginx);
    - [ ] Discord > gateway;
    - [ ] `AbortController` we froncie;
    - [ ] równoległe `compose`;
    - [ ] ujednolicić dokumentację.

- [ ] **P1-11. Scrapper: `_running` może zostać `True` na zawsze** ✔
  - Problem: `PageScrapper/app/ingest.py:140-143`: jeśli `save_run` w `finally` rzuci wyjątek, scheduler i `/scrap/all` (409) stoją do restartu. Przerwane runy zostają `running`.
  - Do zrobienia:
    - [ ] `_running = False` w osobnym `try/finally`;
    - [ ] sprzątanie runów `running` przy starcie;
    - [ ] obsługa `CancelledError`.

- [ ] **P1-12. Frontend nie pokazuje `limitations`, `summary`, `mock`**
  - Problem: `Frontend/src/app/core/gateway-results.ts:28-67` je pomija.
  - Do zrobienia:
    - [ ] wyświetlać zastrzeżenia i flagę mock.

- [ ] **P1-13. ContextGraph/Neo4j: brak auth, domyślne hasło, testy czyszczą graf stacku**
  - Problem:
    - Endpointy zapisu nie mają autoryzacji; hasło `contextgraph`.
    - `ContextGraph/tests/test_contract.py:5-6` każe użyć `bolt://localhost:7687`, czyli portu stacku, i wykonuje `DETACH DELETE`. Seed nie wraca.
  - Do zrobienia:
    - [ ] auth (patrz P0-3);
    - [ ] wymagane hasło Neo4j;
    - [ ] testy na osobnym porcie/instancji z bezpiecznikiem.

- [ ] **P1-14. `/verify/live` bez ograniczeń per check**
  - Problem: `APIGateway/app/routers/verify.py:63-85`: każdy, kto zna `check_id`, może wyzwalać je wielokrotnie.
  - Do zrobienia:
    - [ ] maks. jedno live na check, bez live dla wyniku live.

---

## P2 — średnie

### Bezpieczeństwo i prywatność
- [ ] Limit uploadu nie tylko z `Content-Length` — chunked omija `APIGateway/app/main.py:89-91`.
- [ ] `DELETE /api/v1/checks/{id}`:
  - [ ] autoryzacja (dziś anonimowe; `APIGateway/tests/test_api.py:298` to utrwala);
  - [ ] usuwanie także wpisów audytu.
- [ ] Maskować PII w URL zapisywanym w `checks` (`APIGateway/app/verification.py:54-61,167`).
- [ ] Live re-scrape ma używać oryginalnego, a nie zamaskowanego URL (`verification.py:98-104`).
- [ ] Osobne role Postgres per serwis zamiast jednego superusera (scrapper z Chromium ma pełny dostęp).
- [ ] `injection_suspected` ze stance ma osłabiać werdykt, nie tylko trafiać do logu.
- [ ] Walidacja wyjaśnienia w `compose.py:44-65` przepuszcza tekst wstrzyknięty ze źródła.
- [ ] ContextGraph `/events/extract`: walidować agentów i cele z wyjścia LLM, nie tylko `quote` (`events.py:178-188`).

### Dane i schemat
- [ ] Migracje (Alembic albo podobne) zamiast `create_all` / `IF NOT EXISTS`. Seed tworzy tabele z góry.
- [ ] Jawny kontrakt zamiast bezpośredniego czytania tabeli `documents` scrappera przez pipeline (`corpus.py:189-193`).
- [ ] `vector(1024)` na sztywno (`db/schema.sql:50`) vs konfigurowalny `embeddings.dim`.
- [ ] Ujednolicić dwie ścieżki zapisu korpusu (offline Kedro vs `/ingest`): `dup_group`, `corpus_version`.
- [ ] `store_documents` trzyma transakcję przez całe liczenie embeddingów i blokuje `source`. Push ze scrappera (30 s) wtedy pada.
- [ ] Push do RAG bez retry: dokument oznaczony jako „unchanged” nigdy nie trafi do indeksu.
- [ ] ContextGraph nie dostaje pushy — tylko ręczne `make graph-sync`.
- [ ] `upsert` scrappera (select-then-insert) → `IntegrityError` i 500 w live search. Dwa źródła nadpisują ten sam URL.

### Pipeline / LLM
- [ ] Stance bez fallbacku: błąd jednego twierdzenia psuje cały check.
- [ ] `_drop_broken_items` po cichu odrzuca pozycje, co może zmienić werdykt (`llm/tasks.py:49-62`).
- [ ] Nadpisanie modelu z gatewaya zachowuje `api_base`/`temperature` z env (`gateway.py:93-94`).
- [ ] Kolejność polityki:
  - [ ] `missing_context` wygrywa z dwoma źródłami pierwotnymi;
  - [ ] `insufficient_evidence` z `evidence_strength: strong`.
- [ ] Neo4j:
  - [ ] `ORDER BY v.id` sortuje wersje jako tekst;
  - [ ] driver bez locka i niezamykany;
  - [ ] ok. 60 zapytań per twierdzenie;
  - [ ] przy niezgodności modelu fallback na full-text zamiast `[]`.

### Scrapper / rendering
- [ ] Całkowity deadline na fetch i run (slowloris; timeout httpx jest per odczyt).
- [ ] Błędy Playwright poza `goto` dają 500 — fallback do tekstu statycznego.
- [ ] Restart martwej przeglądarki.
- [ ] Limit rozmiaru `page.content()`.
- [ ] Parsowanie BeautifulSoup/trafilatura poza event loopem.
- [ ] Scheduled run (odyseusz) zajmuje oba sloty renderowania, więc live search dostaje timeout.

### Gateway / Discord / Frontend
- [ ] Brak trasy `/ws`, a nginx, README i frontend jej używają (pętla reconnect co 10 s). Usunąć albo zaimplementować.
- [ ] Błąd zapisu wyniku (`APIGateway/app/verification.py:249`) daje 500 i traci gotowy wynik.
- [ ] Discord: obraz pobierany przed `defer` (`DiscordBot/bot/main.py:149-155`) — przekroczony limit 3 s.
- [ ] Discord deklaruje GIF/BMP/TIFF, które gateway odrzuca (415).

### Eval (`VerificationPipeline/src/prawdowid/pipelines/eval/nodes.py`)
- [ ] „recall@k” w raporcie to faktycznie hit-rate@k — zmienić nazwę.
- [ ] Wybór najlepszego twierdzenia per case jest optymistyczny; `best_score` pochodzi z innego twierdzenia.
- [ ] Mieszanie skal reranker/RRF w `best_score`.
- [ ] `rerank_min` kalibrowany na zbiorze ewaluacyjnym — wydzielić hold-out.

---

## P3 — higiena

- [ ] `GET /api/v1/config`, `/health/services` (nazwy wyjątków), `/docs` są publiczne. `APP_ENV` nieużywany.
- [ ] Nagłówki bezpieczeństwa / CSP w nginx.
- [ ] Frontend i ContextGraph działają jako root. ContextGraph nie ma HEALTHCHECK.
- [ ] Healthcheck OCR na `/readyz`.
- [ ] Obrazy bazowe przypiąć digestem; lock zależności dla pipeline'u i ContextGraph.
- [ ] Discord: escapować `[`/`]` w tytułach stron w linkach (`DiscordBot/bot/format.py:96`).
- [ ] Seedy (38 MB + 8 MB) przenieść do Git LFS.
- [ ] `make seed-dump` z `pipefail`.
- [ ] `start.sh`: parsowanie JSON grepem; `python` vs `python3`.
- [ ] Pipeline ucina wejście do 4000 znaków (gateway przyjmuje 10 000) bez wpisu w `limitations`.
- [ ] `_escape` w promptach: escapować `"` w atrybucie `publisher` (`llm/tasks.py:99-125`).
- [ ] HNSW prawdopodobnie nieużywany przez drugi klucz `ORDER BY` [?] (`retrieval/postgres.py:25-27`).
- [ ] Kruche adaptery:
  - [ ] `euvsdisinfo_mcp.py:56,71`;
  - [ ] `sejm_eli.py:781,805`;
  - [ ] brak `nextPageToken` w Google Fact Check.
- [ ] Rozbieżności README z kodem: timeout, `/ws`, „30/min na klienta”, `X-Forwarded-For`.

## Testy i CI

- [ ] Job CI `compose up` + smoke test przez nginx. Dziś `stack` tylko buduje obrazy; smoke test wyłapałby P1-9, P1-10 i `/ws`.
- [ ] Filtr `infra` w `.github/workflows/services.yml` ma obejmować `db/**` i `Makefile`.
- [ ] Frontend: uruchamiać testy w CI (dziś tylko `npm run build`).
- [ ] Pipeline: marker `db` nigdzie nieużyty.
- [ ] Pipeline: testy `/verify`, `/ingest`, `params_for`, cache providerów, syncu z seedem.
- [ ] PageScrapper: testy na Postgresie i prawdziwym `_guard` (dziś SQLite + atrapa).
- [ ] Poprawić testy utrwalające złe zachowanie: `APIGateway/tests/test_api.py:298` (anonimowy DELETE), `:265-271` (wspólny bucket).

## Weryfikacja po naprawach (checklista)

- [ ] Z innej maszyny `curl <host>:8000/health` → brak połączenia.
- [ ] `PATCH :4200/api/v1/config` z `change-me-admin` → 403.
- [ ] 31 żądań przez `:4200` z dwóch IP → każde IP ma własny limit.
- [ ] `docker compose stop postgres` + `POST /api/v1/verify/claim` → status `error`, nie `insufficient_evidence`.
- [ ] `make clean && make up` → `GET :8003/corpus/stats` pokazuje `bge-m3`.
- [ ] Z kontenera `page-scrapper`: `curl verification-pipeline:8000/ingest` bez sekretu → 401.
- [ ] „500 000 000 zł” przechodzi przez gateway i pipeline bez maskowania.
- [ ] `make test` zielone.
