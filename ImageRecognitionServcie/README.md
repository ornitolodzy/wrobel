# ImageRecognitionServcie OCR

Serwis HTTP, który czyta tekst ze zrzutów ekranu (polski + angielski). Wysyłasz obrazek, a serwis wysyła rozpoznany tekst na skonfigurowany adres (callback) albo zwraca go od razu w odpowiedzi.

Wszystko działa lokalnie: bez chmury i zewnętrznych API. Silnik to PaddleOCR.

---

## Jak to przetestować (Windows)

1. Kliknij dwukrotnie **`start-tester.bat`**.
2. Poczekaj. Otworzą się dwa okna, „OCR serwis” i „OCR odbiornik callbackow”, a potem przeglądarka. Za pierwszym razem trwa to kilka minut, bo pobierają się zależności i model.
3. Na stronie wybierz, przeciągnij albo wklej (Ctrl+V) zrzut ekranu.
4. Kliknij **„Wyślij do OCR”**.
5. Zobaczysz przebieg (wysłano → 202 → callback dostarczony), rozpoznany tekst, ramki na obrazku i JSON-y.

Aby zakończyć, zamknij oba okna.

Tryb bez callbacku (wynik w odpowiedzi): w konsoli wpisz `set TESTER_MODE=sync`, potem uruchom `start-tester.bat`.

---

## Jak to działa

```
Twój system ──POST /v1/ocr (obrazek)──▶ serwis OCR ──▶ 202 {"request_id": "..."}
                                            │
                                            └─ OCR w tle ──POST (wynik JSON)──▶ OCR_CALLBACK_URL
```

- **Callback:** gdy ustawione jest `OCR_CALLBACK_URL`, serwis od razu odpowiada `202`, a wynik wysyła na ten adres.
- **Bez callbacku:** gdy `OCR_CALLBACK_URL` jest puste, wynik wraca od razu w odpowiedzi (`200`).
- **Błędy synchroniczne:** zły plik, za duży plik, zły język albo przeciążenie wracają od razu jako błąd, bez callbacku.

---

## Uruchomienie ręczne

**Lokalnie** (Python przez [uv](https://docs.astral.sh/uv/)):

```powershell
uv sync                                          # zależności
Copy-Item .env.example .env                      # konfiguracja (edytuj wg potrzeb)
uv run python -m ocr_service.download_models     # jednorazowo: pobranie modelu
uv run ocr-service                               # start na http://localhost:8000
```

Jeśli nie masz `uv` w PATH, pisz `python -m uv` zamiast `uv`.

**Docker** (model jest w obrazie, kontener nie potrzebuje internetu):

```bash
docker build -t image-recognition-servcie .
docker run -p 8000:8000 -e OCR_CALLBACK_URL=http://twoj-system/ocr-results image-recognition-servcie
```

---

## API w skrócie

**Wysłanie obrazka:** `POST /v1/ocr`

```bash
curl -H "X-Request-ID: zamowienie-42" -F "file=@zrzut.png" http://localhost:8000/v1/ocr
```

- **Formaty:** PNG, JPEG, WebP. Zamiast pliku można wysłać JSON `{"image": "<base64>"}`.
- **Parametry opcjonalne:** `?langs=pl,en` oraz `?output=blocks|text` (`text` = bez współrzędnych).
- **`X-Request-ID`** (opcjonalny): Twój identyfikator. Wróci w odpowiedzi i w callbacku.

**Odpowiedź w trybie callback:** `202`

```json
{"status": "accepted", "request_id": "zamowienie-42"}
```

**Co przychodzi na `OCR_CALLBACK_URL`** (POST JSON, nagłówek `Authorization: Bearer <OCR_CALLBACK_TOKEN>`, jeśli token jest ustawiony):

```json
{
  "status": "ok",
  "request_id": "zamowienie-42",
  "text": "Zażółć gęślą jaźń\nDrugi wiersz",
  "lang": "pl",
  "blocks": [{"text": "Zażółć gęślą jaźń", "bbox": [12, 40, 230, 68], "confidence": 0.98}],
  "engine": "paddleocr",
  "duration_ms": 412
}
```

Przy błędzie: `{"status": "error", "request_id": "...", "error": {"code": "invalid_image", "message": "..."}}`.

- **`bbox`:** współrzędne `[x1, y1, x2, y2]` w pikselach.
- **`confidence`:** pewność modelu od 0 do 1.
- **Ponawianie:** gdy Twój adres nie odpowie albo zwróci 5xx/429, serwis ponawia wysyłkę.

**Odpowiedź bez callbacku:** `200` z tymi samymi polami, ale bez `status` i `request_id`.

**Błędy** zawsze mają postać `{"error": {"code": "...", "message": "..."}}`:

| Kod HTTP | Kiedy |
|---|---|
| `400` | Uszkodzony obraz albo base64 |
| `401` | Brak lub zły `X-API-Key` (gdy włączony) |
| `413` | Za duży plik albo obraz |
| `415` | Format inny niż PNG/JPEG/WebP |
| `422` | Zły parametr, np. nieobsługiwany język |
| `503` | Model się ładuje albo serwis przeciążony. Spróbuj ponownie po `Retry-After` |

**Inne endpointy:**

| Endpoint | Co zwraca |
|---|---|
| `/healthz` | Czy proces żyje |
| `/readyz` | Czy model jest gotowy; zawiera też tryb (`delivery`) |
| `/metrics` | Metryki Prometheus |
| `/docs` | Pełna dokumentacja API |

**Klient Python:** [`examples/ocr_client.py`](examples/ocr_client.py) (tryb bez callbacku).

**Przykładowy odbiornik callbacków:** [`examples/callback_receiver.py`](examples/callback_receiver.py).

---

## Najważniejsze ustawienia

Konfiguracja odbywa się przez zmienne środowiskowe albo plik `.env`. Pełna lista z opisami jest w [`.env.example`](.env.example).

| Zmienna | Domyślnie | Co robi |
|---|---|---|
| `OCR_CALLBACK_URL` | puste | Adres, na który idzie wynik. Puste = wynik w odpowiedzi |
| `OCR_CALLBACK_TOKEN` | puste | Token wysyłany jako `Authorization: Bearer ...` |
| `OCR_CALLBACK_RETRIES` | `3` | Ile razy ponawiać nieudaną wysyłkę |
| `OCR_MIN_CONFIDENCE` | `0.5` | Próg pewności (0–1): tekst o niższej pewności jest pomijany |
| `OCR_API_KEY` | puste | Gdy ustawione, żądania muszą mieć nagłówek `X-API-Key` |
| `OCR_LANGS` | `pl,en` | Obsługiwane języki |
| `OCR_WORKERS` | `0` (auto) | Ile obrazów przetwarzać równolegle. Auto = tyle, ile rdzeni CPU (i ile zmieści się w RAM) |
| `OCR_MAX_FILE_BYTES` | `10485760` | Maksymalny rozmiar pliku (10 MB) |
| `OCR_PORT` | `8000` | Port serwisu |

---

## Wydajność

- **Równoległość:** każdy worker to osobny proces z własnym modelem, więc serwis wykorzystuje wszystkie rdzenie. Zawieszony lub padnięty worker jest automatycznie restartowany.
- **Pamięć:** jeden worker to ok. 0,5–0,7 GB RAM.
- **Windows jest wolny:** brak przyspieszenia oneDNN daje ok. 6–10 s na zrzut. Na Linuksie i w Dockerze powinno być wyraźnie szybciej.
- **Pomiar przepustowości:** `uv run python scripts/loadtest.py zrzut.png -c 16 -n 100`.
- **Ograniczenie:** zadania przyjęte w trybie callback są trzymane w pamięci. Restart serwisu w trakcie przetwarzania gubi niewysłane wyniki.

---

## Dla deweloperów

```powershell
uv run pytest -m "not ocr"     # szybkie testy
uv run pytest                  # + testy z prawdziwym OCR (OCR_MODEL_DIR=models)
uv run ruff check src tests examples scripts
uv run mypy
```

- **Kod:** `src/ocr_service/`. Opis architektury dla Claude'a jest w [`CLAUDE.md`](CLAUDE.md).
- **CI:** GitHub Actions uruchamia testy i buduje obraz Dockera.
- **GPU:** szkic w `docker/Dockerfile.gpu`.
