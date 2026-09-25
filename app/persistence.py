"""Persistence: LangGraph checkpointer (conversation state), execution traces, and
the Chainlit data layer (chat history shown in the sidebar). Postgres in production,
SQLite for local development."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy import Column, DateTime, Index, Integer, MetaData, String, Table, Text, and_, inspect, insert, select, text, update
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

apps = Table(  # Engineer: full web apps (git repo -> Cloud Build -> Convex + Firebase Hosting)
    "apps",
    metadata,
    Column("slug", String(40), primary_key=True),
    Column("title", String(120), nullable=False),
    Column("repo", String(200), nullable=False),  # owner/name on GitHub
    Column("branch", String(80), nullable=False, default="main"),
    Column("build_dir", String(80), nullable=False, default="dist"),
    Column("convex_secret", String(120)),  # Secret Manager name holding CONVEX_DEPLOY_KEY, or NULL for static apps
    Column("firebase_site", String(80), nullable=False),
    Column("custom_domain", String(200)),
    Column("url", Text),
    Column("status", String(16), nullable=False, default="registered"),  # registered | building | live | failed
    Column("last_build_id", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

builds = Table(
    "builds",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("slug", String(40), index=True, nullable=False),
    Column("status", String(16), nullable=False),  # queued | running | success | failed
    Column("log", Text, nullable=False, default=""),
    Column("url", Text),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
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
    Column("status", String(16), nullable=False, default="active"),  # active | pending | suspended
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
    # Admin row only: the ADMIN_PASSWORD_HASH value last copied into password_hash.
    # ensure_admin() re-applies the env hash only when it differs from this, so a
    # password changed in the app survives restarts while rotating the env var
    # still works as a recovery path.
    Column("env_hash_applied", Text),
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

token_usage = Table(  # track LLM token consumption per conversation turn
    "token_usage",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(64), index=True, nullable=False, server_default="admin"),
    Column("thread_id", String(64), index=True, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("model", String(60), nullable=False),  # provider:model (e.g. anthropic:claude-sonnet-4-5)
    Column("input_tokens", Integer, nullable=False, default=0),
    Column("output_tokens", Integer, nullable=False, default=0),
    Column("total_tokens", Integer, nullable=False, default=0),
    # Subsets of input_tokens, so /turns and usage reports can tell whether caching works.
    Column("cache_read_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("cache_creation_tokens", Integer, nullable=False, default=0, server_default="0"),
)

# An approval is a durable authorization for exactly one paused graph checkpoint.
# It binds the decision to the signed-in user and lets the database—not a Cloud Run
# instance's memory—decide which click/reply, if any, may resume that checkpoint.
approvals = Table(
    "approvals",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("thread_id", String(64), index=True, nullable=False),
    Column("tenant_id", String(64), index=True, nullable=False),
    Column("email", String(200), nullable=False),
    Column("tool_names", Text, nullable=False, default="[]"),
    Column("status", String(32), index=True, nullable=False),  # pending | approved_running | rejected_running | completed | expired | superseded | failed
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("claimed_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
)
Index(
    "ix_approvals_one_pending_thread",
    approvals.c.thread_id,
    unique=True,
    # Keep at most one unclaimed authorization per conversation across every
    # Cloud Run instance. Terminal rows remain available for audit/debugging.
    postgresql_where=text("status = 'pending'"),
    sqlite_where=text("status = 'pending'"),
)

_engine: AsyncEngine | None = None
_sync_engine = None
_checkpoint_pool = None
_checkpoint_setup_lock = None
_checkpoint_ready = False
_POOL_SIZE = 1
_MAX_OVERFLOW = 0
_POOL_TIMEOUT = 20


def async_engine_kwargs() -> dict[str, Any]:
    """Keep each Cloud Run instance within Cloud SQL's small connection budget."""
    if not settings.is_postgres:
        return {}
    return {
        "pool_size": _POOL_SIZE,
        "max_overflow": _MAX_OVERFLOW,
        "pool_timeout": _POOL_TIMEOUT,
        # Cloud SQL drops idle connections; recycle well before that and ping
        # before every checkout so a dead socket is replaced instead of raised
        # ("connection is closed" 500s on /api/run, 2026-09-24).
        "pool_recycle": 600,
        "pool_pre_ping": True,
    }


def sync_engine_kwargs() -> dict[str, Any]:
    """Settings shared by the few synchronous helper engines."""
    if not settings.is_postgres:
        return {}
    return {
        "pool_size": 1,
        "max_overflow": 0,
        "pool_timeout": _POOL_TIMEOUT,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }


def sync_engine():
    """Blocking engine for background workers (the Spaces deploy thread)."""
    global _sync_engine
    if _sync_engine is None:
        from sqlalchemy import create_engine

        _sync_engine = create_engine(settings.sqlalchemy_sync_url, **sync_engine_kwargs())
    return _sync_engine


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.sqlalchemy_async_url, **async_engine_kwargs())
    return _engine


def chainlit_data_layer():
    """Create Chainlit's data layer under the same small Cloud SQL budget."""
    from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

    # Chainlit 2.12 does not expose engine kwargs. Patch its imported factory
    # only while the data layer is initialized, so its normal constructor builds
    # the correct bounded engine/session pair from the outset.
    from unittest.mock import patch
    import chainlit.data.sql_alchemy as chainlit_sqlalchemy

    real_create_async_engine = chainlit_sqlalchemy.create_async_engine

    def bounded_create_async_engine(url: str, *args: Any, **kwargs: Any) -> AsyncEngine:
        kwargs.update(async_engine_kwargs())
        return real_create_async_engine(url, *args, **kwargs)

    with patch.object(chainlit_sqlalchemy, "create_async_engine", bounded_create_async_engine):
        return SQLAlchemyDataLayer(conninfo=settings.sqlalchemy_async_url)


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
    await expire_pending_approvals()
    log.info("database ready (%s)", "postgres" if settings.is_postgres else "sqlite")


async def close_db() -> None:
    """Close pooled database resources cleanly when a Cloud Run instance stops."""
    global _engine, _sync_engine, _checkpoint_pool, _checkpoint_setup_lock, _checkpoint_ready
    if _checkpoint_pool is not None:
        await _checkpoint_pool.close()
        _checkpoint_pool = None
    _checkpoint_setup_lock = None
    _checkpoint_ready = False
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    if _sync_engine is not None:
        _sync_engine.dispose()
        _sync_engine = None


def _migrate(conn) -> None:
    """Add columns introduced after a table already existed (create_all never alters)."""
    insp = inspect(conn)
    wanted = {
        "traces": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'"},
        "spaces": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'"},
        "memories": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'"},
        "schedules": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'", "label": "VARCHAR(60) NOT NULL DEFAULT ''"},
        "scheduled_runs": {"tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'admin'"},
        "accounts": {"env_hash_applied": "TEXT"},
        "token_usage": {"cache_read_tokens": "INTEGER NOT NULL DEFAULT 0",
                        "cache_creation_tokens": "INTEGER NOT NULL DEFAULT 0"},
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


def _approval_now() -> datetime:
    return datetime.now(timezone.utc)


async def expire_pending_approvals() -> None:
    """Durably expire unanswered approvals without touching their graph checkpoint.

    The next non-approval message safely supersedes the still-paused graph state,
    rather than allowing an old financial/write action to run after its approval
    window has elapsed.
    """
    now = _approval_now()
    async with engine().begin() as conn:
        await conn.execute(
            update(approvals)
            .where(and_(approvals.c.status == "pending", approvals.c.expires_at <= now))
            .values(status="expired", completed_at=now)
        )


async def create_pending_approval(thread_id: str, tenant_id: str, email: str, tool_names: list[str],
                                  ttl: timedelta = timedelta(hours=5)) -> str:
    """Create the one durable approval record for a paused graph checkpoint."""
    now = _approval_now()
    approval_id = str(uuid.uuid4())
    async with engine().begin() as conn:
        # The partial unique index below makes this close/reopen atomic across
        # instances. Retiring a previous pending approval preserves the audit
        # trail but leaves it impossible to claim.
        await conn.execute(update(approvals).where(and_(
            approvals.c.thread_id == thread_id, approvals.c.status == "pending"
        )).values(status="superseded", completed_at=now))
        await conn.execute(
            insert(approvals).values(
                id=approval_id,
                thread_id=thread_id,
                tenant_id=tenant_id,
                email=email.lower(),
                tool_names=json.dumps(tool_names),
                status="pending",
                created_at=now,
                expires_at=now + ttl,
            )
        )
    return approval_id


async def claim_pending_approval(thread_id: str, tenant_id: str, email: str, approved: bool,
                                 approval_id: str | None = None) -> tuple[str | None, str]:
    """Atomically claim a pending approval before any graph/tool work starts.

    Compare-and-set status transition is the cross-instance exactly-once barrier:
    duplicate action events, typed replies, or a second Cloud Run instance can only
    make one ``pending`` row become ``*_running``. A claimed approval is never
    automatically retried because an external tool could already have run.
    """
    now = _approval_now()
    email = email.lower()
    async with engine().begin() as conn:
        if approval_id is None:
            row = (
                await conn.execute(
                    select(approvals.c.id)
                    .where(
                        and_(
                            approvals.c.thread_id == thread_id,
                            approvals.c.tenant_id == tenant_id,
                            approvals.c.email == email,
                            approvals.c.status == "pending",
                        )
                    )
                    .order_by(approvals.c.created_at.desc())
                    .limit(1)
                )
            ).first()
            if not row:
                return None, "no_pending"
            approval_id = row.id

        result = await conn.execute(
            update(approvals)
            .where(
                and_(
                    approvals.c.id == approval_id,
                    approvals.c.thread_id == thread_id,
                    approvals.c.tenant_id == tenant_id,
                    approvals.c.email == email,
                    approvals.c.status == "pending",
                    approvals.c.expires_at > now,
                )
            )
            .values(
                status="approved_running" if approved else "rejected_running",
                claimed_at=now,
            )
        )
        if result.rowcount:
            return approval_id, "claimed"

        # Return a deliberately generic result to callers unless the record belongs
        # to this user. This avoids disclosing another tenant's thread or approval.
        row = (await conn.execute(select(approvals).where(approvals.c.id == approval_id))).first()
        if not row:
            return None, "no_pending"
        record = row._mapping
        if record["thread_id"] != thread_id or record["tenant_id"] != tenant_id or record["email"] != email:
            return None, "not_owner"
        if record["status"] == "pending":
            expired = await conn.execute(
                update(approvals)
                .where(
                    and_(
                        approvals.c.id == approval_id,
                        approvals.c.status == "pending",
                        approvals.c.expires_at <= now,
                    )
                )
                .values(status="expired", completed_at=now)
            )
            if expired.rowcount:
                return approval_id, "expired"
        return approval_id, record["status"]


async def supersede_pending_approval(thread_id: str, tenant_id: str, email: str) -> str:
    """Cancel an unanswered approval when the owner sends a different message.

    Never supersede an execution that another instance already claimed; that is a
    safe stop rather than risking a conflicting graph update or double tool run.
    """
    now = _approval_now()
    email = email.lower()
    async with engine().begin() as conn:
        await conn.execute(
            update(approvals)
            .where(
                and_(
                    approvals.c.thread_id == thread_id,
                    approvals.c.tenant_id == tenant_id,
                    approvals.c.email == email,
                    approvals.c.status == "pending",
                )
            )
            .values(status="superseded", completed_at=now)
        )
        active = (
            await conn.execute(
                select(approvals.c.status)
                .where(
                    and_(
                        approvals.c.thread_id == thread_id,
                        approvals.c.tenant_id == tenant_id,
                        approvals.c.email == email,
                        approvals.c.status.in_(("approved_running", "rejected_running")),
                    )
                )
                .order_by(approvals.c.claimed_at.desc())
                .limit(1)
            )
        ).first()
    return "running" if active else "ok"


async def finish_approval(approval_id: str, status: str = "completed") -> None:
    """Record a terminal result after the claimed graph continuation returns."""
    if status not in ("completed", "failed"):
        raise ValueError("invalid terminal approval status")
    now = _approval_now()
    async with engine().begin() as conn:
        await conn.execute(
            update(approvals)
            .where(
                and_(
                    approvals.c.id == approval_id,
                    approvals.c.status.in_(("approved_running", "rejected_running")),
                )
            )
            .values(status=status, completed_at=now)
        )


async def approval_tool_names(approval_id: str) -> list[str] | None:
    """Return the exact tools authorized by an approval after it was claimed."""
    async with engine().connect() as conn:
        row = (
            await conn.execute(
                select(approvals.c.tool_names).where(
                    and_(
                        approvals.c.id == approval_id,
                        approvals.c.status.in_(("approved_running", "rejected_running")),
                    )
                )
            )
        ).first()
    if not row:
        return None
    try:
        names = json.loads(row.tool_names)
    except (TypeError, json.JSONDecodeError):
        return None
    return names if isinstance(names, list) and all(isinstance(name, str) for name in names) else None


async def trace(thread_id: str, kind: str, payload: Any) -> None:
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


async def tokens_used_today() -> int:
    """Cost-weighted tokens this tenant has used since 00:00 UTC. 0 if the lookup fails.

    Weighted like guards.TurnBudget.cost_weighted: cache reads count 0.1x and cache
    writes 1.25x, so a day of well-cached turns is judged by what it cost, not by
    how many times the same prefix was re-read.
    """
    from sqlalchemy import func

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        async with engine().connect() as conn:
            v = (await conn.execute(
                select(func.coalesce(func.sum(
                    token_usage.c.total_tokens
                    - 0.9 * token_usage.c.cache_read_tokens
                    + 0.25 * token_usage.c.cache_creation_tokens), 0))
                .where(token_usage.c.tenant_id == _tenant_or_admin())
                .where(token_usage.c.ts >= start))).scalar()
        return int(v or 0)
    except Exception:  # noqa: BLE001 - a failed lookup must never block a turn
        log.exception("daily token lookup failed")
        return 0


async def log_token_usage(thread_id: str, model: str, input_tokens: int, output_tokens: int,
                          cache_read: int = 0, cache_creation: int = 0) -> None:
    """Record token usage for a conversation turn."""
    import uuid

    try:
        async with engine().begin() as conn:
            await conn.execute(
                insert(token_usage).values(
                    id=str(uuid.uuid4()),
                    thread_id=thread_id,
                    tenant_id=(_tenant_or_admin()),
                    ts=datetime.now(timezone.utc),
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_creation,
                )
            )
    except Exception:  # noqa: BLE001 — token tracking must never break a turn
        log.exception("token usage logging failed")


@asynccontextmanager
async def checkpointer() -> AsyncIterator[Any]:
    """Yield a LangGraph checkpointer bound to the configured database."""
    if settings.is_postgres:
        global _checkpoint_pool, _checkpoint_setup_lock, _checkpoint_ready
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        if _checkpoint_setup_lock is None:
            _checkpoint_setup_lock = asyncio.Lock()
        async with _checkpoint_setup_lock:
            if _checkpoint_pool is None:
                _checkpoint_pool = AsyncConnectionPool(
                    conninfo=settings.database_url,
                    min_size=0,
                    max_size=1,
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": 0,
                        "row_factory": dict_row,
                        # libpq TCP keepalives so a silently dropped socket is
                        # noticed instead of failing mid-turn.
                        "keepalives": 1,
                        "keepalives_idle": 60,
                        "keepalives_interval": 10,
                        "keepalives_count": 3,
                    },
                    # Verify the connection before handing it out; retire idle
                    # ones before Cloud SQL closes them ("server closed the
                    # connection unexpectedly" mid-run, 2026-09-24).
                    check=AsyncConnectionPool.check_connection,
                    max_idle=300,
                    max_lifetime=1800,
                    open=False,
                )
                await _checkpoint_pool.open()
            if not _checkpoint_ready:
                await AsyncPostgresSaver(_checkpoint_pool).setup()
                _checkpoint_ready = True
        saver = AsyncPostgresSaver(_checkpoint_pool)
        yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = settings.database_url.replace("sqlite:///", "")
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            yield saver
