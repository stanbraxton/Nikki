"""Persistence: LangGraph checkpointer (conversation state), execution traces, and
the Chainlit data layer (chat history shown in the sidebar). Postgres in production,
SQLite for local development."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, insert, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import ROOT, settings

log = logging.getLogger("nikki.persistence")

metadata = MetaData()
traces = Table(
    "traces",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("thread_id", String(64), index=True, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("kind", String(32), nullable=False),  # user | assistant | tool_call | tool_result | approval | error
    Column("payload", Text, nullable=False),
)

spaces = Table(
    "spaces",
    metadata,
    Column("slug", String(40), primary_key=True),
    Column("title", String(120), nullable=False),
    Column("framework", String(16), nullable=False),  # fastapi | streamlit | node
    Column("status", String(16), nullable=False),  # queued | building | pushing | provisioning | live | failed | deleted
    Column("stage_log", Text, nullable=False, default=""),
    Column("url", Text),
    Column("service_name", String(64), nullable=False),
    Column("public", String(5), nullable=False, default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_error", Text),
)

_engine: AsyncEngine | None = None
_sync_engine = None


def sync_engine():
    """Blocking engine for background workers (the Spaces deploy thread)."""
    global _sync_engine
    if _sync_engine is None:
        from sqlalchemy import create_engine

        _sync_engine = create_engine(settings.sqlalchemy_sync_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _sync_engine


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.sqlalchemy_async_url, pool_pre_ping=True)
    return _engine


async def init_db() -> None:
    """Create Nikki's own tables plus the Chainlit data-layer schema (idempotent)."""
    eng = engine()
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
        schema_file = ROOT / "app" / ("chainlit_schema_pg.sql" if settings.is_postgres else "chainlit_schema_sqlite.sql")
        for stmt in schema_file.read_text().split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
    log.info("database ready (%s)", "postgres" if settings.is_postgres else "sqlite")


async def trace(thread_id: str, kind: str, payload: Any) -> None:
    import uuid

    try:
        async with engine().begin() as conn:
            await conn.execute(
                insert(traces).values(
                    id=str(uuid.uuid4()),
                    thread_id=thread_id,
                    ts=datetime.now(timezone.utc),
                    kind=kind,
                    payload=json.dumps(payload, default=str)[:20000],
                )
            )
    except Exception:  # noqa: BLE001 — tracing must never break a turn
        log.exception("trace write failed")


@asynccontextmanager
async def checkpointer():
    """Yield a LangGraph checkpointer bound to the configured database."""
    if settings.is_postgres:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
            await saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = settings.database_url.replace("sqlite:///", "")
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            yield saver
