# PRAWDOWID - API Gateway

API gateway for our PRAWDOWID project (HackNation 2026). It's the single entry point for the web app and the Discord bot.
It doesn't check facts by itself - it validates input, hides personal data, sends the request to the right service
(LLM, OCR, scrapper) and saves the result to Postgres.

```
Web UI / Discord bot -> API Gateway -> LLM service -> RAG / scrapper
                             |      -> ImageRecognitionService (OCR)
                             |      -> PageScrapper (/scrap/all)
                             -> PostgreSQL (check history + audit log)
```

## How to run

### Docker (easiest)

```bash
cp .env.example .env      # set ADMIN_API_KEY and POSTGRES_PASSWORD
docker compose up -d --build
```

This starts Postgres and the gateway. Tables are created automatically.

Swagger: http://localhost:8000/docs

Stop it with `docker compose down`.

### Without Docker (Python 3.12)

You still need Postgres, so start just the db: `docker compose up -d postgres`

```bash
python -m venv .venv
.venv\Scripts\activate    # linux/mac: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Tip: use `127.0.0.1` in `DATABASE_URL`, not `localhost` - on Windows `localhost` tries IPv6 first and every db connection gets ~2s slower.

### Tests

```bash
pytest
```

They run on SQLite so you don't need Docker. To run them on real Postgres:

```bash
docker exec prawdowid-postgres psql -U prawdowid -d prawdowid -c "create database prawdowid_test;"
TEST_DATABASE_URL=postgresql+asyncpg://prawdowid:<password>@127.0.0.1:5432/prawdowid_test pytest
```

## Mock mode

If a service URL in `.env` is empty, the gateway returns a fake answer for it (`"mock": true`, verdict `insufficient_evidence`).
So the frontend and the bot can be built before the other services are ready.

## Endpoints

The gateway is the only public API. LLM, OCR and PageScrapper live in the internal network and only the gateway talks to them.

No key needed, except admin endpoints (`X-API-Key` header with `ADMIN_API_KEY`).
Frontend/bot should send `X-Channel: web` or `X-Channel: discord` so we know where the request came from
(missing or unknown value = `unknown`).

| Method | Path | Who | What it does |
|---|---|---|---|
| POST | `/api/v1/verify/claim` | everyone | check a text claim |
| POST | `/api/v1/verify/url` | everyone | check a web page |
| POST | `/api/v1/verify/image` | everyone | read text from an image and check it |
| POST | `/api/v1/text-recognition-from-img` | everyone | only read text from an image |
| GET | `/api/v1/checks/{check_id}` | everyone | get a saved check |
| DELETE | `/api/v1/checks/{check_id}` | everyone | delete a saved check |
| GET | `/api/v1/config` | everyone | current settings |
| PATCH | `/api/v1/config` | admin | change settings, e.g. LLM model (no restart needed) |
| GET | `/api/v1/sources` | everyone | list of sources we use (public, for audit) |
| GET | `/api/v1/corpus/stats` | everyone | knowledge base summary: documents, domains, source types |
| POST | `/api/v1/scrap/all` | admin | run the scrapper for all sources |
| GET | `/api/v1/scrap/runs` | admin | history of scrapper runs |
| GET | `/api/v1/scrap/runs/{run_id}` | admin | status of one scrapper run |
| GET | `/api/v1/documents` | admin | documents collected by the scrapper (`?source_id=&limit=&offset=`) |
| GET | `/api/v1/documents/{doc_id}` | admin | one document with full text |
| GET | `/api/v1/admin/events` | admin | audit log |
| GET | `/health` | everyone | is the gateway alive |
| GET | `/health/services` | everyone | status of the other services (`ok` / `down` / `mock`) |

Examples:

```bash
curl -X POST http://localhost:8000/api/v1/verify/claim \
  -H "X-Channel: web" -H "Content-Type: application/json" \
  -d '{"text": "Polish airspace was violated on 20.11"}'

curl -X POST http://localhost:8000/api/v1/verify/url \
  -H "X-Channel: web" -H "Content-Type: application/json" \
  -d '{"url": "https://www.gov.pl/web/dyplomacja"}'

curl -X POST http://localhost:8000/api/v1/verify/image -H "X-Channel: web" -F "file=@screenshot.png"

curl -X PATCH http://localhost:8000/api/v1/config \
  -H "X-API-Key: <admin-key>" -H "Content-Type: application/json" \
  -d '{"llm_provider": "bielik", "llm_model": "bielik-11b-v2.3"}'
```

## Response

```json
{
  "check_id": "6f1c...",
  "status": "completed",
  "input_type": "claim",
  "channel": "web",
  "duration_ms": 4210,
  "claims": [
    {
      "claim": "Polish airspace was violated on 20.11",
      "verdict": "confirmed",
      "explanation": "...",
      "evidence_strength": "strong",
      "sources": [{"title": "...", "publisher": "...", "url": "...", "published_at": "2026-11-20", "fragment": "..."}]
    }
  ],
  "summary": "...",
  "limitations": [],
  "mock": false,
  "error": null
}
```

`status` is the technical status, **not** the verdict (the task requires keeping them separate):

| status | HTTP | meaning |
|---|---|---|
| `completed` | 200 | done, verdicts are in `claims[].verdict` |
| `needs_clarification` | 200 | question is unclear, see `clarification_question` |
| `error` | 502 | some service is down, no verdict |
| `api_limit` | 503 | provider limit / missing API key, no verdict |

Verdicts: `confirmed`, `false`, `misleading`, `insufficient_evidence`, `not_verifiable`.

## What the other services need to implement

**LLM service** - `POST {LLM_SERVICE_URL}/verify`

Gateway sends:

```json
{
  "check_id": "uuid",
  "input_type": "claim | url | image",
  "content": {"text": "..."},
  "options": {"language": "pl", "max_claims": 3, "max_sources": 5},
  "model": {"provider": "mistral", "name": "mistral-large-latest"}
}
```

`content` depends on the input type (personal data is already masked):
- claim: `{"text": "..."}`
- image: `{"ocr_text": "..."}`
- url: `{"url": "...", "text": "page text", "title": "...", "publisher": "...", "published_at": "2026-11-20"}`
  - the gateway asks PageScrapper for the page text first, so the LLM service doesn't need to download anything
  - if PageScrapper isn't configured you only get `{"url": "..."}`

Should return `{"status": "completed", "claims": [...], "summary": "...", "limitations": []}`.
You can also add `"usage": {...}` (tokens, cost) - it only goes to the audit log.
If the response has a wrong format the gateway returns `error`, and HTTP 429/402 is treated as `api_limit`.

**ImageRecognitionService** - `POST {IMAGE_RECOGNITION_URL}/text-recognition-from-img`
gets a multipart `file`, returns `{"text": "...", "confidence": 0.93, "language": "pl", "limitations": []}`.

**PageScrapper** (separate repo)
- `POST {PAGE_SCRAPPER_URL}/scrape` with `{"url": "..."}` - used by `/api/v1/verify/url`. If the scrapper can't read
  the page (blocked by the site, no text, PDF...) the user gets `needs_clarification` asking to paste the text or a screenshot.
  If the scrapper itself is down the user gets `error`.
- `POST {PAGE_SCRAPPER_URL}/scrap/all` - gateway just forwards the JSON body and returns whatever comes back.

Every service should also have `GET /health`.

## Security stuff

- input validation (length limits, unknown fields rejected, image type checked by file content)
- URLs: only public `http(s)` on ports 80/443, no localhost / private IPs / cloud metadata (SSRF protection)
- personal data masking (email, PESEL, phone, IBAN, card number, ID number) before sending anywhere and before saving
- rate limit per IP (`RATE_LIMIT_PER_MINUTE`), behind a proxy the real IP is taken from `X-Forwarded-For`,
  but only if the request comes from `TRUSTED_PROXIES`
- audit log of important events (failed logins, rate limits, errors, config changes, ...)
- old data is deleted automatically after `RETENTION_DAYS`
- admin endpoints need `ADMIN_API_KEY`, empty key = admin endpoints are disabled
- prompt injection protection is the LLM service's job, the gateway only keeps user content separate from settings

All settings are in [.env.example](.env.example).

## Project structure

```
app/
  main.py            app setup, middleware, errors, data retention
  config.py          settings from .env
  runtime_config.py  settings you can change while running
  deps.py            channel, admin key, rate limit
  validation.py      text / URL / image validation
  pii.py             personal data masking
  services.py        calls to other services + mock mode
  verification.py    the main check flow
  db.py              database
  schemas.py         request/response models
  routers/           endpoints
tests/
```
