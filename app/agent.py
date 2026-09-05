"""LangGraph ReAct agent for Nikki.

The graph is rebuilt per turn from the live tool registry (cheap) so hot-loaded skills
are available immediately. The graph interrupts before the `tools` node; the UI layer
inspects pending tool calls, asks the human for approval where required, and resumes.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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


_BILLING_MARKERS = ("credit balance is too low", "insufficient_quota", "billing_hard_limit_reached", "exceeded your current quota")


def is_billing_error(e: BaseException) -> bool:
    """True when a provider refused the call because the account is out of credit."""
    txt = f"{type(e).__name__}: {e}".lower()
    return any(m in txt for m in _BILLING_MARKERS)


def fallback_model_for(model_spec: str | None) -> str | None:
    """Fallback spec to use after a billing error on `model_spec`, or None if there is none."""
    fb = settings.fallback_model
    cur = model_spec or settings.model
    if not fb or fb == cur:
        return None
    provider = fb.partition(":")[0]
    if provider == "openai" and not settings.openai_api_key:
        return None
    if provider == "anthropic" and not settings.anthropic_api_key:
        return None
    return fb


_TOO_LARGE_MARKERS = ("request too large", "tokens per min", "context_length_exceeded", "prompt is too long", "maximum context length")


def is_too_large_error(e: BaseException) -> bool:
    """True when the provider rejected the call because the request exceeded a token/TPM limit."""
    txt = f"{type(e).__name__}: {e}".lower()
    return any(m in txt for m in _TOO_LARGE_MARKERS)


def friendly_error(e: BaseException) -> str:
    """Human-readable message for an unrecoverable turn failure: problem / solutions / recommendation."""
    if is_billing_error(e):
        return (
            "⚠️ **Problem:** my language-model provider refused the request — the API account is out of credit.\n\n"
            "**Possible solutions:**\n"
            "1. Top up the Anthropic account (console.anthropic.com → Plans & Billing) and enable auto-reload.\n"
            "2. Pick a different model from the chat settings for this thread.\n\n"
            "**Recommendation:** option 1 — no redeploy is needed; I work again as soon as the balance is positive."
        )
    if is_too_large_error(e):
        return (
            "⚠️ **Problem:** this conversation has grown larger than the model's per-request token limit, "
            "so the provider rejected the call.\n\n"
            "**Possible solutions:**\n"
            "1. Start a new chat thread (I keep long-term memory and the knowledge base, so context is not lost).\n"
            "2. Pick a model with a higher token limit from the chat settings.\n"
            "3. Raise the provider's rate limits (OpenAI: platform.openai.com/account/rate-limits).\n\n"
            "**Recommendation:** option 1 — fastest and free."
        )
    return (
        f"⚠️ **Problem:** the request failed with `{type(e).__name__}: {str(e)[:600]}`.\n\n"
        "**Possible solutions:**\n"
        "1. Send the message again (transient provider errors are common).\n"
        "2. Start a new thread if the error repeats.\n"
        "3. Ask Stan to check the service logs if it persists.\n\n"
        "**Recommendation:** option 1 first, then 2."
    )


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
        from app.tools.knowledge import index_text

        kb_index = index_text() or "empty"
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
            "Engineer toolchain: you maintain real software projects hosted on GitHub — repo_open a repo, then "
            "repo_list/repo_read/repo_search to understand it, repo_edit/repo_write to change it (local, ungated), "
            "repo_git for status/diff/log, repo_commit_push to publish (gated), deploy_app to build on Cloud Build and "
            "publish to Firebase Hosting (+ Convex backend) (gated), app_status/build_log to follow a build. Work like "
            "a careful engineer: read before editing, keep diffs minimal, summarize the diff before pushing, and never "
            "deploy with uncommitted changes. "
            "Your own source code is the GitHub repo stanbraxton/Nikki (this Chainlit/FastAPI app): you CAN change "
            "your own UI and behavior with the same tools — repo_open stanbraxton/Nikki, then edit "
            ".chainlit/config.toml ([[UI.header_links]] = top-header links), public/nikki.js / public/nikki.css, or the "
            "HTML pages in app/, and repo_commit_push. You cannot redeploy yourself: after pushing, tell Stan the change "
            "is committed and needs a redeploy of the nikki Cloud Run service. Never claim a change to your own UI is "
            "impossible or ask which repo you live in. "
            f"Knowledge base (curated docs about the owner, his company, this system and every project; read the "
            f"relevant doc with kb_read before answering questions about them, search with kb_search, record durable "
            f"learnings with kb_write): {kb_index}. "
        )
    else:
        extra = ""
    persona = settings.persona if admin else settings.tenant_persona
    return (
        f"{persona}\n\n{who}\n"
        f"Current date/time: {datetime.now(timezone.utc):%A %Y-%m-%d %H:%M} UTC.\n\n"
        f"{memory_block}{common}{extra}"
        "Tools marked as requiring approval will pause for the user's confirmation; explain briefly "
        "what you are about to do before calling them. Answer in plain, well-structured Markdown. "
        "Whenever you encounter an error (a failed tool call, an API refusal, missing access), never just report "
        "the raw error: state the problem in plain words, list the possible solutions, and give your recommendation."
    )


def repair_history(messages: list[Any]) -> list[Any]:
    """Return a provider-valid copy of the conversation.

    Chat providers reject histories where a tool_result has no matching tool_use in the
    immediately preceding assistant message, or where a tool_use got no result (this
    happens after crashes mid-approval, container restarts, or overlapping turns on one
    thread). The repair is non-destructive: the stored checkpoint is left untouched and
    only the messages sent to the model are fixed.
    """
    out: list[Any] = []
    open_calls: dict[str, dict] = {}  # tool_call_id -> tool_call awaiting a result

    def close_open() -> None:
        for tc_id, tc in open_calls.items():
            out.append(ToolMessage(content="(no result recorded — the call was interrupted)", tool_call_id=tc_id, name=tc.get("name", "tool"), status="error"))
        open_calls.clear()

    for m in messages:
        if isinstance(m, ToolMessage):
            if m.tool_call_id in open_calls:
                del open_calls[m.tool_call_id]
                out.append(m)
            else:
                log.warning("dropping orphan tool_result %s (%s)", m.tool_call_id, m.name)
            continue
        close_open()
        if isinstance(m, AIMessage):
            if m.tool_calls:
                open_calls.update({tc["id"]: tc for tc in m.tool_calls})
            elif not text_of(m.content).strip() and not getattr(m, "invalid_tool_calls", None):
                continue  # empty assistant turns are rejected by Anthropic
        out.append(m)
    close_open()
    # Anthropic requires the first message to be a user turn.
    while out and not isinstance(out[0], HumanMessage):
        out.pop(0)
    return out


def _approx_tokens(m: Any) -> int:
    """Cheap token estimate (~4 chars/token) covering text content and tool-call arguments."""
    n = len(text_of(m.content))
    if isinstance(m, AIMessage) and m.tool_calls:
        n += sum(len(str(tc.get("args", ""))) + 40 for tc in m.tool_calls)
    return n // 4 + 8


def trim_history(messages: list[Any], budget_tokens: int | None = None) -> list[Any]:
    """Drop the oldest turns until the history fits the token budget.

    Always keeps the most recent user turn and everything after it. Cuts only at a
    HumanMessage boundary so tool_use/tool_result pairs are never split. The stored
    checkpoint is untouched; only the messages sent to the model are trimmed.
    """
    budget = budget_tokens or settings.history_budget_tokens
    total = sum(_approx_tokens(m) for m in messages)
    if total <= budget:
        return messages
    human_idx = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    cut = 0
    for i in human_idx[1:]:
        if total <= budget:
            break
        total -= sum(_approx_tokens(m) for m in messages[cut:i])
        cut = i
    if cut:
        log.info("trimmed history: dropped %d messages, ~%d tokens remain", cut, total)
    return messages[cut:]


def _pre_model_hook(state: Any) -> dict:
    msgs = state["messages"] if isinstance(state, dict) else state.messages
    return {"llm_input_messages": trim_history(repair_history(list(msgs)))}


def build_graph(checkpointer: BaseCheckpointSaver, model_spec: str | None = None) -> CompiledStateGraph:
    return create_react_agent(
        make_model(model_spec),
        tools=registry.tools(),
        prompt=system_prompt(),
        pre_model_hook=_pre_model_hook,
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
