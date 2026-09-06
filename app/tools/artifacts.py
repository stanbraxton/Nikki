"""Shared helpers for tools that create files the user should be able to download.

A tool that saves a file for the user ends its result with `saved file: <relative path> (...)`.
The UI (ui.py `tool_result`) scans tool output for FILE_MARK lines and attaches the file to
the chat (inline image / audio player / download card). Keep that contract stable.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.config import settings

FILE_MARK = "saved file: "
_MARK_RE = re.compile(r"^saved file: (?P<rel>[^\n(]+?)\s*(?:\(|$)", re.M)


def workspace_path(rel: str, must_exist: bool = False) -> Path:
    """Resolve a workspace-relative path and refuse escapes."""
    base = settings.workspace_dir.resolve()
    p = (base / rel.lstrip("/")).resolve()
    if base != p and base not in p.parents:
        raise ValueError(f"path escapes the workspace: {rel}")
    if must_exist and not p.is_file():
        raise FileNotFoundError(f"no such file in the workspace: {rel} (use list_files to look around)")
    return p


def out_dir(kind: str) -> Path:
    d = settings.workspace_dir / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel(p: Path) -> str:
    return str(p.resolve().relative_to(settings.workspace_dir.resolve()))


def saved(p: Path, note: str = "") -> str:
    size = p.stat().st_size
    human = f"{size / 1024:.0f} KB" if size >= 1024 else f"{size} B"
    extra = f", {note}" if note else ""
    return f"{FILE_MARK}{rel(p)} ({human}{extra}). The file is attached to the chat for the user."


def marks_in(text: str) -> list[str]:
    """All workspace-relative paths announced with FILE_MARK in a tool result."""
    return [m.group("rel").strip() for m in _MARK_RE.finditer(text or "")]


def safe_name(name: str, default: str = "file") -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]+", "-", name).strip(" .-")
    return s or default
