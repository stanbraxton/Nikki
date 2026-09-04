"""Scheduled tasks: Cloud Scheduler calls `POST /api/run` on a cron; Nikki runs the stored
prompt headlessly and records the result in `scheduled_runs`. `/schedules` is the admin
page (login cookie), `/api/run` accepts a Google OIDC token from the scheduler service
account or the ADMIN_API_TOKEN bearer (for manual triggers)."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from chainlit.auth import get_current_user
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, update

from app import persistence

log = logging.getLogger("nikki.scheduler")
router = APIRouter()


def public_url() -> str:
    return (os.environ.get("PUBLIC_URL") or "https://nikkiaia.com").rstrip("/")


def scheduler_sa() -> str | None:
    return os.environ.get("SCHEDULER_SERVICE_ACCOUNT") or None


class RunRequest(BaseModel):
    schedule: str = "manual"
    prompt: str | None = None  # defaults to the schedule's stored prompt
    auto_approve: bool | None = None
    wait: bool = False  # manual callers may wait for the result


def _verify_oidc(token: str) -> bool:
    try:
        from google.auth.transport.requests import Request as GRequest
        from google.oauth2 import id_token

        info = id_token.verify_oauth2_token(token, GRequest(), audience=f"{public_url()}/api/run")
        return bool(info.get("email_verified")) and info.get("email") == scheduler_sa()
    except Exception as e:  # noqa: BLE001
        log.warning("oidc verify failed: %s", e)
        return False


def _authorized(authorization: str) -> bool:
    if not authorization.startswith("Bearer "):
        return False
    token = authorization[7:].strip()
    admin = os.environ.get("ADMIN_API_TOKEN")
    if admin and token == admin:
        return True
    return _verify_oidc(token)


async def _execute(run_id: str, schedule: str, prompt: str, auto_approve: bool, thread_id: str) -> None:
    from app.headless import run_prompt

    r = persistence.scheduled_runs
    try:
        out = await asyncio.wait_for(run_prompt(prompt, thread_id, auto_approve=auto_approve), timeout=25 * 60)
        values = {"status": "ok", "output": out[:20000]}
    except Exception as e:  # noqa: BLE001
        log.exception("scheduled run failed")
        values = {"status": "error", "error": f"{type(e).__name__}: {e}"[:4000]}
    async with persistence.engine().begin() as conn:
        await conn.execute(update(r).where(r.c.id == run_id).values(finished_at=datetime.now(timezone.utc), **values))


@router.post("/api/run", status_code=202)
async def api_run(req: RunRequest, authorization: str = Header(default="")) -> dict:
    if not _authorized(authorization):
        raise HTTPException(status_code=401)
    s = persistence.schedules
    auto = bool(req.auto_approve)
    prompt = req.prompt
    async with persistence.engine().connect() as conn:
        row = (await conn.execute(select(s).where(s.c.name == req.schedule))).first()
    if row is not None:
        prompt = prompt or row._mapping["prompt"]
        if req.auto_approve is None:
            auto = row._mapping["auto_approve"] == "true"
    if not prompt:
        raise HTTPException(status_code=400, detail="no prompt (unknown schedule and no prompt given)")
    run_id, now = str(uuid.uuid4()), datetime.now(timezone.utc)
    thread_id = f"sched-{req.schedule}-{now:%Y%m%d-%H%M%S}"
    async with persistence.engine().begin() as conn:
        await conn.execute(persistence.scheduled_runs.insert().values(
            id=run_id, schedule=req.schedule, thread_id=thread_id, started_at=now, status="running"))
    task = asyncio.create_task(_execute(run_id, req.schedule, prompt, auto, thread_id))
    if req.wait:
        await task
        async with persistence.engine().connect() as conn:
            r = (await conn.execute(select(persistence.scheduled_runs).where(persistence.scheduled_runs.c.id == run_id))).first()
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r._mapping.items()}
    return {"run_id": run_id, "thread_id": thread_id, "status": "running"}


@router.get("/api/schedules")
async def api_schedules(user=Depends(get_current_user)) -> dict:
    if user is None:
        raise HTTPException(status_code=401)
    s, r = persistence.schedules, persistence.scheduled_runs
    async with persistence.engine().connect() as conn:
        sched = [dict(x._mapping) for x in await conn.execute(select(s).order_by(s.c.created_at))]
        runs = [dict(x._mapping) for x in await conn.execute(select(r).order_by(r.c.started_at.desc()).limit(100))]
    for coll in (sched, runs):
        for d in coll:
            for k, v in list(d.items()):
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
    return {"schedules": sched, "runs": runs}


@router.get("/schedules", response_class=HTMLResponse)
async def schedules_page() -> HTMLResponse:
    return HTMLResponse(PAGE)


@router.get("/schedules/", include_in_schema=False)
async def schedules_slash():
    return RedirectResponse("/schedules")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Schedules · Nikki</title>
<style>
:root{--bg:#0f1014;--card:#171922;--line:#262a38;--text:#e8e9f0;--muted:#8b90a5;--primary:#7c5cff;--ok:#2fbf71;--bad:#ff5c72;--warn:#f5b342}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,Segoe UI,Inter,Roboto,sans-serif}
header{display:flex;align-items:center;justify-content:space-between;padding:18px 28px;border-bottom:1px solid var(--line)}
header h1{font-size:18px;margin:0;font-weight:600}header a{color:var(--muted);text-decoration:none;font-size:14px}header a:hover{color:var(--text)}
main{max-width:1100px;margin:0 auto;padding:28px}h2{font-size:15px;color:var(--muted);font-weight:600;margin:26px 0 12px;text-transform:uppercase;letter-spacing:.6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:12px}
.row{display:flex;justify-content:space-between;gap:14px;align-items:baseline;flex-wrap:wrap}.name{font-weight:600;font-size:16px}
.meta{color:var(--muted);font-size:12.5px}.prompt{margin-top:8px;color:#c6c9d6;font-size:13.5px;white-space:pre-wrap}
.chip{font-size:11.5px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.chip.ok{border-color:var(--ok);color:var(--ok)}.chip.error{border-color:var(--bad);color:var(--bad)}.chip.running{border-color:var(--primary);color:#c9bdff;animation:pulse 1.4s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.55}}
details{margin-top:8px}summary{cursor:pointer;color:var(--muted);font-size:13px}pre{white-space:pre-wrap;font-size:12.5px;background:#0c0d12;padding:10px;border-radius:8px;max-height:360px;overflow:auto;color:#dfe2ee}
.empty{color:var(--muted);text-align:center;padding:60px 20px}.empty b{color:var(--text)}
</style></head><body>
<header><h1>Schedules</h1><a href="/">← Back to Nikki</a></header>
<main><div id="root" class="empty">Loading…</div></main>
<script>
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const when=s=>s?new Date(s).toLocaleString():"";
async function load(){
  const r=await fetch("/api/schedules",{credentials:"same-origin"});
  if(r.status===401){location.href="/login?redirect="+encodeURIComponent("/schedules");return}
  const d=await r.json(),root=document.getElementById("root");root.className="";
  let h="<h2>Recurring tasks</h2>";
  if(!d.schedules.length)h+='<div class="card empty" style="padding:30px">No schedules yet. Tell Nikki: <b>“every weekday at 7am, summarize my unread email”</b>.</div>';
  for(const s of d.schedules){const auto=s.auto_approve==="true";
    h+=`<div class="card"><div class="row"><span class="name">${esc(s.name)}</span><span class="meta">${esc(s.cron)} · ${esc(s.timezone)}${auto?" · <span class='chip'>auto-approve</span>":""}</span></div><div class="prompt">${esc(s.prompt)}</div></div>`}
  h+="<h2>Recent runs</h2>";
  if(!d.runs.length)h+='<div class="meta">No runs yet.</div>';
  for(const x of d.runs){h+=`<div class="card"><div class="row"><span><span class="name">${esc(x.schedule)}</span> <span class="chip ${esc(x.status)}">${esc(x.status)}</span></span><span class="meta">${when(x.started_at)}${x.finished_at?" → "+when(x.finished_at):""}</span></div>
    <details ${x.status!=="running"?"":""}><summary>${x.status==="error"?"error":"output"}</summary><pre>${esc(x.error||x.output||"(running…)")}</pre></details></div>`}
  root.innerHTML=h;
  if(d.runs.some(x=>x.status==="running"))setTimeout(load,5000);
}
load();
</script></body></html>"""
