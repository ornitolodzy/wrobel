# PRAWDOWID - PageScrapper

Scraper service for our PRAWDOWID project (HackNation 2026). It does two things:

1. **Reads any link from a user** (`POST /scrape`) - the API Gateway calls it when someone checks a URL,
   and the LLM gets clean page text instead of a link.
2. **Builds our knowledge base** (`POST /scrap/all`) - downloads documents from the sources listed in
   [sources.yaml](sources.yaml), saves them to Postgres and (optionally) sends them to the RAG service.

```
user link:  API Gateway -> POST /scrape -> page text + title + date -> LLM
sources:    API Gateway (admin) -> POST /scrap/all -> sources.yaml -> Postgres (+ RAG)
```

## Sources

The full list is in [sources.yaml](sources.yaml) - it's in the repo on purpose, so anyone can audit it.
How we pick, refresh and drop sources (plus the ones we rejected and why) is in **[SOURCES.md](SOURCES.md)**.

In short:
- **official:** RCB alerts, MON, MSZ Odyseusz, Policja (KGP + Mazowsze), CERT Polska, Dziennik Ustaw (Sejm ELI API)
- **fact_check:** Demagog, Konkret24, FakeNews.pl, EUvsDisinfo (+ Google Fact Check API with a key)
- **disinfo_db** (context only): EUvsDisinfo case database (needs a token) and the organizer's incident file (JSON/CSV)

Without any keys that's 10 domains and ~280 documents per full run, which covers the task requirement
(min. 100 documents, 10 domains, 2 source types). Check it live with `GET /stats`.

Every document has: url, title, publisher, publication date, download date, text, source type.

**Adding a source:** add an entry to `sources.yaml` - no code changes needed for gov.pl lists, RSS feeds,
simple HTML lists and JSON/CSV files (see SOURCES.md).

**Incident file from the organizer:** put it in `imports/` (e.g. `imports/incidents.json`), fix the field mapping
of `baza-incydentow` in `sources.yaml` and run `/scrap/all`. Files in `imports/` are not committed.

## How to run

### Docker

```bash
cp .env.example .env      # set POSTGRES_PASSWORD (and GOOGLE_FACTCHECK_API_KEY if you have one)
docker compose up -d --build
```

Swagger: http://localhost:8002/docs

Postgres for the scraper runs on port 5433, so it doesn't clash with the gateway's Postgres on 5432.

### Without Docker (Python 3.12)

```bash
docker compose up -d scraper-postgres
python -m venv .venv
.venv\Scripts\activate    # linux/mac: source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium
cp .env.example .env
uvicorn app.main:app --port 8002
```

Don't use `--reload` on Windows - the headless browser doesn't work with it.

### Tests

```bash
pytest
```

Tests don't touch the internet (all pages are faked).

## Endpoints

| Method | Path | What it does |
|---|---|---|
| POST | `/scrape` | read one page: `{"url": "...", "render": "auto"}` |
| POST | `/scrap/all` | start scraping in the background: `{"source_ids": [...], "max_items_per_source": 10}` (both optional) |
| GET | `/scrap/runs` | history of scraping runs |
| GET | `/scrap/runs/{run_id}` | status of one run (new / updated / unchanged / errors per source) |
| GET | `/sources` | the source catalog + how many documents we have |
| GET | `/stats` | corpus summary: documents, domains, source types, date range |
| GET | `/documents` | saved documents (`?source_id=&limit=&offset=`), only a preview of the text |
| GET | `/documents/{id}` | one document with full text |
| GET | `/health` | is it alive |

`render`: `auto` = use the headless browser only if the normal download gives almost no text,
`never` = never, `always` = always.

`/scrape` errors (always `{"detail": {"code": "...", "message": "..."}}`):

| HTTP | code | when |
|---|---|---|
| 422 | `invalid_url` / `blocked_url` / `dns_error` | bad link, private / internal address |
| 422 | `no_text` | page has no readable text (or blocks bots) |
| 415 | `unsupported_content` | not a web page (pdf, image...) |
| 502 / 504 | `http_error` / `fetch_error` / `timeout` | the site is down or refused |

The scraper does **not** save pages from users, only documents from the source catalog.

## Sending documents to RAG

Set `RAG_INGEST_URL` and every new or changed document is POSTed there as:

```json
{"url": "...", "source_id": "rcb-komunikaty", "source_type": "official", "title": "...", "publisher": "...",
 "published_at": "2026-11-20", "retrieved_at": "...", "language": "pl", "text": "...", "content_hash": "..."}
```

The RAG service can also just read the `documents` table or `GET /documents`.

## How it behaves

- scrapes automatically every `SCRAPE_INTERVAL_MINUTES` (default in `.env.example`: 6 hours), runs show
  `"trigger": "schedule"` or `"api"` in their history
- one retry after a network error (the hackathon wifi is flaky)
- respects `robots.txt` for catalog sources and waits between requests to the same site (`CRAWL_DELAY_SECONDS`)
- doesn't bypass paywalls, logins or bot protection - if a site blocks us we say so
  (e.g. pap.pl homepage blocks bots, Wikipedia wants a User-Agent with contact info - set `USER_AGENT`)
- every link and redirect is checked so it can't point to localhost / private networks (SSRF),
  the headless browser also blocks requests to private addresses
- page size limit, timeouts, only text/html content
- second run doesn't download known articles again, Odyseusz profiles are refreshed (they change)
- Demagog's robots.txt says `ai-train=no` - we only use their articles for search and citing with a link
- page text is untrusted data - the scraper never "executes" it, prompt injection protection is in the LLM service
- Google Fact Check needs `GOOGLE_FACTCHECK_API_KEY` (free in Google Cloud Console), without it the source is skipped
- EUvsDisinfo database needs `EUVSDISINFO_TOKEN` (the organizer offered help with tokens), without it the source is skipped
- the scraper has no auth - don't expose port 8002 to the internet, only the gateway should call it

## Project structure

```
app/
  main.py          app setup
  config.py        settings from .env
  catalog.py       loads sources.yaml
  fetch.py         safe downloading (SSRF checks, redirects, size limit, robots.txt)
  render.py        headless browser (Playwright) for JS pages
  extract.py       text / title / date extraction
  scrape.py        POST /scrape logic
  ingest.py        POST /scrap/all logic + sending to RAG
  db.py            database
  sources/         one file per source type (govpl, html_list, odyseusz, rss, sejm_eli,
                   google_factcheck, euvsdisinfo_mcp, file_import)
  routers/         endpoints
sources.yaml       the source catalog
SOURCES.md         rules for picking / refreshing / dropping sources
imports/           JSON / CSV files to import (not committed)
tests/
```

## Licenses of what we use

All libraries are open source with permissive licenses (no copyleft), checked from the installed package metadata:

| library | license |
|---|---|
| fastapi, pydantic, pydantic-settings, beautifulsoup4, PyYAML, SQLAlchemy, aiosqlite, pytest | MIT |
| uvicorn, httpx, lxml | BSD-3-Clause |
| trafilatura, asyncpg, playwright | Apache-2.0 |
| Chromium headless shell (installed by Playwright in Docker) | BSD-3-Clause + bundled third-party licenses |
| PostgreSQL (Docker image) | PostgreSQL License |

Licenses / terms of the data sources are in [sources.yaml](sources.yaml) (`license` field) and [SOURCES.md](SOURCES.md).
