"""Engineer toolchain: Nikki maintains real software projects.

Repos are cloned from GitHub into an ephemeral working directory (`ENGINEER_REPOS_DIR`,
default /tmp/repos) and re-cloned transparently when the instance was recycled — GitHub is
the source of truth, the working tree is scratch. Local edits and read-only git commands
are ungated; anything that leaves the box (push, repo creation, deploys, custom domains)
requires approval.

Full builds never run inside Nikki's container (1 GiB). `deploy_app` snapshots the repo's
HEAD, uploads it to Cloud Build and runs: bun install → `convex deploy` (which builds the
frontend with the production Convex URL) → Firebase Hosting deploy. Progress lands in the
`builds` table (queued → running → success | failed) and `app_status` reads it back.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from langchain_core.tools import tool
from sqlalchemy import delete, insert, select, update

from app import persistence

log = logging.getLogger("nikki.engineer")

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
MAX_READ = 60_000
GIT_AUTHOR = ("Nikki", "nikki@nikkiaia.com")
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "ls-files", "blame", "stash", "checkout", "switch",
                 "add", "reset", "restore", "rm", "mv", "fetch", "pull", "merge", "rebase", "tag", "rev-parse"}
SECRET_ENV_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|DATABASE_URL|CREDENTIALS)", re.I)


def _cfg(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v is not None and v.strip() else default


def repos_dir() -> Path:
    p = Path(_cfg("ENGINEER_REPOS_DIR", "/tmp/repos"))  # type: ignore[arg-type]
    p.mkdir(parents=True, exist_ok=True)
    return p


def project() -> str | None:
    return _cfg("SPACES_PROJECT") or _cfg("GOOGLE_CLOUD_PROJECT")


def region() -> str:
    return _cfg("SPACES_REGION", "us-east4")  # type: ignore[return-value]


def builder_sa() -> str:
    return _cfg("BUILDER_SERVICE_ACCOUNT") or f"{_cfg('PROJECT_NUMBER', '')}-compute@developer.gserviceaccount.com"


def gcloud_bin() -> str:
    return _cfg("GCLOUD_BIN") or shutil.which("gcloud") or "gcloud"


def github_token() -> str | None:
    return _cfg("GITHUB_TOKEN")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ git plumbing
def _repo_dir(repo: str) -> Path:
    if not REPO_RE.match(repo):
        raise ValueError("repo must look like owner/name")
    return repos_dir() / repo.replace("/", "__")


def _remote(repo: str) -> str:
    tok = github_token()
    return f"https://x-access-token:{tok}@github.com/{repo}.git" if tok else f"https://github.com/{repo}.git"


def _git(repo_path: Path, *args: str, timeout: int = 120, check: bool = True) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_AUTHOR_NAME": GIT_AUTHOR[0], "GIT_AUTHOR_EMAIL": GIT_AUTHOR[1],
           "GIT_COMMITTER_NAME": GIT_AUTHOR[0], "GIT_COMMITTER_EMAIL": GIT_AUTHOR[1]}
    r = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True, timeout=timeout, env=env)
    out = (r.stdout + r.stderr).strip()
    tok = github_token()
    if tok:
        out = out.replace(tok, "***")
    if check and r.returncode != 0:
        raise RuntimeError(out[-2000:] or f"git {args[0]} failed")
    return out


def _ensure_clone(repo: str, branch: str | None = None) -> Path:
    path = _repo_dir(repo)
    if not (path / ".git").exists():
        if path.exists():
            shutil.rmtree(path)
        args = ["clone", "--depth", "50", "--no-single-branch"]
        if branch:
            args += ["--branch", branch]
        args += [_remote(repo), str(path)]
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=300, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            msg = (r.stdout + r.stderr)
            if github_token():
                msg = msg.replace(github_token() or "", "***")
            hint = "" if github_token() else " (GITHUB_TOKEN is not configured — private repos need it)"
            raise RuntimeError(f"clone failed{hint}: {msg[-800:]}")
    else:
        _git(path, "remote", "set-url", "origin", _remote(repo))
        if branch:
            _git(path, "checkout", branch, check=False)
    return path


def _resolve(repo_path: Path, rel: str) -> Path:
    p = (repo_path / rel.lstrip("/")).resolve()
    if repo_path.resolve() != p and repo_path.resolve() not in p.parents:
        raise ValueError(f"path escapes the repo: {rel}")
    if ".git" in p.relative_to(repo_path.resolve()).parts:
        raise ValueError("refusing to touch .git internals")
    return p


def _tree(repo_path: Path, sub: str = ".", depth: int = 2) -> str:
    base = _resolve(repo_path, sub)
    if base.is_file():
        return f"{sub} ({base.stat().st_size} B)"
    rows: list[str] = []
    skip = {".git", "node_modules", "dist", ".venv", "__pycache__", ".next", "build"}

    def walk(d: Path, level: int) -> None:
        try:
            children = sorted(d.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
        except PermissionError:
            return
        for c in children:
            if c.name in skip:
                rows.append("  " * level + f"{c.name}/ (skipped)")
                continue
            rel = c.relative_to(repo_path)
            if c.is_dir():
                rows.append("  " * level + f"{c.name}/")
                if level + 1 < depth:
                    walk(c, level + 1)
            else:
                rows.append("  " * level + f"{c.name}  {c.stat().st_size}B")
            if len(rows) > 400:
                rows.append("... (truncated)")
                return

    walk(base, 0)
    return "\n".join(rows) or "(empty)"


# ------------------------------------------------------------------ repo tools (ungated)
@tool
def repo_open(repo: str, branch: str = "") -> str:
    """Clone (or refresh) a GitHub repository `owner/name` into the working area and show its top-level layout.
    Call this before any other repo_* tool for that repo. Private repos need GITHUB_TOKEN configured."""
    try:
        path = _ensure_clone(repo.strip(), branch.strip() or None)
        pull = _git(path, "pull", "--ff-only", check=False)
        head = _git(path, "log", "-1", "--format=%h %s (%cr)", check=False)
        cur = _git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
        return f"{repo} @ {cur}: {head}\n{pull.splitlines()[-1] if pull else ''}\n\n{_tree(path, '.', 2)}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def repo_list(repo: str, path: str = ".", depth: int = 2) -> str:
    """List files in an opened repo (node_modules/dist/.git are skipped). `path` is relative to the repo root."""
    try:
        return _tree(_repo_dir(repo), path, max(1, min(depth, 5)))
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def repo_read(repo: str, path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a text file from an opened repo, optionally a line range (1-based, inclusive). Truncated at 60k chars."""
    try:
        p = _resolve(_repo_dir(repo), path)
        if not p.is_file():
            return f"(not a file) {path}"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if end_line and end_line >= start_line:
            lines = lines[start_line - 1:end_line]
            text = "\n".join(f"{i}: {l}" for i, l in enumerate(lines, start=start_line))
        else:
            text = "\n".join(lines)
        return text[:MAX_READ] + (f"\n[... truncated, {len(text) - MAX_READ} more chars]" if len(text) > MAX_READ else "")
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def repo_search(repo: str, pattern: str, path_glob: str = "") -> str:
    """Search an opened repo with `git grep -n` (regex). Optional `path_glob` like 'src/**/*.tsx' limits the files."""
    try:
        args = ["grep", "-n", "-I", "--max-depth=-1", "-E", pattern]
        if path_glob:
            args += ["--", path_glob]
        out = _git(_repo_dir(repo), *args, check=False)
        lines = out.splitlines()
        return "\n".join(lines[:200]) + (f"\n... {len(lines) - 200} more" if len(lines) > 200 else "") or "(no matches)"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def repo_write(repo: str, path: str, content: str) -> str:
    """Create or overwrite a file in the local working tree of an opened repo (nothing is pushed until
    repo_commit_push). Parent folders are created."""
    try:
        p = _resolve(_repo_dir(repo), path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def repo_edit(repo: str, path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    """Replace an exact text snippet inside a file of an opened repo. `old_text` must occur exactly once unless
    replace_all=True. Prefer this over repo_write for small changes to large files."""
    try:
        p = _resolve(_repo_dir(repo), path)
        if not p.is_file():
            return f"(not a file) {path}"
        src = p.read_text(encoding="utf-8")
        n = src.count(old_text)
        if n == 0:
            return "error: old_text not found (whitespace must match exactly)"
        if n > 1 and not replace_all:
            return f"error: old_text occurs {n} times; pass replace_all=True or make it more specific"
        p.write_text(src.replace(old_text, new_text) if replace_all else src.replace(old_text, new_text, 1), encoding="utf-8")
        return f"edited {path} ({n} occurrence{'s' if n > 1 else ''})"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def repo_git(repo: str, command: str) -> str:
    """Run a local git command in an opened repo, e.g. 'status', 'diff', 'log -5', 'checkout -b feature/x',
    'add -A', 'reset --hard origin/main'. Network-writing commands (push, remote, config) are not allowed here —
    use repo_commit_push."""
    try:
        parts = shlex.split(command)
        if not parts or parts[0] not in READ_ONLY_GIT:
            return f"error: allowed subcommands: {', '.join(sorted(READ_ONLY_GIT))}"
        return _git(_repo_dir(repo), *parts, check=False)[-8000:] or "(ok)"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


# ------------------------------------------------------------------ gated repo tools
@tool
def repo_commit_push(repo: str, message: str, branch: str = "") -> str:
    """Stage all changes, commit as Nikki and push to GitHub. Requires approval. `branch` defaults to the
    current branch; a new branch name is created and pushed with upstream tracking."""
    try:
        path = _repo_dir(repo)
        if not github_token():
            return "error: GITHUB_TOKEN not configured — ask the admin to add a GitHub token in Secret Manager"
        if branch:
            _git(path, "checkout", "-B", branch)
        cur = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        _git(path, "add", "-A")
        if not _git(path, "status", "--porcelain", check=False):
            return "nothing to commit"
        _git(path, "commit", "-m", message)
        out = _git(path, "push", "-u", "origin", cur, timeout=300)
        sha = _git(path, "rev-parse", "--short", "HEAD")
        return f"pushed {sha} to {repo}@{cur}\n{out[-500:]}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def github_create_repo(name: str, private: bool = True, description: str = "") -> str:
    """Create a new GitHub repository under the token owner's account. Requires approval. Returns owner/name."""
    tok = github_token()
    if not tok:
        return "error: GITHUB_TOKEN not configured"
    r = httpx.post("https://api.github.com/user/repos", timeout=30,
                   headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                   json={"name": name, "private": private, "description": description, "auto_init": False})
    if r.status_code >= 300:
        return f"error {r.status_code}: {r.text[:300]}"
    return r.json().get("full_name", name)


@tool
def repo_run(repo: str, command: str, timeout_seconds: int = 300) -> str:
    """Run a shell command inside an opened repo (typecheck, lint, unit tests, small scripts). Requires approval.
    Runs in Nikki's own container with ~1 GiB RAM and no secrets in the environment — do NOT run full frontend
    builds or `bun install` here; use deploy_app, which builds on Cloud Build."""
    try:
        path = _repo_dir(repo)
        env = {k: v for k, v in os.environ.items() if not SECRET_ENV_RE.search(k)}
        env["CI"] = "1"
        r = subprocess.run(["bash", "-lc", command], cwd=path, capture_output=True, text=True,
                           timeout=max(5, min(timeout_seconds, 900)), env=env)
        out = (r.stdout + ("\n" + r.stderr if r.stderr else "")).strip()
        return f"exit {r.returncode}\n{out[-8000:]}"
    except subprocess.TimeoutExpired:
        return "error: command timed out"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


# ------------------------------------------------------------------ GCP helpers (metadata-server auth)
def _access_token() -> str:
    import google.auth
    from google.auth.transport.requests import Request

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    return creds.token


def _gapi(method: str, url: str, **kw) -> dict:
    hdr = {"Authorization": f"Bearer {_access_token()}", "x-goog-user-project": project() or ""}
    r = httpx.request(method, url, headers=hdr, timeout=60, **kw)
    if r.status_code >= 300:
        raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:400]}")
    return r.json() if r.text else {}


def _ensure_firebase_site(site: str) -> str:
    p = project()
    base = f"https://firebasehosting.googleapis.com/v1beta1/projects/{p}/sites"
    try:
        _gapi("GET", f"{base}/{site}")
    except RuntimeError as e:
        if "404" not in str(e):
            raise
        _gapi("POST", f"{base}?siteId={site}", json={})
    return f"https://{site}.web.app"


def _store_secret(name: str, value: str) -> None:
    p = project()
    g = gcloud_bin()
    exists = subprocess.run([g, "secrets", "describe", name, "--project", p], capture_output=True, text=True).returncode == 0
    if not exists:
        subprocess.run([g, "secrets", "create", name, "--replication-policy=automatic", "--project", p, "-q"],
                       check=True, capture_output=True, text=True)
    subprocess.run([g, "secrets", "versions", "add", name, "--data-file=-", "--project", p, "-q"],
                   input=value, check=True, capture_output=True, text=True)
    subprocess.run([g, "secrets", "add-iam-policy-binding", name, "--project", p, "-q",
                    "--member", f"serviceAccount:{builder_sa()}", "--role", "roles/secretmanager.secretAccessor"],
                   check=True, capture_output=True, text=True)


# ------------------------------------------------------------------ app registry
def _app(slug: str) -> dict | None:
    with persistence.sync_engine().connect() as c:
        r = c.execute(select(persistence.apps).where(persistence.apps.c.slug == slug)).first()
        return dict(r._mapping) if r else None


def _set_app(slug: str, **values) -> None:
    values["updated_at"] = _now()
    with persistence.sync_engine().begin() as c:
        c.execute(update(persistence.apps).where(persistence.apps.c.slug == slug).values(**values))


def _build_row(bid: str) -> dict | None:
    with persistence.sync_engine().connect() as c:
        r = c.execute(select(persistence.builds).where(persistence.builds.c.id == bid)).first()
        return dict(r._mapping) if r else None


def _set_build(bid: str, **values) -> None:
    with persistence.sync_engine().begin() as c:
        c.execute(update(persistence.builds).where(persistence.builds.c.id == bid).values(**values))


def _append_build_log(bid: str, line: str) -> None:
    row = _build_row(bid)
    if row:
        _set_build(bid, log=((row["log"] or "") + line.rstrip() + "\n")[-12000:])


@tool
def register_app(
    slug: str,
    title: str,
    repo: str,
    convex_deploy_key: str = "",
    firebase_site: str = "",
    branch: str = "main",
    build_dir: str = "dist",
) -> str:
    """Register (or update) a deployable web app: a GitHub repo built by Cloud Build and hosted on Firebase
    Hosting, optionally with a Convex backend. Requires approval. `convex_deploy_key` (a *production* deploy key
    from the Convex dashboard) is stored in Secret Manager, never in the database; pass '' for static apps or to
    keep the existing key. `firebase_site` defaults to the slug (URL https://{site}.web.app)."""
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        return "rejected: slug must match ^[a-z][a-z0-9-]{1,30}$"
    if not REPO_RE.match(repo.strip()):
        return "rejected: repo must be owner/name"
    if not project():
        return "rejected: GCP project not configured"
    site = (firebase_site.strip() or slug).lower()
    try:
        url = _ensure_firebase_site(site)
        existing = _app(slug)
        secret = existing["convex_secret"] if existing else None
        if convex_deploy_key.strip():
            secret = f"nikki-app-{slug}-convex"
            _store_secret(secret, convex_deploy_key.strip())
        vals = dict(title=title.strip(), repo=repo.strip(), branch=branch.strip() or "main",
                    build_dir=build_dir.strip() or "dist", convex_secret=secret, firebase_site=site,
                    url=(existing or {}).get("url") or url, updated_at=_now())
        with persistence.sync_engine().begin() as c:
            if existing:
                c.execute(update(persistence.apps).where(persistence.apps.c.slug == slug).values(**vals))
            else:
                c.execute(insert(persistence.apps).values(slug=slug, status="registered", created_at=_now(), **vals))
        return f"registered {slug}: repo={repo} site={site} convex={'yes' if secret else 'no'} url={url}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


# ------------------------------------------------------------------ Cloud Build deploy
def _cloudbuild_yaml(app: dict) -> tuple[str, str]:
    p = project()
    site = app["firebase_site"]
    build_dir = app["build_dir"]
    steps: list[dict] = [
        {"name": "oven/bun:1", "id": "install", "entrypoint": "bash",
         "args": ["-c", "bun install --frozen-lockfile || bun install"]},
    ]
    if app.get("convex_secret"):
        steps.append({
            "name": "oven/bun:1", "id": "convex+build", "entrypoint": "bash", "secretEnv": ["CONVEX_DEPLOY_KEY"],
            "args": ["-c", "bunx convex deploy --yes --cmd 'bun run build' --cmd-url-env-var-name VITE_CONVEX_URL"],
        })
    else:
        steps.append({"name": "oven/bun:1", "id": "build", "entrypoint": "bash", "args": ["-c", "bun run build"]})
    steps.append({
        "name": "node:22", "id": "hosting", "entrypoint": "bash",
        "args": ["-c", f"npx -y firebase-tools@14 deploy --only hosting --project {p} --non-interactive --force"],
    })
    cfg: dict = {"steps": steps, "timeout": "1500s", "options": {"logging": "CLOUD_LOGGING_ONLY"}}
    if app.get("convex_secret"):
        cfg["availableSecrets"] = {"secretManager": [
            {"versionName": f"projects/{p}/secrets/{app['convex_secret']}/versions/latest", "env": "CONVEX_DEPLOY_KEY"}]}
    firebase_json = {"hosting": {"site": site, "public": build_dir, "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
                                 "rewrites": [{"source": "**", "destination": "/index.html"}]}}
    return json.dumps(cfg, indent=2), json.dumps(firebase_json, indent=2)


def _stage_dir(app: dict, bid: str) -> Path:
    src = _repo_dir(app["repo"])
    stage = Path("/tmp/builds") / bid
    stage.mkdir(parents=True, exist_ok=True)
    # bring the clone up to date with GitHub first (fast-forward only; local commits are kept)
    _git(src, "fetch", "origin", check=False)
    _git(src, "pull", "--ff-only", check=False)
    # git archive of HEAD: only committed content is deployed
    ar = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=src, capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(stage)], input=ar.stdout, check=True)
    cb, fb = _cloudbuild_yaml(app)
    (stage / "cloudbuild.yaml").write_text(cb)
    fbp = stage / "firebase.json"
    if fbp.exists():
        # keep the repo's hosting config but always pin the registered site (otherwise
        # firebase-tools deploys to the project's default site)
        try:
            cfg = json.loads(fbp.read_text())
            hosting = cfg.get("hosting")
            if isinstance(hosting, dict):
                hosting["site"] = app["firebase_site"]
                cfg["hosting"] = hosting
                fbp.write_text(json.dumps(cfg, indent=2))
        except (ValueError, OSError):
            fbp.write_text(fb)
    else:
        fbp.write_text(fb)
    (stage / ".gcloudignore").write_text("node_modules\n.git\ndist\n")
    return stage


def _run_build(bid: str, app: dict) -> None:
    slug = app["slug"]
    try:
        stage = _stage_dir(app, bid)
        _set_build(bid, status="running")
        _set_app(slug, status="building", last_build_id=bid)
        cmd = [gcloud_bin(), "builds", "submit", str(stage), "--config", str(stage / "cloudbuild.yaml"),
               "--project", project(), "--region", region(),
               "--gcs-source-staging-dir", f"gs://run-sources-{project()}-{region()}/engineer", "-q"]
        _append_build_log(bid, "$ gcloud builds submit ... (Cloud Build)")
        proc = subprocess.Popen(cmd, cwd=stage, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env={**os.environ, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"})
        assert proc.stdout
        for line in proc.stdout:
            if line.strip():
                _append_build_log(bid, line)
        rc = proc.wait(timeout=1800)
        shutil.rmtree(stage, ignore_errors=True)
        if rc != 0:
            cb_id = _cloud_build_id(_build_row(bid) or {})
            if cb_id:
                try:
                    _append_build_log(bid, "--- Cloud Build errors ---\n" + _cloud_build_errors(cb_id))
                except (subprocess.SubprocessError, OSError):
                    pass
            _set_build(bid, status="failed", finished_at=_now())
            _set_app(slug, status="failed")
            return
        url = f"https://{app['custom_domain']}" if app.get("custom_domain") else f"https://{app['firebase_site']}.web.app"
        _set_build(bid, status="success", finished_at=_now(), url=url)
        _set_app(slug, status="live", url=url)
    except Exception as e:  # noqa: BLE001
        log.exception("build %s failed", bid)
        _append_build_log(bid, f"error: {e}")
        _set_build(bid, status="failed", finished_at=_now())
        _set_app(slug, status="failed")


_CB_ID_RE = re.compile(r"/builds/([0-9a-f-]{36})")
_CB_TERMINAL = {"SUCCESS": "success", "FAILURE": "failed", "TIMEOUT": "failed", "CANCELLED": "failed",
                "INTERNAL_ERROR": "failed", "EXPIRED": "failed"}


def _cloud_build_id(b: dict) -> str | None:
    m = _CB_ID_RE.search(b.get("log") or "")
    return m.group(1) if m else None


def _cloud_build_errors(cb_id: str) -> str:
    """Error-ish lines from the Cloud Build log (so failures are diagnosable without the console)."""
    r = subprocess.run([gcloud_bin(), "builds", "log", cb_id, "--region", region(), "--project", project() or ""],
                       capture_output=True, text=True, timeout=120)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    errs = [ln for ln in lines if re.search(r"error|failed|✖|ERROR|not found|Cannot", ln)]
    return "\n".join((errs or lines)[-40:])


def _reconcile_build(b: dict | None) -> dict | None:
    """The build thread runs inside a Cloud Run instance that may be throttled or recycled before Cloud Build
    finishes, leaving the row at queued/running forever. Ask Cloud Build for the truth when we read a build."""
    if not b or b["status"] not in ("queued", "running"):
        return b
    age = (_now() - b["started_at"]).total_seconds()
    if age < 90:
        return b
    cb_id = _cloud_build_id(b)
    if not cb_id:
        if age > 1800:
            _append_build_log(b["id"], "error: build never reached Cloud Build (instance recycled?) — marked failed")
            _set_build(b["id"], status="failed", finished_at=_now())
            _set_app(b["slug"], status="failed")
            return _build_row(b["id"])
        return b
    try:
        r = subprocess.run([gcloud_bin(), "builds", "describe", cb_id, "--region", region(), "--project", project() or "",
                            "--format", "value(status)"], capture_output=True, text=True, timeout=60)
        cb_status = (r.stdout or "").strip()
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("reconcile %s: %s", b["id"], e)
        return b
    final = _CB_TERMINAL.get(cb_status)
    if not final:
        return b
    app = _app(b["slug"])
    if final == "success" and app:
        url = f"https://{app['custom_domain']}" if app.get("custom_domain") else f"https://{app['firebase_site']}.web.app"
        _append_build_log(b["id"], f"Cloud Build {cb_id}: SUCCESS (reconciled)")
        _set_build(b["id"], status="success", finished_at=_now(), url=url)
        _set_app(b["slug"], status="live", url=url)
    else:
        try:
            errs = _cloud_build_errors(cb_id)
        except (subprocess.SubprocessError, OSError):
            errs = "(could not fetch Cloud Build log)"
        _append_build_log(b["id"], f"Cloud Build {cb_id}: {cb_status}\n--- Cloud Build errors ---\n{errs}")
        _set_build(b["id"], status="failed", finished_at=_now())
        _set_app(b["slug"], status="failed")
    return _build_row(b["id"])


@tool
def deploy_app(slug: str) -> str:
    """Deploy a registered app: snapshot the repo's committed HEAD, build on Cloud Build (bun install, convex
    deploy + frontend build, Firebase Hosting deploy) and publish. Requires approval. Returns a build id
    immediately; the build takes 3-8 minutes — check with app_status / build_log, don't poll in a loop."""
    slug = slug.strip().lower()
    app = _app(slug)
    if not app:
        return f"error: no app '{slug}' — register_app first (list_apps shows what exists)"
    path = _repo_dir(app["repo"])
    if not (path / ".git").exists():
        return f"error: repo {app['repo']} is not opened — call repo_open first"
    if _git(path, "status", "--porcelain", check=False):
        return "error: working tree has uncommitted changes — commit (repo_commit_push) or discard them first"
    running = _reconcile_build(_build_row(app["last_build_id"])) if app.get("last_build_id") else None
    if running and running["status"] in ("queued", "running"):
        return f"a build for {slug} is already running ({running['id']})"
    bid = f"{slug}-{uuid.uuid4().hex[:8]}"
    with persistence.sync_engine().begin() as c:
        c.execute(insert(persistence.builds).values(id=bid, slug=slug, status="queued", log="", started_at=_now()))
    threading.Thread(target=_run_build, args=(bid, app), name=f"build-{bid}", daemon=True).start()
    return f"build {bid} queued for {slug} (HEAD {_git(path, 'rev-parse', '--short', 'HEAD', check=False)}). Check app_status('{slug}') in a few minutes."


@tool
def app_status(slug: str) -> str:
    """Status of a registered app and its latest build (stage, URL, last log lines)."""
    app = _app(slug.strip().lower())
    if not app:
        return "not found"
    out = [f"{app['slug']} — {app['title']} | repo={app['repo']}@{app['branch']} | status={app['status']} | url={app['url']}"]
    if app.get("custom_domain"):
        out.append(f"custom domain: {app['custom_domain']}")
    if app.get("last_build_id"):
        b = _reconcile_build(_build_row(app["last_build_id"]))
        if b:
            app = _app(app["slug"]) or app
            out[0] = f"{app['slug']} — {app['title']} | repo={app['repo']}@{app['branch']} | status={app['status']} | url={app['url']}"
            out.append(f"build {b['id']}: {b['status']} started {b['started_at']:%H:%M:%S}Z"
                       + (f" finished {b['finished_at']:%H:%M:%S}Z" if b.get("finished_at") else ""))
            out.append("--- log tail ---\n" + "\n".join((b["log"] or "").splitlines()[-25:]))
    return "\n".join(out)


@tool
def build_log(build_id: str, tail_lines: int = 120) -> str:
    """Full (or tail of the) log of a build id returned by deploy_app."""
    b = _reconcile_build(_build_row(build_id.strip()))
    if not b:
        return "not found"
    lines = (b["log"] or "").splitlines()
    return f"{b['id']} status={b['status']}\n" + "\n".join(lines[-max(10, tail_lines):])


@tool
def list_apps() -> str:
    """List all registered apps (repo, status, URL)."""
    with persistence.sync_engine().connect() as c:
        rows = [dict(r._mapping) for r in c.execute(select(persistence.apps).order_by(persistence.apps.c.slug))]
    return "\n".join(f"{r['slug']}: {r['title']} | {r['repo']} | {r['status']} | {r['url']}" for r in rows) or "(no apps registered)"


@tool
def add_custom_domain(slug: str, domain: str) -> str:
    """Attach a custom domain (e.g. app.example.com) to a registered app's Firebase Hosting site. Requires approval.
    Returns the DNS records the domain owner must create; the certificate is issued automatically once they resolve."""
    app = _app(slug.strip().lower())
    if not app:
        return "not found"
    domain = domain.strip().lower()
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        return "rejected: invalid domain"
    p = project()
    base = f"https://firebasehosting.googleapis.com/v1beta1/projects/{p}/sites/{app['firebase_site']}/customDomains"
    try:
        try:
            res = _gapi("GET", f"{base}/{domain}")
        except RuntimeError as e:
            if "404" not in str(e):
                raise
            res = _gapi("POST", f"{base}?customDomainId={domain}", json={})
            res = res.get("response", res)
        _set_app(app["slug"], custom_domain=domain)
        recs = []
        req = res.get("requiredDnsUpdates") or {}
        for rec in req.get("desired", []):
            for r in rec.get("records", []):
                recs.append(f"{r.get('type')} {r.get('domainName')} -> {r.get('rdata')}")
        state = res.get("hostState") or res.get("state") or "PENDING"
        return f"custom domain {domain} attached to {app['slug']} (state {state}).\nDNS records needed:\n" + ("\n".join(recs) or "(none reported yet — call add_custom_domain again in a minute to fetch them)")
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def delete_app(slug: str, delete_hosting_site: bool = False) -> str:
    """Remove an app from the registry (optionally deleting its Firebase Hosting site). The GitHub repo and
    Convex project are never touched. Requires approval."""
    app = _app(slug.strip().lower())
    if not app:
        return "not found"
    try:
        if delete_hosting_site:
            _gapi("DELETE", f"https://firebasehosting.googleapis.com/v1beta1/projects/{project()}/sites/{app['firebase_site']}")
        with persistence.sync_engine().begin() as c:
            c.execute(delete(persistence.apps).where(persistence.apps.c.slug == app["slug"]))
            c.execute(delete(persistence.builds).where(persistence.builds.c.slug == app["slug"]))
        return f"deleted {app['slug']}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


TOOLS = [repo_open, repo_list, repo_read, repo_search, repo_write, repo_edit, repo_git,
         repo_commit_push, github_create_repo, repo_run,
         register_app, deploy_app, app_status, build_log, list_apps, add_custom_domain, delete_app]
for _t in (repo_open, repo_list, repo_read, repo_search, repo_write, repo_edit, repo_git, app_status, build_log, list_apps):
    _t.metadata = {"requires_approval": False}
for _t in (repo_commit_push, github_create_repo, repo_run, register_app, deploy_app, add_custom_domain, delete_app):
    _t.metadata = {"requires_approval": True}
