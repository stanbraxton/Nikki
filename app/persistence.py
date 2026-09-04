"""Persistence: LangGraph checkpointer (conversation state), execution traces, and
the Chainlit data layer (chat history shown in the sidebar). Postgres in production,
SQLite for local development."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, inspect, insert, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import ROOT, settings

log = logging.getLogger("nikki.persistence")

metadata = MetaData()
traces = Table(
    "traces",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("thread_id", String(64), index=True, nullable=False),
    Column("tenant_id", String(64), index=True, nullable=False, server_default="admin"),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("kind", String(32), nullable=False),  # user | assistant | tool_call | tool_result | approval | error
    Column("payload", Text, nullable=False),
)

spaces = Table(
    "spaces",
    metadata,
    Column("slug", String(40), primary_key=True),
    Column("tenant_id", String(64), index=True, nullable=False, server_default="admin"),
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

memories = Table(
    "memories",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(64), index=True, nullable=False, server_default="admin"),
    Column("kind", String(24), nullable=False),  # fact | preference | project | person | other
    Column("content", Text, nullable=False),
    Column("source_thread", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

google_tokens = Table(
    "google_tokens",
    metadata,
    Column("email", String(200), primary_key=True),
    Column("refresh_token", Text, nullable=False),
    Column("scopes", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

schedules = Table(
    "schedules",
    metadata,
    Column("name", String(60), primary_key=True),  # globally unique job key: {tenant_short}-{label}
    Column("tenant_id", String(64), index=True, nullable=False, server_default="admin"),
    Column("label", String(60), nullable=False, server_default=""),  # user-facing name
    Column("cron", String(60), nullable=False),
    Column("timezone", String(60), nullable=False),
    Column("prompt", Text, nullable=False),
    Column("job_name", Text, nullable=False),  # Cloud Scheduler resource name
    Column("auto_approve", String(5), nullable=False, default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

scheduled_runs = Table(
    "scheduled_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("schedule", String(60), index=True, nullable=False),
    Column("tenant_id", String(64), index=True, nullable=False, server_default="admin"),
    Column("thread_id", String(64), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String(16), nullable=False),  # running | ok | error
    Column("output", Text),
    Column("error", Text),
)

# ---- multi-tenant SaaS tables ------------------------------------------------
tenants = Table(
    "tenants",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("plan", String(24), nullable=False, default="free"),
    Column("status", String(16), nullable=False, default="active"),  # active | suspended
    Column("created_at", DateTime(timezone=True), nullable=False),
)

accounts = Table(
    "accounts",
    metadata,
    Column("email", String(200), primary_key=True),  # login identifier (lowercase)
    Column("tenant_id", String(64), index=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", String(16), nullable=False, default="member"),  # owner | member | admin
    Column("display_name", String(120)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_login_at", DateTime(timezone=True)),
)

integrations = Table(
    "integrations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(64), index=True, nullable=False),
    Column("provider", String(40), nullable=False),  # google | microsoft | tavily | custom_rest ...
    Column("label", String(200), nullable=False),  # account email / key nickname (unique per tenant+provider)
    Column("kind", String(16), nullable=False),  # oauth2 | apikey
    Column("secret_enc", Text, nullable=False),  # encrypted refresh token / api key
    Column("scopes", Text, nullable=False, default=""),
    Column("meta", Text, nullable=False, default="{}"),  # json: base_url, auth_header, ...
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

provider_credentials = Table(  # developer app registrations, set by the platform admin
    "provider_credentials",
    metadata,
    Column("provider", String(40), primary_key=True),
    Column("client_id", Text, nullable=False),
    Column("client_secret_enc", Text, nullable=False),
    Column("meta", Text, nullable=False, default="{}"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
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
        await conn.run_sync(_migrate)
        schema_file = ROOT / "app" / ("chainlit_schema_pg.sql" if settings.is_postgres else "chainlit_schema_sqlite.sql")
        for stmt in schema_file.read_text().split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
    log.info("database ready (%s)", "postgres" if settings.is_postgres else "sqlite")


def _migrate(conn) -> None:
    """Add columns introduced after a table already existed (create_all never alters)."""
    insp = inspect(conn)
    wanted = {
        "traces": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'"},
        "spaces": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'"},
        "memories": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'"},
        "schedules": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'", "label": "VARCHAR(60) NOT NULL DEFAULT ''"},
        "scheduled_runs": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'"},
    }
    for table, cols in wanted.items():
        if not insp.has_table(table):
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for col, ddl in cols.items():
            if col not in have:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}'))
                log.info("migrated: %s.%s", table, col)
    # schedules created before labels existed: label = name
    if insp.has_table("schedules"):
        conn.execute(text("UPDATE schedules SET label = name WHERE label = ''"))


def _tenant_or_admin() -> str:
    from app.tenancy import maybe_principal

    p = maybe_principal()
    return p.tenant_id if p else "admin"


async def trace(thread_id: str, kind: str, payload: Any) -> None:
    import uuid

    try:
        async with engine().begin() as conn:
            await conn.execute(
                insert(traces).values(
                    id=str(uuid.uuid4()),
                    thread_id=thread_id,
                    tenant_id=(_tenant_or_admin()),
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
