from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# PageScrapper catalog types (sources.yaml) -> policy types of the Verification Pipeline
# (conf/base/parameters.yml policy.source_weights). Contract types map to themselves.
DEFAULT_SOURCE_TYPE_MAP = {
    "official": "primary",
    "fact_check": "factcheck",
    "disinfo_db": "secondary",
    "primary": "primary",
    "factcheck": "factcheck",
    "secondary": "secondary",
    "web": "web",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "contextgraph"
    neo4j_database: str = "neo4j"

    chunk_max_chars: int = 600
    # How many fulltext / entity hits seed the expansion.
    seed_limit: int = 20
    # Seconds to wait for Neo4j on startup (the container needs a while).
    connect_timeout_s: float = 60.0

    # Embeddings: must be the SAME model the Verification Pipeline uses for queries (bge-m3, 1024).
    # provider: none (store chunks without vectors, backfill later) | ollama
    embeddings_provider: str = "none"
    embeddings_model: str = "bge-m3"
    embeddings_api_base: str = "http://127.0.0.1:11435"
    embeddings_dim: int = 1024
    embeddings_batch: int = 16
    embeddings_timeout_s: float = 60.0

    # Layer 2 (events): an OpenAI-compatible chat endpoint, e.g. Bielik on vLLM or Ollama /v1.
    # provider: none | openai
    events_provider: str = "none"
    events_api_base: str = "http://127.0.0.1:11435/v1"
    events_model: str = "bielik"
    events_api_key: str = ""
    events_timeout_s: float = 60.0

    # Reconciliation with the source of truth (PageScrapper `documents`): GET /documents/revisions,
    # re-ingest what is missing or older, embed what has no vector. Empty url = off.
    scraper_url: str = ""
    reconcile_interval_minutes: float = 0  # 0 = only on demand (POST /sync/reconcile)
    reconcile_timeout_s: float = 60.0

    source_type_map: dict[str, str] = DEFAULT_SOURCE_TYPE_MAP

    def contract_type(self, raw: str | None) -> str:
        return self.source_type_map.get((raw or "web").strip().lower(), "web")


@lru_cache
def get_settings() -> Settings:
    return Settings()
