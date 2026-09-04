"""LangGraph ReAct agent for Nikki.

The graph is rebuilt per turn from the live tool registry (cheap) so hot-loaded skills
are available immediately. The graph interrupts before the `tools` node; the UI layer
inspects pending tool calls, asks the human for approval where required, and resumes.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from app.config import settings
from app.tools import registry

log = logging.getLogger("nikki.agent")


def make_model(spec: str | None = None) -> BaseChatModel:
    """`provider:model` -> chat model. Providers: anthropic, openai."""
    provider, _, name = (spec or settings.model).partition(":")
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=name, max_tokens=settings.max_tokens, streaming=True, api_key=settings.anthropic_api_key)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=name, streaming=True, api_key=settings.openai_api_key)
    raise ValueError(f"unknown model provider: {provider!r} (use anthropic:<model> or openai:<model>)")


def system_prompt() -> str:
    from datetime import datetime, timezone

    from app.tenancy import maybe_principal
    from app.tools.memory import memory_digest

    p = maybe_principal()
    admin = bool(p and p.is_admin)
    digest = memory_digest()
    memory_block = f"What you remember about the user (long-term memory):\n{digest}\n\n" if digest else ""
    who = f"You are talking with {p.email}." if p else ""
    common = (
        "Capabilities: web_search + http_fetch for live web information (always search when a question depends on "
        "current facts, then cite URLs); integrations the user connected at /integrations — Google (Drive, Calendar"
        + (", Gmail" if admin else "") + "), Microsoft 365 (Outlook mail, Calendar, OneDrive) and custom REST APIs "
        "(list_apis / call_api). google_accounts and microsoft_accounts show what is connected; if nothing is, tell the "
        "user to open /integrations. Scheduling tools (schedule_task creates recurring unattended runs whose results "
        "appear at /schedules). Long-term memory (remember/recall/forget — proactively call remember when the user "
        "shares a lasting fact, preference, person or project detail; never store secrets). "
    )
    if admin:
        skills = registry.skill_report()
        loaded = ", ".join(t for s in skills for t in s["tools"]) or "none yet"
        extra = (
            "Admin-only capabilities: file tools (workspace-sandboxed), SQL tools, and self-maintenance tools that let "
            "you author new Python skills (write_skill) which become live tools instantly. "
            f"User-authored skills currently loaded: {loaded}. "
            "When a task needs a capability you lack, propose a skill, use skill_template, then write_skill. "
            "Spaces: you can build and deploy independent web micro-apps (dashboards, trackers, calculators) "
            "with deploy_space. Write a complete, self-contained, production-quality app: for 'fastapi' pass a "
            "full index.html (inline CSS/JS, responsive, polished) and optionally a main.py exposing `app` for "
            "JSON endpoints; for 'streamlit' pass a full app.py; for 'node' pass index.html plus an optional "
            "Express server.js. Pick a short slug, state the slug and framework before calling the tool, and tell "
            "the user the build takes 3-6 minutes and the link appears in the Spaces Gallery (/spaces). Do not poll "
            "space_status repeatedly in one turn; the UI tracks progress. "
        )
    else:
        extra = ""
    persona = settings.persona if admin else settings.tenant_persona
    return (
        f"{persona}\n\n{who}\n"
        f"Current date/time: {datetime.now(timezone.utc):%A %Y-%m-%d %H:%M} UTC.\n\n"
        f"{memory_block}{common}{extra}"
        "Tools marked as requiring approval will pause for the user's confirmation; explain briefly "
        "what you are about to do before calling them. Answer in plain, well-structured Markdown."
    )


def build_graph(checkpointer: BaseCheckpointSaver, model_spec: str | None = None) -> CompiledStateGraph:
    return create_react_agent(
        make_model(model_spec),
        tools=registry.tools(),
        prompt=system_prompt(),
        checkpointer=checkpointer,
        interrupt_before=["tools"],
        interrupt_after=["tools"],  # lets the UI rebuild the graph after a skill hot-load
        name="nikki",
    )


def pending_tool_calls(state: Any) -> list[dict]:
    """Tool calls the graph is about to execute (graph is paused before `tools`)."""
    if not state.next or "tools" not in state.next:
        return []
    msgs = state.values.get("messages", [])
    last = msgs[-1] if msgs else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return list(last.tool_calls)
    return []


def rejection_messages(tool_calls: list[dict], reason: str = "The user declined this action.") -> list[ToolMessage]:
    return [ToolMessage(content=reason, tool_call_id=tc["id"], name=tc["name"], status="error") for tc in tool_calls]


def text_of(content: Any) -> str:
    """Normalize message content (str or Anthropic-style block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content)
