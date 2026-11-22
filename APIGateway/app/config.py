from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"

    admin_api_key: str = ""
    allowed_channels: str = "web,discord"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    trusted_proxies: str = "127.0.0.1"

    rate_limit_per_minute: int = 30
    max_text_length: int = 5000
    max_url_length: int = 2048
    max_image_size_mb: int = 10

    database_url: str = "postgresql+asyncpg://prawdowid:prawdowid@127.0.0.1:5432/prawdowid"
    retention_days: int = 30

    llm_service_url: str = ""
    page_scrapper_url: str = ""
    image_recognition_url: str = ""
    downstream_timeout_seconds: float = 30.0
    mock_mode: bool = False
    # Wyszukiwanie na żywo (PageScrapper /live-search), gdy w bazie brak potwierdzonych danych
    live_search_enabled: bool = True
    live_search_timeout_seconds: float = 90.0
    live_search_max_documents: int = 5
    # Pętla informacji: sprawy moderatora dla zgłoszeń bez rozstrzygnięcia
    cases_enabled: bool = True
    case_escalate_after: int = 2          # tyle podobnych zgłoszeń -> rekomendacja przekazania
    case_similarity: float = 0.5          # Jaccard rdzeni słów, od którego zgłoszenia są „podobne”
    moderator_webhook_url: str = ""       # np. webhook kanału moderatorów na Discordzie; puste = bez wysyłki

    llm_provider: str = "mistral"
    llm_model: str = "mistral-large-latest"
    default_language: str = "pl"
    default_max_claims: int = 3
    default_max_sources: int = 5

    @property
    def trusted_proxy_list(self) -> list[str]:
        return [p.strip() for p in self.trusted_proxies.split(",") if p.strip()]

    @property
    def allowed_channel_list(self) -> list[str]:
        return [c.strip().lower() for c in self.allowed_channels.split(",") if c.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_image_size_bytes(self) -> int:
        return self.max_image_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
