"""Workspace file tools return errors as text instead of raising (CLAUDE.md §2).

2026-09-25: list_files("../repos") raised ValueError("path escapes the workspace")
and ended Nikki's turn mid-deploy. Loads the real app/tools/files.py with the
langchain `tool` decorator and settings stubbed, against a temp workspace.

Run:  python3 tests/test_file_tool_errors.py
"""
from __future__ import annotations

import inspect
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILED = 0


def ok(cond: bool, msg: str) -> None:
    global FAILED
    FAILED += 0 if cond else 1
    print(("PASS " if cond else "FAIL ") + msg)


def load(workspace: Path):
    lc = types.ModuleType("langchain_core.tools")
    lc.tool = lambda fn: fn  # the real decorator only wraps; the body is what's under test
    sys.modules.update({"langchain_core": types.ModuleType("langchain_core"), "langchain_core.tools": lc,
                        "app": types.ModuleType("app")})
    cfg = types.ModuleType("app.config")
    cfg.settings = types.SimpleNamespace(workspace_dir=workspace)
    sys.modules["app.config"] = cfg
    mod = types.ModuleType("files_under_test")
    exec(compile((ROOT / "app" / "tools" / "files.py").read_text(), "files.py", "exec"), mod.__dict__)
    return mod


with tempfile.TemporaryDirectory() as d:
    ws = Path(d) / "workspace"
    ws.mkdir()
    (ws / "notes.txt").write_text("hello")
    f = load(ws)

    for call, label in [(lambda: f.list_files("../repos"), "list_files"),
                        (lambda: f.read_file("../../etc/passwd"), "read_file"),
                        (lambda: f.write_file("../x.txt", "hi"), "write_file"),
                        (lambda: f.delete_file("../notes.txt"), "delete_file")]:
        try:
            out = call()
            ok(isinstance(out, str) and out.startswith("error:"), f"{label} escape returns text, not a raise")
        except Exception as e:  # noqa: BLE001
            ok(False, f"{label} raised {type(e).__name__}: {e}")

    ok("repo_list" in f.list_files("../repos"), "escape message points to the repo tools")
    ok(not (Path(d) / "x.txt").exists(), "an escaping write still writes nothing")
    ok("notes.txt" in f.list_files("."), "normal listing still works")
    ok(f.read_file("notes.txt") == "hello", "normal read still works")
    ok(list(inspect.signature(f.write_file).parameters) == ["path", "content", "append"],
       "signature preserved for the tool schema")
    ok("Create, overwrite, or append" in (f.write_file.__doc__ or ""), "docstring preserved for the tool description")

if FAILED:
    raise SystemExit(f"{FAILED} check(s) failed")
