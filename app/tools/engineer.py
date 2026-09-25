"""Engineer toolchain: Nikki maintains real software projects.

Repos are cloned from GitHub into an ephemeral working directory (`ENGINEER_REPOS_DIR`,
default /tmp/repos) and re-cloned transparently when the instance was recycled — GitHub is
the source of truth, the working tree is scratch. Uncommitted edits are backed up to a hidden
ref (refs/nikki-wip/<branch>) after every change and restored by repo_open, so a recycle no
longer loses work. Local edits and read-only git commands
are ungated; anything that leaves the box (push, repo creation, deploys, custom domains)
requires approval.

Full builds never run inside Nikki's container (1 GiB). `deploy_app` snapshots the repo's
HEAD, uploads it to Cloud Build and runs: bun install → `convex deploy` (which builds the
frontend with the production Convex URL) → Firebase Hosting deploy. Progress lands in the
`builds` table (queued → running → success | failed) and `app_status` reads it back.
"""
from __future__ import annotations

import asyncio
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
from app.guards import convex_lint_tree

log = logging.getLogger("nikki.engineer")

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
MAX_READ = 60_000
GIT_AUTHOR = ("Nikki", "nikki@nikkiaia.com")
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "ls-files", "blame", "stash", "checkout", "switch",
                 "add", "reset", "restore", "rm", "mv", "fetch", "pull", "merge", "rebase", "tag", "rev-parse"}
SECRET_ENV_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|DATABASE_URL|CREDENTIALS)", re.I)
TEMPLATE_REPO_DEFAULT = "stanbraxton/nikki-app-template"
TEMPLATE_PLACEHOLDERS = ("__APP_NAME__", "__APP_DESCRIPTION__", "__APP_SLUG__", "__FIREBASE_SITE__")
TEXT_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".html", ".css", ".txt", ".yaml", ".yml", ".toml", ".example"}


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


def _git(repo_path: Path, *args: str, timeout: int = 120, check: bool = True,
         extra_env: dict[str, str] | None = None) -> str:
    env = {**os.environ, **(extra_env or {}), "GIT_TERMINAL_PROMPT": "0",
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


# ------------------------------------------------------------------ work-in-progress autosave
#
# The working tree lives in ENGINEER_REPOS_DIR (/tmp/repos on Cloud Run), which is wiped
# whenever the instance is recycled. On 2026-09-25 a full day of unpushed Golden Picks
# edits vanished that way and were redone from memory - worse. So every local edit is
# snapshotted to a hidden ref on GitHub, refs/nikki-wip/<branch>. It is not a branch:
# no build trigger fires on it, it does not show in the branch list, and it never
# touches the real index, HEAD or working tree. repo_open restores it; a real push or
# a clean working tree deletes it.

WIP_PREFIX = "refs/nikki-wip/"


def _wip_ref(path: Path) -> str:
    cur = _git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "HEAD"
    safe = re.sub(r"[^A-Za-z0-9._/-]", "-", "detached" if cur == "HEAD" else cur)
    return WIP_PREFIX + safe


def _autosave(repo: str) -> str:
    """Snapshot uncommitted changes to the hidden WIP ref. Never raises; returns a short note."""
    try:
        path = _repo_dir(repo)
        if not github_token() or not (path / ".git").exists():
            return ""
        ref = _wip_ref(path)
        if not _git(path, "status", "--porcelain", check=False).strip():
            _git(path, "push", "origin", f":{ref}", timeout=60, check=False)  # nothing unsaved: clear it
            return ""
        # A throwaway index, passed to these three calls only: the real index, HEAD and
        # working tree are never touched, and concurrent tool calls are unaffected.
        tmp_index = path / ".git" / "nikki-wip-index"
        idx = {"GIT_INDEX_FILE": str(tmp_index)}
        try:
            _git(path, "read-tree", "HEAD", extra_env=idx)
            _git(path, "add", "-A", extra_env=idx)
            tree = _git(path, "write-tree", extra_env=idx).strip()
        finally:
            tmp_index.unlink(missing_ok=True)
        head = _git(path, "rev-parse", "HEAD").strip()
        sha = _git(path, "commit-tree", tree, "-p", head, "-m", f"nikki wip autosave {_now():%Y-%m-%d %H:%M} UTC").strip()
        _git(path, "push", "--force", "origin", f"{sha}:{ref}", timeout=60)
        return ""
    except Exception as e:  # noqa: BLE001 - autosave must never break an edit
        log.warning("wip autosave failed for %s: %s", repo, e)
        return "\n(warning: work-in-progress backup failed - push to a branch soon so this isn't lost)"


def _restore_wip(path: Path) -> str:
    """Re-apply an autosaved snapshot onto a clean working tree. Returns a note for repo_open."""
    if _git(path, "status", "--porcelain", check=False).strip():
        return ""  # local edits already present; never clobber them
    ref = _wip_ref(path)
    if not _git(path, "ls-remote", "origin", ref, check=False).strip():
        return ""
    _git(path, "fetch", "origin", f"{ref}:{ref}", "--force", timeout=120)
    diff = _git(path, "diff", "--binary", "HEAD", ref, check=False)
    if not diff.strip():
        return ""
    r = subprocess.run(["git", "apply", "--3way", "--whitespace=nowarn"], cwd=path, input=diff + "\n",
                       capture_output=True, text=True, timeout=120)
    when = _git(path, "log", "-1", "--format=%s", ref, check=False)
    files = _git(path, "diff", "--name-only", "HEAD", ref, check=False).splitlines()
    if r.returncode != 0:
        return (f"\n\n⚠️ Found unsaved work from an earlier session ({when}) but it no longer applies cleanly "
                f"to the current branch. It is kept on GitHub at {ref}; inspect with repo_git "
                f"'diff HEAD {ref}'.")
    return (f"\n\n♻️ Restored unsaved work from an earlier session ({when}) - "
            f"{len(files)} file(s): {', '.join(files[:10])}{' ...' if len(files) > 10 else ''}. "
            "Check it with repo_git 'diff' before continuing.")


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
        try:
            restored = _restore_wip(path) if github_token() else ""
        except Exception as e:  # noqa: BLE001
            restored = f"\n\n(warning: could not check for unsaved work from an earlier session: {e})"
        return f"{repo} @ {cur}: {head}\n{pull.splitlines()[-1] if pull else ''}{restored}\n\n{_tree(path, '.', 2)}"
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
    repo_commit_push). Parent folders are created. Keep one call under ~40,000 characters: a larger
    `content` can be cut off mid-call, which shows up as a missing-`content` error. If you get that
    error, do NOT retry the same write - split the data across several smaller files instead."""
    try:
        p = _resolve(_repo_dir(repo), path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}" + _autosave(repo)
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
        return f"edited {path} ({n} occurrence{'s' if n > 1 else ''})" + _autosave(repo)
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
        out = _git(_repo_dir(repo), *parts, check=False)[-8000:] or "(ok)"
        # These change the working tree - keep the backup in step, including deleting it when
        # the tree is now clean (e.g. after discarding changes). Not stash: a stash is local,
        # so deleting the backup on stash would lose that work on the next recycle.
        if parts[0] in {"checkout", "reset", "rm", "mv", "restore", "switch", "merge", "rebase"}:
            out += _autosave(repo)
        return out
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def _convex_sources(path: Path, only: list[str] | None = None) -> dict[str, str]:
    """{relative path: source} for convex/*.ts, plus schema.ts so the lint can check columns."""
    out: dict[str, str] = {}
    cdir = path / "convex"
    if not cdir.is_dir():
        return out
    for fp in cdir.rglob("*.ts"):
        if "_generated" in fp.parts or "node_modules" in fp.parts:
            continue
        rel = str(fp.relative_to(path))
        if only is not None and rel not in only and not rel.endswith("convex/schema.ts"):
            continue
        try:
            out[rel] = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


def _convex_gate(path: Path) -> str | None:
    """Refusal message if the staged convex/ files carry a known build breaker, else None."""
    staged = [ln for ln in _git(path, "diff", "--cached", "--name-only", check=False).splitlines()
              if ln.startswith("convex/") and ln.endswith((".ts", ".tsx"))]
    if not staged:
        return None
    findings = convex_lint_tree(_convex_sources(path, only=staged))
    findings = [f for f in findings if f.path in staged]
    if not findings:
        return None
    lines = "\n".join(f"  {f}" for f in findings[:20])
    return ("BLOCKED before push — these will fail `tsc -b` on Cloud Build:\n\n"
            f"{lines}\n\n"
            "Fix them and push again. A failed Cloud Build costs several minutes and a full "
            "container build; this check costs nothing. If you are certain a finding is wrong, "
            "call repo_commit_push again with force=True and say why.")


@tool
def repo_check(repo: str) -> str:
    """Static check of an opened repo's convex/ files for the two known build-breaking TypeScript
    patterns, using its own schema.ts to tell required columns from optional ones. Read-only, instant,
    no approval. Run it after editing anything under convex/ and before proposing a push — it costs
    nothing, and a failed Cloud Build costs minutes."""
    try:
        path = _repo_dir(repo)
        sources = _convex_sources(path)
        if not sources:
            return "(no convex/ directory — nothing to check)"
        findings = convex_lint_tree(sources)
        if not findings:
            return f"checked {len(sources)} convex file(s) — no known build-breaking patterns found"
        return (f"checked {len(sources)} file(s), {len(findings)} finding(s):\n"
                + "\n".join(str(f) for f in findings))
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


# ------------------------------------------------------------------ gated repo tools
# ------------------------------------------------------------------ pre-push review
#
# A second, stronger model reads the exact staged diff before it leaves the box. Added
# 2026-09-25 after a Golden Picks change passed repo_check but would have (a) flipped every
# game to "suspect" 20 minutes after first pitch and (b) stopped Elo from learning from most
# games. Neither is a syntax error; both are the kind of thing a careful reviewer catches.

REVIEW_MAX_DIFF = 80_000

REVIEW_SYSTEM = (
    "You are a strict senior engineer reviewing a diff before it is pushed. Find real defects only: "
    "logic that is wrong, data that will be corrupted or silently excluded, a filter applied to the "
    "wrong query, a derived value computed from the wrong inputs, broken behaviour for existing "
    "rows, missing handling for a case the change itself introduces, secrets, or anything that "
    "will fail to build or deploy. Ignore style, naming and nice-to-haves. Trace how each changed "
    "function is used before judging it. Reply with ONLY a JSON object: "
    '{"blocking": [{"file": "...", "issue": "...", "fix": "..."}], "notes": ["..."]}. '
    "`blocking` is for defects that would break production or corrupt/omit data; everything "
    "else goes in `notes`. Empty lists are a valid, good answer."
)


def _review_diff(path: Path, message: str) -> tuple[list[dict], list[str], str]:
    """(blocking, notes, status). Fails open: any error returns no findings and a status note."""
    from app.config import settings

    spec = getattr(settings, "review_model", "") or ""
    provider, _, name = spec.partition(":")
    if not spec or provider != "anthropic" or not settings.anthropic_api_key:
        return [], [], ""
    diff = _git(path, "diff", "--cached", check=False)
    if not diff.strip():
        return [], [], ""
    truncated = len(diff) > REVIEW_MAX_DIFF
    body = diff[:REVIEW_MAX_DIFF]
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=180)
        r = client.messages.create(
            model=name, max_tokens=4000, system=REVIEW_SYSTEM,
            messages=[{"role": "user", "content":
                       f"Commit message: {message}\n\n"
                       + ("(diff truncated to the first 80k characters)\n\n" if truncated else "")
                       + f"```diff\n{body}\n```"}],
        )
        text = "".join(getattr(b, "text", "") for b in r.content)
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {}
        blocking = [b for b in data.get("blocking", []) if isinstance(b, dict)]
        notes = [str(n) for n in data.get("notes", [])]
        log.info("pre-push review (%s): %d blocking, %d notes", name, len(blocking), len(notes))
        return blocking, notes, ("(review covered only the first 80k characters of the diff)" if truncated else "")
    except Exception as e:  # noqa: BLE001 - a review outage must never block a push
        log.warning("pre-push review failed: %s", e)
        return [], [], f"(pre-push review unavailable: {type(e).__name__}; pushed without it)"


def _format_findings(blocking: list[dict], notes: list[str]) -> str:
    out = []
    for b in blocking:
        out.append(f"  - {b.get('file', '?')}: {b.get('issue', '')}"
                   + (f"\n    fix: {b['fix']}" if b.get("fix") else ""))
    if notes:
        out.append("  Notes: " + "; ".join(notes[:8]))
    return "\n".join(out)


@tool
def repo_commit_push(repo: str, message: str, branch: str = "", force: bool = False,
                     override_review: bool = False) -> str:
    """Stage all changes, commit as Nikki and push to GitHub. Requires approval. `branch` defaults to the
    current branch; a new branch name is created and pushed with upstream tracking. Staged convex/ files
    are checked first for the two known build-breaking TypeScript patterns; `force=True` skips that check
    and must be justified out loud. Then a stronger reviewer model reads the staged diff; if it reports
    blocking defects the push is refused - fix them and push again. `override_review=True` pushes anyway
    and must be justified out loud to the user (e.g. the finding is demonstrably wrong)."""
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
        if not force:
            blocked = _convex_gate(path)
            if blocked:
                return blocked
        review_blocking, review_notes, review_status = _review_diff(path, message)
        if review_blocking and not override_review:
            return ("REVIEW BLOCKED — nothing was pushed. A pre-push review found defects that would "
                    "break production or corrupt data:\n\n" + _format_findings(review_blocking, review_notes)
                    + "\n\nFix them and call repo_commit_push again. If a finding is demonstrably wrong, "
                    "explain why to the user and call again with override_review=True.")
        _git(path, "commit", "-m", message)
        out = _git(path, "push", "-u", "origin", cur, timeout=300)
        _autosave(repo)  # tree is clean now, so this deletes the hidden WIP backup
        sha = _git(path, "rev-parse", "--short", "HEAD")
        extra = ""
        if review_blocking and override_review:
            extra += "\n\nPushed OVER review objections (override_review=True):\n" + _format_findings(review_blocking, [])
        if review_notes:
            extra += "\n\nReviewer notes (non-blocking) - mention any that matter to the user:\n" + _format_findings([], review_notes)
        if review_status:
            extra += "\n" + review_status
        return f"pushed {sha} to {repo}@{cur}\n{out[-500:]}{extra}"
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


def _read_secret(name: str) -> str | None:
    p = project()
    r = subprocess.run([gcloud_bin(), "secrets", "versions", "access", "latest", "--secret", name, "--project", p],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _convex_env_secret(slug: str) -> str:
    return f"nikki-app-{slug}-convex-env"


def _convex_env(slug: str) -> dict[str, str]:
    raw = _read_secret(_convex_env_secret(slug))
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _generate_auth_keys() -> tuple[str, str]:
    """Convex Auth key pair: (JWT_PRIVATE_KEY PKCS8 PEM, JWKS JSON). Mirrors @convex-dev/auth generateKeys."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pub = key.public_key().public_numbers()

    def b64u(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    jwk = {"use": "sig", "kty": "RSA", "n": b64u(pub.n, 256), "e": b64u(pub.e, 3), "alg": "RS256"}
    return pem, json.dumps({"keys": [jwk]})


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
    has_env = bool(app.get("convex_secret")) and bool(_convex_env(app["slug"]))
    if has_env:
        steps.append({
            "name": "oven/bun:1", "id": "convex-env", "entrypoint": "bash",
            "secretEnv": ["CONVEX_DEPLOY_KEY", "CONVEX_ENV_JSON"],
            "args": ["-c", "bun run .nikki/convex_env.mjs"],
        })
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
        secrets = [{"versionName": f"projects/{p}/secrets/{app['convex_secret']}/versions/latest", "env": "CONVEX_DEPLOY_KEY"}]
        if has_env:
            secrets.append({"versionName": f"projects/{p}/secrets/{_convex_env_secret(app['slug'])}/versions/latest",
                            "env": "CONVEX_ENV_JSON"})
        cfg["availableSecrets"] = {"secretManager": secrets}
    firebase_json = {"hosting": {"site": site, "public": build_dir, "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
                                 "rewrites": [{"source": "**", "destination": "/index.html"}]}}
    return json.dumps(cfg, indent=2), json.dumps(firebase_json, indent=2)


CONVEX_ENV_SCRIPT = """// Sets Convex production env vars from the CONVEX_ENV_JSON secret (idempotent).
import { spawnSync } from "node:child_process";
const env = JSON.parse(process.env.CONVEX_ENV_JSON || "{}");
for (const [name, value] of Object.entries(env)) {
  if (!/^[A-Z][A-Z0-9_]*$/.test(name)) { console.error(`skip bad name ${name}`); continue; }
  const r = spawnSync("bunx", ["convex", "env", "set", name, "--", String(value)], { stdio: ["ignore", "pipe", "pipe"], encoding: "utf8" });
  if (r.status !== 0) { console.error(`convex env set ${name} failed:\\n${(r.stderr || r.stdout || "").slice(-800)}`); process.exit(1); }
  console.log(`set ${name}`);
}
"""


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
    (stage / ".nikki").mkdir(exist_ok=True)
    (stage / ".nikki" / "convex_env.mjs").write_text(CONVEX_ENV_SCRIPT)
    return stage


# ---------------------------------------------------------------- build follow-up
# A deploy runs in a background thread and outlives the chat turn that started it,
# so the model never learns the result unless someone asks. We remember the chat
# session that queued each build and post the outcome back into that conversation
# when it finishes (live if the chat is open; saved to the thread either way).
_build_chats: dict[str, tuple] = {}  # build id -> (chainlit session, event loop)


def _capture_chat() -> tuple | None:
    """The Chainlit session + loop of the chat turn calling this tool, or None (headless run)."""
    try:
        from chainlit.context import get_context

        ctx = get_context()
        if not getattr(ctx.session, "thread_id", None):
            return None
        return ctx.session, ctx.loop
    except Exception:  # noqa: BLE001 — no chat context (scheduled/headless run)
        return None


def _build_result_text(bid: str, slug: str) -> str:
    b = _build_row(bid) or {}
    if b.get("status") == "success":
        return f"✅ Deploy finished: **{slug}** (build `{bid}`) succeeded and is live at {b.get('url') or '(no url)'}."
    log_text = b.get("log") or ""
    marker = "--- Cloud Build errors ---"
    tail = log_text.split(marker, 1)[1] if marker in log_text else log_text
    lines = [ln for ln in tail.strip().splitlines() if ln.strip()][-25:]
    return (f"❌ Deploy failed: **{slug}** (build `{bid}`). Nothing new went live.\n\n```\n"
            + "\n".join(lines) + "\n```")


def _notify_build(bid: str, slug: str) -> None:
    """Post the finished build's result into the conversation that started it (once)."""
    target = _build_chats.pop(bid, None)
    if not target:
        return
    session, loop = target
    text = _build_result_text(bid, slug)

    async def _post() -> None:
        import chainlit as cl
        from chainlit.context import init_ws_context

        init_ws_context(session)
        await cl.Message(content=text).send()

    try:
        asyncio.run_coroutine_threadsafe(_post(), loop).result(timeout=30)
    except Exception as e:  # noqa: BLE001 — a missed notification must never fail the build
        log.warning("could not post build result for %s: %s", bid, e)


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
            _notify_build(bid, slug)
            return
        url = f"https://{app['custom_domain']}" if app.get("custom_domain") else f"https://{app['firebase_site']}.web.app"
        _set_build(bid, status="success", finished_at=_now(), url=url)
        _set_app(slug, status="live", url=url)
        _notify_build(bid, slug)
    except Exception as e:  # noqa: BLE001
        log.exception("build %s failed", bid)
        _append_build_log(bid, f"error: {e}")
        _set_build(bid, status="failed", finished_at=_now())
        _set_app(slug, status="failed")
        _notify_build(bid, slug)


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
    if not lines:
        err = (r.stderr or "").strip().splitlines()[-3:]
        return "(no log lines returned; needs roles/logging.viewer on the runtime service account) " + " | ".join(err)
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
    if final == "success":
        if app:
            url = f"https://{app['custom_domain']}" if app.get("custom_domain") else f"https://{app['firebase_site']}.web.app"
            _set_app(b["slug"], status="live", url=url)
        else:  # self-deploy: the old instance (and its build thread) is replaced by the new revision
            url = _cfg("PUBLIC_URL", "")
        _append_build_log(b["id"], f"Cloud Build {cb_id}: SUCCESS (reconciled)")
        _set_build(b["id"], status="success", finished_at=_now(), url=url)
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
    immediately; the build takes 3-8 minutes. When started from a chat, the result (success + URL, or the
    build errors) is posted into this conversation automatically when it finishes — so don't poll: tell the
    user it's queued and that the result will appear here, then end your turn."""
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
    chat = _capture_chat()
    if chat:
        _build_chats[bid] = chat
    threading.Thread(target=_run_build, args=(bid, app), name=f"build-{bid}", daemon=True).start()
    head = _git(path, 'rev-parse', '--short', 'HEAD', check=False)
    if chat:
        return (f"build {bid} queued for {slug} (HEAD {head}). The result will be posted in this conversation "
                "automatically when it finishes (3-8 min) — don't poll; tell the user and end your turn.")
    return f"build {bid} queued for {slug} (HEAD {head}). Check app_status('{slug}') in a few minutes."


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



# ------------------------------------------------------------------ self deploy (Nikki's own Cloud Run service)
SELF_REPO = "stanbraxton/Nikki"


def self_service() -> str:
    return _cfg("SELF_SERVICE", "nikki")  # type: ignore[return-value]


def _run_self_build(bid: str, path: Path, tag: str) -> None:
    stage = Path("/tmp/builds") / bid
    try:
        stage.mkdir(parents=True, exist_ok=True)
        ar = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=path, capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(stage)], input=ar.stdout, check=True)
        if not (stage / "cloudbuild.yaml").exists():
            raise RuntimeError("cloudbuild.yaml missing in repo HEAD")
        _set_build(bid, status="running")
        cmd = [gcloud_bin(), "builds", "submit", str(stage), "--config", str(stage / "cloudbuild.yaml"),
               "--project", project(), "--region", region(),
               "--substitutions", f"_REGION={region()},_SERVICE={self_service()},_TAG={tag}",
               "--gcs-source-staging-dir", f"gs://run-sources-{project()}-{region()}/engineer", "-q"]
        _append_build_log(bid, f"$ gcloud builds submit ... (Cloud Build, tag {tag})")
        proc = subprocess.Popen(cmd, cwd=stage, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env={**os.environ, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"})
        assert proc.stdout
        for line in proc.stdout:
            if line.strip():
                _append_build_log(bid, line)
        rc = proc.wait(timeout=1800)
        if rc != 0:
            cb_id = _cloud_build_id(_build_row(bid) or {})
            if cb_id:
                try:
                    _append_build_log(bid, "--- Cloud Build errors ---\n" + _cloud_build_errors(cb_id))
                except (subprocess.SubprocessError, OSError):
                    pass
            _set_build(bid, status="failed", finished_at=_now())
            return
        rev = subprocess.run([gcloud_bin(), "run", "services", "describe", self_service(), "--region", region(),
                              "--project", project() or "", "--format", "value(status.latestReadyRevisionName)"],
                             capture_output=True, text=True, timeout=120).stdout.strip()
        _append_build_log(bid, f"live revision: {rev}")
        _set_build(bid, status="success", finished_at=_now(), url=_cfg("PUBLIC_URL", ""))
    except Exception as e:  # noqa: BLE001
        log.exception("self build %s failed", bid)
        _append_build_log(bid, f"error: {e}")
        _set_build(bid, status="failed", finished_at=_now())
    finally:
        shutil.rmtree(stage, ignore_errors=True)


@tool
def deploy_self() -> str:
    """Redeploy Nikki herself: snapshot the committed HEAD of stanbraxton/Nikki (must be pushed and clean), build the
    container on Cloud Build and roll a new revision of the nikki Cloud Run service (settings/secrets are kept).
    Requires approval. Returns a build id immediately; the rollout takes 4-8 minutes — follow it with build_log,
    don't poll in a loop. The running conversation may drop for a few seconds when the new revision takes traffic."""
    try:
        path = _ensure_clone(SELF_REPO, "main")
        _git(path, "fetch", "origin", check=False)
        if _git(path, "status", "--porcelain", check=False):
            return "error: working tree has uncommitted changes — repo_commit_push (or discard them) first"
        _git(path, "pull", "--ff-only", check=False)
        if _git(path, "log", "--oneline", "origin/main..HEAD", check=False):
            return "error: local commits are not pushed — repo_commit_push first so GitHub matches what gets deployed"
        sha = _git(path, "rev-parse", "--short", "HEAD")
        with persistence.sync_engine().connect() as c:
            running = [dict(r._mapping) for r in c.execute(
                select(persistence.builds).where(persistence.builds.c.slug == "nikki",
                                                 persistence.builds.c.status.in_(("queued", "running"))))]
        for b in running:
            b = _reconcile_build(b)
            if b and b["status"] in ("queued", "running"):
                return f"a self-deploy is already running ({b['id']}) — build_log('{b['id']}')"
        bid = f"nikki-{uuid.uuid4().hex[:8]}"
        tag = f"{sha}-{_now():%Y%m%d-%H%M%S}"
        with persistence.sync_engine().begin() as c:
            c.execute(insert(persistence.builds).values(id=bid, slug="nikki", status="queued", log="", started_at=_now()))
        threading.Thread(target=_run_self_build, args=(bid, path, tag), name=f"build-{bid}", daemon=True).start()
        return f"self-deploy {bid} queued (HEAD {sha}, image tag {tag}). Check build_log('{bid}') in ~5 minutes."
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


# ------------------------------------------------------------------ scaffolding from the starter template
def _replace_placeholders(root: Path, mapping: dict[str, str]) -> int:
    changed = 0
    for f in root.rglob("*"):
        if not f.is_file() or ".git" in f.parts or "node_modules" in f.parts:
            continue
        if f.suffix not in TEXT_SUFFIXES and f.name not in {".env.example", ".gitignore"}:
            continue
        try:
            txt = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        out = txt
        for k, v in mapping.items():
            out = out.replace(k, v)
        if out != txt:
            f.write_text(out, encoding="utf-8")
            changed += 1
    return changed


@tool
def scaffold_app(
    slug: str,
    title: str,
    description: str = "",
    firebase_site: str = "",
    template_repo: str = "",
) -> str:
    """Create a NEW full-stack app from the starter template (React/Vite/Tailwind + Convex Auth + multi-tenant
    orgs + Firebase Hosting). Requires approval. Creates a private GitHub repo `<owner>/<slug>`, fills in the
    app name/description, commits and pushes, creates the Firebase Hosting site, generates the Convex Auth key
    pair and stores it (with SITE_URL and ADMIN_SECRET) as the app's Convex env, and registers the app.
    Afterwards the ONLY manual step is a Convex production deploy key from the owner: call
    `register_app(slug, title, repo, convex_deploy_key=...)` with it, then `deploy_app(slug)`.
    Then open the repo and build the real domain on top (see README.md in the repo)."""
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        return "rejected: slug must match ^[a-z][a-z0-9-]{1,30}$"
    title = title.strip()
    if len(title) < 2:
        return "rejected: title required"
    tok = github_token()
    if not tok:
        return "error: GITHUB_TOKEN not configured"
    if not project():
        return "rejected: GCP project not configured"
    tpl = (template_repo.strip() or _cfg("APP_TEMPLATE_REPO") or TEMPLATE_REPO_DEFAULT)
    if not REPO_RE.match(tpl):
        return "rejected: template_repo must be owner/name"
    site = (firebase_site.strip() or slug).lower()
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    try:
        owner = httpx.get("https://api.github.com/user", headers=headers, timeout=30).json().get("login")
        if not owner:
            return "error: could not resolve GitHub owner from token"
        repo = f"{owner}/{slug}"
        if httpx.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=30).status_code == 200:
            return f"error: repo {repo} already exists — pick another slug or use repo_open/register_app"

        # 1) template → fresh working tree
        work = _repo_dir(repo)
        if work.exists():
            shutil.rmtree(work)
        r = subprocess.run(["git", "clone", "--depth", "1", _remote(tpl), str(work)], capture_output=True, text=True,
                           timeout=300, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            return f"error: template clone failed: {(r.stdout + r.stderr).replace(tok, '***')[-600:]}"
        shutil.rmtree(work / ".git")
        desc = description.strip() or f"{title} — built on the Nikki app platform."
        n = _replace_placeholders(work, {"__APP_NAME__": title, "__APP_DESCRIPTION__": desc,
                                         "__APP_SLUG__": slug, "__FIREBASE_SITE__": site})

        # 2) new private repo + first push
        cr = httpx.post("https://api.github.com/user/repos", headers=headers, timeout=30,
                        json={"name": slug, "private": True, "description": desc[:300], "auto_init": False})
        if cr.status_code >= 300:
            return f"error creating repo {cr.status_code}: {cr.text[:300]}"
        _git(work, "init", "-q", "-b", "main")
        _git(work, "remote", "add", "origin", _remote(repo))
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", f"{title}: scaffold from {tpl}")
        _git(work, "push", "-u", "origin", "main", timeout=300)
        sha = _git(work, "rev-parse", "--short", "HEAD")

        # 3) hosting site + Convex env (auth keys, site url, admin secret)
        url = _ensure_firebase_site(site)
        pem, jwks = _generate_auth_keys()
        env = {"JWT_PRIVATE_KEY": pem, "JWKS": jwks, "SITE_URL": url, "ADMIN_SECRET": uuid.uuid4().hex}
        resend = _cfg("RESEND_API_KEY")
        if resend:
            env["RESEND_API_KEY"] = resend
        _store_secret(_convex_env_secret(slug), json.dumps(env))

        # 4) registry row (no Convex key yet)
        vals = dict(title=title, repo=repo, branch="main", build_dir="dist", convex_secret=None,
                    firebase_site=site, url=url, updated_at=_now())
        with persistence.sync_engine().begin() as c:
            if _app(slug):
                c.execute(update(persistence.apps).where(persistence.apps.c.slug == slug).values(**vals))
            else:
                c.execute(insert(persistence.apps).values(slug=slug, status="registered", created_at=_now(), **vals))
        missing = "" if resend else " RESEND_API_KEY is not set — add it with set_convex_env before deploying or emails will not send."
        return (f"scaffolded {repo} @ {sha} ({n} files templated) → site {url}\n"
                f"Convex env prepared: JWT_PRIVATE_KEY, JWKS, SITE_URL, ADMIN_SECRET{', RESEND_API_KEY' if resend else ''}.\n"
                f"NEXT: ask the owner for a Convex *production* deploy key (Convex dashboard → new project '{slug}' → "
                f"Production → Settings → Deploy Keys), then register_app('{slug}', '{title}', '{repo}', "
                f"convex_deploy_key=<key>) and deploy_app('{slug}').{missing}")
    except Exception as e:  # noqa: BLE001
        return f"error: {str(e).replace(tok, '***')}"


@tool
def set_convex_env(slug: str, name: str, value: str) -> str:
    """Set (or clear with value='') a Convex production environment variable for a registered app — API keys,
    SITE_URL, EMAIL_FROM, etc. Requires approval. Values are stored in Secret Manager and applied on the next
    deploy_app (Cloud Build runs `convex env set` before deploying). Never echo secret values back."""
    slug = slug.strip().lower()
    name = name.strip()
    if not _app(slug):
        return f"error: no app '{slug}'"
    if not re.match(r"^[A-Z][A-Z0-9_]*$", name):
        return "rejected: name must be UPPER_SNAKE_CASE"
    try:
        env = _convex_env(slug)
        if value == "":
            env.pop(name, None)
        else:
            env[name] = value
        _store_secret(_convex_env_secret(slug), json.dumps(env))
        return f"{slug}: {name} {'cleared' if value == '' else 'set'} ({len(env)} vars staged: {', '.join(sorted(env))}). Applied on next deploy_app."
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@tool
def list_convex_env(slug: str) -> str:
    """List the NAMES of Convex env vars staged for an app (values are never shown)."""
    env = _convex_env(slug.strip().lower())
    return ", ".join(sorted(env)) if env else "(none)"


TOOLS = [repo_open, repo_list, repo_read, repo_search, repo_write, repo_edit, repo_git, repo_check,
         repo_commit_push, github_create_repo, repo_run,
         scaffold_app, set_convex_env, list_convex_env,
         register_app, deploy_app, deploy_self, app_status, build_log, list_apps, add_custom_domain, delete_app]
for _t in (repo_open, repo_list, repo_read, repo_search, repo_write, repo_edit, repo_git, repo_check,
           app_status, build_log, list_apps, list_convex_env):
    _t.metadata = {"requires_approval": False}
for _t in (repo_commit_push, github_create_repo, repo_run, scaffold_app, set_convex_env, register_app, deploy_app,
           deploy_self, add_custom_domain, delete_app):
    _t.metadata = {"requires_approval": True}
