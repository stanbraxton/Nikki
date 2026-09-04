"""Microsoft 365 tools (Outlook mail, Calendar, OneDrive) via Microsoft Graph, backed by the
Microsoft connections on /integrations (per tenant). Read tools are ungated; sending is gated."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from langchain_core.tools import tool

from app.integrations import store
from app.tenancy import tenant_id

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_tokens: dict[tuple[str, str], tuple[str, float]] = {}


def _resolve(account: str) -> str:
    return store.resolve_label("microsoft", account)


def _access_token(label: str) -> str:
    key = (tenant_id(), label)
    tok = _tokens.get(key)
    if tok and tok[1] > time.time() + 60:
        return tok[0]
    refresh, row = store.secret_for("microsoft", label)
    cc = store.client_credentials("microsoft")
    if not cc:
        raise RuntimeError("Microsoft provider is not configured by the administrator")
    r = httpx.post(TOKEN_URL, data={"client_id": cc[0], "client_secret": cc[1], "grant_type": "refresh_token",
                                    "refresh_token": refresh, "scope": row.get("scopes") or "offline_access User.Read Mail.Read Mail.Send Calendars.ReadWrite Files.ReadWrite"},
                   timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Microsoft token refresh failed: {r.text[:300]}")
    d = r.json()
    if d.get("refresh_token") and d["refresh_token"] != refresh:  # Graph rotates refresh tokens
        store.upsert_sync("microsoft", label, "oauth2", d["refresh_token"], scopes=row.get("scopes") or "", meta=row.get("meta") or {})
    _tokens[key] = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
    return d["access_token"]


def _get(label: str, path: str, params: dict | None = None) -> Any:
    r = httpx.get(f"{GRAPH}{path}", params=params, headers={"Authorization": f"Bearer {_access_token(label)}"}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Graph {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def _post(label: str, path: str, body: dict) -> Any:
    r = httpx.post(f"{GRAPH}{path}", json=body, headers={"Authorization": f"Bearer {_access_token(label)}"}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Graph {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


@tool
def microsoft_accounts() -> str:
    """List the Microsoft 365 accounts connected for this user."""
    conns = store.connections("microsoft")
    if not conns:
        return "No Microsoft 365 account connected. Ask the user to open /integrations and connect Microsoft 365."
    return "Connected Microsoft accounts:\n" + "\n".join(f"- {c['label']}" for c in conns)


@tool
def outlook_search(query: str = "", max_results: int = 10, account: str = "", folder: str = "inbox") -> str:
    """Search Outlook mail (Microsoft Graph $search syntax, e.g. 'from:bob subject:invoice'); empty query lists the newest messages."""
    try:
        label = _resolve(account)
        params: dict[str, Any] = {"$top": max(1, min(int(max_results), 25)), "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview"}
        if query.strip():
            params["$search"] = f'"{query.strip()}"'
        else:
            params["$orderby"] = "receivedDateTime desc"
        d = _get(label, f"/me/mailFolders/{folder}/messages", params)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    msgs = d.get("value", [])
    if not msgs:
        return "no messages found"
    out = [f"Messages ({label}):"]
    for m in msgs:
        frm = (m.get("from") or {}).get("emailAddress", {})
        out.append(f"- [{m['id']}] {m.get('receivedDateTime', '')[:16]} {'' if m.get('isRead') else '(unread) '}from {frm.get('name', '')} <{frm.get('address', '')}>: {m.get('subject', '')}\n    {m.get('bodyPreview', '')[:160]}")
    return "\n".join(out)


@tool
def outlook_read(message_id: str, account: str = "", max_chars: int = 8000) -> str:
    """Read one Outlook message in full (plain text)."""
    try:
        label = _resolve(account)
        m = _get(label, f"/me/messages/{message_id}", {"$select": "subject,from,toRecipients,receivedDateTime,body"})
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    body = (m.get("body") or {})
    text = body.get("content", "")
    if body.get("contentType", "").lower() == "html":
        from bs4 import BeautifulSoup

        text = BeautifulSoup(text, "lxml").get_text("\n")
    frm = (m.get("from") or {}).get("emailAddress", {})
    to = ", ".join(r.get("emailAddress", {}).get("address", "") for r in m.get("toRecipients", []))
    return f"Subject: {m.get('subject')}\nFrom: {frm.get('name')} <{frm.get('address')}>\nTo: {to}\nDate: {m.get('receivedDateTime')}\n\n{text.strip()[:max_chars]}"


@tool
def outlook_send(to: str, subject: str, body: str, account: str = "", cc: str = "") -> str:
    """Send an email from the connected Outlook account (to/cc comma-separated). Requires approval."""
    try:
        label = _resolve(account)
        msg = {"subject": subject, "body": {"contentType": "Text", "content": body},
               "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()]}
        if cc.strip():
            msg["ccRecipients"] = [{"emailAddress": {"address": a.strip()}} for a in cc.split(",") if a.strip()]
        _post(label, "/me/sendMail", {"message": msg, "saveToSentItems": True})
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"sent '{subject}' to {to} from {label}"


@tool
def mscal_list_events(days: int = 7, account: str = "") -> str:
    """List upcoming Outlook Calendar events for the next `days` days."""
    try:
        label = _resolve(account)
        now = datetime.now(timezone.utc)
        d = _get(label, "/me/calendarView", {"startDateTime": now.isoformat(), "endDateTime": (now + timedelta(days=max(1, min(days, 90)))).isoformat(),
                                             "$orderby": "start/dateTime", "$top": 50, "$select": "id,subject,start,end,location"})
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    evs = d.get("value", [])
    if not evs:
        return f"no events in the next {days} days ({label})"
    return f"Events for {label}:\n" + "\n".join(
        f"- {e['start']['dateTime'][:16]} — {e.get('subject', '(no title)')}" + (f" @ {e['location']['displayName']}" if (e.get('location') or {}).get('displayName') else "")
        for e in evs)


@tool
def onedrive_search(query: str, max_results: int = 10, account: str = "") -> str:
    """Search OneDrive files by name/content."""
    try:
        label = _resolve(account)
        d = _get(label, f"/me/drive/root/search(q='{query.replace(chr(39), '')}')", {"$top": max(1, min(int(max_results), 25)), "$select": "id,name,size,lastModifiedDateTime,webUrl,file"})
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    items = d.get("value", [])
    if not items:
        return "no files found"
    return "\n".join(f"- [{i['id']}] {i['name']} ({i.get('size', 0)} B, {i.get('lastModifiedDateTime', '')[:10]}) {i.get('webUrl', '')}" for i in items)


@tool
def onedrive_read(item_id: str, account: str = "", max_chars: int = 12000) -> str:
    """Read a OneDrive file's text content (text-like files, PDFs and Office docs where possible)."""
    try:
        label = _resolve(account)
        meta = _get(label, f"/me/drive/items/{item_id}", {"$select": "name,file"})
        r = httpx.get(f"{GRAPH}/me/drive/items/{item_id}/content", headers={"Authorization": f"Bearer {_access_token(label)}"},
                      timeout=60, follow_redirects=True)
        if r.status_code >= 400:
            return f"error: download failed {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    mime = (meta.get("file") or {}).get("mimeType", "")
    data = r.content
    if mime == "application/pdf" or meta.get("name", "").lower().endswith(".pdf"):
        import io

        from pypdf import PdfReader

        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages[:40])
    else:
        text = data.decode("utf-8", "replace")
    return f"{meta.get('name')} ({mime}):\n\n{text[:max_chars]}"


for _t in (microsoft_accounts, outlook_search, outlook_read, mscal_list_events, onedrive_search, onedrive_read):
    _t.metadata = {"requires_approval": False}
outlook_send.metadata = {"requires_approval": True}

TOOLS = [microsoft_accounts, outlook_search, outlook_read, outlook_send, mscal_list_events, onedrive_search, onedrive_read]
