"""Nikki must be told which model she runs on, and not to deny models from memory.

She answered "Claude 3.5 Sonnet" when asked her version, and told Stan that Opus 5.5
"doesn't exist" (2026-09-24) - both from stale training data.

Run:  python3 tests/test_model_identity.py
"""
from __future__ import annotations

import ast
import types
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "app" / "agent.py").read_text()


def _ns() -> dict:
    ns: dict = {"settings": types.SimpleNamespace(model="anthropic:claude-sonnet-5")}
    for node in ast.parse(SRC).body:
        if isinstance(node, ast.FunctionDef) and node.name == "model_identity":
            exec(compile(ast.Module([node], []), "agent.py", "exec"), ns)
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FRESHNESS_RULE":
            exec(compile(ast.Module([node], []), "agent.py", "exec"), ns)
    return ns


def test_identity_names_the_routed_model() -> None:
    f = _ns()["model_identity"]
    assert "claude-opus-5-5" in f("anthropic:claude-opus-5-5")
    assert "claude-sonnet-5" in f(None)  # falls back to the configured default


def test_freshness_rule_forbids_denial_from_memory() -> None:
    rule = _ns()["FRESHNESS_RULE"]
    assert "platform.claude.com/docs/en/models/overview" in rule
    assert "does not exist based on memory" in rule


def test_both_are_in_the_stable_prompt() -> None:
    body = SRC[SRC.index("def system_prompt_parts"):SRC.index("def system_message")]
    assert "model_identity(model_spec)" in body and "FRESHNESS_RULE" in body


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
