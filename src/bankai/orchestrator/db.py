"""Durable stores (Postgres): LLM usage ledger and the audit trail.

The audit table is append-only by convention and by grant (the app role has INSERT/SELECT
only - see migrations). Ship it to your SIEM / WORM storage for retention requirements.
Payloads are summaries without raw PII.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Integer, String, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(128), index=True)
    agent: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Ledger:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str) -> Ledger:
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if not url.startswith("sqlite"):
            kwargs |= {"pool_size": 10, "max_overflow": 10}
        return cls(create_async_engine(url, **kwargs))

    async def create_schema(self) -> None:
        """Dev/test convenience only; staging/prod schema is owned by Alembic migrations."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def record_usage(self, **fields: Any) -> None:
        async with self.sessions() as s, s.begin():
            s.add(LlmUsage(**fields))

    async def audit(self, event_type: str, *, subject: str, outcome: str, session_id: str | None = None,
                    request_id: str | None = None, **details: Any) -> None:
        async with self.sessions() as s, s.begin():
            s.add(AuditEvent(event_type=event_type, subject=subject, outcome=outcome, session_id=session_id,
                             request_id=request_id, details=details))

    async def usage_summary(self, since: datetime) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            rows = await s.execute(
                select(LlmUsage.agent, LlmUsage.provider, LlmUsage.model, func.count(),
                       func.sum(LlmUsage.input_tokens), func.sum(LlmUsage.output_tokens), func.sum(LlmUsage.cost_usd))
                .where(LlmUsage.created_at >= since)
                .group_by(LlmUsage.agent, LlmUsage.provider, LlmUsage.model)
            )
            return [dict(zip(["agent", "provider", "model", "calls", "input_tokens", "output_tokens", "cost_usd"],
                             r, strict=True)) for r in rows.all()]

    async def dispose(self) -> None:
        await self.engine.dispose()
