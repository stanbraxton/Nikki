"""Local file operations, sandboxed to the durable workspace directory."""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from app.config import settings

MAX_READ = 12_000      # was 60_000; one read_file was returning 60,034 chars
MAX_WRITE = 4_000      # per-call write ceiling, well inside any model output budget


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
    """Read a UTF-8 text file from the workspace. Large files are truncated at 12k characters."""
    p = _resolve(path)
    if not p.is_file():
        return f"(not a file) {path}"
    text = p.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ:
        return text[:MAX_READ] + f"\n\n[... truncated, {len(text) - MAX_READ} more chars]"
    return text


@tool
def write_file(path: str, content: str | None = None, append: bool = False) -> str:
    """Create, overwrite, or append to a UTF-8 text file in the workspace.

    Writes are capped at 4000 characters per call. To write a larger file, make
    the first call with append=False, then keep calling with append=True until
    the whole body is written. Split on natural boundaries (a complete JSON
    array element, a whole markdown section); the file is only read back once
    it is complete, so intermediate chunks may be syntactically incomplete.
    Parent folders are created automatically.
    """
    # `content` is Optional on purpose. While it was required, a dropped or
    # oversized body was rejected by schema validation before this function
    # ran, and the model got "content: Field required" -- which it cannot act
    # on, so it retried the identical call until the round cap killed it.
    if content is None:
        return (
            "ERROR: the `content` argument did not arrive. This means the file body "
            "was too large to survive as a single tool argument.\n"
            "\n"
            "Do NOT retry the same call. Write the file in pieces instead:\n"
            f"  1. write_file(path=..., content=<first {MAX_WRITE} chars or fewer>, append=False)\n"
            "  2. write_file(path=..., content=<next chunk>, append=True)\n"
            "  3. repeat until the whole body is written\n"
        )

    if len(content) > MAX_WRITE:
        needed = (len(content) // MAX_WRITE) + 1
        return (
            f"ERROR: content is {len(content)} chars, over the {MAX_WRITE}-char per-call limit.\n"
            f"Split it across about {needed} calls: the first with append=False, "
            f"the rest with append=True."
        )

    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with p.open(mode, encoding="utf-8") as fh:
        fh.write(content)
    total = p.stat().st_size
    verb = "appended" if append else "wrote"
    return (f"{verb} {len(content)} chars to {path} (file is now {total} bytes). "
            f"Continue with append=True if the file is not complete.")


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
