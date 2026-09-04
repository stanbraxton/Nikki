"""Tenant context. Every request / chat turn / headless run sets a `Principal`; tools and
persistence helpers read it via `principal()` and scope their queries to `tenant_id`.

Roles: owner (created the tenant) | member | admin (platform operator — the only role that
sees the owner-only tools: files, SQL, self-maintenance, Spaces)."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

ADMIN_TENANT = "admin"  # the platform operator's tenant id


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    email: str
    role: str = "member"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


_current: ContextVar[Principal | None] = ContextVar("nikki_principal", default=None)


def set_principal(p: Principal | None):
    return _current.set(p)


def reset_principal(token) -> None:
    _current.reset(token)


def principal() -> Principal:
    p = _current.get()
    if p is None:
        raise RuntimeError("no tenant context — this code path must run inside a request or chat turn")
    return p


def maybe_principal() -> Principal | None:
    return _current.get()


def tenant_id() -> str:
    return principal().tenant_id


def from_user_metadata(identifier: str, metadata: dict | None) -> Principal:
    md = metadata or {}
    return Principal(tenant_id=str(md.get("tenant_id") or identifier), email=str(md.get("email") or identifier),
                     role=str(md.get("role") or "member"))
