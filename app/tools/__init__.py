"""Tool registry.

Built-in tools live in this package. User-authored skills are plain Python files in
`settings.skills_dir`; each file exposes either a module-level `TOOLS = [...]` list of
LangChain tools, or one or more functions decorated with `@tool`. Skills are
(re)loaded on every `registry.tools()` call when their mtime changes, which is what
gives Nikki hot-reload after the self-maintenance tool writes a new skill.

Approval policy: a tool needs human approval when
`tool.metadata.get("requires_approval")` is truthy. Every skill-file tool defaults to
requiring approval unless the skill sets `REQUIRES_APPROVAL = False` explicitly.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import BaseTool

from app.config import settings

log = logging.getLogger("nikki.tools")


@dataclass
class SkillStatus:
    path: Path
    mtime: float
    tools: list[BaseTool] = field(default_factory=list)
    error: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._builtin: list[BaseTool] = []
        self._skills: dict[str, SkillStatus] = {}

    # ---- built-ins -------------------------------------------------------
    def _load_builtin(self) -> None:
        if self._builtin:
            return
        from app.tools import custom_api, db_query, files, google_ws, memory, microsoft, scheduler, self_maintain, spaces, web

        for mod in (files, db_query, self_maintain, spaces):  # platform-admin only
            for t in mod.TOOLS:
                t.metadata = {**(t.metadata or {}), "admin_only": True}
            self._builtin.extend(mod.TOOLS)
        for mod in (web, google_ws, microsoft, custom_api, scheduler, memory):
            self._builtin.extend(mod.TOOLS)
        for name in google_ws.GMAIL_TOOLS:  # restricted Google scope: admin tenant only until verified
            for t in google_ws.TOOLS:
                if t.name == name:
                    t.metadata = {**(t.metadata or {}), "admin_only": True}

    # ---- skills ----------------------------------------------------------
    def _load_skill_file(self, path: Path) -> SkillStatus:
        status = SkillStatus(path=path, mtime=path.stat().st_mtime)
        mod_name = f"nikki_skill_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            found: list[BaseTool] = []
            if hasattr(module, "TOOLS"):
                found.extend(t for t in module.TOOLS if isinstance(t, BaseTool))
            else:
                for obj in vars(module).values():
                    if isinstance(obj, BaseTool):
                        found.append(obj)
            default_approval = getattr(module, "REQUIRES_APPROVAL", True)
            for t in found:
                t.metadata = {**(t.metadata or {})}
                t.metadata.setdefault("requires_approval", bool(default_approval))
                t.metadata["source"] = f"skill:{path.name}"
            if not found:
                status.error = "no tools found (define TOOLS = [...] or use @tool)"
            status.tools = found
        except Exception:  # noqa: BLE001 — a broken skill must never take Nikki down
            status.error = traceback.format_exc(limit=3)
            log.warning("skill %s failed to load: %s", path.name, status.error)
        return status

    def refresh_skills(self) -> None:
        seen: set[str] = set()
        for path in sorted(settings.skills_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            seen.add(path.name)
            cur = self._skills.get(path.name)
            if cur is None or cur.mtime != path.stat().st_mtime:
                self._skills[path.name] = self._load_skill_file(path)
        for name in list(self._skills):
            if name not in seen:
                del self._skills[name]

    # ---- public API ------------------------------------------------------
    def tools(self, admin: bool | None = None) -> list[BaseTool]:
        """Tools visible to the current principal (admin sees everything incl. user skills)."""
        from app.tenancy import maybe_principal

        if admin is None:
            p = maybe_principal()
            admin = bool(p and p.is_admin)
        self._load_builtin()
        out: dict[str, BaseTool] = {t.name: t for t in self._builtin if admin or not (t.metadata or {}).get("admin_only")}
        if not admin:
            return list(out.values())
        self.refresh_skills()
        for st in self._skills.values():
            for t in st.tools:
                if t.name in out:
                    log.warning("skill tool %s shadows an existing tool; skipped", t.name)
                    continue
                out[t.name] = t
        return list(out.values())

    def by_name(self) -> dict[str, BaseTool]:
        return {t.name: t for t in self.tools(admin=True)}

    def requires_approval(self, tool_name: str) -> bool:
        t = self.by_name().get(tool_name)
        if t is None:
            return True
        return bool((t.metadata or {}).get("requires_approval", False))

    def skill_report(self) -> list[dict]:
        self.refresh_skills()
        return [
            {
                "file": name,
                "tools": [t.name for t in st.tools],
                "error": st.error,
            }
            for name, st in sorted(self._skills.items())
        ]


registry = ToolRegistry()
