"""The round-limit message must not call real progress a loop.

A 16-step data task (Golden Picks series-fade analysis, 2026-09-24) made 16 different
calls and was told it had been "looping rather than progressing". Only a turn that
actually repeated calls should hear that; everything else gets a plain "continue".

Run:  python3 tests/test_round_limit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.guards import TurnBudget  # noqa: E402


def _budget() -> TurnBudget:
    b = TurnBudget(max_tool_rounds=3, max_repeats=2, token_ceiling=0)
    for _ in range(3):
        b.start_round()
    return b


def test_distinct_calls_get_continue_message() -> None:
    b = _budget()
    for i in range(3):
        b.record("repo_read", {"path": f"f{i}.ts"})
    msg = b.stop_reason()
    assert msg and "continue" in msg and "loop" not in msg and "circles" not in msg


def test_repeated_calls_get_loop_message() -> None:
    b = _budget()
    b.record("repo_read", {"path": "a.ts"})
    b.record("repo_read", {"path": "a.ts"})
    msg = b.stop_reason()
    assert msg and "circles" in msg


def test_under_limit_is_silent() -> None:
    b = TurnBudget(max_tool_rounds=3, max_repeats=2, token_ceiling=0)
    b.start_round()
    assert b.stop_reason() is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
