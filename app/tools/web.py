"""Web tools: `web_search` (Tavily when TAVILY_API_KEY is set, DuckDuckGo otherwise) and
`http_fetch` (GET a URL and return readable text). Both are read-only, so neither needs
approval. `http_fetch` refuses private/link-local addresses so the model can never be
talked into reading the Cloud Run metadata server or anything inside the VPC."""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

import json

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

MAX_CHARS = 6000
UA = "Mozilla/5.0 (compatible; Nikki/1.0; +https://nikkiaia.com)"


def _blocked_host(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"cannot resolve host {host!r}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return f"refusing to fetch non-public address {ip}"
    return None


def _clean(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "iframe"]):
        t.decompose()
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return (f"# {title}\n\n" if title else "") + text


@tool
def http_fetch(url: str, max_chars: int = MAX_CHARS) -> str:
    """Fetch a public web page (or JSON/text URL) and return its readable text content.
    Use after web_search to read a result in full. Only http(s) URLs to public hosts."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return "error: only http(s) URLs are allowed"
    if (why := _blocked_host(p.hostname)):
        return f"error: {why}"
    try:
        with httpx.Client(follow_redirects=True, timeout=25, headers={"User-Agent": UA}) as c:
            r = c.get(url)
    except httpx.HTTPError as e:
        return f"error: {type(e).__name__}: {e}"
    final = urlparse(str(r.url))
    if final.hostname and (why := _blocked_host(final.hostname)):
        return f"error: redirect target blocked: {why}"
    ctype = r.headers.get("content-type", "")
    body = r.text
    if "html" in ctype:
        body = _clean(body)
    max_chars = max(500, min(int(max_chars), 60000))
    head = f"[{r.status_code} {ctype.split(';')[0]} · {len(r.content):,} bytes · {r.url}]\n"
    if len(body) > max_chars:
        body = body[:max_chars] + f"\n\n… truncated ({len(body):,} chars total; raise max_chars to read more)"
    return head + body


def _tavily_key() -> str | None:
    """Tenant's Tavily connection first, then the platform-wide env key."""
    try:
        from app.integrations import store

        conns = store.connections("tavily")
        if conns:
            return store.secret_for("tavily", conns[0]["label"])[0]
    except Exception:  # noqa: BLE001 — no tenant context / table yet
        pass
    return (os.environ.get("TAVILY_API_KEY") or "").strip() or None


def _tavily(query: str, n: int) -> str | None:
    key = _tavily_key()
    if not key:
        return None
    r = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": n, "include_answer": True},
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    out = []
    if data.get("answer"):
        out.append(f"Summary: {data['answer']}\n")
    for i, item in enumerate(data.get("results", []), 1):
        out.append(f"{i}. {item.get('title')}\n   {item.get('url')}\n   {(item.get('content') or '')[:400]}")
    return "\n".join(out) or "(no results)"


def _ddg(query: str, n: int) -> str:
    from ddgs import DDGS

    rows = list(DDGS().text(query, max_results=n))
    if not rows:
        return "(no results)"
    return "\n".join(
        f"{i}. {r.get('title')}\n   {r.get('href')}\n   {(r.get('body') or '')[:400]}" for i, r in enumerate(rows, 1)
    )


@tool
def web_search(query: str, max_results: int = 6) -> str:
    """Search the web. Returns titles, URLs and snippets; call http_fetch on a URL to read it.
    Uses Tavily when a TAVILY_API_KEY is configured, otherwise DuckDuckGo."""
    n = max(1, min(int(max_results), 10))
    try:
        res = _tavily(query, n)
        if res is not None:
            return f"[tavily] {query}\n{res}"
    except Exception as e:  # noqa: BLE001 — fall back to DDG
        note = f"(tavily failed: {type(e).__name__}; using DuckDuckGo)\n"
    else:
        note = ""
    try:
        return f"{note}[duckduckgo] {query}\n{_ddg(query, n)}"
    except Exception as e:  # noqa: BLE001
        return f"error: search failed: {type(e).__name__}: {e}"


@tool
def http_request(
    method: str,
    url: str,
    headers: dict | None = None,
    json_body: dict | list | None = None,
    body: str | None = None,
    cookies: list | dict | None = None,
    max_chars: int = 8000,
) -> str:
    """Call a web API directly: any HTTP method, custom headers, JSON or raw body, and cookies (the list from
    browser_cookies or a {name: value} dict). Returns status, response headers of interest and the body text.
    Use it to replay calls discovered with browser_network — a site's own JSON API is faster and far more reliable
    than clicking through its UI. GET/HEAD/OPTIONS run without approval; anything that changes data
    (POST/PUT/PATCH/DELETE) is approval-gated."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return "error: only http(s) URLs are allowed"
    if (why := _blocked_host(p.hostname)):
        return f"error: {why}"
    method = method.upper()
    ck: dict[str, str] = {}
    if isinstance(cookies, dict):
        ck = {str(k): str(v) for k, v in cookies.items()}
    elif isinstance(cookies, list):
        ck = {str(c.get("name")): str(c.get("value")) for c in cookies if isinstance(c, dict) and c.get("name")}
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
            **{str(k): str(v) for k, v in (headers or {}).items()}}
    try:
        with httpx.Client(follow_redirects=True, timeout=40, headers=hdrs, cookies=ck) as c:
            r = c.request(method, url, json=json_body if json_body is not None else None, content=body if body is not None and json_body is None else None)
    except httpx.HTTPError as e:
        return f"error: {type(e).__name__}: {e}"
    ct = r.headers.get("content-type", "")
    text = r.text
    if "json" in ct:
        try:
            text = json.dumps(r.json(), indent=1)
        except ValueError:
            pass
    elif "html" in ct:
        text = _clean(text)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[... {len(text) - max_chars:,} more chars]"
    set_cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
    return f"HTTP {r.status_code} {method} {r.url}\ncontent-type: {ct}" + (f"\nnew cookies: {set_cookie[:600]}" if set_cookie else "") + f"\n\n{text}"


def _http_request_gate(args: dict) -> bool:
    return str(args.get("method", "GET")).upper() not in ("GET", "HEAD", "OPTIONS")


for _t in (web_search, http_fetch):
    _t.metadata = {"requires_approval": False}
http_request.metadata = {"requires_approval": False, "requires_approval_if": _http_request_gate}

TOOLS = [web_search, http_fetch, http_request]
