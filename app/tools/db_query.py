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
    try:
        with _eng().connect() as conn:
            res = conn.execute(text(sql))
            rows = res.fetchmany(MAX_ROWS)
            return _fmt(rows, list(res.keys()))
    except Exception as e:  # noqa: BLE001 - surface to the model so it can fix the query
        msg = str(getattr(e, "orig", e)).splitlines()[0][:400]
        hint = ""
        if "createdAt" in msg or "created_at" in msg or "threadId" in msg or "thread_id" in msg:
            hint = ' Hint: Chainlit columns are camelCase and must be double-quoted: "createdAt", "threadId", "userIdentifier". Prefer recall_chats / recall_thread instead of raw SQL.'
        return f"query error: {msg}.{hint} Fix the SQL and try again (call db_schema if unsure)."


@tool
def recall_chats(search: str = "", limit: int = 10) -> str:
    """List Nikki's recent chat sessions (id, name, started). Optional case-insensitive
    `search` matches the session name OR any message text in the session. Use this to find
    an earlier conversation (e.g. a sermon prep) before recall_thread."""
    limit = max(1, min(int(limit or 10), 50))
    params: dict = {"lim": limit}
    where = ""
    if search:
        where = ('WHERE lower(coalesce(t.name, \'\')) LIKE :q OR EXISTS ('
                 'SELECT 1 FROM steps s WHERE s."threadId" = t.id AND lower(coalesce(s.output, \'\')) LIKE :q)')
        params["q"] = f"%{search.lower()}%"
    sql = (f'SELECT t.id, t.name, t."createdAt" AS started, '
           f'(SELECT count(*) FROM steps s WHERE s."threadId"=t.id AND s.type IN (\'user_message\',\'assistant_message\')) AS messages '
           f'FROM threads t {where} ORDER BY t."createdAt" DESC LIMIT :lim')
    try:
        with _eng().connect() as conn:
            res = conn.execute(text(sql), params)
            return _fmt(res.fetchall(), list(res.keys()))
    except Exception as e:  # noqa: BLE001
        return f"recall_chats error: {str(getattr(e, 'orig', e))[:300]}"


@tool
def recall_thread(thread_id: str, max_chars: int = 60000) -> str:
    """Return the full user/assistant transcript of an earlier chat session (thread id or
    unique id prefix from recall_chats), oldest first. Use it to harvest material from a
    previous conversation, e.g. sermon prep done in another chat."""
    sql = ('SELECT s.type, s.output FROM steps s JOIN threads t ON t.id = s."threadId" '
           'WHERE t.id::text LIKE :tid AND s.type IN (\'user_message\',\'assistant_message\') '
           'ORDER BY s."createdAt"')
    try:
        with _eng().connect() as conn:
            rows = conn.execute(text(sql), {"tid": f"{thread_id}%"}).fetchall()
    except Exception as e:  # noqa: BLE001
        return f"recall_thread error: {str(getattr(e, 'orig', e))[:300]}"
    if not rows:
        return "no messages found for that thread id (use recall_chats to list sessions)"
    out = []
    for typ, output in rows:
        who = "STAN" if typ == "user_message" else "NIKKI"
        out.append(f"{who}: {(output or '').strip()}")
    txt = "\n\n".join(out)
    if len(txt) > max_chars:
        txt = txt[-max_chars:]
        txt = "[...earlier part truncated...]\n" + txt
    return txt


@tool
def db_execute(sql: str) -> str:
    """Run a write / DDL SQL statement (INSERT, UPDATE, DELETE, CREATE ...). Requires approval."""
    with _eng().begin() as conn:
        res = conn.execute(text(sql))
        return f"ok, rowcount={res.rowcount}"


db_schema.metadata = {"requires_approval": False}
db_query.metadata = {"requires_approval": False}
db_execute.metadata = {"requires_approval": True}
recall_chats.metadata = {"requires_approval": False}
recall_thread.metadata = {"requires_approval": False}

TOOLS = [db_schema, db_query, db_execute, recall_chats, recall_thread]
