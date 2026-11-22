import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Integer, String, Text, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger("scraper.db")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(256), nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    results: Mapped[dict[str, Any]] = mapped_column(JSON)


@dataclass
class DocumentIn:
    url: str
    source_id: str
    source_type: str
    title: str | None
    publisher: str | None
    published_at: date | None
    text: str
    language: str | None = "pl"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        payload = f"{self.title}\n{self.published_at}\n{self.text}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, url: str) -> None:
        options: dict[str, Any] = {} if url.startswith("sqlite") else {"pool_pre_ping": True}
        self.engine: AsyncEngine = create_async_engine(url, **options)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self, attempts: int = 10, delay_seconds: float = 2.0) -> None:
        for attempt in range(1, attempts + 1):
            try:
                async with self.engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                return
            except (OSError, OperationalError) as exc:
                if attempt == attempts:
                    raise
                logger.warning("Database unavailable (%s), attempt %d/%d", type(exc).__name__, attempt, attempts)
                await asyncio.sleep(delay_seconds)

    async def close(self) -> None:
        await self.engine.dispose()

    async def upsert(self, doc: DocumentIn) -> tuple[str, Document]:
        async with self.sessionmaker() as s:
            existing = (await s.execute(select(Document).where(Document.url == doc.url))).scalar_one_or_none()
            digest = doc.content_hash
            if existing is not None and existing.content_hash == digest:
                return "unchanged", existing
            target = existing or Document(url=doc.url)
            target.source_id = doc.source_id
            target.source_type = doc.source_type
            target.title = doc.title
            target.publisher = doc.publisher
            target.published_at = doc.published_at
            target.retrieved_at = utcnow()
            target.language = doc.language
            target.text = doc.text
            target.content_hash = digest
            target.extra = doc.extra or None
            if existing is None:
                s.add(target)
            await s.commit()
            return ("new" if existing is None else "updated"), target

    async def existing_urls(self, urls: list[str]) -> set[str]:
        if not urls:
            return set()
        async with self.sessionmaker() as session:
            rows = await session.execute(select(Document.url).where(Document.url.in_(urls)))
            return set(rows.scalars())

    async def known_urls(self, source_id: str) -> set[str]:
        async with self.sessionmaker() as s:
            rows = await s.execute(select(Document.url).where(Document.source_id == source_id))
            return set(rows.scalars())

    async def list_documents(self, source_id: str | None, limit: int, offset: int) -> tuple[int, list[Document]]:
        async with self.sessionmaker() as s:
            query = select(Document)
            count_query = select(func.count(Document.id))
            if source_id:
                query = query.where(Document.source_id == source_id)
                count_query = count_query.where(Document.source_id == source_id)
            query = query.order_by(Document.published_at.desc().nulls_last(), Document.id.desc()).limit(limit).offset(offset)
            total = (await s.execute(count_query)).scalar_one()
            return total, list((await s.execute(query)).scalars())

    async def get_document(self, doc_id: int) -> Document | None:
        async with self.sessionmaker() as s:
            return await s.get(Document, doc_id)

    async def stats_by_source(self) -> dict[str, dict[str, Any]]:
        async with self.sessionmaker() as s:
            rows = await s.execute(
                select(Document.source_id, func.count(Document.id), func.max(Document.retrieved_at)).group_by(Document.source_id)
            )
            return {sid: {"documents": count, "last_retrieved_at": last} for sid, count, last in rows.all()}

    async def corpus_rows(self) -> list[tuple[str, str, str, date | None]]:
        async with self.sessionmaker() as s:
            rows = await s.execute(select(Document.url, Document.source_id, Document.source_type, Document.published_at))
            return [tuple(r) for r in rows.all()]

    async def save_run(self, run: ScrapeRun) -> None:
        async with self.sessionmaker() as s:
            await s.merge(run)
            await s.commit()

    async def get_run(self, run_id: str) -> ScrapeRun | None:
        async with self.sessionmaker() as s:
            return await s.get(ScrapeRun, run_id)

    async def list_runs(self, limit: int) -> list[ScrapeRun]:
        async with self.sessionmaker() as s:
            return list((await s.execute(select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(limit))).scalars())
