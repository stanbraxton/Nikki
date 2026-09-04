"""Gmail and Google Drive tools backed by the accounts linked at /connect/google.
Read tools run without approval; anything that sends or creates content is gated."""
from __future__ import annotations

import base64
import io
from email.message import EmailMessage
from html import unescape
from typing import Any

from langchain_core.tools import tool
from sqlalchemy import create_engine, select

from app import google_oauth
from app import persistence
from app.config import settings

_eng = None
_creds: dict[str, Any] = {}

EXPORT = {  # Google-native types -> export MIME
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
TEXT_LIKE = ("text/", "application/json", "application/xml", "application/csv")


def _e():
    global _eng
    if _eng is None:
        _eng = create_engine(settings.sqlalchemy_sync_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _eng


def _accounts() -> list[str]:
    t = persistence.google_tokens
    with _e().connect() as c:
        return [r[0] for r in c.execute(select(t.c.email).order_by(t.c.created_at))]


def _resolve(account: str) -> str:
    accts = _accounts()
    if not accts:
        raise RuntimeError("no Google account linked — ask the user to open /connect/google")
    if not account:
        if len(accts) == 1:
            return accts[0]
        raise RuntimeError("several accounts are linked; pass account=<email>: " + ", ".join(accts))
    account = account.lower()
    hits = [a for a in accts if a == account or a.startswith(account)]
    if len(hits) != 1:
        raise RuntimeError(f"account {account!r} not linked; linked: " + ", ".join(accts))
    return hits[0]


def _credentials(email: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    cred = _creds.get(email)
    if cred is None:
        t = persistence.google_tokens
        with _e().connect() as c:
            row = c.execute(select(t.c.refresh_token, t.c.scopes).where(t.c.email == email)).first()
        if not row:
            raise RuntimeError(f"no token for {email}")
        cred = Credentials(None, refresh_token=row[0], token_uri=google_oauth.TOKEN_URL,
                           client_id=google_oauth.client_id(), client_secret=google_oauth.client_secret(),
                           scopes=row[1].split() or None)
        _creds[email] = cred
    if not cred.valid:
        cred.refresh(Request())
    return cred


def _svc(email: str, api: str, ver: str):
    from googleapiclient.discovery import build

    return build(api, ver, credentials=_credentials(email), cache_discovery=False)


def _hdr(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body_text(payload: dict) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    plain, html = [], []

    def walk(p: dict) -> None:
        mime, data = p.get("mimeType", ""), p.get("body", {}).get("data")
        if data:
            txt = base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
            (plain if mime == "text/plain" else html if mime == "text/html" else []).append(txt)
        for part in p.get("parts", []) or []:
            walk(part)

    walk(payload)
    if plain:
        return "\n".join(plain)
    if html:
        from bs4 import BeautifulSoup

        return unescape(BeautifulSoup("\n".join(html), "lxml").get_text("\n"))
    return "(no readable body)"


@tool
def google_accounts() -> str:
    """List the Google accounts linked to Nikki (Gmail + Drive access)."""
    accts = _accounts()
    if not accts:
        return "No Google account linked. Ask the user to open /connect/google while logged in."
    return "Linked Google accounts:\n" + "\n".join(f"- {a}" for a in accts)


@tool
def gmail_search(query: str, max_results: int = 10, account: str = "") -> str:
    """Search Gmail with normal Gmail search syntax (e.g. 'from:bob newer_than:7d is:unread').
    Returns id, date, from, subject, snippet for each message. account = linked email (optional if only one)."""
    try:
        email = _resolve(account)
        svc = _svc(email, "gmail", "v1")
        res = svc.users().messages().list(userId="me", q=query, maxResults=max(1, min(int(max_results), 25))).execute()
        ids = [m["id"] for m in res.get("messages", [])]
        if not ids:
            return f"[{email}] no messages match {query!r}"
        out = [f"[{email}] {len(ids)} message(s) for {query!r}:"]
        for mid in ids:
            m = svc.users().messages().get(userId="me", id=mid, format="metadata",
                                           metadataHeaders=["From", "Subject", "Date"]).execute()
            out.append(f"- id={mid} | {_hdr(m, 'Date')[:25]} | {_hdr(m, 'From')[:60]} | {_hdr(m, 'Subject')[:90]}\n"
                       f"    {m.get('snippet', '')[:160]}")
        return "\n".join(out)
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


@tool
def gmail_read(message_id: str, account: str = "", max_chars: int = 8000) -> str:
    """Read one Gmail message in full (headers + body text) by id from gmail_search."""
    try:
        email = _resolve(account)
        m = _svc(email, "gmail", "v1").users().messages().get(userId="me", id=message_id, format="full").execute()
        atts = [p.get("filename") for p in (m.get("payload", {}).get("parts") or []) if p.get("filename")]
        head = (f"From: {_hdr(m, 'From')}\nTo: {_hdr(m, 'To')}\nCc: {_hdr(m, 'Cc')}\nDate: {_hdr(m, 'Date')}\n"
                f"Subject: {_hdr(m, 'Subject')}\nThread: {m.get('threadId')}\nLabels: {', '.join(m.get('labelIds', []))}\n"
                f"Attachments: {', '.join(atts) or 'none'}\n\n")
        body = _body_text(m.get("payload", {}))
        if len(body) > max_chars:
            body = body[:max_chars] + f"\n… truncated ({len(body):,} chars)"
        return head + body
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


@tool
def gmail_send(to: str, subject: str, body: str, account: str = "", cc: str = "",
               reply_to_message_id: str = "") -> str:
    """Send a plain-text email from a linked Gmail account. Requires approval.
    reply_to_message_id: id of a message to reply to (keeps the thread)."""
    try:
        email = _resolve(account)
        svc = _svc(email, "gmail", "v1")
        msg = EmailMessage()
        msg["To"], msg["From"], msg["Subject"] = to, email, subject
        if cc:
            msg["Cc"] = cc
        thread_id = None
        if reply_to_message_id:
            orig = svc.users().messages().get(userId="me", id=reply_to_message_id, format="metadata",
                                              metadataHeaders=["Message-ID", "Subject"]).execute()
            mid = _hdr(orig, "Message-ID")
            if mid:
                msg["In-Reply-To"] = msg["References"] = mid
            thread_id = orig.get("threadId")
        msg.set_content(body)
        raw = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        if thread_id:
            raw["threadId"] = thread_id
        sent = svc.users().messages().send(userId="me", body=raw).execute()
        return f"sent from {email} to {to} (id={sent.get('id')})"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


@tool
def drive_search(query: str, max_results: int = 10, account: str = "") -> str:
    """Search Google Drive by name/content words. Returns id, name, type, modified time, link."""
    try:
        email = _resolve(account)
        svc = _svc(email, "drive", "v3")
        safe = query.replace("'", "\\'")
        q = f"(name contains '{safe}' or fullText contains '{safe}') and trashed = false"
        res = svc.files().list(q=q, pageSize=max(1, min(int(max_results), 25)), orderBy="modifiedTime desc",
                               fields="files(id,name,mimeType,modifiedTime,webViewLink,size)",
                               supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        if not files:
            return f"[{email}] nothing in Drive matches {query!r}"
        return f"[{email}] {len(files)} file(s):\n" + "\n".join(
            f"- id={f['id']} | {f['name']} | {f['mimeType'].split('/')[-1].replace('vnd.google-apps.', '')} | "
            f"{f.get('modifiedTime', '')[:10]} | {f.get('webViewLink', '')}" for f in files)
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


@tool
def drive_read(file_id: str, account: str = "", max_chars: int = 12000) -> str:
    """Read a Drive file as text: Google Docs/Sheets/Slides are exported (text/csv);
    plain text, CSV, JSON and PDF files are downloaded and extracted."""
    try:
        email = _resolve(account)
        svc = _svc(email, "drive", "v3")
        meta = svc.files().get(fileId=file_id, fields="id,name,mimeType,size,webViewLink", supportsAllDrives=True).execute()
        mime = meta["mimeType"]
        if mime in EXPORT:
            data = svc.files().export(fileId=file_id, mimeType=EXPORT[mime]).execute()
            text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        elif mime == "application/pdf" or mime.startswith(TEXT_LIKE):
            if int(meta.get("size") or 0) > 25_000_000:
                return "error: file larger than 25 MB"
            data = svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
            if mime == "application/pdf":
                from pypdf import PdfReader

                text = "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
            else:
                text = data.decode("utf-8", "replace")
        else:
            return f"{meta['name']} is {mime}; cannot render as text. Link: {meta.get('webViewLink')}"
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n… truncated ({len(text):,} chars)"
        return f"# {meta['name']} ({mime})\n{meta.get('webViewLink', '')}\n\n{text}"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


@tool
def drive_create_doc(title: str, content: str, account: str = "", folder_id: str = "") -> str:
    """Create a Google Doc from plain text (Markdown is kept as text). Requires approval."""
    try:
        from googleapiclient.http import MediaIoBaseUpload

        email = _resolve(account)
        svc = _svc(email, "drive", "v3")
        meta: dict[str, Any] = {"name": title, "mimeType": "application/vnd.google-apps.document"}
        if folder_id:
            meta["parents"] = [folder_id]
        media = MediaIoBaseUpload(io.BytesIO(content.encode()), mimetype="text/plain", resumable=False)
        f = svc.files().create(body=meta, media_body=media, fields="id,webViewLink", supportsAllDrives=True).execute()
        return f"created Google Doc '{title}': {f.get('webViewLink')} (id={f['id']})"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


for _t in (google_accounts, gmail_search, gmail_read, drive_search, drive_read):
    _t.metadata = {"requires_approval": False}
for _t in (gmail_send, drive_create_doc):
    _t.metadata = {"requires_approval": True}

TOOLS = [google_accounts, gmail_search, gmail_read, gmail_send, drive_search, drive_read, drive_create_doc]
