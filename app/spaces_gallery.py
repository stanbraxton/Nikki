"""Spaces Gallery: `/spaces` HTML page + `/api/spaces` JSON, protected by the same login
cookie as the chat UI (Chainlit's `get_current_user`)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app import persistence
from app.auth import require_admin

router = APIRouter()

STAGES = ["building", "pushing", "provisioning", "live"]
STAGE_LABELS = {"queued": "Queued", "building": "Building Container", "pushing": "Pushing to Registry",
                "provisioning": "Provisioning Cloud Run", "live": "Live", "failed": "Failed", "deleted": "Deleted"}


async def _rows() -> list[dict[str, Any]]:
    async with persistence.engine().connect() as conn:
        res = await conn.execute(select(persistence.spaces).order_by(persistence.spaces.c.created_at.desc()))
        out = []
        for r in res:
            d = dict(r._mapping)
            for k in ("created_at", "updated_at"):
                if d.get(k) is not None:
                    d[k] = d[k].isoformat()
            d["stage_log"] = (d.get("stage_log") or "")[-1500:]
            out.append(d)
        return out


@router.get("/api/spaces")
async def api_spaces(_=Depends(require_admin)) -> list[dict]:
    """Gallery data. Admin login cookie required (401/403 otherwise)."""
    return await _rows()


@router.get("/api/apps")
async def api_apps(_=Depends(require_admin)) -> list[dict]:
    """Engineer-managed apps (git repo -> Cloud Build -> Firebase Hosting) with their latest build."""
    async with persistence.engine().connect() as conn:
        apps = [dict(r._mapping) for r in await conn.execute(select(persistence.apps).order_by(persistence.apps.c.slug))]
        out = []
        for a in apps:
            b = None
            if a.get("last_build_id"):
                r = (await conn.execute(select(persistence.builds).where(persistence.builds.c.id == a["last_build_id"]))).first()
                b = dict(r._mapping) if r else None
            for k in ("created_at", "updated_at"):
                if a.get(k) is not None:
                    a[k] = a[k].isoformat()
            a["build"] = {"id": b["id"], "status": b["status"], "log": (b["log"] or "")[-1500:]} if b else None
            out.append(a)
        return out


@router.get("/spaces", response_class=HTMLResponse)
async def spaces_page() -> HTMLResponse:
    """Static shell; it fetches /api/spaces and bounces to /login on 401, so no data leaks unauthenticated."""
    return HTMLResponse(PAGE)


@router.get("/spaces/", include_in_schema=False)
async def spaces_slash():
    return RedirectResponse("/spaces")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spaces · Nikki</title>
<style>
:root{--bg:#0f1014;--card:#171922;--line:#262a38;--text:#e8e9f0;--muted:#8b90a5;--primary:#7c5cff;--ok:#2fbf71;--bad:#ff5c72;--warn:#f5b342}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,Segoe UI,Inter,Roboto,sans-serif}
header{display:flex;align-items:center;justify-content:space-between;padding:18px 28px;border-bottom:1px solid var(--line)}
header h1{font-size:18px;margin:0;font-weight:600;letter-spacing:.2px}header a{color:var(--muted);text-decoration:none;font-size:14px}header a:hover{color:var(--text)}
main{max-width:1180px;margin:0 auto;padding:28px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;display:flex;flex-direction:column;gap:12px;transition:border-color .2s}
.card:hover{border-color:#3a3f55}.card h2{margin:0;font-size:17px;font-weight:600}.meta{color:var(--muted);font-size:12.5px;display:flex;gap:10px;flex-wrap:wrap}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{font-size:11.5px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);color:var(--muted);background:#12141b}
.chip.done{border-color:rgba(47,191,113,.4);color:var(--ok)}.chip.active{border-color:var(--primary);color:#c9bdff;background:rgba(124,92,255,.12);animation:pulse 1.4s infinite}
.chip.live{border-color:var(--ok);color:var(--ok);background:rgba(47,191,113,.12)}.chip.failed{border-color:var(--bad);color:var(--bad);background:rgba(255,92,114,.1)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.55}}
.actions{display:flex;gap:10px;margin-top:auto}.btn{display:inline-block;padding:8px 14px;border-radius:9px;font-size:13.5px;font-weight:600;text-decoration:none;border:1px solid var(--line);color:var(--text);background:#1d2030}
.btn.primary{background:var(--primary);border-color:var(--primary);color:#fff}.btn[aria-disabled=true]{opacity:.45;pointer-events:none}
.url{font-size:12px;color:var(--muted);word-break:break-all}.err{font-size:12px;color:var(--bad);white-space:pre-wrap;max-height:90px;overflow:auto}
.empty{color:var(--muted);text-align:center;padding:40px 20px}.sect{font-size:15px;font-weight:600;margin:6px 0 14px;color:var(--text)}.sub{color:var(--muted);font-weight:400;font-size:12.5px;margin-left:8px}.empty b{color:var(--text)}
details{font-size:12px;color:var(--muted)}pre{white-space:pre-wrap;font-size:11px;background:#0c0d12;padding:8px;border-radius:8px;max-height:160px;overflow:auto}
</style></head><body>
<header><h1>Spaces Gallery</h1><a href="/">← Back to Nikki</a></header>
<main><h2 class="sect">Apps <span class="sub">full projects · GitHub → Cloud Build → Firebase Hosting</span></h2><div id="apps" class="empty">Loading…</div>
<h2 class="sect">Micro-Spaces <span class="sub">single-file apps on Cloud Run</span></h2><div id="root" class="empty">Loading…</div></main>
<script>
const STAGES=["building","pushing","provisioning","live"],LABEL={building:"Building Container",pushing:"Pushing to Registry",provisioning:"Provisioning Cloud Run",live:"Live"};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function chips(st){if(st==="failed")return '<span class="chip failed">Failed</span>';const idx=STAGES.indexOf(st);return STAGES.map((s,i)=>{let c=i<idx?"done":i===idx?(s==="live"?"live":"active"):"todo";if(st==="queued")c="todo";return `<span class="chip ${c}">${LABEL[s]}</span>`}).join("")}
function card(r){const live=r.status==="live"&&r.url;const d=new Date(r.created_at);return `<div class="card">
<h2>${esc(r.title)}</h2><div class="meta"><span>${esc(r.slug)}</span><span>·</span><span>${esc(r.framework)}</span><span>·</span><span>${d.toLocaleDateString()} ${d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</span>${r.public==="false"?'<span>· private</span>':''}</div>
<div class="chips">${chips(r.status)}</div>
${r.url?`<div class="url">${esc(r.url)}</div>`:""}${r.status==="failed"&&r.last_error?`<div class="err">${esc(r.last_error.split("\n").slice(-4).join("\n"))}</div>`:""}
${r.stage_log?`<details><summary>Build log</summary><pre>${esc(r.stage_log)}</pre></details>`:""}
<div class="actions"><a class="btn primary" ${live?`href="${esc(r.url)}" target="_blank" rel="noopener"`:'aria-disabled="true"'}>Launch ↗</a></div></div>`}
function appCard(a){const b=a.build;const st=a.status;const cls=st==="live"?"live":st==="failed"?"failed":st==="building"?"active":"todo";return `<div class="card">
<h2>${esc(a.title)}</h2><div class="meta"><span>${esc(a.slug)}</span><span>·</span><span>${esc(a.repo)}@${esc(a.branch)}</span>${a.custom_domain?`<span>· ${esc(a.custom_domain)}</span>`:""}</div>
<div class="chips"><span class="chip ${cls}">${esc(st)}</span>${b?`<span class="chip">build ${esc(b.status)}</span>`:""}</div>
${a.url?`<div class="url">${esc(a.url)}</div>`:""}${b&&b.log?`<details><summary>Build log</summary><pre>${esc(b.log)}</pre></details>`:""}
<div class="actions"><a class="btn primary" ${st==="live"&&a.url?`href="${esc(a.url)}" target="_blank" rel="noopener"`:'aria-disabled="true"'}>Launch ↗</a><a class="btn" href="https://github.com/${esc(a.repo)}" target="_blank" rel="noopener">Repo</a></div></div>`}
async function loadApps(){try{const res=await fetch("/api/apps",{credentials:"same-origin"});if(res.status===401){location.href="/login?redirect=/spaces";return}const rows=await res.json();const el=document.getElementById("apps");
if(!rows.length){el.className="empty";el.innerHTML="<b>No apps registered.</b><br>Ask Nikki to register a GitHub repo with register_app.";}else{el.className="grid";el.innerHTML=rows.map(appCard).join("")}
const busy=rows.some(r=>r.status==="building");setTimeout(loadApps,busy?5000:30000)}catch(e){setTimeout(loadApps,8000)}}
loadApps();
async function load(){try{const res=await fetch("/api/spaces",{credentials:"same-origin"});if(res.status===401){location.href="/login?redirect=/spaces";return}const rows=await res.json();const root=document.getElementById("root");
if(!rows.length){root.className="empty";root.innerHTML="<b>No Spaces yet.</b><br>Ask Nikki: “Build me a Space that tracks …” and it will appear here.";return}
root.className="grid";root.innerHTML=rows.map(card).join("");const busy=rows.some(r=>!["live","failed","deleted"].includes(r.status));setTimeout(load,busy?4000:20000)}catch(e){setTimeout(load,8000)}}
load();
</script></body></html>"""
