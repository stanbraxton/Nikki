"""Tests for app/agent.py::make_model - the thinking configuration per model family.

This exists because the first version of extended thinking 400'd every Anthropic
turn on the canary, twice, with:

    "thinking.type.enabled" is not supported for this model.
    Use "thinking.type.adaptive" and "output_config.effort" ...

There were actually TWO removals on Sonnet 5 / Opus 5 / Opus 4.7+, and fixing only
the first would have failed again on the second:

  1. thinking.type "enabled" + budget_tokens  -> removed, 400
  2. temperature (and the other sampling params) -> removed, 400

So the negatives carry the weight here: it is not enough to assert that adaptive
models get `reasoning_effort`. The test must assert they DON'T get `temperature`
and DON'T get a `thinking` dict, because either one alone reproduces the outage.

Two further behaviours are load-bearing and easy to "tidy" into breakage:

  - `reasoning_effort` is set ALONE and `thinking` is left unset on purpose.
    langchain then defaults thinking to {"type": "adaptive", "display": "summarized"}.
    "summarized" is what makes the reasoning visible: the API default "omitted"
    returns thinking blocks with EMPTY text, and ui.py renders a block only when
    block["thinking"] is truthy. Setting `thinking` by hand yields a request that
    succeeds and renders nothing - indistinguishable from thinking being off.
  - an UNKNOWN model id must take the adaptive path, so a future model ID does not
    silently inherit the removed API.

Run:  python3 tests/test_thinking_config.py
"""
from __future__ import annotations

import ast
import logging
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "app" / "agent.py").read_text()


class _Captured(Exception):
    """Not an error - carries the kwargs make_model would have constructed with."""

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs


def _load(max_tokens: int, budget: int, effort: str) -> Any:
    """exec the REAL make_model source with a stub client, per CLAUDE.md section 1.

    Pulling the source out with ast keeps this honest: it tests the text that ships,
    not a paraphrase of it, and it runs without langchain installed.
    """
    tree = ast.parse(SRC)
    wanted = {"make_model", "uses_adaptive_thinking"}
    chunks, consts = [], []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            chunks.append(ast.get_source_segment(SRC, node))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_LEGACY_THINKING_PREFIXES":
                    consts.append(ast.get_source_segment(SRC, node))

    class S:
        pass

    S.model = "anthropic:claude-sonnet-5"
    S.anthropic_api_key = "sk-test"
    S.openai_api_key = "sk-test"
    S.max_tokens = max_tokens
    S.thinking_budget_tokens = budget
    S.thinking_effort = effort

    def _stub(**kwargs: Any) -> None:
        raise _Captured(kwargs)

    # Stub the provider modules so the function-local imports resolve.
    for name, attr in (("langchain_anthropic", "ChatAnthropic"),
                       ("langchain_openai", "ChatOpenAI")):
        mod = types.ModuleType(name)
        setattr(mod, attr, _stub)
        sys.modules[name] = mod

    ns: dict[str, Any] = {"settings": S(), "Any": Any,
                          "log": logging.getLogger("test"), "BaseChatModel": object}
    exec("\n".join(consts + chunks), ns)
    return ns


def kwargs_for(spec: str, *, max_tokens: int = 8192, budget: int = 4000,
               effort: str = "medium") -> dict[str, Any]:
    ns = _load(max_tokens, budget, effort)
    try:
        ns["make_model"](spec)
    except _Captured as c:
        return c.kwargs
    raise AssertionError(f"make_model({spec!r}) did not construct a client")


FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(name)


# ── adaptive models: the shape that replaced the removed one ────────────────
for spec in ("anthropic:claude-sonnet-5", "anthropic:claude-opus-5"):
    k = kwargs_for(spec)
    short = spec.split(":", 1)[1]
    check(f"{short}: sets reasoning_effort", k.get("reasoning_effort") == "medium", repr(k))
    # The two removals. Either one alone reproduces the 400.
    check(f"{short}: NO temperature (400 on this model)", "temperature" not in k, repr(k))
    check(f"{short}: NO thinking dict (400, and 'omitted' would hide it)",
          "thinking" not in k, repr(k))
    check(f"{short}: max_tokens NOT bumped (no fixed budget to fit)",
          k.get("max_tokens") == 8192, repr(k))

# ── legacy models: the fixed-budget path must survive untouched ─────────────
for spec in ("anthropic:claude-sonnet-4-5", "anthropic:claude-haiku-4-5-20251001"):
    k = kwargs_for(spec)
    short = spec.split(":", 1)[1]
    check(f"{short}: keeps enabled+budget_tokens",
          k.get("thinking") == {"type": "enabled", "budget_tokens": 4000}, repr(k))
    check(f"{short}: keeps temperature=1", k.get("temperature") == 1, repr(k))
    check(f"{short}: bumps max_tokens above the budget",
          k.get("max_tokens") == 8192 and k["max_tokens"] > 4000, repr(k))
    check(f"{short}: NO reasoning_effort", "reasoning_effort" not in k, repr(k))

# a small max_tokens must still be raised clear of the budget
k = kwargs_for("anthropic:claude-sonnet-4-5", max_tokens=2048, budget=4000)
check("legacy: max_tokens raised to budget+4096 when too small",
      k["max_tokens"] == 8096, repr(k))

# ── thinking off: neither path may leak a parameter ─────────────────────────
for spec in ("anthropic:claude-sonnet-5", "anthropic:claude-sonnet-4-5"):
    k = kwargs_for(spec, budget=0)
    short = spec.split(":", 1)[1]
    check(f"{short}: budget=0 sets no thinking parameters",
          not ({"thinking", "reasoning_effort", "temperature"} & set(k)), repr(k))

# ── an unknown/future model must default to the SUPPORTED path ─────────────
k = kwargs_for("anthropic:claude-something-9")
check("unknown model defaults to adaptive, not the removed API",
      k.get("reasoning_effort") == "medium" and "thinking" not in k, repr(k))

# ── effort is configurable and passed through verbatim ─────────────────────
k = kwargs_for("anthropic:claude-sonnet-5", effort="xhigh")
check("effort passes through", k.get("reasoning_effort") == "xhigh", repr(k))

# ── openai path carries no Anthropic-only parameters ───────────────────────
k = kwargs_for("openai:gpt-4.1")
check("openai: no thinking/effort parameters",
      not ({"thinking", "reasoning_effort"} & set(k)), repr(k))

print(f"\n{len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
