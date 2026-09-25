"""Uncommitted repo edits must survive an instance recycle.

2026-09-25: a day of unpushed Golden Picks edits in /tmp/repos vanished when Cloud Run
recycled Nikki's instance. Runs the real autosave/restore functions from
app/tools/engineer.py (extracted with ast, as CLAUDE.md prescribes) against a local
bare repository standing in for GitHub.

Run:  python3 tests/test_wip_autosave.py
"""
from __future__ import annotations

import ast
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "app" / "tools" / "engineer.py").read_text()
NAMES = {"_git", "_wip_ref", "_autosave", "_restore_wip", "_now"}


def _load(workdir: Path) -> dict:
    ns: dict = {"Path": Path, "os": os, "re": re, "subprocess": subprocess, "datetime": datetime,
                "timezone": timezone, "log": logging.getLogger("t"), "GIT_AUTHOR": ("Nikki", "n@x"),
                "WIP_PREFIX": "refs/nikki-wip/", "github_token": lambda: "TOKEN123",
                "_repo_dir": lambda repo: workdir}
    for node in ast.parse(SRC).body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            exec(compile(ast.Module([node], []), "engineer.py", "exec"), ns)
    return ns


def sh(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_edit_survives_recycle_and_push_clears_backup() -> None:
    root = Path(tempfile.mkdtemp())
    try:
        origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        seed = root / "seed"
        subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
        (seed / "a.txt").write_text("one\n")
        sh(seed, "-c", "user.name=s", "-c", "user.email=s@x", "add", "-A")
        sh(seed, "-c", "user.name=s", "-c", "user.email=s@x", "commit", "-qm", "init")
        sh(seed, "push", "-q", "origin", "main")

        work = root / "work"
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
        ns = _load(work)

        # edit + new file, then autosave
        (work / "a.txt").write_text("one\ntwo\n")
        (work / "new.txt").write_text("fresh\n")
        assert ns["_autosave"]("o/r") == ""
        assert "refs/nikki-wip/main" in sh(origin, "for-each-ref")
        assert sh(work, "status", "--porcelain").strip(), "real working tree must be untouched"
        assert not sh(work, "diff", "--cached").strip(), "real index must be untouched"

        # instance recycle: working copy gone, fresh clone
        shutil.rmtree(work)
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
        note = ns["_restore_wip"](work)
        assert "Restored unsaved work" in note, note
        assert (work / "a.txt").read_text() == "one\ntwo\n"
        assert (work / "new.txt").read_text() == "fresh\n"

        # a real commit+push leaves a clean tree -> backup deleted
        sh(work, "-c", "user.name=s", "-c", "user.email=s@x", "add", "-A")
        sh(work, "-c", "user.name=s", "-c", "user.email=s@x", "commit", "-qm", "real")
        sh(work, "push", "-q", "origin", "main")
        ns["_autosave"]("o/r")
        assert "nikki-wip" not in sh(origin, "for-each-ref")

        # restore on a clean clone with no backup is a no-op
        assert ns["_restore_wip"](work) == ""
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_restore_never_clobbers_local_edits() -> None:
    root = Path(tempfile.mkdtemp())
    try:
        origin = root / "o.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        work = root / "w"
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
        (work / "f").write_text("x\n")
        sh(work, "-c", "user.name=s", "-c", "user.email=s@x", "add", "-A")
        sh(work, "-c", "user.name=s", "-c", "user.email=s@x", "commit", "-qm", "i")
        sh(work, "push", "-q", "origin", "main")
        ns = _load(work)
        (work / "f").write_text("saved\n")
        ns["_autosave"]("o/r")
        (work / "f").write_text("newer local edit\n")
        assert ns["_restore_wip"](work) == ""
        assert (work / "f").read_text() == "newer local edit\n"
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
