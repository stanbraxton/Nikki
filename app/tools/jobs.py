"""Background jobs: run a Python script for minutes or hours outside the chat, on Cloud Run Jobs.

Why: a chat turn must finish in minutes and the browser/LLM loop is slow. Polling a website every
15 seconds until 5 pm, bulk downloads, long scrapes — those belong in a plain script that runs on its
own. Nikki writes the script, `job_start` ships it as a Cloud Run Job (python:3.12-slim + pip deps),
`job_logs` / `job_status` follow it, `job_stop` cancels it.

Secrets never go into the script or the chat: `secret_put(name, value)` stores them in Secret Manager
and `job_start(secrets={"ENV_NAME": "secret-name"})` mounts them as environment variables. Jobs run
as the no-privilege spaces service account; each secret is granted to it individually.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone

from langchain_core.tools import tool

from app.tools.engineer import _cfg, gcloud_bin, project, region

JOB_PREFIX = "nikki-job-"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
IMAGE = "python:3.12-slim"
MAX_SCRIPT_BYTES = 24_000  # env-var payload; Cloud Run caps the whole env block at 32 KiB


def jobs_sa() -> str:
    return _cfg("JOBS_SERVICE_ACCOUNT") or _cfg("SPACES_SERVICE_ACCOUNT") or f"nikki-spaces@{project()}.iam.gserviceaccount.com"


def _g(*args: str, timeout: int = 180, stdin: str | None = None) -> tuple[int, str]:
    cmd = [gcloud_bin(), *args, "--project", project() or "", "-q"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
    return r.returncode, (r.stdout + r.stderr).strip()


def _job(name: str) -> str:
    if not NAME_RE.match(name):
        raise ValueError("job name: lowercase letters, digits, dashes; 2-31 chars; starts with a letter")
    return JOB_PREFIX + name


def _describe(job: str) -> dict | None:
    rc, out = _g("run", "jobs", "describe", job, "--region", region(), "--format", "json")
    if rc != 0:
        return None
    try:
        return json.loads(out[out.index("{"):])
    except ValueError:
        return None


def _executions(job: str, limit: int = 3) -> list[dict]:
    rc, out = _g("run", "jobs", "executions", "list", "--job", job, "--region", region(), "--format", "json", "--limit", str(limit))
    if rc != 0:
        return []
    try:
        return json.loads(out[out.index("["):])
    except ValueError:
        return []


def _exec_summary(e: dict) -> str:
    st = e.get("status", {})
    conds = {c.get("type"): c for c in st.get("conditions", [])}
    completed = conds.get("Completed", {})
    if st.get("runningCount"):
        state = "RUNNING"
    elif st.get("cancelledCount"):
        state = "CANCELLED"
    elif completed.get("status") == "True":
        state = "FINISHED"
    elif completed.get("status") == "False":
        state = f"FAILED ({(completed.get('message') or '')[:120]})"
    else:
        state = "STARTING"
    started = st.get("startTime") or e.get("metadata", {}).get("creationTimestamp", "")
    return f"{e['metadata']['name']}: {state}, started {started[:19].replace('T', ' ')} UTC"


@tool
def secret_put(name: str, value: str) -> str:
    """Store a credential (password, API key, token) in Secret Manager under `name` so a background job can use it
    as an environment variable via job_start(secrets=...). Never paste secrets into scripts or skills — store them
    here once, then reference by name. Requires approval."""
    if not SECRET_RE.match(name):
        return "error: secret name may contain letters, digits, dashes and underscores only"
    if not value.strip():
        return "error: empty value"
    exists, _ = _g("secrets", "describe", name)
    if exists != 0:
        rc, out = _g("secrets", "create", name, "--replication-policy=automatic")
        if rc != 0:
            return f"error creating secret: {out[-500:]}"
    rc, out = _g("secrets", "versions", "add", name, "--data-file=-", stdin=value)
    if rc != 0:
        return f"error storing secret: {out[-500:]}"
    _g("secrets", "add-iam-policy-binding", name, "--member", f"serviceAccount:{jobs_sa()}", "--role", "roles/secretmanager.secretAccessor")
    return f"stored secret {name} (new version); reference it in job_start(secrets={{'ENV_VAR': '{name}'}})"


@tool
def job_start(
    name: str,
    script: str,
    env: dict | None = None,
    secrets: dict | None = None,
    pip_packages: str = "httpx",
    timeout_hours: float = 8,
    replace: bool = True,
) -> str:
    """Run a Python script as a background job on Cloud Run (up to 24 h), independent of this chat.
    `script` is complete Python 3.12 source; print progress as one JSON line per event (job_logs shows stdout).
    `env` = plain environment variables; `secrets` = {"ENV_VAR": "secret-manager-name"} (create with secret_put).
    `pip_packages` = space-separated packages installed before the run. If a job with this name is already running
    and replace=True, it is cancelled first. The script must stop itself (time check / done condition) — it is
    killed at timeout_hours. Requires approval."""
    try:
        job = _job(name)
    except ValueError as e:
        return f"error: {e}"
    if not project():
        return "error: GOOGLE_CLOUD_PROJECT is not configured"
    if len(script.encode()) > MAX_SCRIPT_BYTES:
        return f"error: script is {len(script.encode()):,} bytes; keep it under {MAX_SCRIPT_BYTES:,} (move data out, shorten comments)"
    for k, v in (env or {}).items():
        if re.search(r"(PASS|PASSWORD|SECRET|TOKEN|API_KEY|APIKEY)", str(k), re.I) and str(v).strip():
            return f"error: env var {k!r} looks like a credential — store it with secret_put and pass it via `secrets` instead"
    for k, v in (secrets or {}).items():
        if not SECRET_RE.match(str(v)):
            return f"error: bad secret name {v!r} for {k}"
        rc, _ = _g("secrets", "describe", str(v))
        if rc != 0:
            return f"error: secret {v!r} does not exist — create it with secret_put first"
        _g("secrets", "add-iam-policy-binding", str(v), "--member", f"serviceAccount:{jobs_sa()}", "--role", "roles/secretmanager.secretAccessor")

    for e in _executions(job, 5):
        if e.get("status", {}).get("runningCount"):
            if not replace:
                return f"error: {_exec_summary(e)} — pass replace=True to cancel it and start a new run"
            _g("run", "jobs", "executions", "cancel", e["metadata"]["name"], "--region", region())

    b64 = base64.b64encode(script.encode()).decode()
    env_all = {"SCRIPT_B64": b64, "TZ": _cfg("TZ", "America/New_York"), "PYTHONUNBUFFERED": "1", **{str(k): str(v) for k, v in (env or {}).items()}}
    env_arg = "^|^" + "|".join(f"{k}={v}" for k, v in env_all.items())
    pkgs = " ".join(shlex.quote(p) for p in pip_packages.split()) if pip_packages.strip() else ""
    cmd = (f"pip install -q --root-user-action=ignore {pkgs} && " if pkgs else "") + \
          'echo "$SCRIPT_B64" | base64 -d > /tmp/job.py && python /tmp/job.py'
    # gcloud's --args list splitting is comma-based; use a custom delimiter so commas in the shell command survive.
    common = ["--region", region(), "--image", IMAGE, "--service-account", jobs_sa(), "--max-retries", "0",
              "--task-timeout", f"{int(timeout_hours * 3600)}s", "--memory", "512Mi", "--cpu", "1",
              "--command", "bash", "--args", "^~~^-c~~" + cmd, "--set-env-vars", env_arg]
    if secrets:
        common += ["--set-secrets", ",".join(f"{k}={v}:latest" for k, v in secrets.items())]
    else:
        common += ["--clear-secrets"]
    verb = "update" if _describe(job) else "create"
    rc, out = _g("run", "jobs", verb, job, *common, timeout=300)
    if rc != 0:
        return f"error ({verb} job): {out[-800:]}"
    rc, out = _g("run", "jobs", "execute", job, "--region", region(), "--format", "value(metadata.name)", timeout=180)
    if rc != 0:
        return f"job configured but failed to start: {out[-600:]}"
    m = re.search(rf"({re.escape(job)}-[a-z0-9]+)", out)
    exec_name = m.group(1) if m else "?"
    return (f"started background job {name} (execution {exec_name}) at {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC; "
            f"it runs until the script exits or {timeout_hours} h. Follow it with job_logs('{name}') / job_status('{name}'); stop with job_stop('{name}').")


@tool
def job_status(name: str = "") -> str:
    """Status of background jobs: the last executions of `name` (RUNNING / FINISHED / FAILED / CANCELLED), or,
    with no name, every job that exists."""
    if name:
        try:
            job = _job(name)
        except ValueError as e:
            return f"error: {e}"
        if not _describe(job):
            return f"no background job named {name}"
        ex = _executions(job, 5)
        return f"job {name}:\n" + ("\n".join(_exec_summary(e) for e in ex) or "never executed")
    rc, out = _g("run", "jobs", "list", "--region", region(), "--format", "value(metadata.name)")
    names = [n[len(JOB_PREFIX):] for n in out.splitlines() if n.startswith(JOB_PREFIX)] if rc == 0 else []
    if not names:
        return "no background jobs"
    lines = []
    for n in names:
        ex = _executions(JOB_PREFIX + n, 1)
        lines.append(f"{n}: " + (_exec_summary(ex[0]) if ex else "never executed"))
    return "\n".join(lines)


@tool
def job_logs(name: str, limit: int = 60, filter: str = "") -> str:
    """Recent stdout/stderr lines of background job `name` (newest last), optionally filtered by substring.
    Scripts should print one JSON line per event so this stays readable."""
    try:
        job = _job(name)
    except ValueError as e:
        return f"error: {e}"
    q = f'resource.type="cloud_run_job" resource.labels.job_name="{job}" logName:"run.googleapis.com"'
    if filter:
        f = filter.replace('"', "")
        q += f' (textPayload:"{f}" OR jsonPayload.kind="{f}" OR jsonPayload.message:"{f}")'
    rc, out = _g("logging", "read", q, "--limit", str(max(1, min(limit, 300))), "--format", "json", "--freshness", "2d")
    if rc != 0:
        return f"error reading logs: {out[-500:]}"
    try:
        entries = json.loads(out[out.index("["):])
    except ValueError:
        entries = []
    lines = []
    for e in entries:  # newest first from gcloud
        ts = (e.get("timestamp") or "")[11:19]
        body = e.get("textPayload")
        if body is None and e.get("jsonPayload") is not None:
            body = json.dumps(e["jsonPayload"], separators=(",", ":"))
        if body and body.strip():
            lines.append(f"{ts} {body.strip()}")
    lines.reverse()
    return "\n".join(l[:400] for l in lines) or "no log lines yet (a new job needs ~60 s to install packages and start)"


@tool
def job_stop(name: str) -> str:
    """Cancel the running execution(s) of background job `name`. The job definition stays; job_start runs it again."""
    try:
        job = _job(name)
    except ValueError as e:
        return f"error: {e}"
    stopped = []
    for e in _executions(job, 5):
        if e.get("status", {}).get("runningCount"):
            rc, out = _g("run", "jobs", "executions", "cancel", e["metadata"]["name"], "--region", region(), timeout=240)
            stopped.append(e["metadata"]["name"] + ("" if rc == 0 else f" (cancel failed: {out[-200:]})"))
    return f"stopped {', '.join(stopped)}" if stopped else f"job {name} has no running execution"


@tool
def job_delete(name: str) -> str:
    """Delete background job `name` entirely (stops it first). Requires approval."""
    try:
        job = _job(name)
    except ValueError as e:
        return f"error: {e}"
    job_stop.func(name)
    rc, out = _g("run", "jobs", "delete", job, "--region", region(), timeout=240)
    return f"deleted job {name}" if rc == 0 else f"error: {out[-400:]}"


for _t in (job_status, job_logs, job_stop):
    _t.metadata = {"requires_approval": False}
for _t in (secret_put, job_start, job_delete):
    _t.metadata = {"requires_approval": True}

TOOLS = [secret_put, job_start, job_status, job_logs, job_stop, job_delete]
