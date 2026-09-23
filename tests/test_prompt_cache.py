"""Tests for the prompt-cache fixes in app/agent.py.

Anthropic's prompt cache only hits when a request starts with exactly the same bytes as
an earlier one. Two things in Nikki broke that on nearly every user turn:

  1. The system prompt carried the clock to the minute (HH:MM). It sits in front of the
     whole conversation, so a new minute invalidated the cached history - every turn
     paid full price for all of it.
  2. trim_history, once a thread was over budget, moved its cut by one turn on every
     message. The first message sent changed each turn, so again: no cache hit.

The tests pin the fixes: the volatile system block holds no time of day, the timestamp on
user messages is stable across calls, and the trim cut does not move while a thread grows
a little.

Run:  python3 tests/test_prompt_cache.py
"""
from __future__ import annotations

import ast
import logging
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "app" / "agent.py").read_text()


class _Msg:
    def __init__(self, content: Any, id: str | None = None) -> None:
        self.content, self.id = content, id

    def model_copy(self, update: dict) -> "_Msg":
        m = type(self)(self.content, self.id)
        m.__dict__.update(update)
        return m


class HumanMessage(_Msg):
    pass


class AIMessage(_Msg):
    tool_calls: list = []


class ToolMessage(_Msg):
    pass


def _load(*names: str) -> dict[str, Any]:
    tree = ast.parse(SRC)
    ns: dict[str, Any] = {
        "Any": Any, "HumanMessage": HumanMessage, "AIMessage": AIMessage, "ToolMessage": ToolMessage,
        "log": logging.getLogger("test"), "_STAMPS": {},
        "settings": types.SimpleNamespace(history_budget_tokens=1000),
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "agent.py", "exec"), ns)
    return ns


def _thread(turns: int, chars: int = 400) -> list[_Msg]:
    out: list[_Msg] = []
    for i in range(turns):
        out.append(HumanMessage("u" * chars, id=f"h{i}"))
        out.append(AIMessage("a" * chars, id=f"a{i}"))
    return out


def test_system_prompt_has_no_clock() -> None:
    body = SRC[SRC.index("def system_prompt_parts"):SRC.index("def system_message")]
    volatile_line = next(l for l in body.splitlines() if l.strip().startswith("volatile ="))
    assert "%H" not in volatile_line and "%M" not in volatile_line, volatile_line


def test_trim_cut_is_stable_as_thread_grows() -> None:
    ns = _load("text_of", "_approx_tokens", "trim_history")
    trim = ns["trim_history"]
    firsts = []
    for n in range(8, 20):
        kept = trim(_thread(n), 1000)
        assert sum(ns["_approx_tokens"](m) for m in kept) <= 1000
        assert isinstance(kept[0], HumanMessage)
        firsts.append(kept[0].id)
    changes = sum(1 for a, b in zip(firsts, firsts[1:]) if a != b)
    # 11 growth steps of one turn each; the old per-turn trim moved the cut on all 11.
    # Checkpoints every budget//2 tokens move it once per ~half-budget of growth.
    assert changes <= 6, (changes, firsts)


def test_trim_under_budget_is_untouched() -> None:
    ns = _load("text_of", "_approx_tokens", "trim_history")
    msgs = _thread(2)
    assert ns["trim_history"](msgs, 100000) is msgs


def test_trim_keeps_latest_turn_even_if_huge() -> None:
    ns = _load("text_of", "_approx_tokens", "trim_history")
    msgs = _thread(5) + [HumanMessage("x" * 20000, id="big")]
    kept = ns["trim_history"](msgs, 1000)
    assert kept[-1].id == "big" and isinstance(kept[0], HumanMessage)


def test_stamp_is_stable_across_calls_and_turns() -> None:
    ns = _load("stamp_latest_user_message")
    stamp = ns["stamp_latest_user_message"]
    t1 = [HumanMessage("hi", id="h0")]
    a = stamp(t1)
    ns["_STAMPS"]["h0"] = ns["_STAMPS"]["h0"]  # remembered
    b = stamp(t1)
    assert a[0].content == b[0].content and a[0].content.startswith("[Current time:")
    # Next turn: the earlier message keeps its original stamp, so the prefix is unchanged.
    t2 = t1 + [AIMessage("hello", id="a0"), HumanMessage("more", id="h1")]
    c = stamp(t2)
    assert c[0].content == a[0].content
    assert c[2].content.startswith("[Current time:")
    assert t1[0].content == "hi"  # stored message never mutated


def test_stamp_skips_unknown_older_messages() -> None:
    ns = _load("stamp_latest_user_message")
    msgs = [HumanMessage("old", id="x"), AIMessage("r", id="y"), HumanMessage("new", id="z")]
    out = ns["stamp_latest_user_message"](msgs)
    assert out[0].content == "old" and out[2].content.startswith("[Current time:")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
