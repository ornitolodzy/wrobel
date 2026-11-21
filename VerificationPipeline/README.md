# Prawdowid: Verification Pipeline

Fact-checking pipeline for the MSZ track (HackNation 2026). The LLM handles only narrow
language tasks with JSON output; the **verdict is decided by plain code** (`policy.py`).
Design notes: `hacknation-llm-wiki/wiki/03-tech-stack/prawdowid-tech-stack.md`.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
# real models (optional): pip install -e ".[llm]" and
cp conf/local/parameters.yml.example conf/local/parameters.yml
```

## Run
```bash
.venv/bin/python -m pytest                      # policy A–J, citations, end-to-end on mocks
.venv/bin/uvicorn prawdowid.api.main:app --reload   # POST /check {"text": "..."}
.venv/bin/kedro run --pipeline eval             # metrics -> data/eval/report.json
.venv/bin/kedro run --pipeline ingestion        # corpus ingestion (skeleton)
.venv/bin/kedro viz                             # DAG view of every step
docker compose up db                            # Postgres 16 + pgvector + schema
```

## Layout
| Path | What |
|---|---|
| `src/prawdowid/models.py` | data contracts shared by all steps |
| `src/prawdowid/pipelines/verification/nodes/` | steps 0–7 as pure functions (no Kedro imports) |
| `src/prawdowid/pipelines/verification/pipeline.py` | the DAG |
| `src/prawdowid/llm/` | provider-agnostic LLM layer (LiteLLM, NLI, mock) + the 3 LLM tasks |
| `src/prawdowid/retrieval/` | `Retriever` interface (verification does not depend on the DB) |
| `docs/adr/` | decisions: AI models (0001), databases incl. graph (0002) |
| `src/prawdowid/runtime.py` | `verify(raw, channel)`, the single entry point for every channel |
| `src/prawdowid/hooks.py` | Kedro hooks = per-step audit trace |
| `src/prawdowid/pipelines/{eval,ingestion}/` | batch pipelines |
| `conf/base/parameters.yml` | models, thresholds, source weights (swap without code changes) |
| `conf/base/sources.yml` | audited source catalog |
| `db/schema.sql` | source · document · chunk · check · case |

## Models
Each LLM role (`claims`, `stance`, `compose`) is configured separately in `conf/*/parameters.yml`.
Any LiteLLM model string works (`anthropic/claude-opus-5-5`, `mistral/mistral-large-latest`,
`ollama/bielik`, `openai/<name>` + `api_base` for vLLM). Details: `docs/adr/0001-modele-ai.md`.

## Web search
1. `web_search`: links per factual claim (`web_results` in the API). Catalog domains
   (`conf/base/sources.yml`) are ranked first and labelled.
2. `fetch_web_evidence`: the top `web_fetch.top_n_pages` pages are downloaded (public IPs only,
   size/time limits, robots.txt), text extracted with trafilatura, the best-matching chunks become
   evidence and go through stance -> citation validator -> policy like corpus evidence.
3. Pages outside the catalog get `source_type: web` (weight 0.3, below `min_weight_to_count`):
   they are shown as sources but can never decide a verdict alone. Near-duplicate pages share a
   `dup_group`, so reprints are not independent confirmations.

Provider in `web_search.provider`: `mock` (default, no network), `ddgs` (DuckDuckGo, no key),
`searxng` (self-hosted, recommended for deployment), `brave` (`BRAVE_API_KEY`).
Install: `pip install -e ".[websearch]"`.

## Status
Real: normalization + PII masking, query building, verdict policy, citation validator, trace
(incl. model/tokens/cost per call), LLM layer (LiteLLM/NLI), eval harness, API.
Mocked by default (`provider: mock`, `backend: mock`): LLM calls, Postgres retrieval,
ingestion fetch/chunk/dedup/embed.
