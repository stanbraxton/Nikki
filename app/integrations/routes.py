"""Integrations page + generic OAuth2 / API-key connection routes (tenant-scoped) and the
admin page for provider developer credentials."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app import persistence
from app.auth import require_admin, require_principal
from app.integrations import providers, store
from app.tenancy import Principal

log = logging.getLogger("nikki.integrations")
router = APIRouter()


def public_url() -> str:
    return (os.environ.get("PUBLIC_URL") or "https://nikkiaia.com").rstrip("/")


def redirect_uri(provider: str) -> str:
    return f"{public_url()}/oauth/{provider}/callback"


def _secret() -> bytes:
    return (os.environ.get("CHAINLIT_AUTH_SECRET") or "nikki-dev").encode()


def _sign_state(tid: str) -> str:
    ts = str(int(time.time()))
    mac = hmac.new(_secret(), f"{tid}|{ts}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{tid}.{ts}.{mac}"


def _check_state(state: str, tid: str) -> bool:
    try:
        s_tid, ts, mac = state.rsplit(".", 2)
    except ValueError:
        return False
    good = hmac.compare_digest(hmac.new(_secret(), f"{s_tid}|{ts}".encode(), hashlib.sha256).hexdigest()[:32], mac)
    return good and s_tid == tid and time.time() - int(ts) < 900


def _scopes(p: providers.Provider, principal: Principal) -> list[str]:
    return list(p.scopes) + (list(p.admin_scopes) if principal.is_admin else [])


# ---------------------------------------------------------------- oauth2
@router.get("/connect/{provider}")
async def connect(provider: str, principal: Principal = Depends(require_principal)):
    try:
        p = providers.get(provider)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown provider")
    if p.kind != "oauth2":
        return RedirectResponse("/integrations")
    creds = store.client_credentials(provider)
    if not creds:
        return HTMLResponse(_page(f"{p.name} is not set up yet",
                                  "The platform admin has to register Nikki with this provider first (Admin → Providers)."), status_code=503)
    q = {"client_id": creds[0], "redirect_uri": redirect_uri(provider), "response_type": "code",
         "scope": " ".join(_scopes(p, principal)), "state": _sign_state(principal.tenant_id), **p.extra_auth_params}
    return RedirectResponse(f"{p.auth_url}?{urlencode(q)}")


@router.get("/oauth/{provider}/callback")
async def callback(provider: str, request: Request, principal: Principal = Depends(require_principal)):
    try:
        p = providers.get(provider)
    except KeyError:
        raise HTTPException(status_code=404)
    qp = request.query_params
    if qp.get("error"):
        return HTMLResponse(_page("Connection cancelled", f"{p.name} returned: {qp.get('error_description') or qp['error']}"), status_code=400)
    code, state = qp.get("code"), qp.get("state", "")
    if not code or not _check_state(state, principal.tenant_id):
        raise HTTPException(status_code=400, detail="invalid state")
    creds = store.client_credentials(provider)
    if not creds:
        raise HTTPException(status_code=503)
    async with httpx.AsyncClient(timeout=20) as c:
        tok = await c.post(p.token_url, data={"code": code, "client_id": creds[0], "client_secret": creds[1],
                                              "redirect_uri": redirect_uri(provider), "grant_type": "authorization_code",
                                              **({"scope": " ".join(_scopes(p, principal))} if provider == "microsoft" else {})})
        if tok.status_code != 200:
            return HTMLResponse(_page("Token exchange failed", tok.text[:600]), status_code=400)
        data = tok.json()
        me = await c.get(p.userinfo_url, headers={"Authorization": f"Bearer {data['access_token']}"})
        info = me.json() if me.status_code == 200 else {}
    label = (info.get("email") or info.get("mail") or info.get("userPrincipalName") or "").lower()
    refresh = data.get("refresh_token")
    if not label or not refresh:
        return HTMLResponse(_page("Missing refresh token",
                                  f"{p.name} did not return a refresh token. Revoke Nikki's access in your {p.name} account settings and try again."), status_code=400)
    await store.upsert(provider, label, "oauth2", refresh, scopes=data.get("scope", " ".join(_scopes(p, principal))),
                       meta={"name": info.get("name") or info.get("displayName") or ""}, tid=principal.tenant_id)
    return RedirectResponse("/integrations?connected=" + provider, status_code=303)


# ---------------------------------------------------------------- api keys
@router.post("/api/integrations/{provider}")
async def add_apikey(provider: str, request: Request, principal: Principal = Depends(require_principal)) -> dict:
    try:
        p = providers.get(provider)
    except KeyError:
        raise HTTPException(status_code=404)
    if p.kind != "apikey":
        raise HTTPException(status_code=400, detail="use /connect/{provider} for OAuth providers")
    body = await request.json()
    values = {f.name: str(body.get(f.name, "")).strip() for f in p.fields}
    missing = [f.label for f in p.fields if f.required and not values.get(f.name)]
    if missing:
        raise HTTPException(status_code=400, detail="missing: " + ", ".join(missing))
    secret = values.pop("api_key")
    label = values.pop("label", "") or p.id
    meta = {k: v for k, v in values.items() if v}
    if provider == "custom_rest" and not meta.get("base_url", "").startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="base_url must start with http(s)://")
    iid = await store.upsert(provider, label.lower(), "apikey", secret, meta=meta, tid=principal.tenant_id)
    return {"ok": True, "id": iid}


@router.get("/api/integrations")
async def list_integrations(principal: Principal = Depends(require_principal)) -> dict:
    conns = await store.list_for_tenant(principal.tenant_id)
    ready = {pid: (p.kind == "apikey" or store.client_credentials(pid) is not None) for pid, p in providers.PROVIDERS.items()}
    cat = [{"id": p.id, "name": p.name, "kind": p.kind, "icon": p.icon, "description": p.description, "tools": p.tools,
            "multi": p.multi, "ready": ready[p.id],
            "fields": [{"name": f.name, "label": f.label, "secret": f.secret, "placeholder": f.placeholder, "required": f.required} for f in p.fields]}
           for p in providers.PROVIDERS.values()]
    return {"providers": cat, "connections": conns, "me": {"email": principal.email, "role": principal.role, "tenant": principal.tenant_id}}


@router.post("/api/integrations/{integration_id}/disconnect")
async def disconnect(integration_id: str, principal: Principal = Depends(require_principal)) -> dict:
    ok = await store.remove(principal.tenant_id, integration_id)
    if not ok:
        raise HTTPException(status_code=404)
    return {"ok": True}


# ---------------------------------------------------------------- admin: provider credentials
@router.get("/api/admin/providers")
async def admin_providers(_: Principal = Depends(require_admin)) -> dict:
    conf = await store.configured_providers()
    t, a = persistence.tenants, persistence.accounts
    async with persistence.engine().connect() as conn:
        tenants = [dict(r._mapping) for r in await conn.execute(
            select(t.c.id, t.c.name, t.c.plan, t.c.status, t.c.created_at, func.count(a.c.email).label("accounts"))
            .outerjoin(a, a.c.tenant_id == t.c.id).group_by(t.c.id).order_by(t.c.created_at.desc()))]
    for d in tenants:
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
    return {"providers": [{"id": p.id, "name": p.name, "configured": conf.get(p.id, ""),
                           "redirect_uri": redirect_uri(p.id), "hint": p.setup_hint.format(public_url=public_url())}
                          for p in providers.PROVIDERS.values() if p.kind == "oauth2"],
            "tenants": tenants}


@router.post("/api/admin/providers/{provider}")
async def admin_set_provider(provider: str, request: Request, _: Principal = Depends(require_admin)) -> dict:
    try:
        p = providers.get(provider)
    except KeyError:
        raise HTTPException(status_code=404)
    if p.kind != "oauth2":
        raise HTTPException(status_code=400)
    body = await request.json()
    cid, sec = str(body.get("client_id", "")).strip(), str(body.get("client_secret", "")).strip()
    if not cid or not sec:
        raise HTTPException(status_code=400, detail="client_id and client_secret required")
    await store.set_client_credentials(provider, cid, sec)
    return {"ok": True}


@router.delete("/api/admin/providers/{provider}")
async def admin_clear_provider(provider: str, _: Principal = Depends(require_admin)) -> dict:
    await store.clear_client_credentials(provider)
    return {"ok": True}


@router.post("/api/admin/tenants/{tenant_id}/approve")
async def admin_approve_tenant(tenant_id: str, _: Principal = Depends(require_admin)) -> dict:
    from sqlalchemy import update
    t = persistence.tenants
    async with persistence.engine().begin() as conn:
        result = await conn.execute(update(t).where(t.c.id == tenant_id).values(status="active"))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="tenant not found")
    log.info("tenant %s approved", tenant_id)
    return {"ok": True}


@router.post("/api/admin/tenants/{tenant_id}/suspend")
async def admin_suspend_tenant(tenant_id: str, _: Principal = Depends(require_admin)) -> dict:
    from sqlalchemy import update
    t = persistence.tenants
    async with persistence.engine().begin() as conn:
        result = await conn.execute(update(t).where(t.c.id == tenant_id).values(status="suspended"))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="tenant not found")
    log.info("tenant %s suspended", tenant_id)
    return {"ok": True}


# ---------------------------------------------------------------- pages
@router.get("/integrations", response_class=HTMLResponse, include_in_schema=False)
async def integrations_page() -> HTMLResponse:
    return HTMLResponse(INTEGRATIONS_HTML)


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page() -> HTMLResponse:
    return HTMLResponse(ADMIN_HTML)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title} · Nikki</title>
<style>body{{margin:0;background:#0f1014;color:#e8e9f0;font:15px/1.6 -apple-system,Segoe UI,Inter,sans-serif;display:grid;place-items:center;height:100vh}}
.card{{background:#171922;border:1px solid #262a38;border-radius:14px;padding:28px 32px;max-width:520px}}h1{{font-size:18px;margin:0 0 10px}}a{{color:#c9bdff}}</style></head>
<body><div class="card"><h1>{title}</h1><p>{body}</p><p><a href="/integrations">← Integrations</a> · <a href="/">Nikki</a></p></div></body></html>"""


_BASE_CSS = """
:root{--bg:#0f1014;--card:#171922;--line:#262a38;--fg:#e8e9f0;--muted:#9aa0b4;--accent:#7c5cff;--ok:#3ddc97;--warn:#ffb454;--err:#ff7b7b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,Segoe UI,Inter,sans-serif}
header{display:flex;align-items:center;gap:14px;padding:18px 28px;border-bottom:1px solid var(--line)}header h1{font-size:18px;margin:0}
nav a{color:var(--muted);text-decoration:none;margin-left:16px}nav a:hover{color:var(--fg)}main{max-width:1080px;margin:0 auto;padding:28px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;display:flex;flex-direction:column;gap:10px}
.card h2{font-size:16px;margin:0;display:flex;align-items:center;gap:8px}.muted{color:var(--muted);font-size:13px}
.chip{display:inline-block;font-size:12px;padding:2px 9px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.chip.ok{color:var(--ok);border-color:var(--ok)}.chip.warn{color:var(--warn);border-color:var(--warn)}
.conn{display:flex;justify-content:space-between;align-items:center;background:#0f1014;border:1px solid var(--line);border-radius:9px;padding:8px 12px;font-size:14px}
button,.btn{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:14px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--muted)}button:disabled{opacity:.5;cursor:default}
input,textarea{width:100%;background:#0f1014;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:14px;margin-top:4px}
label{font-size:12px;color:var(--muted);display:block;margin-top:8px}.tools{font-size:12px;color:var(--muted)}.tools code{background:#0f1014;padding:1px 5px;border-radius:5px;margin-right:4px}
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:#232637;border:1px solid var(--line);padding:10px 16px;border-radius:10px;display:none}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}th{color:var(--muted);font-weight:500;font-size:12px}
"""

INTEGRATIONS_HTML = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Integrations · Nikki</title><style>{_BASE_CSS}</style></head><body>
<header><h1>🔌 Integrations</h1><span class="muted" id="who"></span><nav style="margin-left:auto"><a href="/">Chat</a><a href="/schedules">Schedules</a><a href="/admin" id="adminlink" style="display:none">Admin</a></nav></header>
<main><p class="muted">Connect the services Nikki may use on your behalf. Credentials are encrypted and only used for actions you ask for; anything that sends or creates content still asks for your approval.</p>
<div class="grid" id="grid"></div></main><div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s);let data=null;
function toast(m){{const t=$('#toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',3500)}}
async function load(){{const r=await fetch('/api/integrations');if(r.status===401){{location.href='/login';return}}data=await r.json();render()}}
function esc(s){{return String(s??'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]))}}
function render(){{
 $('#who').textContent=data.me.email+' · '+data.me.tenant;if(data.me.role==='admin')$('#adminlink').style.display='';
 const g=$('#grid');g.innerHTML='';
 for(const p of data.providers){{
  const conns=data.connections.filter(c=>c.provider===p.id);
  const card=document.createElement('div');card.className='card';
  const status=conns.length?`<span class="chip ok">Connected${{conns.length>1?' ×'+conns.length:''}}</span>`:p.ready?`<span class="chip">Not connected</span>`:`<span class="chip warn">Not set up</span>`;
  let body=`<h2>${{p.icon}} ${{esc(p.name)}} ${{status}}</h2><div class="muted">${{esc(p.description)}}</div>
   <div class="tools">${{p.tools.map(t=>'<code>'+t+'</code>').join('')}}</div>`;
  for(const c of conns){{body+=`<div class="conn"><span>${{esc(c.label)}}${{c.meta&&c.meta.name?' <span class="muted">· '+esc(c.meta.name)+'</span>':''}}</span><button class="ghost" onclick="disc('${{c.id}}')">Disconnect</button></div>`}}
  if(p.kind==='oauth2'){{ if(p.ready&&(p.multi||!conns.length))body+=`<a class="btn" href="/connect/${{p.id}}">Connect ${{esc(p.name)}}</a>`;
    else if(!p.ready)body+=`<div class="muted">The administrator hasn't registered Nikki with ${{esc(p.name)}} yet.</div>`}}
  else if(p.multi||!conns.length){{body+=`<details><summary class="muted" style="cursor:pointer">Add ${{esc(p.name)}}</summary><form onsubmit="return addKey(event,'${{p.id}}')">`+
    p.fields.map(f=>`<label>${{esc(f.label)}}${{f.required?'':' <i>(optional)</i>'}}${{f.name==='notes'?`<textarea name="${{f.name}}" rows="2" placeholder="${{esc(f.placeholder)}}"></textarea>`:`<input name="${{f.name}}" type="${{f.secret?'password':'text'}}" placeholder="${{esc(f.placeholder)}}" ${{f.required?'required':''}}>`}}</label>`).join('')+
    `<div style="margin-top:10px"><button type="submit">Save</button></div></form></details>`}}
  card.innerHTML=body;g.appendChild(card);
 }}
}}
async function addKey(e,pid){{e.preventDefault();const fd=new FormData(e.target);const body={{}};fd.forEach((v,k)=>body[k]=v);
 const r=await fetch('/api/integrations/'+pid,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}});
 if(r.ok){{toast('Saved');load()}}else{{toast((await r.json()).detail||'error')}}return false}}
async function disc(id){{if(!confirm('Disconnect this account?'))return;const r=await fetch('/api/integrations/'+id+'/disconnect',{{method:'POST'}});if(r.ok){{toast('Disconnected');load()}}}}
load();if(new URLSearchParams(location.search).get('connected'))toast('Connected ✓');
</script></body></html>"""

ADMIN_HTML = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin · Nikki</title><style>{_BASE_CSS}</style></head><body>
<header><h1>🛠 Admin</h1><nav style="margin-left:auto"><a href="/">Chat</a><a href="/integrations">Integrations</a><a href="/schedules">Schedules</a><a href="/spaces">Spaces</a></nav></header>
<main><h2 style="font-size:16px">Provider registrations</h2><p class="muted">One-time developer app registration per OAuth provider. Once saved here, every tenant can connect with one click. Secrets are stored encrypted.</p>
<div class="grid" id="prov"></div>
<h2 style="font-size:16px;margin-top:34px">Tenants</h2><table id="tenants"><thead><tr><th>Tenant</th><th>Name</th><th>Plan</th><th>Status</th><th>Accounts</th><th>Created</th><th>Actions</th></tr></thead><tbody></tbody></table>
</main><div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s);function toast(m){{const t=$('#toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',3500)}}
function esc(s){{return String(s??'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]))}}
async function load(){{const r=await fetch('/api/admin/providers');if(r.status===401){{location.href='/login';return}}if(r.status===403){{document.body.innerHTML='<main><h1>Admin only</h1></main>';return}}
 const d=await r.json();const g=$('#prov');g.innerHTML='';
 for(const p of d.providers){{const el=document.createElement('div');el.className='card';
  el.innerHTML=`<h2>${{esc(p.name)}} ${{p.configured?'<span class="chip ok">Configured ('+p.configured+')</span>':'<span class="chip warn">Not configured</span>'}}</h2>
   <div class="muted">${{esc(p.hint)}}</div><div class="muted">Redirect URI: <code>${{esc(p.redirect_uri)}}</code></div>
   <form onsubmit="return save(event,'${{p.id}}')"><label>Client ID<input name="client_id" required></label><label>Client secret<input name="client_secret" type="password" required></label>
   <div style="margin-top:10px;display:flex;gap:8px"><button type="submit">Save</button>${{p.configured==='db'?`<button type="button" class="ghost" onclick="clr('${{p.id}}')">Remove</button>`:''}}</div></form>`;g.appendChild(el)}}
 const tb=$('#tenants tbody');tb.innerHTML=d.tenants.map(t=>`<tr><td><code>${{esc(t.id)}}</code></td><td>${{esc(t.name)}}</td><td>${{esc(t.plan)}}</td><td><span class="chip ${{t.status==='active'?'ok':t.status==='pending'?'warn':''}}">${{esc(t.status)}}</span></td><td>${{t.accounts}}</td><td class="muted">${{(t.created_at||'').slice(0,10)}}</td><td>${{t.status==='pending'?`<button class="ghost" onclick="approveTenant('${{t.id}}')">Approve</button>`:t.status==='active'?`<button class="ghost" onclick="suspendTenant('${{t.id}}')">Suspend</button>`:''}}</td></tr>`).join('');
}}
async function save(e,pid){{e.preventDefault();const fd=new FormData(e.target);const body={{}};fd.forEach((v,k)=>body[k]=v);
 const r=await fetch('/api/admin/providers/'+pid,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}});toast(r.ok?'Saved':'error');load();return false}}
async function clr(pid){{if(!confirm('Remove stored credentials?'))return;await fetch('/api/admin/providers/'+pid,{{method:'DELETE'}});load()}}
async function approveTenant(tid){{if(!confirm('Approve this tenant?'))return;const r=await fetch('/api/admin/tenants/'+tid+'/approve',{{method:'POST'}});toast(r.ok?'Approved':'error');load()}}
async function suspendTenant(tid){{if(!confirm('Suspend this tenant?'))return;const r=await fetch('/api/admin/tenants/'+tid+'/suspend',{{method:'POST'}});toast(r.ok?'Suspended':'error');load()}}
load();
</script></body></html>"""
