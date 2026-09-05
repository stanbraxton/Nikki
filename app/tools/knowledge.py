"""Knowledge base: curated markdown documents Nikki consults on demand.

Two layers: the repo folder `knowledge/` (shipped in the image, source of truth, edited
through git) and a writable overlay in `KNOWLEDGE_DIR` (persistent volume) where `kb_write`
saves updates at runtime. The overlay wins on name collisions. Only the index (one line per
document) goes into the system prompt; bodies are fetched with kb_read / kb_search.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from langchain_core.tools import tool

from app.config import ROOT

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
MAX_READ = 60_000
DEFAULT_READ = 8_000


def base_dir() -> Path:
    return Path(os.environ.get("KNOWLEDGE_BASE_DIR") or (ROOT / "knowledge"))


def overlay_dir() -> Path:
    p = Path(os.environ.get("KNOWLEDGE_DIR") or (ROOT / "data" / "knowledge"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _docs() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for d in (base_dir(), overlay_dir()):
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                out[f.stem] = f
    return dict(sorted(out.items()))


def _title(path: Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line.strip():
            return line.strip()[:100]
    return path.stem


def index_text(max_chars: int = 4000) -> str:
    """Compact index for the system prompt."""
    rows = [f"{name}: {_title(p)}" for name, p in _docs().items()]
    txt = "; ".join(rows)
    return txt[:max_chars] + ("…" if len(txt) > max_chars else "")


@tool
def kb_list() -> str:
    """List the documents in Nikki's knowledge base (name, title, size)."""
    rows = [f"{n} — {_title(p)} ({p.stat().st_size} B)" for n, p in _docs().items()]
    return "\n".join(rows) or "(knowledge base is empty)"


@tool
def kb_read(name: str, max_chars: int = DEFAULT_READ, offset: int = 0) -> str:
    """Read one knowledge-base document by name (as listed by kb_list). Returns up to max_chars starting at
    offset; prefer kb_search for a specific fact and only raise max_chars / page with offset when you really
    need the whole document."""
    p = _docs().get(name.strip().removesuffix(".md"))
    if not p:
        return f"not found: {name}. Use kb_list."
    t = p.read_text(encoding="utf-8", errors="replace")
    limit = max(500, min(int(max_chars), MAX_READ))
    start = max(0, int(offset))
    chunk = t[start : start + limit]
    rest = len(t) - (start + len(chunk))
    return chunk + (f"\n[... {rest:,} more chars; call kb_read again with offset={start + len(chunk)}]" if rest > 0 else "")


@tool
def kb_search(query: str, context_lines: int = 1) -> str:
    """Search all knowledge-base documents (case-insensitive regex or plain text). Returns matching lines with
    a little context, grouped by document."""
    try:
        rx = re.compile(query, re.I)
    except re.error:
        rx = re.compile(re.escape(query), re.I)
    out: list[str] = []
    total = 0
    for name, p in _docs().items():
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        hits = [i for i, l in enumerate(lines) if rx.search(l)]
        if not hits:
            continue
        out.append(f"## {name}")
        shown: set[int] = set()
        for i in hits[:12]:
            for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
                if j not in shown:
                    shown.add(j)
                    out.append(f"{j + 1}: {lines[j]}")
            out.append("…")
            total += 1
        if total > 60:
            out.append("(more results omitted — refine the query)")
            break
    return "\n".join(out) or "(no matches)"


@tool
def kb_write(name: str, content: str, append: bool = False) -> str:
    """Create, overwrite or append to a knowledge-base document (markdown; start with a '# Title' line).
    Requires approval. Use it to record durable facts, decisions, procedures and lessons — never secrets."""
    name = name.strip().removesuffix(".md").lower()
    if not NAME_RE.match(name):
        return "rejected: name must be lowercase letters/digits/._- (max 80 chars)"
    p = overlay_dir() / f"{name}.md"
    if append and not p.exists():
        src = _docs().get(name)
        if src:
            p.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    with p.open("a" if append else "w", encoding="utf-8") as fh:
        fh.write(content if content.endswith("\n") else content + "\n")
    return f"saved {name} ({p.stat().st_size} B)"


TOOLS = [kb_list, kb_read, kb_search, kb_write]
for _t in (kb_list, kb_read, kb_search):
    _t.metadata = {"requires_approval": False}
kb_write.metadata = {"requires_approval": True}
