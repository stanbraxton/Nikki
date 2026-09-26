"""Adaptive routing: whole-word hints, thread-scoped stickiness that fades, the model
asking to escalate, approvals resuming on the routed model, and the build-plan rule.

Run:  python3 tests/test_adaptive_routing.py
"""
from __future__ import annotations

import ast
import asyncio
import logging
import re
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
AGENT = (ROOT / "app" / "agent.py").read_text()
UI = (ROOT / "app" / "ui.py").read_text()
HEADLESS = (ROOT / "app" / "headless.py").read_text()
REGISTRY = (ROOT / "app" / "tools" / "__init__.py").read_text()
ESCALATION = (ROOT / "app" / "tools" / "escalation.py").read_text()
KB = (ROOT / "knowledge" / "nikki-system.md").read_text()

ENG = "anthropic:claude-opus-5-5"
CHAT = "anthropic:claude-sonnet-5"
LIGHT = "anthropic:claude-haiku-4-5"


class HumanMessage:
    def __init__(self, content: str = "") -> None:
        self.content = content


class AIMessage:
    def __init__(self, content: str = "", tool_calls: list[dict] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class ToolMessage:
    def __init__(self, content: str = "") -> None:
        self.content = content


NAMES = {"ENGINEER_TOOLS", "ROUTE_STICKY_TOOLS", "ROUTE_STICKY_TURNS", "_ENGINEER_HINTS",
         "_ENGINEER_HINT_RE", "engineer_hint", "recent_thread_tools", "recent_user_texts", "thread_messages",
         "route_model", "route_light_model", "is_auto_model", "escalation_target"}


def _ns(engineer_model: str = ENG, light_model: str = "") -> dict:
    S = types.SimpleNamespace(engineer_model=engineer_model, model=CHAT, light_model=light_model,
                              light_model_max_chars=280)
    ns: dict[str, Any] = {"settings": S, "log": logging.getLogger("t"), "re": re, "Any": Any,
                          "HumanMessage": HumanMessage, "AIMessage": AIMessage,
                          "BaseCheckpointSaver": object,
                          "text_of": lambda c: c if isinstance(c, str) else ""}
    for node in ast.parse(AGENT).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NAMES:
            exec(compile(ast.Module([node], []), "agent.py", "exec"), ns)
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", "") in NAMES for t in node.targets):
            exec(compile(ast.Module([node], []), "agent.py", "exec"), ns)
    return ns


def _call(name: str) -> dict:
    return {"name": name, "args": {}, "id": name}


def _turn(user: str, *tools: str) -> list[Any]:
    out: list[Any] = [HumanMessage(user)]
    for t in tools:
        out += [AIMessage("", [_call(t)]), ToolMessage("ok")]
    out.append(AIMessage("done"))
    return out


# ---------------------------------------------------------------- hints

def test_hints_match_whole_words_only() -> None:
    hint = _ns()["engineer_hint"]
    # Used to route to the engineer model by substring: repo-rt, commit-tee, push-back.
    for text in ("send me the weekly report", "the finance committee meets friday",
                 "I got some pushback on the sermon", "she pushed back on the date",
                 "what's the weather in Detroit today", "hi nikki", "building a sermon series on grace",
                 "tell Kelly I'll be late", "that push-back was fair", "rebuilding trust with the team"):
        assert hint(text) is None, text
    for text, want in (("open the repo", "repo"), ("deploy it", "deploy"), ("the build failed", "build"),
                       ("push to main", "push"), ("run a backtest", "backtest"),
                       ("analyze the series-fade games", "analyze"), ("what's the log-loss", "log-loss"),
                       ("logloss by month", "logloss"), ("calibration curve", "calibration"),
                       ("check out-of-sample results", "out-of-sample"), ("the win/loss record", "win/loss"),
                       ("DEPLOYED yet?", "deployed"), ("paste the stack trace", "stack trace"),
                       ("what's the log loss", "log loss"), ("win-rate by month", "win-rate"),
                       ("two tracebacks", "tracebacks"), ("open pull requests", "pull requests"),
                       ("use kelly sizing", "kelly"), ("half Kelly criterion stakes", "kelly criterion")):
        assert hint(text) == want, (text, hint(text))


def test_route_model_still_routes_analysis_and_engineering() -> None:
    rm = _ns()["route_model"]
    assert rm("run a backtest on the underdog picks") == ENG
    assert rm("what is the losing percentage for series fade games") == ENG
    assert rm("fix the convex schema") == ENG
    assert rm("summarize this report for me") is None


def test_routing_off_when_no_engineer_model() -> None:
    ns = _ns(engineer_model="")
    assert ns["route_model"]("deploy the repo", {"repo_commit_push"}) is None
    assert ns["escalation_target"](CHAT, [_call("escalate")]) is None


# ---------------------------------------------------------------- stickiness

def test_recent_tools_cover_last_three_turns_then_fade() -> None:
    ns = _ns()
    recent, rm = ns["recent_thread_tools"], ns["route_model"]
    thread = _turn("fix the login bug", "repo_read", "repo_edit")
    assert rm("thanks!", recent(thread)) == ENG  # the turn right after engineering work
    thread += _turn("thanks!")
    assert rm("and the other one?", recent(thread)) == ENG
    thread += _turn("and the other one?")
    assert rm("looks good", recent(thread)) == ENG  # still inside the last three turns
    thread += _turn("looks good")
    assert rm("what's for dinner", recent(thread)) is None  # three plain turns later it has faded
    assert "repo_read" not in recent(thread)


def test_stickiness_survives_a_reload_because_it_comes_from_the_thread() -> None:
    # A reload used to empty the session set; the thread's history is still there.
    ns = _ns()
    thread = _turn("start the smarttutor build", "repo_open", "repo_write")
    assert ns["route_model"]("continue", ns["recent_thread_tools"](thread)) == ENG


def test_recent_tools_include_the_unfinished_turn() -> None:
    recent = _ns()["recent_thread_tools"]
    thread = [HumanMessage("ship it"), AIMessage("", [_call("repo_commit_push")])]
    assert recent(thread) == {"repo_commit_push"}
    assert recent([]) == set()


def test_light_model_never_takes_a_sticky_thread() -> None:
    ns = _ns(light_model=LIGHT)
    recent, light = ns["recent_thread_tools"], ns["route_light_model"]
    assert light("thanks", set()) == LIGHT
    assert light("thanks", recent(_turn("x", "repo_read"))) is None
    assert light("thanks", recent(_turn("x", "escalate"))) is None
    assert light("send me the report", set()) == LIGHT  # 'report' is not 'repo' any more


def test_thread_messages_reads_the_checkpoint_and_never_raises() -> None:
    ns = _ns()

    class CP:
        def __init__(self, tup: Any = None, boom: bool = False) -> None:
            self.tup, self.boom = tup, boom

        async def aget_tuple(self, config: dict) -> Any:
            if self.boom:
                raise RuntimeError("db down")
            return self.tup

    msgs = [HumanMessage("hi")]
    tup = types.SimpleNamespace(checkpoint={"channel_values": {"messages": msgs}})
    run = lambda cp: asyncio.run(ns["thread_messages"](cp, {}))  # noqa: E731
    assert run(CP(tup)) == msgs
    assert run(CP(None)) == []
    assert run(CP(types.SimpleNamespace(checkpoint={"channel_values": {}}))) == []
    assert run(CP(boom=True)) == []


# ---------------------------------------------------------------- escalate

def test_escalation_switches_only_automatic_choices() -> None:
    ns = _ns(light_model=LIGHT)
    esc = ns["escalation_target"]
    calls = [_call("repo_read"), _call("escalate")]
    assert esc(CHAT, calls) == ENG
    assert esc(None, calls) == ENG  # headless default
    assert esc(LIGHT, calls) == ENG
    assert esc(ENG, calls) is None  # already there
    assert esc("openai:gpt-4.1", calls) is None  # user's explicit pick (or billing fallback) wins
    assert esc(CHAT, [_call("repo_read")]) is None
    assert esc(CHAT, []) is None


def test_escalate_keeps_the_next_turns_on_the_engineer_model() -> None:
    ns = _ns()
    thread = _turn("why is this number so high?", "escalate")
    assert ns["route_model"]("ok keep going", ns["recent_thread_tools"](thread)) == ENG


def test_escalate_tool_is_ungated_and_admin_only() -> None:
    # Admin-only: a subscriber must not be able to buy Opus-priced turns by asking for them.
    assert "escalate.metadata = {\"requires_approval\": False}" in ESCALATION
    assert "TOOLS = [escalate]" in ESCALATION
    tree = ast.parse(REGISTRY)
    fors = [n for n in ast.walk(tree) if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple)]
    admin = [e.id for e in fors[0].iter.elts]
    everyone = [e.id for e in fors[1].iter.elts]
    assert "escalation" in admin and "escalation" not in everyone


def test_analysis_wording_is_sticky_even_without_engineer_tools() -> None:
    # A hint-routed analysis turn that only used db_query must keep the engineer model
    # for its follow-up ("and last season?") and for an approval resume.
    ns = _ns()
    recent, texts, rm = ns["recent_thread_tools"], ns["recent_user_texts"], ns["route_model"]
    thread = _turn("analyze the series-fade backtest", "db_query")
    assert rm("and last season?", recent(thread), texts(thread)) == ENG
    resume = [HumanMessage("analyze the series-fade backtest"), AIMessage("", [_call("db_execute")])]
    assert rm("", recent(resume), texts(resume)) == ENG
    thread += _turn("ok") + _turn("nice") + _turn("thanks")
    assert rm("what's for dinner", recent(thread), texts(thread)) is None


# ---------------------------------------------------------------- UI wiring

def _fn(src: str, name: str) -> str:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


def test_approval_resume_routes_from_the_thread() -> None:
    body = _fn(UI, "_continue_approval")
    route_at = body.find("route_model(")
    build_at = body.find("build_graph(cp, model)")
    assert route_at != -1, "approval resume must re-route, or every push drops to the chat model"
    assert "recent_thread_tools(history), recent_user_texts(history)" in body
    assert route_at < build_at, "route before the graph is built"
    assert "if model == settings.model" in body, "an explicit model pick must still win"


def test_new_turn_routes_from_the_thread_not_the_session() -> None:
    body = _fn(UI, "_run_turn")
    assert "history = await thread_messages(cp, config)" in body
    assert "recent_user_texts(history)" in body
    assert body.find("route_model(") < body.find("build_graph(cp, model)")
    assert "model = await _drive(graph, cp, model" in body  # token usage logged on the final model
    assert 'user_session.get("recent_tools")' not in UI
    assert 'user_session.set("recent_tools"' not in UI


def test_drive_switches_on_escalate_before_rebuilding() -> None:
    body = _fn(UI, "_drive")
    at = body.find("escalation_target(model, calls)")
    assert at != -1
    rebuild = body.find("graph = build_graph(cp, model)", at)
    assert rebuild != -1, "the rebuild after the switch is what puts the next call on the new model"
    assert body.rstrip().endswith("return model")
    assert "None if rejected else escalation_target(model, calls)" in body
    assert body.find("rejected = False") < body.find("rejected = not approved") < at
    assert not re.search(r"^\s*return\s*$", body, re.M), "every exit must report the model"


def test_headless_escalates_unless_model_was_explicit() -> None:
    body = _fn(HEADLESS, "run_prompt")
    assert "explicit = model is not None" in body
    assert "None if explicit or rejected else escalation_target(model, calls)" in body
    assert body.find("recent_user_texts(history)") < body.find("build_graph(cp, model)")


# ---------------------------------------------------------------- build plan

def test_build_plan_rule_is_in_prompt_and_kb() -> None:
    assert "BUILD_PLAN.md" in AGENT and "step limit without warning" in AGENT
    assert "Multi-turn builds keep a `BUILD_PLAN.md`" in KB
    # kb_read returns the first 8,000 chars by default: the checklist must stay inside it.
    assert KB.find("## Overview") < 8000
    assert KB.find("escalate") < 8000


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        f()
        print("ok", n)
    print(f"{len(tests)} passed")
