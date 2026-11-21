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
docker compose -f infra/docker/docker-compose.yml up db   # Postgres 16 + pgvector + schema
```

## Layout
| Path | What |
|---|---|
| `src/prawdowid/models.py` | data contracts shared by all steps |
| `src/prawdowid/pipelines/verification/nodes/` | steps 0–7 as pure functions (no Kedro imports) |
| `src/prawdowid/pipelines/verification/pipeline.py` | the DAG |
| `src/prawdowid/llm/` | provider-agnostic LLM layer (LiteLLM, NLI, mock) + the 3 LLM tasks |
| `src/prawdowid/retrieval/` | `Retriever` interface (verification does not depend on the DB) |
| `docs/adr/` | decisions: AI models (0001), databases incl. graph (0002), citation validator + answer format (0003), RAG index + retriever (0004), context only from the graph (0005) |
| `src/prawdowid/runtime.py` | `verify(raw, channel)`, the single entry point for every channel |
| `src/prawdowid/hooks.py` | Kedro hooks = per-step audit trace |
| `src/prawdowid/pipelines/{eval,ingestion}/` | batch pipelines |
| `conf/base/parameters.yml` | models, thresholds, source weights (swap without code changes) |
| `conf/base/sources.yml` | audited source catalog |
| `db/schema.sql` | source · document · chunk · check · case |
| `src/prawdowid/api/gateway.py` | service contracts of the stack: `/verify` (API Gateway), `/ingest` (PageScrapper) |
| `src/prawdowid/corpus.py` | corpus store fed by PageScrapper: chunk + embed into `document` / `chunk` |
| `conf/docker/` | parameters for the docker stack (`PRAWDOWID_ENV=docker`: Postgres retrieval) |
| `infra/docker/` | Dockerfile (context = repo root), its `.dockerignore`, standalone compose |

## Models
Each LLM role (`claims`, `stance`, `compose`) is configured separately in `conf/*/parameters.yml`.
Any LiteLLM model string works (`anthropic/claude-opus-5-5`, `mistral/mistral-large-latest`,
`ollama/bielik`, `openai/<name>` + `api_base` for vLLM). Details: `docs/adr/0001-modele-ai.md`.

## Context source
The verification pipeline does not access the web. Context comes only from the knowledge graph in
Neo4j, maintained by another team (`retrieval.backend: neo4j`, read-only, `NEO4J_PASSWORD` in env).
Data contract: `docs/neo4j-kontrakt.md`; decision: `docs/adr/0005-kontekst-tylko-z-grafu.md`.
Our own Postgres corpus (`ingestion` pipeline) remains as an alternative backend.

## Corpus (RAG)
```bash
docker compose -f infra/docker/docker-compose.yml up -d db   # Postgres 16 + pgvector, schema from db/schema.sql
.venv/bin/kedro run --pipeline ingestion                 # crawl Linki.md sources (+1 level), embed, store
cat data/ingestion/report.json                           # docs per domain, failures, >=100 docs / >=10 domains
```
Set `retrieval.backend: postgres` and `embeddings.provider: ollama` in `conf/local/parameters.yml`.
Every retrieved chunk carries a `retrieval` trace (dense/sparse ranks, RRF, reranker score, corpus version);
dropped candidates are listed in the check trace with a reason. Details: `docs/adr/0004-rag-indeks-i-retriever.md`.
Tests needing the database are marked `db` and skipped when it is not running.

## Status
Real: normalization + PII masking, query building, verdict policy, citation validator, trace
(incl. model/tokens/cost per call), LLM layer (LiteLLM/NLI), eval harness, API.
Mocked by default (`provider: mock`, `backend: mock`): LLM calls and retrieval.
