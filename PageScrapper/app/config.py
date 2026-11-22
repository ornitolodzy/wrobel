from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: str = "INFO"
    docs_enabled: bool = True
    database_url: str = "postgresql+asyncpg://scraper:scraper@127.0.0.1:5433/scraper"
    sources_file: str = "sources.yaml"

    user_agent: str = "Mozilla/5.0 (compatible; FactCheckBot/0.1)"
    request_timeout_seconds: float = 15.0
    max_page_bytes: int = 5_000_000
    max_redirects: int = 5
    crawl_delay_seconds: float = 0.5
    retry_delay_seconds: float = 1.5

    min_text_chars: int = 200
    render_enabled: bool = True
    render_timeout_seconds: float = 30.0
    max_concurrent_renders: int = 2

    google_factcheck_api_key: str = ""
    euvsdisinfo_token: str = ""
    imports_dir: str = "imports"
    rag_ingest_url: str = ""
    # Several receivers of every stored document, comma-separated (e.g. the ContextGraph
    # /ingest/document and, during the migration, the pipeline /ingest). Push is only the fast
    # path: receivers that miss a document catch up from GET /documents/revisions.
    rag_ingest_urls: str = ""

    scrape_interval_minutes: float = 0
    scrape_on_startup: bool = False

    def ingest_targets(self) -> list[str]:
        urls = [u.strip() for u in self.rag_ingest_urls.split(",")] + [self.rag_ingest_url.strip()]
        return list(dict.fromkeys(u for u in urls if u))


@lru_cache
def get_settings() -> Settings:
    return Settings()
