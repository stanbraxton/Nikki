"""Tenant-scoped integration storage (sync + async helpers) and provider developer credentials."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, delete, insert, select, update

from app import crypto, persistence
from app.config import settings
from app.integrations import providers
from app.tenancy import ADMIN_TENANT, tenant_id

_eng = None


def _e():
    global _eng
    if _eng is None:
        _eng = create_engine(settings.sqlalchemy_sync_url, pool_pre_ping=True, pool_size=3, max_overflow=3)
    return _eng


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_dict(r) -> dict[str, Any]:
    d = dict(r._mapping)
    d["meta"] = json.loads(d.get("meta") or "{}")
    d.pop("secret_enc", None)
    return d


# ---------------------------------------------------------------- connections (sync, for tools)
def connections(provider: str, tid: str | None = None) -> list[dict[str, Any]]:
    i = persistence.integrations
    with _e().connect() as c:
        rows = c.execute(select(i).where(i.c.tenant_id == (tid or tenant_id()), i.c.provider == provider)
                         .order_by(i.c.created_at)).fetchall()
    return [_row_dict(r) for r in rows]


def secret_for(provider: str, label: str, tid: str | None = None) -> tuple[str, dict[str, Any]]:
    """Decrypted secret + row for one connection."""
    i = persistence.integrations
    with _e().connect() as c:
        r = c.execute(select(i).where(i.c.tenant_id == (tid or tenant_id()), i.c.provider == provider, i.c.label == label)).first()
    if not r:
        raise RuntimeError(f"{provider} connection {label!r} not found for this account")
    return crypto.decrypt(r._mapping["secret_enc"]), _row_dict(r)


def resolve_label(provider: str, account: str = "") -> str:
    """Pick the connection to use: the only one, or the one matching `account` (prefix ok)."""
    conns = connections(provider)
    name = providers.get(provider).name
    if not conns:
        raise RuntimeError(f"no {name} account connected — ask the user to open /integrations and connect {name}")
    labels = [c["label"] for c in conns]
    if not account:
        if len(labels) == 1:
            return labels[0]
        raise RuntimeError(f"several {name} connections; pass account=<label>: " + ", ".join(labels))
    account = account.lower()
    hits = [l for l in labels if l.lower() == account or l.lower().startswith(account)]
    if len(hits) != 1:
        raise RuntimeError(f"{name} connection {account!r} not found; connected: " + ", ".join(labels))
    return hits[0]


def upsert_sync(provider: str, label: str, kind: str, secret: str, scopes: str = "", meta: dict | None = None, tid: str | None = None) -> str:
    i = persistence.integrations
    tid = tid or tenant_id()
    with _e().begin() as c:
        row = c.execute(select(i.c.id).where(i.c.tenant_id == tid, i.c.provider == provider, i.c.label == label)).first()
        vals = dict(secret_enc=crypto.encrypt(secret), scopes=scopes, meta=json.dumps(meta or {}), updated_at=_now())
        if row:
            c.execute(update(i).where(i.c.id == row[0]).values(**vals))
            return row[0]
        iid = str(uuid.uuid4())
        c.execute(insert(i).values(id=iid, tenant_id=tid, provider=provider, label=label, kind=kind, created_at=_now(), **vals))
        return iid


# ---------------------------------------------------------------- async (routes)
async def list_for_tenant(tid: str) -> list[dict[str, Any]]:
    i = persistence.integrations
    async with persistence.engine().connect() as conn:
        rows = await conn.execute(select(i).where(i.c.tenant_id == tid).order_by(i.c.provider, i.c.created_at))
        out = []
        for r in rows:
            d = _row_dict(r)
            for k in ("created_at", "updated_at"):
                d[k] = d[k].isoformat() if d.get(k) else None
            out.append(d)
        return out


async def upsert(provider: str, label: str, kind: str, secret: str, scopes: str = "", meta: dict | None = None, tid: str | None = None) -> str:
    i = persistence.integrations
    tid = tid or tenant_id()
    async with persistence.engine().begin() as conn:
        row = (await conn.execute(select(i.c.id).where(i.c.tenant_id == tid, i.c.provider == provider, i.c.label == label))).first()
        vals = dict(secret_enc=crypto.encrypt(secret), scopes=scopes, meta=json.dumps(meta or {}), updated_at=_now())
        if row:
            await conn.execute(update(i).where(i.c.id == row[0]).values(**vals))
            return row[0]
        iid = str(uuid.uuid4())
        await conn.execute(insert(i).values(id=iid, tenant_id=tid, provider=provider, label=label, kind=kind, created_at=_now(), **vals))
        return iid


async def remove(tid: str, integration_id: str) -> bool:
    i = persistence.integrations
    async with persistence.engine().begin() as conn:
        res = await conn.execute(delete(i).where(i.c.tenant_id == tid, i.c.id == integration_id))
        return res.rowcount > 0


# ---------------------------------------------------------------- provider developer credentials
def client_credentials(provider: str) -> tuple[str, str] | None:
    """(client_id, client_secret) from the admin-set table, else env vars, else None."""
    pc = persistence.provider_credentials
    with _e().connect() as c:
        r = c.execute(select(pc).where(pc.c.provider == provider)).first()
    if r:
        return r._mapping["client_id"], crypto.decrypt(r._mapping["client_secret_enc"])
    p = providers.get(provider)
    cid = (os.environ.get(p.env_client_id or "_") or "").strip()
    sec = (os.environ.get(p.env_client_secret or "_") or "").strip()
    return (cid, sec) if cid and sec else None


async def set_client_credentials(provider: str, client_id: str, client_secret: str) -> None:
    pc = persistence.provider_credentials
    async with persistence.engine().begin() as conn:
        await conn.execute(delete(pc).where(pc.c.provider == provider))
        await conn.execute(insert(pc).values(provider=provider, client_id=client_id.strip(),
                                             client_secret_enc=crypto.encrypt(client_secret.strip()), meta="{}", updated_at=_now()))


async def clear_client_credentials(provider: str) -> None:
    pc = persistence.provider_credentials
    async with persistence.engine().begin() as conn:
        await conn.execute(delete(pc).where(pc.c.provider == provider))


async def configured_providers() -> dict[str, str]:
    """provider -> 'db' | 'env' | '' for the admin page."""
    pc = persistence.provider_credentials
    async with persistence.engine().connect() as conn:
        in_db = {r[0] for r in await conn.execute(select(pc.c.provider))}
    out = {}
    for pid, p in providers.PROVIDERS.items():
        if p.kind != "oauth2":
            continue
        if pid in in_db:
            out[pid] = "db"
        elif (os.environ.get(p.env_client_id or "_") or "").strip() and (os.environ.get(p.env_client_secret or "_") or "").strip():
            out[pid] = "env"
        else:
            out[pid] = ""
    return out


# ---------------------------------------------------------------- legacy
async def migrate_legacy_google_tokens(conn) -> None:
    """Pre-tenancy `google_tokens` rows -> integrations(admin tenant, provider google)."""
    g, i = persistence.google_tokens, persistence.integrations
    rows = (await conn.execute(select(g))).fetchall()
    for r in rows:
        d = dict(r._mapping)
        exists = (await conn.execute(select(i.c.id).where(i.c.tenant_id == ADMIN_TENANT, i.c.provider == "google", i.c.label == d["email"]))).first()
        if not exists:
            await conn.execute(insert(i).values(id=str(uuid.uuid4()), tenant_id=ADMIN_TENANT, provider="google", label=d["email"], kind="oauth2",
                                                secret_enc=crypto.encrypt(d["refresh_token"]), scopes=d["scopes"], meta="{}",
                                                created_at=d["created_at"], updated_at=_now()))
        await conn.execute(delete(g).where(g.c.email == d["email"]))
