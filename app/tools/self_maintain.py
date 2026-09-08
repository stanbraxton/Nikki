"""Self-maintenance: Nikki can write, validate, and hot-load new Python skills.

A skill is a single .py file in `settings.skills_dir`. It must define one or more
`@tool`-decorated functions (from langchain_core.tools) or a module-level `TOOLS` list.
Anything the skill does at runtime requires approval unless the file sets
`REQUIRES_APPROVAL = False`.

Every write goes through the approval gate. Before saving, the source is compiled,
checked for obviously dangerous imports, and imported in isolation so a broken skill
never reaches the live registry.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import traceback

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
def write_skill(name: str, source: str) -> str:
    """Create or replace a skill file `<name>.py` with the given Python source, validate it, and hot-load it.
    The source must define @tool functions from langchain_core.tools (or a TOOLS list). Requires approval."""
    problems = _validate(name, source)
    if problems:
        return "rejected:\n- " + "\n- ".join(problems)
    tool_names, err = _trial_import(name, source)
    if err:
        return f"rejected: skill failed to import:\n{err}"
    if not tool_names:
        return "rejected: no @tool functions found"
    path = settings.skills_dir / f"{name}.py"
    existed = path.exists()
    path.write_text(source, encoding="utf-8")
    from app.tools import registry

    registry.refresh_skills()
    st = registry.skill_report()
    mine = next((r for r in st if r["file"] == path.name), None)
    if mine and mine["error"]:
        return f"saved but failed to load: {mine['error'].splitlines()[-1]}"
    return f"{'updated' if existed else 'created'} skill {name}; live tools: {', '.join(tool_names)}"


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
for _t in (write_skill, delete_skill):
    _t.metadata = {"requires_approval": True}

TOOLS = [list_skills, read_skill, skill_template, write_skill, delete_skill]
