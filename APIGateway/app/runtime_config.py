import asyncio

from app.config import Settings
from app.schemas import ResolvedOptions, RuntimeConfig, RuntimeConfigUpdate


class RuntimeConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._lock = asyncio.Lock()
        self._config = RuntimeConfig(
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            default_language=settings.default_language,
            default_max_claims=settings.default_max_claims,
            default_max_sources=settings.default_max_sources,
            mock_mode=settings.mock_mode,
        )

    def get(self) -> RuntimeConfig:
        return self._config.model_copy()

    async def update(self, patch: RuntimeConfigUpdate) -> tuple[RuntimeConfig, dict[str, object]]:
        changes = patch.model_dump(exclude_none=True)
        async with self._lock:
            self._config = self._config.model_copy(update=changes)
            return self._config.model_copy(), changes

    def resolve_options(self) -> ResolvedOptions:
        cfg = self._config
        return ResolvedOptions(
            language=cfg.default_language,
            max_claims=cfg.default_max_claims,
            max_sources=cfg.default_max_sources,
        )
