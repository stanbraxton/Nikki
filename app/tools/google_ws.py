"""Gmail, Google Drive and Google Calendar tools backed by the Google connections on /integrations (per tenant).
Read tools run without approval; anything that sends or creates content is gated."""
from __future__ import annotations

import base64
import io
from email.message import EmailMessage
from html import unescape
from typing import Any

from langchain_core.tools import tool


from app.integrations import store
from app.tenancy import tenant_id

TOKEN_URL = "https://oauth2.googleapis.com/token"

_creds: dict[tuple[str, str], Any] = {}

EXPORT = {  # Google-native types -> export MIME
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
TEXT_LIKE = ("text/", "application/json", "application/xml", "application/csv")


def _accounts() -> list[str]:
    return [c["label"] for c in store.connections("google")]


def _resolve(account: str) -> str:
    return store.resolve_label("google", account)


def _credentials(email: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    key = (tenant_id(), email)
    cred = _creds.get(key)
    if cred is None:
        refresh, row = store.secret_for("google", email)
        cc = store.client_credentials("google")
        if not cc:
            raise RuntimeError("Google provider is not configured by the administrator")
        cred = Credentials(None, refresh_token=refresh, token_uri=TOKEN_URL, client_id=cc[0], client_secret=cc[1],
                           scopes=(row.get("scopes") or "").split() or None)
        _creds[key] = cred
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
        return "No Google account linked. Ask the user to open /integrations and connect Google while logged in."
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


@tool
def drive_find_folder(path: str, account: str = "") -> str:
    """Resolve a Drive folder by path like 'Church/sermons' (case-insensitive, searched from My Drive root
    and shared drives) and return its id + link, listing its contents. Use the id with drive_upload/drive_create_doc."""
    try:
        email = _resolve(account)
        svc = _svc(email, "drive", "v3")
        parts = [x for x in path.replace("\\", "/").split("/") if x.strip()]
        if not parts:
            return "error: empty path"
        FOLDER = "application/vnd.google-apps.folder"

        def _find(name: str, parent: str | None) -> list[dict]:
            safe = name.replace("'", "\\'")
            q = f"name = '{safe}' and mimeType = '{FOLDER}' and trashed = false"
            if parent:
                q += f" and '{parent}' in parents"
            res = svc.files().list(q=q, fields="files(id,name,parents,webViewLink)", pageSize=25,
                                   supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            return res.get("files", [])

        candidates = _find(parts[0], None)
        for name in parts[1:]:
            nxt: list[dict] = []
            for c in candidates:
                nxt += _find(name, c["id"])
            candidates = nxt
        if not candidates:
            return f"[{email}] no folder matches {'/'.join(parts)!r}"
        f = candidates[0]
        kids = svc.files().list(q=f"'{f['id']}' in parents and trashed = false", pageSize=30, orderBy="modifiedTime desc",
                                fields="files(id,name,mimeType,modifiedTime)", supportsAllDrives=True,
                                includeItemsFromAllDrives=True).execute().get("files", [])
        lines = [f"[{email}] folder '{'/'.join(parts)}' id={f['id']} {f.get('webViewLink', '')}"]
        if len(candidates) > 1:
            lines.append(f"(note: {len(candidates)} folders matched; using the first — others: "
                         + ", ".join(c["id"] for c in candidates[1:]) + ")")
        lines += [f"- id={k['id']} | {k['name']} | {k['mimeType'].split('/')[-1].replace('vnd.google-apps.', '')} | "
                  f"{k.get('modifiedTime', '')[:10]}" for k in kids] or ["(empty)"]
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


@tool
def drive_upload(path: str, folder_id: str = "", name: str = "", account: str = "", convert_to_google: bool = False) -> str:
    """Upload a file from Nikki's workspace (e.g. a .docx/.pdf/.xlsx) to Google Drive, optionally into folder_id
    (get it via drive_find_folder). convert_to_google=True turns Office files into Google Docs/Sheets/Slides.
    Requires approval."""
    try:
        import mimetypes

        from googleapiclient.http import MediaFileUpload

        from app.tools.files import _resolve as _ws

        local = _ws(path)
        if not local.is_file():
            return f"error: {path} is not a file in the workspace"
        email = _resolve(account)
        svc = _svc(email, "drive", "v3")
        mime = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        meta: dict[str, Any] = {"name": name or local.name}
        if folder_id:
            meta["parents"] = [folder_id]
        if convert_to_google:
            conv = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.google-apps.document",
                    "application/msword": "application/vnd.google-apps.document",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/vnd.google-apps.spreadsheet",
                    "text/csv": "application/vnd.google-apps.spreadsheet",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "application/vnd.google-apps.presentation"}
            if mime in conv:
                meta["mimeType"] = conv[mime]
        media = MediaFileUpload(str(local), mimetype=mime, resumable=local.stat().st_size > 5_000_000)
        f = svc.files().create(body=meta, media_body=media, fields="id,name,webViewLink", supportsAllDrives=True).execute()
        return f"uploaded '{f['name']}' to Drive ({email}): {f.get('webViewLink')} (id={f['id']})"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"



@tool
def gcal_list_events(days: int = 7, account: str = "", calendar_id: str = "primary", query: str = "") -> str:
    """List upcoming Google Calendar events for the next `days` days (default 7). Optional free-text query."""
    from datetime import datetime, timedelta, timezone as tz

    try:
        email = _resolve(account)
        svc = _svc(email, "calendar", "v3")
        now = datetime.now(tz.utc)
        res = svc.events().list(calendarId=calendar_id, timeMin=now.isoformat(), timeMax=(now + timedelta(days=max(1, min(days, 90)))).isoformat(),
                                singleEvents=True, orderBy="startTime", maxResults=50, q=query or None).execute()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    items = res.get("items", [])
    if not items:
        return f"no events in the next {days} days ({email})"
    out = [f"Events for {email}:"]
    for ev in items:
        st = ev.get("start", {}); when = st.get("dateTime") or st.get("date")
        out.append(f"- {when} — {ev.get('summary', '(no title)')}" + (f" @ {ev['location']}" if ev.get("location") else "") + f" [id {ev['id']}]")
    return "\n".join(out)


@tool
def gcal_create_event(title: str, start: str, end: str, account: str = "", description: str = "", location: str = "",
                      attendees: str = "", calendar_id: str = "primary", timezone: str = "America/New_York") -> str:
    """Create a Google Calendar event. start/end are ISO 8601 (e.g. 2026-09-10T14:00:00); attendees = comma-separated emails. Requires approval."""
    try:
        email = _resolve(account)
        svc = _svc(email, "calendar", "v3")
        body = {"summary": title, "description": description or None, "location": location or None,
                "start": {"dateTime": start, "timeZone": timezone}, "end": {"dateTime": end, "timeZone": timezone}}
        if attendees.strip():
            body["attendees"] = [{"email": a.strip()} for a in attendees.split(",") if a.strip()]
        ev = svc.events().insert(calendarId=calendar_id, body=body, sendUpdates="all" if attendees.strip() else "none").execute()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"created event '{title}' {start} → {end} ({email}): {ev.get('htmlLink', '')}"


for _t in (google_accounts, gmail_search, gmail_read, drive_search, drive_read, drive_find_folder, gcal_list_events):
    _t.metadata = {"requires_approval": False}
for _t in (gmail_send, drive_create_doc, drive_upload, gcal_create_event):
    _t.metadata = {"requires_approval": True}

TOOLS = [google_accounts, gmail_search, gmail_read, gmail_send, drive_search, drive_read, drive_find_folder, drive_upload, drive_create_doc, gcal_list_events, gcal_create_event]
GMAIL_TOOLS = {"gmail_search", "gmail_read", "gmail_send"}
