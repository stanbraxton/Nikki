"""Long-term memory: durable facts about the user, their preferences and projects that
survive across chat sessions. `remember` / `recall` run without approval (low risk);
`forget` is gated. The agent's system prompt is seeded with the most recent memories
(see `memory_digest`) so Nikki knows the user without being asked."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from langchain_core.tools import tool
from sqlalchemy import create_engine, delete, insert, or_, select, update

from app import persistence
from app.config import settings
from app.tenancy import tenant_id

KINDS = ("fact", "preference", "project", "person", "other")
DIGEST_LIMIT = 60
DIGEST_CHARS = 6000

_eng = None


def _e():
    global _eng
    if _eng is None:
        _eng = create_engine(settings.sqlalchemy_sync_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _eng


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(row) -> str:
    d = dict(row._mapping)
    return f"[{d['id'][:8]}] ({d['kind']}, {d['created_at']:%Y-%m-%d}) {d['content']}"


@tool
def remember(content: str, kind: str = "fact") -> str:
    """Store a durable memory about the user (a fact, preference, project detail or person)
    so it is available in every future conversation. Keep it one clear sentence.
    kind: fact | preference | project | person | other."""
    content = " ".join(content.split()).strip()
    if len(content) < 4:
        return "rejected: memory too short"
    kind = kind if kind in KINDS else "other"
    m = persistence.memories
    with _e().begin() as c:
        dup = c.execute(select(m.c.id).where(m.c.tenant_id == tenant_id(), m.c.content == content)).first()
        if dup:
            c.execute(update(m).where(m.c.id == dup[0]).values(updated_at=_now(), kind=kind))
            return f"already known (refreshed) [{dup[0][:8]}]"
        mid = str(uuid.uuid4())
        c.execute(insert(m).values(id=mid, tenant_id=tenant_id(), kind=kind, content=content[:1000], created_at=_now(), updated_at=_now()))
    return f"remembered [{mid[:8]}] ({kind}): {content}"


@tool
def recall(query: str = "", kind: str = "", limit: int = 20) -> str:
    """Search long-term memory. Empty query lists the most recent memories. Optional kind filter."""
    m = persistence.memories
    stmt = select(m).where(m.c.tenant_id == tenant_id()).order_by(m.c.updated_at.desc()).limit(max(1, min(int(limit), 100)))
    words = [w for w in query.split() if len(w) > 2][:8]
    if words:
        stmt = stmt.where(or_(*[m.c.content.ilike(f"%{w}%") for w in words]))
    if kind in KINDS:
        stmt = stmt.where(m.c.kind == kind)
    with _e().connect() as c:
        rows = c.execute(stmt).fetchall()
    if not rows:
        return "(no matching memories)"
    return "\n".join(_fmt(r) for r in rows)


@tool
def forget(memory_id: str) -> str:
    """Delete a memory by its id (the 8-char prefix shown by recall is enough). Requires approval."""
    m = persistence.memories
    with _e().begin() as c:
        rows = c.execute(select(m.c.id, m.c.content).where(m.c.tenant_id == tenant_id(), m.c.id.like(f"{memory_id}%"))).fetchall()
        if not rows:
            return f"no memory with id starting {memory_id!r}"
        if len(rows) > 1:
            return "ambiguous id; matches: " + ", ".join(r[0][:8] for r in rows)
        c.execute(delete(m).where(m.c.id == rows[0][0]))
    return f"forgot [{rows[0][0][:8]}]: {rows[0][1]}"


def memory_digest() -> str:
    """Compact block of recent memories for the system prompt (never raises)."""
    try:
        m = persistence.memories
        with _e().connect() as c:
            rows = c.execute(select(m).where(m.c.tenant_id == tenant_id()).order_by(m.c.updated_at.desc()).limit(DIGEST_LIMIT)).fetchall()
    except Exception:  # noqa: BLE001
        return ""
    out, size = [], 0
    for r in rows:
        line = f"- ({r._mapping['kind']}) {r._mapping['content']}"
        if size + len(line) > DIGEST_CHARS:
            out.append("- … (more via recall)")
            break
        out.append(line)
        size += len(line)
    return "\n".join(out)


for _t in (remember, recall):
    _t.metadata = {"requires_approval": False}
forget.metadata = {"requires_approval": True}

TOOLS = [remember, recall, forget]
