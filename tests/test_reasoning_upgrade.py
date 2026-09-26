"""Engineering/analysis turns get the stronger model and deeper thinking; pushes get reviewed.

Run:  python3 tests/test_reasoning_upgrade.py
"""
from __future__ import annotations

import ast
import json
import logging
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
AGENT = (ROOT / "app" / "agent.py").read_text()
ENG = (ROOT / "app" / "tools" / "engineer.py").read_text()


def _exec(src: str, names: set[str], ns: dict) -> dict:
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "x.py", "exec"), ns)
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", "") in names for t in node.targets):
            exec(compile(ast.Module([node], []), "x.py", "exec"), ns)
    return ns


class _Captured(Exception):
    def __init__(self, kw: dict) -> None:
        self.kw = kw


def _make_model_kwargs(spec: str) -> dict:
    mod = types.ModuleType("langchain_anthropic")

    def stub(**kw: Any) -> None:
        raise _Captured(kw)

    mod.ChatAnthropic = stub
    sys.modules["langchain_anthropic"] = mod
    S = types.SimpleNamespace(model="anthropic:claude-sonnet-5", anthropic_api_key="k", max_tokens=32000,
                              thinking_budget_tokens=4000, thinking_effort="medium",
                              engineer_model="anthropic:claude-opus-5-5", engineer_thinking_effort="high")
    ns = _exec(AGENT, {"_LEGACY_THINKING_PREFIXES", "uses_adaptive_thinking", "make_model"},
               {"settings": S, "Any": Any, "log": logging.getLogger("t"), "BaseChatModel": object})
    try:
        ns["make_model"](spec)
    except _Captured as c:
        return c.kw
    raise AssertionError("no client built")


def test_engineer_turns_think_harder() -> None:
    assert _make_model_kwargs("anthropic:claude-opus-5-5")["reasoning_effort"] == "high"
    assert _make_model_kwargs("anthropic:claude-sonnet-5")["reasoning_effort"] == "medium"
    assert _make_model_kwargs("anthropic:claude-opus-5-5")["model"] == "claude-opus-5-5"


def test_analysis_requests_are_routed() -> None:
    S = types.SimpleNamespace(engineer_model="anthropic:claude-opus-5-5")
    ns = _exec(AGENT, {"ENGINEER_TOOLS", "ROUTE_STICKY_TOOLS", "_ENGINEER_HINTS", "_ENGINEER_HINT_RE",
                       "engineer_hint", "route_model"},
               {"settings": S, "log": logging.getLogger("t"), "re": re})
    rm = ns["route_model"]
    assert rm("run a backtest on the underdog picks") == "anthropic:claude-opus-5-5"
    assert rm("what is the losing percentage for series fade games") == "anthropic:claude-opus-5-5"
    assert rm("what's the weather in Detroit today") is None  # no accidental 'roi' match
    assert rm("hi nikki") is None


def _review_ns(reply: str | Exception) -> dict:
    class Client:
        def __init__(self, **kw: Any) -> None:
            self.messages = self

        def create(self, **kw: Any) -> Any:
            if isinstance(reply, Exception):
                raise reply
            return types.SimpleNamespace(content=[types.SimpleNamespace(text=reply)])

    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=Client)
    cfg = types.ModuleType("app.config")
    cfg.settings = types.SimpleNamespace(review_model="anthropic:claude-opus-5-5", anthropic_api_key="k")
    sys.modules.setdefault("app", types.ModuleType("app"))
    sys.modules["app.config"] = cfg
    ns = {"Path": Path, "json": json, "re": re, "subprocess": subprocess, "log": logging.getLogger("t"),
          "github_token": lambda: None, "GIT_AUTHOR": ("n", "n@x"), "os": __import__("os")}
    return _exec(ENG, {"_git", "REVIEW_MAX_DIFF", "REVIEW_SYSTEM", "_review_diff", "_format_findings"}, ns)


def _staged_repo() -> Path:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / "a.ts").write_text("export const x = 1;\n")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    return d


def test_review_blocks_on_defects() -> None:
    ns = _review_ns('Here: {"blocking":[{"file":"convex/elo.ts","issue":"filters Elo on odds quality","fix":"revert"}],"notes":["ok"]}')
    blocking, notes, status = ns["_review_diff"](_staged_repo(), "msg")
    assert blocking and blocking[0]["file"] == "convex/elo.ts" and notes == ["ok"] and status == ""
    assert "convex/elo.ts" in ns["_format_findings"](blocking, notes)


def test_clean_review_passes() -> None:
    ns = _review_ns('{"blocking":[],"notes":[]}')
    assert ns["_review_diff"](_staged_repo(), "msg") == ([], [], "")


def test_review_outage_fails_open() -> None:
    ns = _review_ns(TimeoutError("down"))
    blocking, notes, status = ns["_review_diff"](_staged_repo(), "msg")
    assert blocking == [] and "unavailable" in status


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
