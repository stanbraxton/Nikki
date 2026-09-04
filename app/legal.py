"""Public /privacy and /terms pages (required by Google's OAuth consent-screen publishing)."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_STYLE = """<style>body{font-family:Inter,system-ui,sans-serif;max-width:720px;margin:48px auto;padding:0 20px;
line-height:1.6;color:#e6e6e6;background:#111}h1{font-size:1.6rem}a{color:#8ab4f8}</style>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · Nikki</title><link rel="icon" type="image/png" href="/public/favicon.png"><link rel="apple-touch-icon" href="/public/apple-touch-icon.png">{_STYLE}</head>
<body><h1><img src="/public/avatars/nikki.png" alt="" style="width:34px;height:34px;border-radius:50%;vertical-align:middle;margin-right:10px">{title}</h1>{body}<p><a href="/">← Back to Nikki</a></p></body></html>""")


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy() -> HTMLResponse:
    return _page("Privacy Policy", """
<p><strong>Nikki</strong> is a private, single-owner AI assistant. It is not offered to the public.</p>
<p><strong>Data we access.</strong> When the owner connects a Google account, Nikki requests access to
Gmail and Google Drive solely to perform actions the owner explicitly asks for (reading, searching, drafting
and sending email; listing, reading and creating Drive files).</p>
<p><strong>Storage.</strong> OAuth refresh tokens, chat history and execution traces are stored in a private
database in the owner's own Google Cloud project. Google user data is not sold, shared with third parties,
or used for advertising or model training.</p>
<p><strong>Retention and deletion.</strong> A connected Google account can be disconnected at any time from
within Nikki, which deletes its stored tokens. Access can also be revoked at
<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>.</p>
<p><strong>Limited Use.</strong> Nikki's use of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>,
including the Limited Use requirements.</p>
<p>Contact: the application owner listed on the consent screen.</p>""")


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms() -> HTMLResponse:
    return _page("Terms of Service", """
<p>Nikki is a private application operated for its owner's personal use. Access is restricted to
authenticated users authorised by the owner. No warranty is provided; the software is used at the
owner's own risk. Use of connected Google services is also subject to Google's terms of service.</p>""")
