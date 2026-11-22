import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger("gateway.db")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Check(Base):

    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    client: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(64))
    input_type: Mapped[str] = mapped_column(String(16))
    input_masked: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)


class AuditEvent(Base):

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    client: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class ModerationCase(Base):
    """Sprawa dla moderatora (pętla informacji): podobne zgłoszenia bez rozstrzygnięcia w jednym miejscu.

    status: open (zapisana) -> recommended (system rekomenduje przekazanie) -> forwarded (faktycznie
    przekazana: webhook moderatora albo decyzja administratora) / closed (zamknięta przez moderatora).
    """

    __tablename__ = "moderation_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    claim: Mapped[str] = mapped_column(Text)
    claim_key: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True, default="open")
    risk: Mapped[bool] = mapped_column(Boolean, default=False)
    reports: Mapped[int] = mapped_column(Integer, default=1)
    check_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    channels: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    forwarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    forwarded_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class Database:
    def __init__(self, url: str) -> None:
        options: dict[str, Any] = {} if url.startswith("sqlite") else {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}
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
                logger.warning("Baza niedostępna (%s), próba %d/%d", type(exc).__name__, attempt, attempts)
                await asyncio.sleep(delay_seconds)

    async def close(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    async def save_check(self, check: Check) -> None:
        async with self.session() as s:
            s.add(check)
            await s.commit()

    async def get_check(self, check_id: str) -> Check | None:
        async with self.session() as s:
            return await s.get(Check, check_id)

    async def delete_check(self, check_id: str) -> bool:
        async with self.session() as s:
            result = await s.execute(delete(Check).where(Check.id == check_id))
            # Minimalizacja danych: zgłoszenie znika też ze sprawy moderatora; pusta sprawa jest usuwana.
            for case in (await s.execute(select(ModerationCase))).scalars():
                if check_id in (case.check_ids or []):
                    remaining = [c for c in case.check_ids if c != check_id]
                    if remaining:
                        case.check_ids = remaining
                        case.reports = max(1, case.reports - 1)
                    else:
                        await s.delete(case)
            await s.commit()
            return (result.rowcount or 0) > 0

    async def log_event(
        self,
        event_type: str,
        *,
        client: str | None = None,
        check_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        try:
            async with self.session() as s:
                s.add(AuditEvent(event_type=event_type, client=client, check_id=check_id, detail=detail))
                await s.commit()
        except Exception:
            logger.exception("Nie udało się zapisać zdarzenia audytowego %s", event_type)
        logger.info("audit %s client=%s check=%s %s", event_type, client, check_id, json.dumps(detail or {}, default=str))

    async def list_events(self, limit: int = 100, event_type: str | None = None) -> list[AuditEvent]:
        async with self.session() as s:
            q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
            if event_type:
                q = q.where(AuditEvent.event_type == event_type)
            return list((await s.execute(q)).scalars())

    async def purge_older_than(self, days: int) -> int:
        cutoff = utcnow() - timedelta(days=days)
        async with self.session() as s:
            r1 = await s.execute(delete(Check).where(Check.created_at < cutoff))
            r2 = await s.execute(delete(AuditEvent).where(AuditEvent.ts < cutoff))
            await s.commit()
            return (r1.rowcount or 0) + (r2.rowcount or 0)
