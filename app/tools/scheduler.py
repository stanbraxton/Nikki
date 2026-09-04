"""Scheduling tools: create/list/run/delete recurring tasks. Each schedule is a Cloud
Scheduler job that POSTs to Nikki's /api/run with an OIDC token; the prompt lives in the
`schedules` table. Creating and deleting schedules is approval-gated."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

from langchain_core.tools import tool
from sqlalchemy import create_engine, delete, insert, select

from app import persistence
from app.config import settings
from app.tenancy import tenant_id

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
CRON_RE = re.compile(r"^(\S+\s+){4}\S+$")
_eng = None


def _e():
    global _eng
    if _eng is None:
        _eng = create_engine(settings.sqlalchemy_sync_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _eng


def _cfg(n: str, d: str | None = None) -> str | None:
    v = os.environ.get(n)
    return v if v not in (None, "") else d


def project() -> str | None:
    return _cfg("SPACES_PROJECT") or _cfg("GOOGLE_CLOUD_PROJECT")


def region() -> str:
    return _cfg("SPACES_REGION", "us-east4")  # type: ignore[return-value]


def public_url() -> str:
    return (_cfg("PUBLIC_URL") or "https://nikkiaia.com").rstrip("/")


def scheduler_sa() -> str | None:
    p = project()
    return _cfg("SCHEDULER_SERVICE_ACCOUNT") or (f"nikki-scheduler@{p}.iam.gserviceaccount.com" if p else None)


def _gcloud(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    exe = _cfg("GCLOUD_BIN") or shutil.which("gcloud") or "gcloud"
    return subprocess.run([exe, *args, "--project", project() or "", "-q"], capture_output=True, text=True, timeout=timeout)


def _key(label: str) -> str:
    """Global schedule key = short tenant prefix + label (Cloud Scheduler names must be unique)."""
    tid = re.sub(r"[^a-z0-9-]", "-", tenant_id().lower())[:20].strip("-")
    return f"{tid}-{label}"


def _job(key: str) -> str:
    return f"nikki-sched-{key}"


def _lookup(label: str):
    """Find this tenant's schedule by label (or full key)."""
    s = persistence.schedules
    with _e().connect() as c:
        row = c.execute(select(s).where(s.c.tenant_id == tenant_id(), (s.c.label == label) | (s.c.name == label))).first()
    return dict(row._mapping) if row else None


@tool
def schedule_task(name: str, cron: str, prompt: str, timezone: str = "America/New_York", auto_approve: bool = False) -> str:
    """Create a recurring task: at each cron tick Nikki runs `prompt` unattended and stores the
    result (visible at /schedules). cron is 5-field unix syntax, e.g. '0 7 * * 1-5'.
    auto_approve=True lets the run execute approval-gated tools (e.g. gmail_send) without a human —
    only set it when the user explicitly wants that. Requires approval."""
    name = name.strip().lower()
    if not NAME_RE.match(name) or len(name) > 30:
        return "error: name must be 2-30 chars, lowercase letters/digits/hyphens, starting with a letter"
    if not CRON_RE.match(cron.strip()):
        return "error: cron must have 5 fields, e.g. '0 7 * * 1-5'"
    if not project() or not scheduler_sa():
        return "error: scheduler is not configured (SPACES_PROJECT / SCHEDULER_SERVICE_ACCOUNT)"
    s = persistence.schedules
    if _lookup(name):
        return f"error: schedule {name!r} already exists (delete_schedule first)"
    key = _key(name)
    body = f'{{"schedule": "{key}"}}'
    common = [
        "--location", region(), "--schedule", cron.strip(), "--time-zone", timezone,
        "--uri", f"{public_url()}/api/run", "--http-method", "POST",
        "--headers", "Content-Type=application/json", "--message-body", body,
        "--oidc-service-account-email", scheduler_sa(), "--oidc-token-audience", f"{public_url()}/api/run",
        "--attempt-deadline", "180s", "--description", f"Nikki schedule {key}",
    ]
    res = _gcloud("scheduler", "jobs", "create", "http", _job(key), *common)
    if res.returncode != 0 and "already exists" in res.stderr:
        res = _gcloud("scheduler", "jobs", "update", "http", _job(key), *common)
    if res.returncode != 0:
        return f"error: Cloud Scheduler: {res.stderr.strip()[-600:]}"
    with _e().begin() as c:
        c.execute(insert(s).values(name=key, tenant_id=tenant_id(), label=name, cron=cron.strip(), timezone=timezone, prompt=prompt.strip(),
                                   job_name=f"projects/{project()}/locations/{region()}/jobs/{_job(key)}",
                                   auto_approve="true" if auto_approve else "false",
                                   created_at=datetime.now(timezone.utc)))
    return (f"scheduled {name!r}: '{cron.strip()}' ({timezone}){' with auto-approve' if auto_approve else ''}. "
            f"Results appear at {public_url()}/schedules")


@tool
def list_schedules() -> str:
    """List recurring tasks and their last run status."""
    s, r = persistence.schedules, persistence.scheduled_runs
    with _e().connect() as c:
        rows = c.execute(select(s).where(s.c.tenant_id == tenant_id()).order_by(s.c.created_at)).fetchall()
        out = []
        for row in rows:
            d = dict(row._mapping)
            d["name"] = d["label"] or d["name"]
            last = c.execute(select(r.c.status, r.c.started_at).where(r.c.schedule == row._mapping["name"])
                             .order_by(r.c.started_at.desc()).limit(1)).first()
            lt = f"last run {last[1]:%Y-%m-%d %H:%M} UTC → {last[0]}" if last else "never run"
            out.append(f"- {d['name']}: '{d['cron']}' {d['timezone']}{' [auto-approve]' if d['auto_approve']=='true' else ''} — {lt}\n"
                       f"    prompt: {d['prompt'][:200]}")
    return "\n".join(out) if out else "(no schedules)"


@tool
def schedule_runs(name: str, limit: int = 5) -> str:
    """Show the most recent results of a scheduled task."""
    r = persistence.scheduled_runs
    sched = _lookup(name)
    if not sched:
        return f"no schedule named {name!r}"
    with _e().connect() as c:
        rows = c.execute(select(r).where(r.c.schedule == sched["name"], r.c.tenant_id == tenant_id()).order_by(r.c.started_at.desc())
                         .limit(max(1, min(int(limit), 20)))).fetchall()
    if not rows:
        return f"no runs recorded for {name!r}"
    parts = []
    for row in rows:
        d = dict(row._mapping)
        parts.append(f"## {d['started_at']:%Y-%m-%d %H:%M} UTC — {d['status']}\n{(d['error'] or d['output'] or '(running)')[:1500]}")
    return "\n\n".join(parts)


@tool
def run_schedule_now(name: str) -> str:
    """Trigger a scheduled task immediately (off-cycle). The result shows up at /schedules in a minute or two."""
    sched = _lookup(name)
    if not sched:
        return f"no schedule named {name!r}"
    res = _gcloud("scheduler", "jobs", "run", _job(sched["name"]), "--location", region())
    if res.returncode != 0:
        return f"error: {res.stderr.strip()[-400:]}"
    return f"triggered {name!r}; check schedule_runs('{name}') shortly"


@tool
def delete_schedule(name: str) -> str:
    """Delete a recurring task (Cloud Scheduler job + record). Requires approval."""
    sched = _lookup(name)
    if not sched:
        return f"no schedule named {name!r}"
    res = _gcloud("scheduler", "jobs", "delete", _job(sched["name"]), "--location", region())
    if res.returncode != 0 and "NOT_FOUND" not in res.stderr and "not found" not in res.stderr.lower():
        return f"error: {res.stderr.strip()[-400:]}"
    with _e().begin() as c:
        c.execute(delete(persistence.schedules).where(persistence.schedules.c.name == sched["name"]))
    return f"deleted schedule {name!r}"


for _t in (list_schedules, schedule_runs, run_schedule_now):
    _t.metadata = {"requires_approval": False}
for _t in (schedule_task, delete_schedule):
    _t.metadata = {"requires_approval": True}

TOOLS = [schedule_task, list_schedules, schedule_runs, run_schedule_now, delete_schedule]
