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

        kwargs: dict[str, Any] = {"model": name, "streaming": True,
                                  "api_key": settings.anthropic_api_key,
                                  "max_tokens": settings.max_tokens}
        budget = settings.thinking_budget_tokens
        if budget > 0:
            # Extended thinking needs max_tokens > budget, and temperature fixed at 1.
            # The UI already renders thinking blocks (stream_segment handles
            # content blocks of type "thinking"); the model just never emitted any.
            kwargs.update({"max_tokens": max(settings.max_tokens, budget + 4096),
                           "temperature": 1,
                           "thinking": {"type": "enabled", "budget_tokens": budget}})
            try:
                return ChatAnthropic(**kwargs)
            except TypeError:
                # langchain-anthropic is unpinned in requirements.txt; an older
                # build has no `thinking` kwarg. Degrade rather than break every turn.
                log.warning("langchain-anthropic does not accept `thinking`; "
                            "continuing without extended thinking")
                for k in ("thinking", "temperature"):
                    kwargs.pop(k, None)
                kwargs["max_tokens"] = settings.max_tokens
        return ChatAnthropic(**kwargs)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=name, streaming=True, api_key=settings.openai_api_key)
    raise ValueError(f"unknown model provider: {provider!r} (use anthropic:<model> or openai:<model>)")



# Tools that mean this turn is engineering work rather than conversation.
ENGINEER_TOOLS = {
    "repo_open", "repo_read", "repo_search", "repo_write", "repo_edit", "repo_git",
    "repo_check", "repo_commit_push", "repo_run", "deploy_app", "deploy_self",
    "scaffold_app", "build_log", "app_status", "set_convex_env",
}

_ENGINEER_HINTS = ("repo", "deploy", "build", "convex", "typescript", "commit", "push",
                   "schema", "mutation", "import error", "typecheck", "wellcollar",
                   "branch", "pull request", "stack trace", "traceback")


def route_model(user_text: str, recent_tools: set[str] | None = None) -> str | None:
    """An Opus-class model for engineering turns, when NIKKI_ENGINEER_MODEL is set.

    Returns None when routing is off or the turn looks like ordinary conversation,
    so the caller keeps whatever model is already selected. Sermon coaching and a
    Convex refactor are not the same cognitive task and should not share a model.
    """
    if not settings.engineer_model:
        return None
    if recent_tools and (recent_tools & ENGINEER_TOOLS):
        return settings.engineer_model
    t = (user_text or "").lower()
    return settings.engineer_model if any(h in t for h in _ENGINEER_HINTS) else None

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
    """Full system prompt as one string (stable part + volatile part)."""
    stable, volatile = system_prompt_parts()
    return f"{stable}\n\n{volatile}"


def system_prompt_parts() -> tuple[str, str]:
    """(stable, volatile) halves of the system prompt.

    The stable half (persona, capabilities, KB index, rules) is identical from turn to turn and is
    marked for Anthropic prompt caching; the volatile half (who, clock, memory digest) follows it so
    changes there never invalidate the cached prefix.
    """
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
            "Admin-only capabilities: file tools (workspace-sandboxed), SQL tools, the engineer "
            "toolchain (GitHub repos, Cloud Build, Firebase, Convex), Spaces micro-app deploys, "
            "browser automation, background jobs, document tools, and self-maintenance tools that "
            "let you author new Python skills which become live tools instantly. "
            f"User-authored skills currently loaded: {loaded}. "
            "\n\nROUTING — the rules for each of these areas live in the knowledge base, not here. "
            "Before you act in one of them, kb_read the named doc and follow it. Read it even when "
            "the task looks small, and do not work from a half-memory of it:\n"
            "- Repo work, commits, deploys, build failures, browser automation, background jobs, "
            "writing your own skills → nikki-system\n"
            "- Creating a NEW app, or deploying a Space → building-apps\n"
            "- Google/Microsoft integrations, custom REST APIs → accounts-and-integrations\n"
            "- Reading or producing documents, spreadsheets, decks, PDFs, audio → files-and-documents\n"
            "- Sermons, series, passages, an \'itch\' → sermon-prep (coach, never author)\n"
            "- Odds, lines, scores → sports\n"
            "- A specific project or company → the matching project-* doc\n\n"
            "Before any push or deploy, run repo_check on the repo. It is read-only, instant and "
            "needs no approval, and it catches the TypeScript patterns that have broken every "
            "Convex build here. A failed Cloud Build costs minutes; this costs nothing. "
            "Your own source code is the GitHub repo stanbraxton/Nikki (this Chainlit/FastAPI app): "
            "you CAN change your own UI and behavior with the same tools — repo_open "
            "stanbraxton/Nikki, then edit .chainlit/config.toml ([[UI.header_links]] = top-header "
            "links), public/nikki.js / public/nikki.css, or the HTML pages in app/, and "
            "repo_commit_push. Pushing to main auto-deploys: a Cloud Build trigger builds the pushed "
            "commit and rolls a new revision of the nikki Cloud Run service in ~5 minutes — tell the "
            "user that, no further action needed. deploy_self (gated) is the fallback if the trigger "
            "fails. Never claim a change to your own UI is impossible or ask which repo you live in. "
            f"\n\nKnowledge base (curated docs about the owner, his company, this system and every "
            f"project; read the relevant doc with kb_read before answering questions about them, "
            f"search with kb_search, record durable learnings with kb_write): {kb_index}."
        )
    else:
        extra = ""
    persona = settings.persona if admin else settings.tenant_persona
    stable = (
        f"{persona}\n\n{common}{extra}"
        "Tools marked as requiring approval will pause for the user's confirmation; explain briefly "
        "what you are about to do before calling them. Answer in plain, well-structured Markdown. "
        "Say plainly when you are unsure, and distinguish what you verified against a tool or document "
        "from what you are inferring. When a request is ambiguous in a way that changes what you would "
        "do, and the work is slow or hard to undo, ask one clarifying question before starting instead "
        "of guessing. "
        "Whenever you encounter an error (a failed tool call, an API refusal, missing access), never just report "
        "the raw error: state the problem in plain words, list the possible solutions, and give your recommendation."
    )
    volatile = f"{who}\nCurrent date/time: {datetime.now(timezone.utc):%A %Y-%m-%d %H:%M} UTC.\n\n{memory_block}".strip()
    return stable, volatile


def system_message(model_spec: str | None = None) -> Any:
    """System prompt for the given model: cached content blocks on Anthropic, plain text elsewhere."""
    stable, volatile = system_prompt_parts()
    if (model_spec or settings.model).partition(":")[0] != "anthropic":
        return f"{stable}\n\n{volatile}"
    from langchain_core.messages import SystemMessage

    return SystemMessage(
        content=[
            {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": volatile},
        ]
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


def compact_old_tool_results(messages: list[Any], max_chars: int | None = None) -> list[Any]:
    """Shorten bulky tool results from *earlier* turns (everything before the latest HumanMessage).

    A full kb_read / http_fetch / repo_read result is needed while the model works on it, but once the
    turn is over it would otherwise be re-sent in full on every later message of the thread. The
    stored checkpoint is untouched; only the messages sent to the model are compacted.
    """
    limit = max_chars or settings.old_tool_result_chars
    if limit <= 0:
        return messages
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    out: list[Any] = []
    for i, m in enumerate(messages):
        if i < last_human and isinstance(m, ToolMessage) and isinstance(m.content, str) and len(m.content) > limit:
            m = m.model_copy(update={"content": m.content[:limit] + f"\n[... {len(m.content) - limit:,} more chars omitted from history; call the tool again if you need them]"})
        out.append(m)
    return out


def mark_cache_breakpoint(messages: list[Any]) -> list[Any]:
    """Put an Anthropic cache breakpoint on the last message so the whole conversation prefix is cached
    for the next turn (5-minute TTL). Non-destructive copy."""
    if not messages:
        return messages
    last = messages[-1]
    content = last.content
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content or " ", "cache_control": {"type": "ephemeral"}}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict) and content[-1].get("type") in ("text", "tool_result", "image"):
        blocks = [*content[:-1], {**content[-1], "cache_control": {"type": "ephemeral"}}]
    else:
        return messages
    return [*messages[:-1], last.model_copy(update={"content": blocks})]


def _prepare_messages(msgs: list[Any], anthropic: bool) -> list[Any]:
    budget = settings.history_budget_tokens_anthropic if anthropic else settings.history_budget_tokens
    out = compact_old_tool_results(trim_history(repair_history(list(msgs)), budget))
    return mark_cache_breakpoint(out) if anthropic else out


def _pre_model_hook(state: Any) -> dict:
    msgs = state["messages"] if isinstance(state, dict) else state.messages
    return {"llm_input_messages": _prepare_messages(msgs, settings.model.startswith("anthropic:"))}


def build_graph(checkpointer: BaseCheckpointSaver, model_spec: str | None = None) -> CompiledStateGraph:
    anthropic = (model_spec or settings.model).startswith("anthropic:")

    def pre_model_hook(state: Any) -> dict:
        msgs = state["messages"] if isinstance(state, dict) else state.messages
        return {"llm_input_messages": _prepare_messages(msgs, anthropic)}

    return create_react_agent(
        make_model(model_spec),
        tools=registry.tools(),
        prompt=system_message(model_spec),
        pre_model_hook=pre_model_hook,
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
