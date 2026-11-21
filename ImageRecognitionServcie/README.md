# ScreenScrapper OCR

Lokalny mikroserwis HTTP do odczytu tekstu ze zrzutów ekranu, po polsku i angielsku.
Działa w całości na własnym sprzęcie, bez zewnętrznych API. Wagi modeli pobiera się raz, a w obrazie Dockera są „wypieczone”.

- **Stos:** Python 3.12, FastAPI + uvicorn, PaddleOCR 3.7 (PP-OCRv5), Pillow, OpenCV.
- **API:** `POST /v1/ocr` (multipart lub JSON z base64), `/healthz`, `/readyz`, `/metrics` (Prometheus), `/docs` (OpenAPI).
- **Wymiana silnika:** silniki implementują interfejs `OcrEngine`, więc Tesseract albo lokalny VLM (Qwen-VL przez Ollama/vLLM) można dodać bez zmian w API.

---

## Spis treści

1. [Uruchomienie lokalnie (Windows, bez Dockera)](#1-uruchomienie-lokalnie-windows-bez-dockera)
2. [Uruchomienie w Dockerze](#2-uruchomienie-w-dockerze)
3. [API](#3-api)
4. [Przykłady wywołań (curl)](#4-przykłady-wywołań-curl)
5. [Klient w Pythonie](#5-klient-w-pythonie)
6. [Zmienne środowiskowe](#6-zmienne-środowiskowe)
7. [Architektura i rozbudowa](#7-architektura-i-rozbudowa)
8. [Testy i jakość kodu](#8-testy-i-jakość-kodu)
9. [Wydajność, skalowanie i ograniczenia](#9-wydajność-skalowanie-i-ograniczenia)

---

## 1. Uruchomienie lokalnie (Windows, bez Dockera)

Komendy są w PowerShellu.

**1. Zainstaluj [uv](https://docs.astral.sh/uv/)**, jeśli go nie masz:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# albo: python -m pip install --user uv   (wtedy zamiast `uv` pisz `python -m uv`)
```

**2. Zainstaluj Pythona 3.12 i zależności** (z `uv.lock`, do `.venv`):

```powershell
cd ScreenScrapper
uv python install 3.12
uv sync
```

**3. Skonfiguruj.** Wszystkie zmienne są opcjonalne:

```powershell
Copy-Item .env.example .env
```

Domyślnie `.env.example` kieruje wagi do `./models` (`OCR_MODEL_DIR=./models`).

**4. Pobierz modele** (jednorazowo, ok. 20 MB, potrzebny internet):

```powershell
uv run python -m ocr_service.download_models
```

Potem możesz ustawić w `.env` `OCR_OFFLINE=true`: serwis wystartuje bez sieci.

**5. Uruchom serwis:**

```powershell
uv run ocr-service
# albo: uv run python -m ocr_service
```

- `GET http://localhost:8000/healthz` odpowiada od razu.
- `GET http://localhost:8000/readyz` zwraca `200`, gdy model się załaduje (kilka sekund).
- Dokumentacja jest pod http://localhost:8000/docs.

> Do pracy lokalnie wygodne są czytelne logi: `OCR_LOG_JSON=false` w `.env`.

**Linux / macOS:** kroki są te same (`cp .env.example .env`).

---

## 2. Uruchomienie w Dockerze

Obraz CPU jest wieloetapowy:

- wagi modeli pobierane są na etapie budowania, a build kończy się błędem, jeśli model nie przejdzie testowej inferencji;
- w runtime `OCR_OFFLINE=true`, więc kontener nie potrzebuje sieci;
- serwis działa jako użytkownik `ocr` (UID 10001, nie-root).

```bash
docker build -t screenscrapper-ocr .
docker run --rm -p 8000:8000 screenscrapper-ocr

# z kluczem API; liczba workerów dobiera się sama do --cpus / --memory:
docker run --rm -p 8000:8000 --cpus 8 --memory 8g -e OCR_API_KEY=tajny screenscrapper-ocr

# bez dostępu do sieci (dowód, że runtime jest offline):
docker run --rm --network none screenscrapper-ocr
```

Przez docker compose:

```bash
docker compose up --build            # wariant CPU, port 8000
docker compose --profile gpu up ocr-gpu   # wariant GPU (szkic), port 8001
```

Inne modele można wypiec przez build arg:

```bash
docker build --build-arg OCR_PADDLE_DET_MODEL=PP-OCRv6_medium_det \
             --build-arg OCR_PADDLE_REC_MODEL=PP-OCRv6_medium_rec -t screenscrapper-ocr:v6 .
```

**GPU:** [`docker/Dockerfile.gpu`](docker/Dockerfile.gpu) to szkic. Podmienia `paddlepaddle` na `paddlepaddle-gpu` (CUDA 12.6) i ustawia `OCR_PADDLE_DEVICE=gpu:0`. Nie jest testowany w CI. Wersję CUDA dopasuj do sterownika.

**CI:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) uruchamia kolejno:

1. ruff, mypy i pytest, w tym test prawdziwego OCR (modele są cache'owane);
2. build obrazu;
3. smoke test kontenera z `--network none` (czeka na `/readyz` i sprawdza, że proces nie działa jako root).

---

## 3. API

### `POST /v1/ocr`

Obraz przyjmowany jest jako `multipart/form-data` (pole `file`) albo `application/json` (`{"image": "<base64>"}`; data URL też działa).

| Parametr | Gdzie | Domyślnie | Opis |
|---|---|---|---|
| `langs` | query / pole formularza / JSON | `pl,en` | Języki oddzielone przecinkami. Muszą być podzbiorem `OCR_LANGS`. |
| `output` | query / pole formularza / JSON | `blocks` | `blocks` zwraca tekst i bloki ze współrzędnymi, `text` tylko tekst. |

Wartość z body ma pierwszeństwo przed query. Format obrazu (PNG, JPEG, WebP) jest rozpoznawany po zawartości, nie po rozszerzeniu.

Odpowiedź `200`:

```json
{
  "text": "Raport sprzedaży - październik 2026\nŁódź: 12 345,67 zł (wzrost o 4,5%)",
  "lang": "pl",
  "blocks": [
    {"text": "Raport sprzedaży - październik 2026", "bbox": [299, 43, 688, 76], "confidence": 0.9934},
    {"text": "Łódź: 12 345,67 zł (wzrost o 4,5%)", "bbox": [299, 113, 521, 130], "confidence": 0.9812}
  ],
  "engine": "paddleocr",
  "duration_ms": 412
}
```

Pola odpowiedzi:

- **`text`:** linie w kolejności czytania, od góry do dołu i od lewej do prawej, rozdzielone `\n`.
- **`bbox`:** `[x1, y1, x2, y2]` w pikselach oryginalnego obrazu, także gdy serwis go wewnętrznie powiększył.
- **`lang`:** dominujący język spośród żądanych; `null`, gdy na obrazie nie ma tekstu.
- **`blocks`:** przy `output=text` tego pola nie ma w odpowiedzi.

Każda odpowiedź ma nagłówek `X-Request-ID`. Można go przekazać w żądaniu, a trafia też do logów.

### Błędy

Wszystkie błędy mają format `{"error": {"code": "...", "message": "..."}}`:

| HTTP | `code` | Kiedy |
|---|---|---|
| 400 | `invalid_image`, `empty_file`, `invalid_base64`, `invalid_multipart` | Uszkodzony obraz lub body |
| 401 | `missing_api_key`, `invalid_api_key` | Włączony `OCR_API_KEY`, a nagłówka `X-API-Key` brak lub jest błędny |
| 404 / 405 | `not_found`, `method_not_allowed` | Zła ścieżka lub metoda |
| 413 | `file_too_large`, `payload_too_large`, `image_too_large` | Przekroczony `OCR_MAX_FILE_BYTES`, limit body lub `OCR_MAX_PIXELS` |
| 415 | `unsupported_media_type` | Format inny niż PNG/JPEG/WebP albo zły `Content-Type` |
| 422 | `validation_error`, `unsupported_language`, `missing_file` | Złe parametry |
| 500 | `ocr_failed`, `internal_error` | Błąd silnika |
| 503 | `not_ready`, `engine_failed`, `busy`, `queue_timeout` | Model się ładuje, model się nie załadował albo serwis jest przeciążony (z nagłówkiem `Retry-After`) |

### Endpointy operacyjne (bez klucza API)

- **`GET /healthz`:** liveness, zawsze `200 {"status":"ok"}`, gdy proces żyje.
- **`GET /readyz`:** readiness. Zwraca `200`, gdy model jest załadowany. Wcześniej zwraca `503` z kodem `not_ready`, a po nieudanym ładowaniu `engine_failed`.
- **`GET /metrics`:** metryki Prometheus:

  | Metryka | Co mierzy |
  |---|---|
  | `ocr_http_requests_total{method,route,status}` | Liczba żądań HTTP |
  | `ocr_http_request_duration_seconds` | Czas obsługi żądania HTTP |
  | `ocr_processing_duration_seconds{engine}` | Czas samego OCR |
  | `ocr_queue_wait_seconds` | Czas oczekiwania na wolny silnik |
  | `ocr_errors_total{code}` | Błędy według kodu |
  | `ocr_inflight_requests` | Żądania w toku |
  | `ocr_engine_ready` | Czy silnik jest gotowy |

- **`GET /docs`:** Swagger UI. `/openapi.json` zawiera schemat.

---

## 4. Przykłady wywołań (curl)

```bash
# multipart (domyślnie output=blocks, langs=pl,en)
curl -F "file=@zrzut.png" http://localhost:8000/v1/ocr

# tylko tekst, tylko polski
curl -F "file=@zrzut.png" "http://localhost:8000/v1/ocr?output=text&langs=pl"

# parametry jako pola formularza
curl -F "file=@zrzut.jpg" -F "output=text" http://localhost:8000/v1/ocr

# z kluczem API
curl -H "X-API-Key: tajny" -F "file=@zrzut.png" http://localhost:8000/v1/ocr

# JSON z base64 (bash)
curl -H "Content-Type: application/json" \
     -d "{\"image\": \"$(base64 -w0 zrzut.png)\", \"output\": \"text\"}" \
     http://localhost:8000/v1/ocr

# readiness i metryki
curl http://localhost:8000/readyz
curl http://localhost:8000/metrics
```

W PowerShellu użyj `curl.exe` (nie aliasu `curl`):

```powershell
curl.exe -F "file=@C:\zrzuty\ekran.png" "http://localhost:8000/v1/ocr?output=text"

# JSON z base64:
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\zrzuty\ekran.png"))
Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/ocr `
  -ContentType "application/json" -Body (@{ image = $b64; output = "text" } | ConvertTo-Json)
```

---

## 5. Klient w Pythonie

Gotowy klient (`httpx`) jest w [`examples/ocr_client.py`](examples/ocr_client.py). Ponawia żądania przy `503`, respektując `Retry-After`, i zamienia błędy API na wyjątek `OcrError`:

```python
from pathlib import Path
from ocr_client import OcrClient, OcrError

with OcrClient("http://localhost:8000", api_key=None) as ocr:
    try:
        result = ocr.ocr(Path("zrzut.png"), langs="pl,en")
    except OcrError as e:
        print("OCR nie powiódł się:", e.code, e)
    else:
        print(result.lang, result.duration_ms, "ms")
        print(result.text)
        for block in result.blocks:
            print(block.bbox, block.confidence, block.text)
```

Z linii poleceń:

```powershell
uv run python examples/ocr_client.py zrzut.png
uv run python examples/ocr_client.py zrzut.png --text
$env:OCR_URL = "http://localhost:8000"; $env:OCR_API_KEY = "tajny"
```

Wersja minimalna, bez dodatkowych klas:

```python
import httpx

with open("zrzut.png", "rb") as f:
    r = httpx.post("http://localhost:8000/v1/ocr", files={"file": f},
                   params={"output": "text"}, timeout=60)
r.raise_for_status()
print(r.json()["text"])
```

---

## 6. Zmienne środowiskowe

Konfiguracja odbywa się wyłącznie przez zmienne środowiskowe (prefiks `OCR_`), opcjonalnie przez plik `.env`. Wzór jest w [`.env.example`](.env.example).

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `OCR_HOST` | `0.0.0.0` | Adres nasłuchu |
| `OCR_PORT` | `8000` | Port HTTP |
| `OCR_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `OCR_LOG_JSON` | `true` | Logi JSON (`false` = czytelny tekst) |
| `OCR_ENGINE` | `paddleocr` | Silnik OCR (rejestr w `engines/registry.py`) |
| `OCR_LANGS` | `pl,en` | Obsługiwane języki; domyślne dla żądań bez `langs` |
| `OCR_EXECUTOR` | `process` | `process`: każdy worker to osobny proces z własnym modelem (zalecane). `thread`: wątki w procesie API (debugowanie, ograniczone przez GIL) |
| `OCR_WORKERS` | `0` (auto) | Liczba workerów = liczba obrazów przetwarzanych równolegle. `0` = dostępne CPU (z limitem kontenera) / `OCR_PADDLE_CPU_THREADS`, ograniczone też pamięcią |
| `OCR_WORKER_MEMORY_MB` | `700` | Budżet RAM na workera przy `OCR_WORKERS=0`; 512 MB zostaje dla procesu API |
| `OCR_MAX_QUEUE` | `64` | Ile żądań może czekać na wolnego workera; ponad to `503 busy` |
| `OCR_QUEUE_TIMEOUT_S` | `30` | Maksymalne oczekiwanie na workera; potem `503 queue_timeout` |
| `OCR_TASK_TIMEOUT_S` | `120` | Maksymalny czas OCR jednego obrazu; potem `504 ocr_timeout`, a zawieszony worker jest zabijany i restartowany |
| `OCR_MAX_FILE_BYTES` | `10485760` | Maksymalny rozmiar obrazu (10 MiB); limit całego body wynosi ×4/3 + 64 KiB |
| `OCR_MAX_PIXELS` | `40000000` | Maksymalna liczba pikseli (ochrona przed decompression bomb) |
| `OCR_API_KEY` | *(puste)* | Gdy ustawione, `/v1/*` wymaga nagłówka `X-API-Key` |
| `OCR_PREPROCESS_UPSCALE_TARGET` | `1280` | Obrazy o dłuższym boku poniżej tej wartości są powiększane (`0` = wyłączone) |
| `OCR_PREPROCESS_MAX_UPSCALE` | `3.0` | Maksymalny współczynnik powiększenia |
| `OCR_PREPROCESS_DARK_MODE` | `true` | Wykrywanie ciemnego motywu i inwersja kolorów |
| `OCR_PREPROCESS_DARK_THRESHOLD` | `110` | Mediana jasności (0–255), poniżej której obraz jest uznany za ciemny |
| `OCR_MODEL_DIR` | *(puste = `~/.paddlex`)* | Katalog wag; eksportowany jako `PADDLE_PDX_CACHE_HOME`. W Dockerze: `/opt/models` |
| `OCR_OFFLINE` | `false` | `true` pomija sprawdzanie hostingu modeli przy starcie. W Dockerze: `true` |
| `OCR_PADDLE_DET_MODEL` | `PP-OCRv5_mobile_det` | Model detekcji tekstu |
| `OCR_PADDLE_REC_MODEL` | `latin_PP-OCRv5_mobile_rec` | Model rozpoznawania (alfabet łaciński, w tym polskie znaki) |
| `OCR_PADDLE_DEVICE` | `cpu` | `cpu` albo `gpu:0` |
| `OCR_PADDLE_CPU_THREADS` | `1` | Wątki CPU na workera. `1` daje najwyższą przepustowość pod obciążeniem; więcej skraca pojedyncze żądanie przy małym ruchu |
| `OCR_PADDLE_ENABLE_MKLDNN` | `auto` | oneDNN: `auto` = włączone poza Windows (na Windows PaddlePaddle 3.x z oneDNN się wywraca) |
| `OCR_PADDLE_MIN_SCORE` | `0.5` | Bloki z pewnością poniżej progu są odrzucane |

Przy ręcznym ustawianiu `OCR_WORKERS` utrzymuj `OCR_WORKERS × OCR_PADDLE_CPU_THREADS` mniej więcej równe liczbie rdzeni. Więcej oznacza walkę o CPU, mniej oznacza niewykorzystane rdzenie. Szczegóły w [sekcji 9](#9-wydajność-skalowanie-i-ograniczenia).

---

## 7. Architektura i rozbudowa

```
src/ocr_service/
├── __main__.py          # start uvicorn (python -m ocr_service / ocr-service)
├── app.py               # create_app(): lifespan, middleware, routery
├── config.py            # Settings (pydantic-settings)
├── api/routes.py        # /v1/ocr, /healthz, /readyz, /metrics
├── service.py           # OcrService: przyjmowanie, kolejka, timeouty, kolejność czytania, metryki
├── backends.py          # ProcessBackend (procesy + nadzór) / ThreadBackend
├── worker_process.py    # pętla procesu roboczego: model ładowany raz, zadania ze wspólnej kolejki
├── pipeline.py          # dekodowanie -> preprocessing -> OCR -> współrzędne (wewnątrz workera)
├── imaging.py           # magic bytes, limity pikseli, EXIF, RGB, upscaling, ciemny motyw
├── text.py              # kolejność czytania, detekcja języka pl/en
├── engines/
│   ├── base.py          # interfejs OcrEngine + TextBlock
│   ├── registry.py      # OCR_ENGINE -> fabryka silnika
│   └── paddle.py        # PaddleOCR 3.x
├── middleware.py        # request id, access log, metryki HTTP, limit rozmiaru body
├── exceptions.py        # ApiError (bez zależności od frameworka, przechodzi między procesami)
├── errors.py            # handlery FastAPI + jednolity format błędów
├── security.py          # X-API-Key
├── logging_setup.py     # logi JSON (też uvicorn i paddle)
└── download_models.py   # pobranie i rozgrzanie modeli (Docker build, CI)
```

**Przepływ żądania:**

```
            ┌─────────────── proces API (asyncio, tylko I/O) ───────────────┐
 HTTP ──▶   │ limit body → parsowanie → OcrService: przyjęcie (503 busy)    │
            │   → czekanie na slot (≤ OCR_WORKERS, 503 queue_timeout)       │
            │   → wspólna kolejka zadań ───────────────┐                    │
            │   ← kolejność czytania, język, metryki ◀─┼── kolejka wyników  │
            └──────────────────────────────────────────┼────────────────────┘
                     ┌──────────────┬──────────────────┴┬─────────────────┐
                     ▼              ▼                   ▼                 ▼
                 worker 0       worker 1     ...    worker N-1     (osobne procesy,
                 model PP-OCR   model PP-OCR        model PP-OCR    każdy z własnym modelem)
                 dekodowanie → preprocessing → OCR → współrzędne
```

1. Middleware odrzuca za duże body.
2. Endpoint rozpoznaje multipart lub JSON. Multipart jest parsowany w pamięci (obrazy nigdy nie trafiają na dysk), base64 jest dekodowane poza pętlą zdarzeń.
3. `OcrService.recognize()` przyjmuje żądanie albo zwraca `503 busy`, gdy czeka już `OCR_MAX_QUEUE` żądań.
4. Żądanie czeka na wolny slot, maksymalnie `OCR_QUEUE_TIMEOUT_S`. Do workerów trafia naraz najwyżej tyle obrazów, ile jest workerów.
5. Wolny worker pobiera zadanie ze wspólnej kolejki, więc obciążenie samo się równoważy. Worker wykonuje dekodowanie, preprocessing, OCR i przeliczenie współrzędnych. Cała praca CPU dzieje się poza procesem API, bez GIL.
6. Proces API układa bloki w kolejności czytania i wykrywa język.

**Nadzór nad workerami:**

- **Crash** (np. segfault w bibliotece natywnej): tylko to jedno żądanie dostaje `500 worker_crashed`, a worker jest restartowany z wykładniczym backoffem.
- **Zawieszenie:** po `OCR_TASK_TIMEOUT_S` żądanie dostaje `504 ocr_timeout`, a worker jest zabijany i restartowany.
- **Zniknięcie procesu API:** workery same się kończą.
- **Monitoring:** `/readyz` pokazuje `workers_ready`, a metryki `ocr_workers_ready` i `ocr_worker_restarts_total`.

Modele ładują się raz na workera, równolegle i w tle po starcie, dlatego `/healthz` działa od razu, a `/readyz` czeka na załadowanie wszystkich workerów.

**Nowy silnik** (np. Tesseract, Qwen-VL):

1. Utwórz `engines/<nazwa>.py` z klasą dziedziczącą po `OcrEngine`. Zaimplementuj `supported_langs`, `load()` (raz, przy starcie) i `recognize(image_rgb, langs) -> list[TextBlock]`.
2. Dopisz fabrykę do `ENGINES` w `engines/registry.py`.
3. Ustaw `OCR_ENGINE=<nazwa>`.

Silnik spoza repozytorium można wskazać ścieżką importu: `OCR_ENGINE=moj_pakiet.silnik:MojSilnik`. Klasa przyjmuje `Settings`. Ten sam mechanizm działa w procesach roboczych.

API, preprocessing, limity i metryki pozostają bez zmian. Silnik VLM, który nie zwraca współrzędnych, może zwrócić jeden blok z `bbox` całego obrazu.

**Tryb asynchroniczny (kolejka)** nie jest jeszcze potrzebny, ale `OcrService.recognize(bytes, langs, output)` nie zależy od HTTP. Żeby go dodać:

1. `POST /v1/jobs` zapisuje obraz w Redisie z TTL i zwraca `job_id`.
2. Worker (np. `arq`/RQ, osobny proces z tym samym obrazem Dockera) tworzy `OcrService` i woła tę samą metodę.
3. `GET /v1/jobs/{id}` zwraca wynik.

Test `test_service_is_reusable_without_http` pokazuje takie użycie.

---

## 8. Testy i jakość kodu

```powershell
uv run pytest                 # wszystko, w tym prawdziwy OCR (pierwsze uruchomienie pobiera modele)
uv run pytest -m "not ocr"    # szybkie testy z fałszywym silnikiem (~15 s, startują procesy)
uv run ruff check src tests examples scripts
uv run ruff format src tests examples scripts
uv run mypy                   # tryb strict
```

Co sprawdzają testy:

- **API:** happy path (multipart, JSON, data URL), sniffing formatu (JPEG z rozszerzeniem `.png`), GIF i tekst zamiast obrazu, obcięty PNG, pusty plik, brak pola `file`, zły `Content-Type`.
- **Limity:** za duży plik, body i base64; za dużo pikseli; PNG udający obraz 50 000×50 000 (decompression bomb).
- **Klucz API:** brak, błędny i poprawny klucz.
- **Stany serwisu:** `/readyz` w trakcie ładowania i po błędzie, metryki, format błędów 404/405.
- **Współbieżność:** `busy`, `queue_timeout`, równoległość workerów.
- **Procesy robocze:** równoległość w osobnych procesach, crash workera (500 i restart), zawieszony worker (504, kill i restart), błąd przechodzący między procesami, błąd ładowania modelu, automatyczny dobór liczby workerów (CPU i pamięć).
- **Preprocessing:** RGBA, EXIF, upscaling, ciemny motyw.
- **Tekst:** kolejność czytania, detekcja języka.
- **OCR end-to-end** (`-m ocr`): PaddleOCR na wygenerowanym obrazie z „Zażółć gęślą jaźń” (jasny i ciemny motyw), tekst angielski, pusty obraz.

---

## 9. Wydajność, skalowanie i ograniczenia

**Pomiar przepustowości** ([`scripts/loadtest.py`](scripts/loadtest.py)): zrzuty 1280×720 z ok. 8 liniami tekstu, 8 żądań równolegle, laptop z 8 wątkami CPU, Windows (bez oneDNN).

| Konfiguracja | Przepustowość | Względem startu |
|---|---|---|
| wątki, 1 worker × 8 wątków (pierwsza wersja) | 0,14 obr./s | 1,0× |
| wątki, 4 × 2 | 0,17 obr./s | 1,2× |
| procesy, 2 × 4 | 0,26 obr./s | 1,9× |
| procesy, 4 × 2 | 0,28 obr./s | 2,0× |
| **procesy, 8 × 1 (domyślne: auto)** | **0,37 obr./s** | **2,6×** |

Wątki w jednym procesie prawie się nie skalują, bo blokuje je GIL. Procesy skalują się z liczbą rdzeni. Na serwerze z Linuksem (oneDNN) i większą liczbą rdzeni przepustowość będzie wyższa; zmierz ją na docelowej maszynie:

```bash
uv run python scripts/loadtest.py zrzut1.png zrzut2.png -c 32 -n 500 --url http://serwer:8000
```

**Strojenie:**

- **Duży ruch** (domyślne): `OCR_PADDLE_CPU_THREADS=1`, `OCR_WORKERS=0`. Daje maksymalną liczbę obrazów na sekundę; pojedyncze żądanie trwa dłużej.
- **Mały ruch, ważne opóźnienie:** `OCR_PADDLE_CPU_THREADS=4`. Mniej workerów, ale każdy obraz przetwarzany jest szybciej.
- **Kolejka:** `OCR_MAX_QUEUE` i `OCR_QUEUE_TIMEOUT_S` decydują, ile ruchu serwis buforuje, zanim zacznie odpowiadać `503` z `Retry-After`. Przykładowy klient sam ponawia takie żądania. Gdy `ocr_queue_wait_seconds` rośnie, dołóż CPU albo repliki.
- **Skalowanie poziome:** serwis jest bezstanowy, więc można uruchomić wiele replik za load balancerem. `/readyz` nadaje się na readiness probe.
- **GPU:** na GPU wąskim gardłem przestaje być CPU. Wtedy warto rozważyć batchowanie żądań; interfejs backendu na to pozwala.

**Ograniczenia:**


- **Windows:** PaddlePaddle 3.x nie działa tam z oneDNN, więc działa wolniejsza ścieżka CPU. Zrzut 1920×1080 z ok. 10 liniami tekstu trwa ok. 6–9 s przy 8 wątkach na jeden obraz (zmierzone lokalnie). Na Linuksie (Docker, CI) oneDNN jest włączony, co zwykle znacząco przyspiesza inferencję. Tego wariantu nie mierzyłem; czasy z CI pokaże `duration_ms` i metryka `ocr_processing_duration_seconds`. Do szybszej pracy na Windows użyj WSL2 albo Dockera na serwerze.
- **Kolejność czytania:** opiera się na grupowaniu bloków w wiersze. Układy wielokolumnowe (pasek boczny obok treści) mogą się przeplatać w polu `text`. Do precyzyjnego układu użyj `blocks` i `bbox`.
- **Typografia:** typograficzne znaki, np. półpauza „–”, bywają odczytywane jako ich proste odpowiedniki („-”).
- **Pamięć:** jeden worker (PP-OCRv5 mobile) zajmuje ok. 300 MB RAM po załadowaniu i ok. 500 MB w szczycie przy zrzucie 1080p (zmierzone na Windows). Stąd domyślny budżet `OCR_WORKER_MEMORY_MB=700`. Na serwerze z 32 rdzeniami tryb auto uruchomi do 32 workerów (ok. 22 GB), chyba że limit pamięci kontenera jest mniejszy.
- **Wyższa dokładność:** modele `PP-OCRv6_medium_det` + `PP-OCRv6_medium_rec` są dokładniejsze, ale ok. 2–3× wolniejsze na CPU.
