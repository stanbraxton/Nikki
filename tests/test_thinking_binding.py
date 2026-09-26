"""Tests for the thinking-signature fix and the external-apps guard.

Production failure (2026-09-26), on an ordinary chat turn:

    400 invalid_request_error: messages.5.content.16: Invalid `signature` in `thinking`
    block. The block is bound to a different conversation. ... Content before this block
    differs from when it was created, first at messages.8.content.1.

The API binds each thinking block to the exact conversation before it. Nikki rewrites
earlier history on every call (trim_history, compact_old_tool_results, time stamps), so
old thinking blocks stop matching. Two fixes, both pinned here:

  1. strip_old_thinking drops thinking from finished turns (the API ignores it anyway)
     and keeps it inside the current turn, where a tool-use loop needs it.
  2. The time stamp on a user message is stored on the message (new_user_message), so a
     turn resumed on another instance or after a restart renders it byte-for-byte the same.

Plus: engineering tools refuse to deploy/push/configure/delete apps managed outside Nikki.

Run:  python3 tests/test_thinking_binding.py
"""
from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
AGENT = (ROOT / "app" / "agent.py").read_text()
ENGINEER = (ROOT / "app" / "tools" / "engineer.py").read_text()


class _Msg:
    def __init__(self, content: Any, id: str | None = None, additional_kwargs: dict | None = None) -> None:
        self.content, self.id = content, id
        self.additional_kwargs = additional_kwargs or {}
        self.tool_calls: list = []

    def model_copy(self, update: dict) -> "_Msg":
        m = type(self)(self.content, self.id, dict(self.additional_kwargs))
        m.tool_calls = self.tool_calls
        m.__dict__.update(update)
        return m


class HumanMessage(_Msg):
    pass


class AIMessage(_Msg):
    pass


class ToolMessage(_Msg):
    pass


def _load(src: str, *names: str, **extra: Any) -> dict[str, Any]:
    ns: dict[str, Any] = {"Any": Any, "HumanMessage": HumanMessage, "AIMessage": AIMessage,
                          "ToolMessage": ToolMessage, "log": logging.getLogger("test"),
                          "_STAMPS": {}, "os": os, **extra}
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "src.py", "exec"), ns)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets):
            exec(compile(ast.Module([node], []), "src.py", "exec"), ns)
    return ns


THINK = {"type": "thinking", "thinking": "hmm", "signature": "sig"}
REDACTED = {"type": "redacted_thinking", "data": "xxx"}
TEXT = {"type": "text", "text": "answer"}
TOOL_USE = {"type": "tool_use", "id": "t1", "name": "x", "input": {}}


def _strip() -> Any:
    return _load(AGENT, "strip_old_thinking", "_THINKING_BLOCK_TYPES")["strip_old_thinking"]


def test_old_turn_thinking_is_removed() -> None:
    msgs = [HumanMessage("q1"), AIMessage([THINK, REDACTED, TEXT]), HumanMessage("q2")]
    out = _strip()(msgs)
    assert out[1].content == [TEXT], out[1].content


def test_current_turn_thinking_is_kept() -> None:
    # Inside a tool-use loop the API requires the thinking block back, untouched.
    current = AIMessage([THINK, TOOL_USE])
    msgs = [HumanMessage("q1"), AIMessage([THINK, TEXT]), HumanMessage("q2"), current,
            ToolMessage("result")]
    out = _strip()(msgs)
    assert out[3] is current and out[3].content == [THINK, TOOL_USE]
    assert out[1].content == [TEXT]


def test_messages_without_thinking_are_untouched() -> None:
    msgs = [HumanMessage("q1"), AIMessage("plain string"), AIMessage([TEXT, TOOL_USE]),
            ToolMessage("r"), HumanMessage("q2")]
    out = _strip()(msgs)
    assert all(a is b for a, b in zip(out, msgs)), "copies made where nothing changed"


def test_stored_checkpoint_is_not_mutated() -> None:
    old = AIMessage([THINK, TEXT])
    _strip()([HumanMessage("q1"), old, HumanMessage("q2")])
    assert old.content == [THINK, TEXT]


def test_strip_runs_in_prepare_messages() -> None:
    body = AGENT[AGENT.index("def _prepare_messages"):AGENT.index("def _pre_model_hook")]
    assert "strip_old_thinking(" in body


def _stamp_ns() -> dict[str, Any]:
    return _load(AGENT, "stamp_latest_user_message", "time_stamp", "STAMP_KWARG")


def test_stored_stamp_wins_over_process_memory() -> None:
    ns = _stamp_ns()
    stored = "[Current time: Saturday 2026-09-26 15:00 UTC]"
    m = HumanMessage("hi", id="h1", additional_kwargs={ns["STAMP_KWARG"]: stored})
    ns["_STAMPS"]["h1"] = "[Current time: something else]"
    out = ns["stamp_latest_user_message"]([m])
    assert out[0].content == f"{stored}\nhi", out[0].content


def test_stored_stamp_is_identical_on_a_fresh_instance() -> None:
    # Two independent namespaces = two Cloud Run instances with empty memos.
    stored = "[Current time: Saturday 2026-09-26 15:00 UTC]"
    a, b = _stamp_ns(), _stamp_ns()
    m = HumanMessage([TEXT], id="h1", additional_kwargs={a["STAMP_KWARG"]: stored})
    out_a = a["stamp_latest_user_message"]([m])
    out_b = b["stamp_latest_user_message"]([m])
    assert out_a[0].content == out_b[0].content


def test_new_user_message_is_used_for_input() -> None:
    for path in ("app/ui.py", "app/headless.py"):
        src = (ROOT / path).read_text()
        assert "new_user_message(" in src, path
        assert "HumanMessage(content=" not in src, f"{path} still builds unstamped user messages"


def _guard(env: str | None = None) -> Any:
    def _cfg(name: str, default: str | None = None) -> str | None:
        v = env if name == "NIKKI_EXTERNAL_APPS" else None
        return v if v is not None and v.strip() else default
    ns = _load(ENGINEER, "external_apps", "_external_refusal", "_DEFAULT_EXTERNAL_APPS", _cfg=_cfg)
    return ns["_external_refusal"]


def test_smarttutor_is_refused_by_slug_and_repo() -> None:
    refuse = _guard()
    for name in ("smarttutor-ai", "SmartTutor-AI", "stanbraxton/smarttutor-ai",
                 "https://github.com/stanbraxton/smarttutor-ai.git"):
        assert refuse(name) and "refused" in refuse(name), name


def test_other_apps_are_not_refused() -> None:
    refuse = _guard()
    for name in ("golden-picks", "stanbraxton/golden-picks", "stanbraxton/Nikki",
                 "smarttutor", "ai-tutor-platform", ""):
        assert refuse(name) is None, name


def test_env_override_replaces_the_list() -> None:
    refuse = _guard("golden-market, wellcollar")
    assert refuse("wellcollar") and refuse("golden-market")
    assert refuse("smarttutor-ai") is None


def test_write_tools_are_guarded() -> None:
    for fn in ("repo_commit_push", "deploy_app", "add_custom_domain", "delete_app", "set_convex_env"):
        start = ENGINEER.index(f"def {fn}(")
        body = ENGINEER[start:ENGINEER.index("\n@tool", start)] if "\n@tool" in ENGINEER[start:] else ENGINEER[start:]
        assert "_external_refusal(" in body, fn


def test_read_only_tools_are_not_guarded() -> None:
    for fn in ("app_status", "list_apps", "repo_read", "build_log"):
        start = ENGINEER.index(f"def {fn}(")
        body = ENGINEER[start:ENGINEER.index("\n@tool", start)]
        assert "_external_refusal(" not in body, fn


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok {name}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {e!r}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
