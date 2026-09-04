"""`call_api`: call any REST API the user registered as a Custom REST integration
(/integrations → Custom REST API). The key is injected server-side; the model never sees it."""
from __future__ import annotations

import json

import httpx
from langchain_core.tools import tool

from app.integrations import store
from app.tools.web import _blocked_host

from contextvars import ContextVar

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_write_ok: ContextVar[bool] = ContextVar("nikki_api_write_ok", default=False)


@tool
def list_apis() -> str:
    """List the custom REST APIs the user has connected (name, base URL, notes)."""
    conns = store.connections("custom_rest")
    if not conns:
        return "No custom APIs connected. The user can add one at /integrations (Custom REST API)."
    return "\n".join(f"- {c['label']}: {c['meta'].get('base_url')}" + (f" — {c['meta'].get('notes')}" if c['meta'].get('notes') else "") for c in conns)


@tool
def call_api(api: str, path: str, method: str = "GET", query: str = "", body: str = "", max_chars: int = 8000) -> str:
    """Call a connected custom REST API. `api` = its name from list_apis, `path` is appended to its base URL
    (e.g. '/customers?limit=5'), `query` optional JSON object of query params, `body` optional JSON string.
    GET is ungated; other methods require approval (handled automatically)."""
    method = method.upper()
    if method not in METHODS:
        return f"error: unsupported method {method}"
    if method != "GET" and not _write_ok.get():
        return "error: use call_api_write for non-GET requests (it asks the user for approval)"
    try:
        label = store.resolve_label("custom_rest", api)
        key, row = store.secret_for("custom_rest", label)
    except RuntimeError as e:
        return f"error: {e}"
    meta = row["meta"]
    base = meta.get("base_url", "").rstrip("/")
    url = base + ("/" + path.lstrip("/") if path else "")
    host = httpx.URL(url).host
    if (why := _blocked_host(host)):
        return f"error: {why}"
    headers = {meta.get("auth_header") or "Authorization": f"{meta.get('auth_prefix', '')}{key}", "Accept": "application/json"}
    try:
        params = json.loads(query) if query.strip() else None
        data = json.loads(body) if body.strip() else None
    except json.JSONDecodeError as e:
        return f"error: query/body must be JSON: {e}"
    try:
        r = httpx.request(method, url, params=params, json=data, headers=headers, timeout=30, follow_redirects=False)
    except httpx.HTTPError as e:
        return f"error: {e}"
    text = r.text
    try:
        text = json.dumps(r.json(), indent=1, ensure_ascii=False)
    except ValueError:
        pass
    return f"HTTP {r.status_code}\n{text[:max_chars]}"


@tool
def call_api_write(api: str, path: str, method: str = "POST", body: str = "", query: str = "") -> str:
    """Write call (POST/PUT/PATCH/DELETE) to a connected custom REST API. Requires approval."""
    if method.upper() == "GET":
        return "use call_api for GET"
    tok = _write_ok.set(True)
    try:
        return call_api.func(api=api, path=path, method=method, query=query, body=body)  # type: ignore[attr-defined]
    finally:
        _write_ok.reset(tok)


list_apis.metadata = {"requires_approval": False}
call_api.metadata = {"requires_approval": False}
call_api_write.metadata = {"requires_approval": True}
TOOLS = [list_apis, call_api, call_api_write]
