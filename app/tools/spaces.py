"""Spaces engine: Nikki builds and deploys independent web micro-apps to Cloud Run.

`deploy_space` writes the generated app into an isolated directory, then runs
`gcloud run deploy --source` in a background thread. Deploys take 3-6 minutes, so the
tool returns immediately with a job reference; progress is written to the `spaces`
table (queued -> building -> pushing -> provisioning -> live | failed) and surfaced by
the Spaces Gallery (`/spaces`) and the in-chat status card.

Every Space runs as the zero-role service account `SPACES_SERVICE_ACCOUNT`, so code
running inside a Space cannot touch anything else in the project. gcloud inside the
container authenticates via the Cloud Run metadata server (no key files).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool
from sqlalchemy import delete, insert, select, update

from app import persistence

log = logging.getLogger("nikki.spaces")

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
FRAMEWORKS = ("fastapi", "streamlit", "node")
STAGES = ("queued", "building", "pushing", "provisioning", "live")
DEPLOY_TIMEOUT_S = 20 * 60


def _cfg(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def project() -> str | None:
    return _cfg("SPACES_PROJECT") or _cfg("GOOGLE_CLOUD_PROJECT") or _cfg("CLOUDSDK_CORE_PROJECT")


def region() -> str:
    return _cfg("SPACES_REGION", "us-east4")  # type: ignore[return-value]


def spaces_sa() -> str | None:
    p = project()
    return _cfg("SPACES_SERVICE_ACCOUNT") or (f"nikki-spaces@{p}.iam.gserviceaccount.com" if p else None)


def gcloud_bin() -> str:
    return _cfg("GCLOUD_BIN") or shutil.which("gcloud") or "gcloud"


def workdir() -> Path:
    return Path(_cfg("SPACES_WORKDIR", "/tmp/spaces"))  # type: ignore[arg-type]


def service_name(slug: str) -> str:
    return f"nikki-space-{slug}"


# ------------------------------------------------------------------ templates
DOCKERFILES = {
    "fastapi": """FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
""",
    "streamlit": """FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
""",
    "node": """FROM node:20-slim
ENV NODE_ENV=production PORT=8080
WORKDIR /app
COPY package.json .
RUN npm install --omit=dev --no-audit --no-fund
COPY . .
EXPOSE 8080
CMD ["node", "server.js"]
""",
}

FASTAPI_DEFAULT_BACKEND = '''"""Static frontend served by FastAPI."""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="{title}")


@app.get("/healthz")
def healthz():
    return {{"ok": True}}


@app.get("/")
def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
'''

NODE_DEFAULT_BACKEND = """const express = require("express");
const path = require("path");
const app = express();
app.use(express.json());
app.get("/healthz", (_req, res) => res.json({ ok: true }));
app.use(express.static(path.join(__dirname, "public")));
app.listen(process.env.PORT || 8080, "0.0.0.0");
"""

BASE_REQUIREMENTS = {
    "fastapi": ["fastapi", "uvicorn[standard]"],
    "streamlit": ["streamlit", "pandas"],
}


def _write_app(root: Path, framework: str, title: str, frontend: str, backend: str | None, extra: list[str]) -> list[str]:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    files: list[str] = []

    def put(rel: str, content: str) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        files.append(rel)

    put("Dockerfile", DOCKERFILES[framework])
    put(".dockerignore", "__pycache__\n*.pyc\nnode_modules\n.git\n")
    if framework == "fastapi":
        put("static/index.html", frontend)
        put("main.py", backend or FASTAPI_DEFAULT_BACKEND.format(title=title.replace('"', "'")))
        put("requirements.txt", "\n".join(BASE_REQUIREMENTS["fastapi"] + extra) + "\n")
    elif framework == "streamlit":
        put("app.py", frontend)
        if backend:
            put("helpers.py", backend)
        put("requirements.txt", "\n".join(BASE_REQUIREMENTS["streamlit"] + extra) + "\n")
    else:  # node
        put("public/index.html", frontend)
        put("server.js", backend or NODE_DEFAULT_BACKEND)
        deps = {"express": "^4.19.2"}
        for spec in extra:
            name, _, ver = spec.partition("@")
            if name:
                deps[name] = ver or "*"
        put("package.json", json.dumps({"name": root.name, "private": True, "dependencies": deps}, indent=2) + "\n")
    return files


# ------------------------------------------------------------------ DB helpers (sync; used from the worker thread)
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(slug: str) -> dict | None:
    with persistence.sync_engine().connect() as c:
        r = c.execute(select(persistence.spaces).where(persistence.spaces.c.slug == slug)).first()
        return dict(r._mapping) if r else None


def _all_rows() -> list[dict]:
    with persistence.sync_engine().connect() as c:
        rows = c.execute(select(persistence.spaces).order_by(persistence.spaces.c.created_at.desc()))
        return [dict(r._mapping) for r in rows]


def _upsert_new(slug: str, title: str, framework: str, public: bool) -> None:
    with persistence.sync_engine().begin() as c:
        c.execute(delete(persistence.spaces).where(persistence.spaces.c.slug == slug))
        c.execute(
            insert(persistence.spaces).values(
                slug=slug, title=title, framework=framework, status="queued", stage_log="",
                url=None, service_name=service_name(slug), public="true" if public else "false",
                created_at=_now(), updated_at=_now(), last_error=None,
            )
        )


def _set(slug: str, **values) -> None:
    values["updated_at"] = _now()
    with persistence.sync_engine().begin() as c:
        c.execute(update(persistence.spaces).where(persistence.spaces.c.slug == slug).values(**values))


def _append_log(slug: str, line: str) -> None:
    row = _row(slug)
    if not row:
        return
    log_text = (row["stage_log"] or "") + line.rstrip() + "\n"
    _set(slug, stage_log=log_text[-6000:])


# ------------------------------------------------------------------ deploy worker
_STAGE_HINTS = (
    ("Uploading sources", "building"),
    ("Building Container", "building"),
    ("Building and deploying", "building"),
    ("Creating Revision", "provisioning"),
    ("Routing traffic", "provisioning"),
    ("Setting IAM Policy", "provisioning"),
)


def _stage_from_line(line: str, current: str) -> str:
    order = {s: i for i, s in enumerate(STAGES)}
    new = current
    for hint, stage in _STAGE_HINTS:
        if hint in line and order[stage] > order.get(new, 0):
            new = stage
    # gcloud reports the build as finished right before it creates the revision; the
    # image push happens inside that build step, so "Building Container ... done" == pushed.
    if "Building Container" in line and re.search(r"done", line, re.I) and order["pushing"] > order.get(new, 0):
        new = "pushing"
    return new


def _deploy_cmd(slug: str, src: Path, public: bool) -> list[str]:
    cmd = [
        gcloud_bin(), "run", "deploy", service_name(slug),
        "--source", str(src), "--region", region(), "--platform", "managed",
        "--port", "8080", "--memory", "512Mi", "--cpu", "1",
        "--min-instances", "0", "--max-instances", "2", "--timeout", "300",
        "--labels", "managed-by=nikki,nikki-space=true",
        "--quiet", "--format", "value(status.url)",
    ]
    if project():
        cmd += ["--project", project()]
    if spaces_sa():
        cmd += ["--service-account", spaces_sa()]
    cmd.append("--allow-unauthenticated" if public else "--no-allow-unauthenticated")
    return cmd


def _run_deploy(slug: str, src: Path, public: bool) -> None:
    cmd = _deploy_cmd(slug, src, public)
    _set(slug, status="building")
    _append_log(slug, "$ " + " ".join(c if " " not in c else repr(c) for c in cmd))
    env = {**os.environ, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"}
    if project():
        env["CLOUDSDK_CORE_PROJECT"] = project()  # type: ignore[assignment]
    stage = "building"
    stderr_tail: list[str] = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

        def pump() -> None:
            nonlocal stage
            assert proc.stderr
            buf = ""
            while True:
                ch = proc.stderr.read(1)
                if not ch:
                    break
                if ch not in "\r\n":
                    buf += ch
                    continue
                line, buf = buf.strip(), ""
                if not line:
                    continue
                stderr_tail.append(line)
                del stderr_tail[:-60]
                new = _stage_from_line(line, stage)
                if new != stage:
                    stage = new
                    _set(slug, status=stage)
                    _append_log(slug, f"[{stage}] {line[:300]}")

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        try:
            out, _ = proc.communicate(timeout=DEPLOY_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            _set(slug, status="failed", last_error=f"deploy exceeded {DEPLOY_TIMEOUT_S // 60} minutes")
            _append_log(slug, "[failed] timeout")
            return
        t.join(timeout=5)
        url = (out or "").strip().splitlines()[-1].strip() if (out or "").strip() else ""
        if proc.returncode == 0 and url.startswith("https://"):
            _set(slug, status="live", url=url, last_error=None)
            _append_log(slug, f"[live] {url}")
        else:
            err = "\n".join(stderr_tail[-25:])[-3000:] or f"gcloud exited {proc.returncode}"
            _set(slug, status="failed", last_error=err)
            _append_log(slug, "[failed] " + (err.splitlines()[-1][:300] if err else ""))
    except Exception as e:  # noqa: BLE001
        log.exception("deploy worker crashed for %s", slug)
        _set(slug, status="failed", last_error=f"{type(e).__name__}: {e}")


def start_deploy(slug: str, src: Path, public: bool) -> None:
    threading.Thread(target=_run_deploy, args=(slug, src, public), name=f"space-{slug}", daemon=True).start()


def _fmt(row: dict) -> str:
    parts = [f"{row['slug']} — {row['title']} [{row['framework']}] status={row['status']}"]
    if row.get("url"):
        parts.append(f"url={row['url']}")
    if row.get("last_error"):
        parts.append("error=" + row["last_error"].splitlines()[-1][:200])
    return " | ".join(parts)


# ------------------------------------------------------------------ tools
@tool
def deploy_space(
    app_slug: str,
    app_title: str,
    frontend_code: str,
    backend_code: str | None = None,
    framework: str = "fastapi",
    extra_requirements: str = "",
    public: bool = True,
) -> str:
    """Build and deploy an independent web micro-app ("Space") to Cloud Run and record it in the Spaces Gallery.
    Requires approval. Returns immediately; the build takes 3-6 minutes — check progress with space_status.

    framework: "fastapi" (frontend_code = complete index.html served at /; backend_code = optional main.py
    exposing `app`, e.g. for JSON APIs), "streamlit" (frontend_code = complete app.py; backend_code = optional
    helpers.py), or "node" (frontend_code = public/index.html; backend_code = optional Express server.js).
    extra_requirements: newline-separated extra pip packages (or npm packages for node).
    app_slug: lowercase letters, digits, hyphens; 2-31 chars. Redeploying an existing slug updates that Space.
    public=False deploys IAM-protected instead of open to the internet."""
    slug = app_slug.strip().lower()
    if not SLUG_RE.match(slug):
        return "rejected: app_slug must match ^[a-z][a-z0-9-]{1,30}$"
    framework = framework.strip().lower()
    if framework not in FRAMEWORKS:
        return f"rejected: framework must be one of {', '.join(FRAMEWORKS)}"
    if not frontend_code.strip():
        return "rejected: frontend_code is empty"
    if not project():
        return "rejected: Spaces engine is not configured (SPACES_PROJECT / GOOGLE_CLOUD_PROJECT unset)"
    if not shutil.which(gcloud_bin()) and not Path(gcloud_bin()).exists():
        return "rejected: gcloud CLI not available in this environment"
    row = _row(slug)
    if row and row["status"] in ("queued", "building", "pushing", "provisioning"):
        return f"a deploy for {slug} is already in progress ({row['status']}); wait for it to finish"
    extra = [x.strip() for x in extra_requirements.splitlines() if x.strip()]
    src = workdir() / slug
    files = _write_app(src, framework, app_title.strip() or slug, frontend_code, backend_code, extra)
    _upsert_new(slug, app_title.strip() or slug, framework, public)
    start_deploy(slug, src, public)
    return (
        f"queued deploy of Space '{slug}' ({framework}, files: {', '.join(files)}) as Cloud Run service "
        f"{service_name(slug)} in {region()}. Live URL will appear in the Spaces Gallery (/spaces) in ~3-6 minutes; "
        f"use space_status('{slug}') to check."
    )


@tool
def space_status(app_slug: str) -> str:
    """Current deploy status, live URL and recent build log of a Space."""
    row = _row(app_slug.strip().lower())
    if not row:
        return f"(no space named {app_slug})"
    tail = (row["stage_log"] or "").strip().splitlines()[-8:]
    return _fmt(row) + ("\n\nlog:\n" + "\n".join(tail) if tail else "")


@tool
def list_spaces() -> str:
    """List every Space Nikki has deployed, with status and live URL."""
    rows = _all_rows()
    if not rows:
        return "(no spaces deployed yet)"
    return "\n".join(_fmt(r) for r in rows)


@tool
def delete_space(app_slug: str) -> str:
    """Delete a Space's Cloud Run service and remove it from the gallery. Requires approval."""
    slug = app_slug.strip().lower()
    row = _row(slug)
    if not row:
        return f"(no space named {slug})"
    cmd = [gcloud_bin(), "run", "services", "delete", service_name(slug), "--region", region(), "--quiet"]
    if project():
        cmd += ["--project", project()]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0 and "could not be found" not in res.stderr:
        return f"delete failed: {res.stderr.strip()[-500:]}"
    with persistence.sync_engine().begin() as c:
        c.execute(delete(persistence.spaces).where(persistence.spaces.c.slug == slug))
    shutil.rmtree(workdir() / slug, ignore_errors=True)
    return f"deleted space {slug} ({service_name(slug)})"


for _t in (space_status, list_spaces):
    _t.metadata = {"requires_approval": False}
for _t in (deploy_space, delete_space):
    _t.metadata = {"requires_approval": True}

TOOLS = [deploy_space, space_status, list_spaces, delete_space]
