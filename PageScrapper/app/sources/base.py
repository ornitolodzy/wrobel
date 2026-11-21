from dataclasses import dataclass, field

from app.catalog import SourceConfig
from app.config import Settings
from app.fetch import Fetcher
from app.render import Renderer


class SourceSkipped(Exception):
    pass


@dataclass
class AdapterContext:
    source: SourceConfig
    fetcher: Fetcher
    renderer: Renderer
    settings: Settings
    known_urls: set[str]
    max_items: int
    errors: list[str] = field(default_factory=list)
    skipped_known: int = 0

    def should_skip(self, url: str) -> bool:
        skip = url in self.known_urls and not self.source.refresh_known
        self.skipped_known += int(skip)
        return skip

    def add_error(self, url: str, message: str) -> None:
        if len(self.errors) < 20:
            self.errors.append(f"{url}: {message}")

    async def require_robots(self, url: str) -> None:
        if not await self.fetcher.allowed_by_robots(url):
            raise SourceSkipped(f"robots.txt nie pozwala na pobieranie {url}")
