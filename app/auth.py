"""Accounts and tenants. Nikki has its own email + password login (bcrypt); every account
belongs to exactly one tenant. The platform admin account is bootstrapped from
ADMIN_USERNAME / ADMIN_PASSWORD_HASH (tenant "admin", role "admin") so the original
single-user login keeps working.

Routes: GET/POST /signup (self-serve tenant creation, off when SIGNUPS_ENABLED=false or
gated by SIGNUP_CODE), GET/POST /account/password (signed-in password change), GET /api/me."""
from __future__ import annotations

import html
import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

import bcrypt
from chainlit.auth import get_current_user
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import insert, select, update

from app import persistence
from app.config import settings
from app.tenancy import ADMIN_TENANT, Principal, from_user_metadata

log = logging.getLogger("nikki.auth")
router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def signups_enabled() -> bool:
    return (os.environ.get("SIGNUPS_ENABLED") or "true").strip().lower() not in ("0", "false", "no")


def signup_code() -> str | None:
    return (os.environ.get("SIGNUP_CODE") or "").strip() or None


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- bootstrap
async def ensure_admin() -> None:
    """Create the admin tenant + account from the legacy env credentials (idempotent) and
    move any pre-tenancy Google tokens into the integrations table under the admin tenant."""
    if not settings.admin_password_hash:
        log.warning("ADMIN_PASSWORD_HASH not set; no admin account bootstrapped")
        return
    t, a = persistence.tenants, persistence.accounts
    ident = settings.admin_username.strip().lower()
    async with persistence.engine().begin() as conn:
        if not (await conn.execute(select(t.c.id).where(t.c.id == ADMIN_TENANT))).first():
            await conn.execute(insert(t).values(id=ADMIN_TENANT, name="Nikki Admin", plan="admin", status="active", created_at=_now()))
        env_hash = settings.admin_password_hash
        row = (await conn.execute(select(a.c.email, a.c.env_hash_applied).where(a.c.email == ident))).first()
        if not row:
            await conn.execute(insert(a).values(email=ident, tenant_id=ADMIN_TENANT, password_hash=env_hash,
                                                role="admin", display_name="Admin", created_at=_now(),
                                                env_hash_applied=env_hash))
        elif row.env_hash_applied != env_hash:
            # The env var is new or was rotated: apply it (this is the recovery path).
            # Otherwise the database is authoritative, so a password changed at
            # /account/password is not reverted by the next deploy or cold start.
            await conn.execute(update(a).where(a.c.email == ident).values(
                password_hash=env_hash, env_hash_applied=env_hash, role="admin", tenant_id=ADMIN_TENANT))
            log.info("admin password hash applied from ADMIN_PASSWORD_HASH")
        else:
            await conn.execute(update(a).where(a.c.email == ident).values(role="admin", tenant_id=ADMIN_TENANT))
        # legacy google_tokens -> integrations (admin tenant)
        from app.integrations.store import migrate_legacy_google_tokens

        await migrate_legacy_google_tokens(conn)


# ---------------------------------------------------------------- login (Chainlit callback)
async def authenticate(identifier: str, password: str) -> Principal | None:
    ident = identifier.strip().lower()
    a, t = persistence.accounts, persistence.tenants
    async with persistence.engine().begin() as conn:
        row = (await conn.execute(select(a.c.email, a.c.tenant_id, a.c.role, a.c.password_hash, t.c.status)
                                  .join(t, t.c.id == a.c.tenant_id).where(a.c.email == ident))).first()
        if not row or not check_password(password, row.password_hash):
            return None
        if row.status == "pending":
            log.warning("login refused: tenant %s is pending approval", row.tenant_id)
            return None
        if row.status != "active":
            log.warning("login refused: tenant %s is %s", row.tenant_id, row.status)
            return None
        await conn.execute(update(a).where(a.c.email == ident).values(last_login_at=_now()))
    return Principal(tenant_id=row.tenant_id, email=row.email, role=row.role)


def principal_of(user) -> Principal | None:
    """Chainlit user (from cookie) -> Principal."""
    if user is None:
        return None
    return from_user_metadata(user.identifier, getattr(user, "metadata", None))


async def require_principal(user=Depends(get_current_user)) -> Principal:
    p = principal_of(user)
    if p is None:
        raise HTTPException(status_code=401)
    return p


async def require_admin(p: Principal = Depends(require_principal)) -> Principal:
    if not p.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return p


# ---------------------------------------------------------------- signup
async def create_tenant(email: str, password: str, name: str) -> Principal:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("enter a valid email address")
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    name = " ".join(name.split())[:120] or email.split("@")[0]
    base = SLUG_RE.sub("-", name.lower()).strip("-")[:40] or "tenant"
    t, a = persistence.tenants, persistence.accounts
    async with persistence.engine().begin() as conn:
        if (await conn.execute(select(a.c.email).where(a.c.email == email))).first():
            raise ValueError("an account with that email already exists — sign in instead")
        tid = base
        while (await conn.execute(select(t.c.id).where(t.c.id == tid))).first():
            tid = f"{base}-{uuid.uuid4().hex[:4]}"
        await conn.execute(insert(t).values(id=tid, name=name, plan="free", status="pending", created_at=_now()))
        await conn.execute(insert(a).values(email=email, tenant_id=tid, password_hash=hash_password(password),
                                            role="owner", display_name=name, created_at=_now()))
    log.info("new tenant %s (%s)", tid, email)
    return Principal(tenant_id=tid, email=email, role="owner")


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_page():
    if not signups_enabled():
        return HTMLResponse(_page("Sign-ups are closed", "Nikki is invite-only right now."), status_code=403)
    return HTMLResponse(_signup_form())


@router.post("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_submit(request: Request, email: str = Form(...), password: str = Form(...),
                        name: str = Form("")):
    if not signups_enabled():
        raise HTTPException(status_code=403)
    try:
        await create_tenant(email, password, name)
    except ValueError as e:
        return HTMLResponse(_signup_form(error=str(e), email=email, name=name), status_code=400)
    return HTMLResponse(_page("Account created", f"Your account is pending approval. You'll be notified at <b>{html.escape(email.lower())}</b> once approved."))


@router.get("/api/me")
async def api_me(p: Principal = Depends(require_principal)) -> dict:
    return {"email": p.email, "tenant_id": p.tenant_id, "role": p.role}


# ── /account/password ─────────────────────────────────────────────────────────
# Change the password of the account that is signed in. The signed-in session is
# the proof of identity, so the current password is not asked for — this is also
# how someone who has forgotten their password but is still signed in on a device
# sets a new one. Admin changes persist across restarts (see ensure_admin).
# Submissions are rate-limited per account, and every message rendered into the
# form is HTML-escaped.
PASSWORD_CHANGE_LIMIT = 5            # submissions per account
PASSWORD_CHANGE_WINDOW_S = 15 * 60   # per this many seconds
_password_attempts: dict[str, deque] = defaultdict(deque)


def _password_rate_limited(email: str, now: float | None = None) -> bool:
    """Record one submission for `email`; True if it exceeds the limit. Per instance —
    a speed bump, not a lockout."""
    now = time.monotonic() if now is None else now
    q = _password_attempts[email]
    while q and now - q[0] > PASSWORD_CHANGE_WINDOW_S:
        q.popleft()
    if len(q) >= PASSWORD_CHANGE_LIMIT:
        return True
    q.append(now)
    return False


@router.get("/account/password", response_class=HTMLResponse, include_in_schema=False)
async def password_page(p: Principal = Depends(require_principal)):
    return HTMLResponse(_password_form(email=p.email))


@router.post("/account/password", response_class=HTMLResponse, include_in_schema=False)
async def password_submit(request: Request, new: str = Form(...), confirm: str = Form(...),
                          p: Principal = Depends(require_principal)):
    if _password_rate_limited(p.email):
        log.warning("password change rate-limited for %s", p.email)
        return HTMLResponse(_password_form(email=p.email, error="Too many attempts. Wait 15 minutes and try again."),
                            status_code=429)
    if len(new) < 10:
        return HTMLResponse(_password_form(email=p.email, error="New password must be at least 10 characters"), status_code=400)
    if new != confirm:
        return HTMLResponse(_password_form(email=p.email, error="New passwords don't match"), status_code=400)

    a = persistence.accounts
    async with persistence.engine().begin() as conn:
        result = await conn.execute(update(a).where(a.c.email == p.email).values(password_hash=hash_password(new)))
        if result.rowcount != 1:
            return HTMLResponse(_password_form(email=p.email, error="Account not found"), status_code=400)

    log.info("password changed for %s", p.email)
    return HTMLResponse(_page("Password changed",
                              "Your password has been updated. Use it the next time you sign in."))


# ---------------------------------------------------------------- html
STYLE = """<style>body{margin:0;background:#0f1014;color:#e8e9f0;font:15px/1.6 -apple-system,Segoe UI,Inter,sans-serif;display:grid;place-items:center;min-height:100vh}
.card{background:#171922;border:1px solid #262a38;border-radius:14px;padding:28px 32px;width:min(420px,92vw)}h1{font-size:20px;margin:0 0 6px}p{margin:8px 0}
label{display:block;font-size:13px;color:#9aa0b4;margin-top:12px}input{width:100%;box-sizing:border-box;margin-top:4px;padding:10px 12px;border-radius:8px;border:1px solid #2c3142;background:#0f1014;color:#e8e9f0;font-size:15px}
button{margin-top:18px;width:100%;padding:11px;border:0;border-radius:8px;background:#7c5cff;color:#fff;font-size:15px;font-weight:600;cursor:pointer}a{color:#c9bdff}.err{color:#ff7b7b;font-size:13px;margin-top:10px}.muted{color:#9aa0b4;font-size:13px}</style>"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · Nikki</title><link rel="icon" type="image/png" href="/public/favicon.png"><link rel="apple-touch-icon" href="/public/apple-touch-icon.png">{STYLE}</head>
<body><div class="card"><h1><img src="/public/avatars/nikki.png" alt="" style="width:34px;height:34px;border-radius:50%;vertical-align:middle;margin-right:10px">{title}</h1><p>{body}</p><p><a href="/">← Back to Nikki</a></p></div></body></html>"""


def _signup_form(error: str = "", email: str = "", name: str = "") -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    email, name = html.escape(email), html.escape(name)
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Create account · Nikki</title><link rel="icon" type="image/png" href="/public/favicon.png"><link rel="apple-touch-icon" href="/public/apple-touch-icon.png">{STYLE}</head>
<body><div class="card"><h1><img src="/public/avatars/nikki.png" alt="" style="width:34px;height:34px;border-radius:50%;vertical-align:middle;margin-right:10px">Create your Nikki account</h1><p class="muted">Nikki AiA your personal Ai Assistant with memory, web research, integrations and scheduled tasks.</p>
<form method="post" action="/signup">
<label>Name or company<input name="name" value="{name}" maxlength="120"></label>
<label>Email<input name="email" type="email" value="{email}" required></label>
<label>Password <span class="muted">(10+ characters)</span><input name="password" type="password" minlength="10" required></label>
{err}
<button type="submit">Create account</button></form>
<p class="muted" style="margin-top:14px">Already have an account? <a href="/login">Sign in</a> · <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a></p></div></body></html>"""


def _password_form(email: str = "", error: str = "") -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    who = f'<p class="muted">Signed in as <b>{html.escape(email)}</b>. You won\'t need your current password.</p>' if email else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Change Password · Nikki</title><link rel="icon" type="image/png" href="/public/favicon.png"><link rel="apple-touch-icon" href="/public/apple-touch-icon.png">{STYLE}</head>
<body><div class="card"><h1><img src="/public/avatars/nikki.png" alt="" style="width:34px;height:34px;border-radius:50%;vertical-align:middle;margin-right:10px">Change Password</h1>{who}
<form method="post" action="/account/password">
<label>New password <span class="muted">(10+ characters)</span><input name="new" type="password" minlength="10" autocomplete="new-password" required></label>
<label>Confirm new password<input name="confirm" type="password" minlength="10" autocomplete="new-password" required></label>
{err}
<button type="submit">Change password</button></form>
<p class="muted" style="margin-top:14px"><a href="/">← Back to Nikki</a></p></div></body></html>"""
