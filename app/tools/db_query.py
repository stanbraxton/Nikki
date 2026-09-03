"""Database tools. `db_query` runs read-only SELECTs without approval; `db_execute`
runs anything else behind the approval gate. Targets TOOL_DATABASE_URL, falling back
to Nikki's own database."""
from __future__ import annotations

import re

from langchain_core.tools import tool
from sqlalchemy import create_engine, inspect, text

from app.config import settings

_engine = None
_READ_ONLY = re.compile(r"^\s*(select|with|explain|show|pragma)\b", re.I)
_FORBIDDEN_IN_READ = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|attach)\b", re.I)
MAX_ROWS = 200


def _eng():
    global _engine
    if _engine is None:
        url = settings.tool_database_url or settings.sqlalchemy_sync_url
        _engine = create_engine(url, pool_pre_ping=True, future=True)
    return _engine


def _fmt(rows, keys) -> str:
    if not rows:
        return "(0 rows)"
    head = " | ".join(keys)
    body = "\n".join(" | ".join("" if v is None else str(v) for v in r) for r in rows)
    note = f"\n(showing first {MAX_ROWS} rows)" if len(rows) >= MAX_ROWS else f"\n({len(rows)} rows)"
    return f"{head}\n{body}{note}"


@tool
def db_schema() -> str:
    """List tables and their columns in the connected database."""
    insp = inspect(_eng())
    out = []
    for t in insp.get_table_names():
        cols = ", ".join(f"{c['name']}:{c['type']}" for c in insp.get_columns(t))
        out.append(f"{t}({cols})")
    return "\n".join(out) or "(no tables)"


@tool
def db_query(sql: str) -> str:
    """Run a read-only SQL query (SELECT / WITH / EXPLAIN) and return up to 200 rows."""
    if not _READ_ONLY.match(sql) or _FORBIDDEN_IN_READ.search(sql):
        return "refused: db_query only accepts read-only statements; use db_execute for writes"
    with _eng().connect() as conn:
        res = conn.execute(text(sql))
        rows = res.fetchmany(MAX_ROWS)
        return _fmt(rows, list(res.keys()))


@tool
def db_execute(sql: str) -> str:
    """Run a write / DDL SQL statement (INSERT, UPDATE, DELETE, CREATE ...). Requires approval."""
    with _eng().begin() as conn:
        res = conn.execute(text(sql))
        return f"ok, rowcount={res.rowcount}"


db_schema.metadata = {"requires_approval": False}
db_query.metadata = {"requires_approval": False}
db_execute.metadata = {"requires_approval": True}

TOOLS = [db_schema, db_query, db_execute]
