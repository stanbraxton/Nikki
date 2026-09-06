"""Chat attachments -> workspace uploads/ + text rendering (kept free of Chainlit imports for testing)."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from app.config import settings
from app.tools.artifacts import safe_name
from app.tools.documents import read_any

log = logging.getLogger("nikki.uploads")


def ingest_uploads(text: str, elements: list) -> str:
    """Copy chat attachments into workspace uploads/<date>/ and append a readable rendering."""
    day_dir = settings.workspace_dir / "uploads" / time.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for el in elements:
        src = Path(getattr(el, "path", "") or "")
        if not src.is_file():
            continue
        name = safe_name(el.name or src.name, src.name)
        dest = day_dir / name
        n = 1
        while dest.exists():
            dest = day_dir / f"{dest.stem.rsplit('-', 1)[0] if n > 1 else dest.stem}-{n}{dest.suffix}"
            n += 1
        try:
            shutil.copyfile(src, dest)
        except OSError as e:
            log.warning("could not copy upload %s: %s", el.name, e)
            dest = src
        rel = dest.relative_to(settings.workspace_dir) if dest.is_relative_to(settings.workspace_dir) else dest
        try:
            body = read_any(dest)
        except Exception as e:  # noqa: BLE001
            log.exception("failed to read upload %s", el.name)
            body = f"(could not read: {type(e).__name__}: {e})"
        if len(body) > 50_000:
            body = body[:50_000] + f"\n[... truncated; read_document('{rel}') for the rest]"
        parts.append(f"[Attached file: {el.name} — saved as {rel}]\n{body}")
    if not parts:
        return text
    joined = "\n\n".join(parts)
    return f"{text}\n\n{joined}" if text else joined

