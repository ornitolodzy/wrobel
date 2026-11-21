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

    scrape_interval_minutes: float = 0
    scrape_on_startup: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
