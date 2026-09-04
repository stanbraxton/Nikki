"""Google account linking (Gmail + Drive) via OAuth 2.0 with offline refresh tokens.

Flow: the logged-in admin opens /connect/google → Google consent → /oauth/google/callback
stores the refresh token in the `google_tokens` table keyed by the account's email. Tools in
app/tools/google_ws.py mint access tokens from those refresh tokens on demand. Several
Google accounts can be linked; tools take an optional `account` (email) argument.

Requires GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET (Web application client with
redirect URI {PUBLIC_URL}/oauth/google/callback)."""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from chainlit.auth import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select

from app import persistence

router = APIRouter()

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def client_id() -> str | None:
    return (os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip() or None


def client_secret() -> str | None:
    return (os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip() or None


def public_url() -> str:
    return (os.environ.get("PUBLIC_URL") or "https://nikkiaia.com").rstrip("/")


def redirect_uri() -> str:
    return f"{public_url()}/oauth/google/callback"


def configured() -> bool:
    return bool(client_id() and client_secret())


def _secret() -> bytes:
    return (os.environ.get("CHAINLIT_AUTH_SECRET") or "nikki-dev").encode()


def _sign_state() -> str:
    ts = str(int(time.time()))
    mac = hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{ts}.{mac}"


def _check_state(state: str) -> bool:
    try:
        ts, mac = state.split(".", 1)
    except ValueError:
        return False
    good = hmac.compare_digest(hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()[:32], mac)
    return good and time.time() - int(ts) < 900


async def _require_user(user):
    if user is None:
        return RedirectResponse("/login")
    return None


@router.get("/connect/google")
async def connect_google(user=Depends(get_current_user)):
    if user is None:
        return RedirectResponse("/login")
    if not configured():
        return HTMLResponse(_page("Google not configured",
                                  "Set the GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET secrets first "
                                  "(see docs/GOOGLE.md), then redeploy."), status_code=503)
    q = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": _sign_state(),
    }
    return RedirectResponse(f"{AUTH_URL}?{urlencode(q)}")


@router.get("/oauth/google/callback")
async def google_callback(request: Request, user=Depends(get_current_user)):
    if user is None:
        return RedirectResponse("/login")
    code, state, err = request.query_params.get("code"), request.query_params.get("state", ""), request.query_params.get("error")
    if err:
        return HTMLResponse(_page("Google link cancelled", f"Google returned: {err}"), status_code=400)
    if not code or not _check_state(state):
        raise HTTPException(status_code=400, detail="invalid state")
    async with httpx.AsyncClient(timeout=20) as c:
        tok = await c.post(TOKEN_URL, data={
            "code": code, "client_id": client_id(), "client_secret": client_secret(),
            "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
        })
        if tok.status_code != 200:
            return HTMLResponse(_page("Token exchange failed", tok.text[:500]), status_code=400)
        data = tok.json()
        me = await c.get("https://openidconnect.googleapis.com/v1/userinfo",
                         headers={"Authorization": f"Bearer {data['access_token']}"})
        email = (me.json() or {}).get("email", "").lower()
    refresh = data.get("refresh_token")
    if not email or not refresh:
        return HTMLResponse(_page("Missing refresh token",
                                  "Google did not return a refresh token. Remove Nikki under "
                                  "myaccount.google.com/permissions and try again."), status_code=400)
    now = datetime.now(timezone.utc)
    t = persistence.google_tokens
    async with persistence.engine().begin() as conn:
        await conn.execute(delete(t).where(t.c.email == email))
        await conn.execute(t.insert().values(email=email, refresh_token=refresh, scopes=data.get("scope", ""),
                                             created_at=now, updated_at=now))
    return HTMLResponse(_page("Google account linked", f"<b>{email}</b> is now available to Nikki's Gmail and Drive tools.<br>"
                              "Tell Nikki: <i>“check my inbox”</i> or <i>“find the lease PDF in Drive”</i>."))


@router.get("/api/google/accounts")
async def list_accounts(user=Depends(get_current_user)) -> list[dict]:
    if user is None:
        raise HTTPException(status_code=401)
    return await linked_accounts()


@router.post("/api/google/disconnect/{email}")
async def disconnect(email: str, user=Depends(get_current_user)) -> dict:
    if user is None:
        raise HTTPException(status_code=401)
    t = persistence.google_tokens
    async with persistence.engine().begin() as conn:
        await conn.execute(delete(t).where(t.c.email == email.lower()))
    return {"ok": True}


async def linked_accounts() -> list[dict]:
    t = persistence.google_tokens
    async with persistence.engine().connect() as conn:
        rows = await conn.execute(select(t.c.email, t.c.scopes, t.c.created_at))
        return [{"email": r.email, "scopes": r.scopes.split(), "linked_at": r.created_at.isoformat()} for r in rows]


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title} · Nikki</title>
<style>body{{margin:0;background:#0f1014;color:#e8e9f0;font:15px/1.6 -apple-system,Segoe UI,Inter,sans-serif;display:grid;place-items:center;height:100vh}}
.card{{background:#171922;border:1px solid #262a38;border-radius:14px;padding:28px 32px;max-width:520px}}h1{{font-size:18px;margin:0 0 10px}}a{{color:#c9bdff}}</style></head>
<body><div class="card"><h1>{title}</h1><p>{body}</p><p><a href="/">← Back to Nikki</a></p></div></body></html>"""
