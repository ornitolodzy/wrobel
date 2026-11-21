import csv
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.db import DocumentIn
from app.extract import clean_text, html_to_text, parse_date
from app.sources.base import AdapterContext, SourceSkipped

DEFAULT_FIELDS = {
    "id": "id",
    "url": "url",
    "title": "title",
    "text": ["text"],
    "published_at": "date",
    "publisher": "publisher",
}


def resolve_import_path(imports_dir: str, file_name: str) -> Path:
    base = Path(imports_dir).resolve()
    path = (base / file_name).resolve()
    if base not in path.parents:
        raise ValueError(f"Plik importu musi leżeć w katalogu {imports_dir}")
    return path


def load_records(path: Path, records_path: str | None = None) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(io.StringIO(raw)))
    data = json.loads(raw)
    for key in (records_path or "").split(".") if records_path else []:
        data = data[key]
    if not isinstance(data, list):
        raise ValueError("Plik JSON musi zawierać listę rekordów (ustaw options.records_path)")
    return data


def _get(record: dict, field: str | None) -> Any:
    value: Any = record
    for part in (field or "").split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_as_text(v) for v in value)
    text = str(value)
    return html_to_text(text) if "<" in text else text.strip()


def record_to_document(record: dict, fields: dict, ctx: AdapterContext, index: int) -> DocumentIn | None:
    source = ctx.source
    record_id = _as_text(_get(record, fields.get("id"))) or str(index)
    url = _as_text(_get(record, fields.get("url"))) or f"{source.url.rstrip('/')}#{record_id}"
    text_fields = fields.get("text") or []
    if isinstance(text_fields, str):
        text_fields = [text_fields]
    parts = []
    for name in text_fields:
        value = _as_text(_get(record, name))
        if value:
            parts.append(f"{name}: {value}" if len(text_fields) > 1 else value)
    if not parts:
        return None
    title = _as_text(_get(record, fields.get("title"))) or None
    text = clean_text("\n".join(([title] if title else []) + parts))
    return DocumentIn(
        url=url,
        source_id=source.id,
        source_type=source.source_type,
        title=title,
        publisher=_as_text(_get(record, fields.get("publisher"))) or source.publisher,
        published_at=parse_date(_as_text(_get(record, fields.get("published_at")))),
        text=text,
        extra={"record_id": record_id, "import_file": source.options.get("file")},
    )


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    source = ctx.source
    file_name = source.options.get("file")
    if not file_name:
        raise ValueError(f"Źródło {source.id}: brak options.file")
    path = resolve_import_path(ctx.settings.imports_dir, file_name)
    if not path.exists():
        raise SourceSkipped(f"Brak pliku {path.name} w katalogu {ctx.settings.imports_dir} - źródło pominięte.")
    fields = {**DEFAULT_FIELDS, **(source.options.get("fields") or {})}
    records = load_records(path, source.options.get("records_path"))
    for index, record in enumerate(records[: ctx.max_items]):
        doc = record_to_document(record, fields, ctx, index)
        if doc is None:
            ctx.add_error(f"{path.name}#{index}", "rekord bez treści")
            continue
        if ctx.should_skip(doc.url):
            continue
        yield doc
