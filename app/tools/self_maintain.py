"""Self-maintenance: Nikki can write, validate, and hot-load new Python skills.

A skill is a single .py file in `settings.skills_dir`. It must define one or more
`@tool`-decorated functions (from langchain_core.tools) or a module-level `TOOLS` list.
Anything the skill does at runtime requires approval unless the file sets
`REQUIRES_APPROVAL = False`.

Every write goes through the approval gate. Before saving, the source is compiled,
checked for obviously dangerous imports, imported in isolation, and then one of its tools
is actually EXECUTED in a subprocess (`test_call`) so a broken or half-finished skill
never reaches the live registry.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from app.config import settings

_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_BLOCKED_MODULES = {"subprocess", "ctypes", "socket", "shutil", "signal", "multiprocessing"}
_BLOCKED_CALLS = {"exec", "eval", "compile", "__import__"}
_SECRET_NAME = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY)", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\b(would add|placeholder|not implemented|TODO)\b", re.IGNORECASE)

SKILL_TEMPLATE = '''"""{description}"""
from langchain_core.tools import tool

REQUIRES_APPROVAL = True  # set False only for read-only tools


@tool
def {name}(text: str) -> str:
    """One-line description the model will see."""
    return text
'''


def _validate(name: str, source: str) -> list[str]:
    problems: list[str] = []
    if not _NAME.match(name):
        problems.append("name must be snake_case, 3-40 chars, start with a letter")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                if m.split(".")[0] in _BLOCKED_MODULES:
                    problems.append(f"blocked import: {m}")
                if m == "app" or m.startswith("app."):
                    # Function-level imports are not exercised by the trial import, so resolve them now.
                    try:
                        found = importlib.util.find_spec(m) is not None
                    except (ImportError, ValueError):
                        found = False
                    if not found:
                        problems.append(
                            f"unknown module {m!r}; built-in tools live under app.tools.* "
                            "(e.g. app.tools.browser exposes browser_open/browser_snapshot/browser_click/browser_type; "
                            "they are @tool objects — call them via .func(...) or .invoke({...}))"
                        )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            problems.append(f"blocked call: {node.func.id}()")
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Name)
                    and _SECRET_NAME.search(tgt.id)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and node.value.value.strip()
                ):
                    problems.append(
                        f"hardcoded secret in {tgt.id}: never store passwords/tokens in skill source; "
                        "read them from os.environ or the tenant integration store"
                    )
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if _PLACEHOLDER.search(node.value.value):
                problems.append(f"placeholder return {node.value.value[:60]!r}: implement the behaviour or leave the tool out")
        if isinstance(node, ast.JoinedStr):
            lit = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if _PLACEHOLDER.search(lit):
                problems.append(f"placeholder text {lit[:60]!r}: implement the behaviour or leave the tool out")
    if "langchain_core.tools" not in source:
        problems.append("skill must import `tool` from langchain_core.tools")
    return problems


def _trial_import(name: str, source: str) -> tuple[list[str], str | None]:
    """Import the candidate in a throwaway module and return the tool names it exposes."""
    mod_name = f"nikki_skill_trial_{name}"
    spec = importlib.util.spec_from_loader(mod_name, loader=None)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    try:
        exec(compile(source, f"<skill {name}>", "exec"), module.__dict__)  # noqa: S102 — validated above
    except Exception:  # noqa: BLE001
        return [], traceback.format_exc(limit=3)
    finally:
        sys.modules.pop(mod_name, None)
    tools = module.__dict__.get("TOOLS") or [v for v in module.__dict__.values() if isinstance(v, BaseTool)]
    return [t.name for t in tools if isinstance(t, BaseTool)], None


_APP_ROOT = Path(__file__).resolve().parents[2]
_TEST_TIMEOUT_S = 120
_FAIL_MARKERS = ("traceback (most recent call last)", "error:", "exception", "browser error", "not implemented", "placeholder")

_RUNNER = r"""
import json, sys, traceback, importlib.util
src = json.load(sys.stdin)["src"]
call = json.load(open(sys.argv[1]))
tool_name, args = call["tool"], call["args"]
spec = importlib.util.spec_from_loader("nikki_skill_under_test", loader=None)
mod = importlib.util.module_from_spec(spec)
try:
    exec(compile(src, "<skill under test>", "exec"), mod.__dict__)
    from langchain_core.tools import BaseTool
    tools = mod.__dict__.get("TOOLS") or [v for v in mod.__dict__.values() if isinstance(v, BaseTool)]
    t = next((t for t in tools if t.name == tool_name), None)
    if t is None:
        print(json.dumps({"ok": False, "out": f"no tool named {tool_name!r}; available: {[x.name for x in tools]}"})); sys.exit(0)
    out = t.invoke(args)
    print(json.dumps({"ok": True, "out": str(out)}))
except BaseException:
    print(json.dumps({"ok": False, "out": traceback.format_exc(limit=6)}))
"""


def _run_test(source: str, test_call: dict) -> tuple[bool, str]:
    """Execute `test_call = {"tool": name, "args": {...}}` against the candidate source in a fresh
    subprocess (same interpreter, same env, PYTHONPATH=app root) and return (passed, output)."""
    tool_name = str(test_call.get("tool") or "")
    args = test_call.get("args") or {}
    if not tool_name or not isinstance(args, dict):
        return False, "test_call must be {\"tool\": \"<tool name>\", \"args\": {...}}"
    spec_file = Path(os.environ.get("TMPDIR", "/tmp")) / f"nikki_skill_test_{os.getpid()}.json"
    spec_file.write_text(json.dumps({"tool": tool_name, "args": args}), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": f"{_APP_ROOT}{os.pathsep}{os.environ.get('PYTHONPATH', '')}", "NIKKI_SKILL_TEST": "1"}
    try:
        r = subprocess.run([sys.executable, "-c", _RUNNER, str(spec_file)], input=json.dumps({"src": source}), capture_output=True,
                           text=True, timeout=_TEST_TIMEOUT_S, env=env, cwd=str(_APP_ROOT))
    except subprocess.TimeoutExpired:
        return False, f"test timed out after {_TEST_TIMEOUT_S}s (the tool hung or waits for input)"
    finally:
        spec_file.unlink(missing_ok=True)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        res = json.loads(line)
    except json.JSONDecodeError:
        return False, f"test process crashed (exit {r.returncode}):\n{(r.stderr or r.stdout)[-1500:]}"
    out = str(res.get("out", ""))
    if not res.get("ok"):
        return False, out[-2000:]
    low = out.lower()[:400]
    if not out.strip():
        return False, "tool returned an empty result"
    if any(m in low for m in _FAIL_MARKERS):
        return False, f"tool ran but its result looks like a failure:\n{out[:1200]}"
    return True, out


@tool
def test_skill(source: str, tool: str, args: dict | None = None) -> str:
    """Run one tool from a candidate skill source with real `args` in an isolated subprocess and return what it
    produced (or the traceback). Use it while developing a skill; write_skill runs the same test before saving."""
    problems = _validate("candidate", source)
    problems = [p for p in problems if not p.startswith("name must")]
    if problems:
        return "rejected before running:\n- " + "\n- ".join(problems)
    ok, out = _run_test(source, {"tool": tool, "args": args or {}})
    return ("PASS\n" if ok else "FAIL\n") + out[:4000]


@tool
def list_skills() -> str:
    """Show the user-authored skills currently loaded, the tools they provide, and any load errors."""
    from app.tools import registry

    rep = registry.skill_report()
    if not rep:
        return "no skills yet"
    return "\n".join(
        f"{r['file']}: tools={r['tools'] or '-'}" + (f" ERROR: {r['error'].splitlines()[-1]}" if r["error"] else "")
        for r in rep
    )


@tool
def read_skill(name: str) -> str:
    """Return the source code of an existing skill by name (without .py)."""
    p = settings.skills_dir / f"{name}.py"
    return p.read_text(encoding="utf-8") if p.exists() else f"(no skill named {name})"


@tool
def skill_template(name: str, description: str = "Describe what this skill does.") -> str:
    """Return a starter skill file for `name` that follows Nikki's skill conventions."""
    return SKILL_TEMPLATE.format(name=name, description=description)


@tool
def write_skill(name: str, source: str, test_call: dict) -> str:
    """Create or replace a skill file `<name>.py` with the given Python source, validate it, RUN it, and hot-load it.
    The source must define @tool functions from langchain_core.tools (or a TOOLS list).
    `test_call` is mandatory: {"tool": "<one of the skill's tools>", "args": {...real arguments...}}; the tool is
    executed in a subprocess and the skill is saved only if it returns a non-error result. Pick a safe, read-only
    or reversible call for the test. Requires approval."""
    problems = _validate(name, source)
    if problems:
        return "rejected:\n- " + "\n- ".join(problems)
    tool_names, err = _trial_import(name, source)
    if err:
        return f"rejected: skill failed to import:\n{err}"
    if not tool_names:
        return "rejected: no @tool functions found"
    if not isinstance(test_call, dict) or not test_call.get("tool"):
        return "rejected: test_call is required — {\"tool\": \"<tool name>\", \"args\": {...}}; a skill is never saved untested"
    if test_call["tool"] not in tool_names:
        return f"rejected: test_call.tool {test_call['tool']!r} is not defined by this skill (tools: {', '.join(tool_names)})"
    passed, test_out = _run_test(source, test_call)
    if not passed:
        return f"rejected: test call {test_call['tool']}({json.dumps(test_call.get('args') or {})[:200]}) failed — fix the code and try again:\n{test_out}"
    path = settings.skills_dir / f"{name}.py"
    existed = path.exists()
    path.write_text(source, encoding="utf-8")
    from app.tools import registry

    registry.refresh_skills()
    st = registry.skill_report()
    mine = next((r for r in st if r["file"] == path.name), None)
    if mine and mine["error"]:
        return f"saved but failed to load: {mine['error'].splitlines()[-1]}"
    return f"{'updated' if existed else 'created'} skill {name}; live tools: {', '.join(tool_names)}\ntest {test_call['tool']} passed, returned: {test_out[:600]}"


@tool
def delete_skill(name: str) -> str:
    """Remove a skill file and unload its tools. Requires approval."""
    p = settings.skills_dir / f"{name}.py"
    if not p.exists():
        return f"(no skill named {name})"
    p.unlink()
    from app.tools import registry

    registry.refresh_skills()
    return f"deleted skill {name}"


for _t in (list_skills, read_skill, skill_template):
    _t.metadata = {"requires_approval": False}
for _t in (test_skill, write_skill, delete_skill):
    _t.metadata = {"requires_approval": True}  # test_skill executes model-written code

TOOLS = [list_skills, read_skill, skill_template, test_skill, write_skill, delete_skill]
