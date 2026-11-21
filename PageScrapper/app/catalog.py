from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["official", "fact_check", "disinfo_db"]
Adapter = Literal["govpl_list", "html_list", "odyseusz", "rss", "sejm_eli", "google_factcheck", "euvsdisinfo_mcp", "file_import"]


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    name: str
    publisher: str
    source_type: SourceType
    adapter: Adapter
    url: str
    active: bool = True
    max_items: int = Field(default=20, ge=1, le=500)
    refresh_known: bool = False
    license: str = ""
    description: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class Catalog(BaseModel):
    sources: list[SourceConfig]

    def get(self, source_id: str) -> SourceConfig | None:
        return next((s for s in self.sources if s.id == source_id), None)


def load_catalog(path: str | Path) -> Catalog:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    catalog = Catalog.model_validate(data)
    ids = [s.id for s in catalog.sources]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate source ids in catalog: {sorted(duplicates)}")
    return catalog
