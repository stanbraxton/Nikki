"""Local file operations, sandboxed to the durable workspace directory."""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from app.config import settings

MAX_READ = 60_000


def _resolve(rel: str) -> Path:
    base = settings.workspace_dir.resolve()
    p = (base / rel.lstrip("/")).resolve()
    if base != p and base not in p.parents:
        raise ValueError(f"path escapes the workspace: {rel}")
    return p


@tool
def list_files(path: str = ".") -> str:
    """List files and folders inside Nikki's workspace. `path` is relative to the workspace root."""
    p = _resolve(path)
    if not p.exists():
        return f"(not found) {path}"
    if p.is_file():
        return f"{path} ({p.stat().st_size} bytes)"
    rows = []
    for child in sorted(p.iterdir()):
        kind = "dir " if child.is_dir() else "file"
        size = "" if child.is_dir() else f" {child.stat().st_size}B"
        rows.append(f"{kind} {child.relative_to(settings.workspace_dir)}{size}")
    return "\n".join(rows) or "(empty)"


@tool
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace. Large files are truncated at 60k characters."""
    p = _resolve(path)
    if not p.is_file():
        return f"(not a file) {path}"
    text = p.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ:
        return text[:MAX_READ] + f"\n\n[... truncated, {len(text) - MAX_READ} more chars]"
    return text


@tool
def write_file(path: str, content: str, append: bool = False) -> str:
    """Create or overwrite (or append to) a UTF-8 text file in the workspace. Parent folders are created."""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with p.open(mode, encoding="utf-8") as fh:
        fh.write(content)
    return f"wrote {len(content)} chars to {path}"


@tool
def delete_file(path: str) -> str:
    """Delete a single file from the workspace (directories are refused)."""
    p = _resolve(path)
    if p.is_dir():
        return "refusing to delete a directory"
    if not p.exists():
        return f"(not found) {path}"
    p.unlink()
    return f"deleted {path}"


for _t in (write_file, delete_file):
    _t.metadata = {"requires_approval": True}
for _t in (list_files, read_file):
    _t.metadata = {"requires_approval": False}

TOOLS = [list_files, read_file, write_file, delete_file]
