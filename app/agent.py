"""LangGraph ReAct agent for Nikki.

The graph is rebuilt per turn from the live tool registry (cheap) so hot-loaded skills
are available immediately. The graph interrupts before the `tools` node; the UI layer
inspects pending tool calls, asks the human for approval where required, and resumes.
"""
from __future__ import annotations

import logging
import socket
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from app.config import settings
from app.tools import registry

log = logging.getLogger("nikki.agent")


# Anthropic models that still take a fixed thinking budget. Everything else is
# assumed to want adaptive thinking: new model IDs trend that way, so an unknown
# name defaults to the supported path rather than the removed one.
#
# The split is not cosmetic. On Sonnet 5 / Opus 5 / Opus 4.7+ the API removed BOTH
# `thinking.type: "enabled"` (with budget_tokens) and the sampling parameters, so
# the old call failed twice over:
#   400 "thinking.type.enabled" is not supported for this model.
#       Use "thinking.type.adaptive" and "output_config.effort" ...
# and `temperature: 1` is rejected on the same models for the same reason.
_LEGACY_THINKING_PREFIXES = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3")


def uses_adaptive_thinking(name: str) -> bool:
    """True when this model wants adaptive thinking + effort, not a token budget."""
    return not name.startswith(_LEGACY_THINKING_PREFIXES)


def make_model(spec: str | None = None) -> BaseChatModel:
    """`provider:model` -> chat model. Providers: anthropic, openai."""
    resolved = spec or settings.model
    provider, _, name = resolved.partition(":")
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {"model": name, "streaming": True,
                                  "api_key": settings.anthropic_api_key,
                                  "max_tokens": settings.max_tokens}
        budget = settings.thinking_budget_tokens
        note = "thinking off"
        if budget > 0:
            if uses_adaptive_thinking(name):
                # `reasoning_effort` alone is the whole configuration: langchain maps it
                # to output_config.effort AND, because `thinking` is left unset, defaults
                # thinking to {"type": "adaptive", "display": "summarized"}.
                #
                # "summarized" is load-bearing, not incidental. The API default is
                # "omitted", which returns thinking blocks whose text is EMPTY - and
                # ui.py's stream_segment only renders a block when block["thinking"] is
                # truthy. Setting `thinking` by hand here would buy a working request
                # that renders nothing, which is indistinguishable from thinking being off.
                #
                # Deliberately NOT set on this path: `temperature` (rejected, 400) and the
                # max_tokens bump (that headroom exists to fit a fixed budget, which
                # adaptive thinking does not have).
                kwargs["reasoning_effort"] = settings.thinking_effort
                note = f"thinking adaptive, effort={settings.thinking_effort}"
            else:
                # Legacy path: a fixed budget needs max_tokens > budget and temperature 1.
                kwargs.update({"max_tokens": max(settings.max_tokens, budget + 4096),
                               "temperature": 1,
                               "thinking": {"type": "enabled", "budget_tokens": budget}})
                note = f"thinking budget={budget}"
        # Logged because route_model() can silently change which model a turn uses.
        # Without this line an unexplained bill has nothing to attribute it to.
        log.info("model resolved: %s (%s, max_tokens=%s)", resolved, note, kwargs["max_tokens"])
        return ChatAnthropic(**kwargs)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        log.info("model resolved: %s (thinking n/a, openai)", resolved)
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
    hit = recent_tools & ENGINEER_TOOLS if recent_tools else set()
    if hit:
        log.info("route_model -> %s (engineer tool used: %s)",
                 settings.engineer_model, ", ".join(sorted(hit)))
        return settings.engineer_model
    t = (user_text or "").lower()
    word = next((h for h in _ENGINEER_HINTS if h in t), None)
    if word:
        log.info("route_model -> %s (matched hint %r)", settings.engineer_model, word)
        return settings.engineer_model
    return None


def route_light_model(user_text: str, recent_tools: set[str] | None = None,
                      has_attachments: bool = False) -> str | None:
    """NIKKI_LIGHT_MODEL for a short, plain conversational turn; None otherwise (off by default)."""
    if not settings.light_model or has_attachments:
        return None
    t = (user_text or "").strip()
    if not t or len(t) > settings.light_model_max_chars:
        return None
    if recent_tools and recent_tools & ENGINEER_TOOLS:
        return None
    if any(h in t.lower() for h in _ENGINEER_HINTS):
        return None
    log.info("route_light_model -> %s (short conversational turn, %d chars)", settings.light_model, len(t))
    return settings.light_model

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


def _is_transient_error(e: BaseException) -> bool:
    """True when retrying the same request could plausibly succeed."""
    if isinstance(e, (TimeoutError, ConnectionError, socket.timeout, socket.gaierror)):
        return True
    try:
        import httpx
        # Timeouts, connection resets, DNS failures, protocol errors.
        if isinstance(e, (httpx.TimeoutException, httpx.TransportError)):
            return True
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            return code == 429 or 500 <= code < 600
    except ImportError:
        pass
    return False


# Deterministic failures: the same input will fail the same way every time,
# so telling the user to resend just burns their tokens and their patience.
#
# ORDER MATTERS — ConnectionError and TimeoutError are subclasses of OSError,
# so _is_transient_error() must be consulted before this tuple is tested.
_BUG_ERRORS = (
    OSError,            # FileNotFoundError, PermissionError, IsADirectoryError, ...
    TypeError,
    ValueError,         # includes json.JSONDecodeError
    KeyError,
    IndexError,
    AttributeError,
    NameError,
    ImportError,
    ZeroDivisionError,
    AssertionError,
    NotImplementedError,
)


def _bug_hint(e: BaseException) -> str:
    """One extra line pointing at the likely cause, when we can guess it."""
    if isinstance(e, FileNotFoundError):
        return (
            "\n\nThis is usually a tool writing to a path that does not exist in the "
            "container — a relative path resolved against a missing working directory, "
            "or `mkdir()` without `parents=True`."
        )
    if isinstance(e, PermissionError):
        return (
            "\n\nThis is usually a tool writing outside the one writable location: on "
            "Cloud Run the filesystem is read-only apart from /tmp and mounted volumes."
        )
    if isinstance(e, ImportError):
        return "\n\nThis is usually a dependency missing from the deployed image."
    if isinstance(e, (KeyError, AttributeError, TypeError)):
        return "\n\nThis is usually a tool receiving a payload in a shape it did not expect."
    return ""


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

    # Always put the full traceback in the logs, whatever branch we return.
    log.exception("Turn failed: %s", type(e).__name__)

    detail = f"`{type(e).__name__}: {str(e)[:600]}`"

    if _is_transient_error(e):
        return (
            f"⚠️ **Problem:** the request failed with {detail}. This looks transient — "
            "a network timeout or a provider hiccup.\n\n"
            "**Possible solutions:**\n"
            "1. Send the message again.\n"
            "2. Start a new thread if the error repeats.\n"
            "3. Ask Stan to check the service logs if it persists.\n\n"
            "**Recommendation:** option 1 first, then 2."
        )

    if isinstance(e, _BUG_ERRORS):
        return (
            f"⚠️ **Problem:** the request failed with {detail}. This is a bug in my code, "
            "not a hiccup — **resending will produce the same error.**"
            f"{_bug_hint(e)}\n\n"
            "**Possible solutions:**\n"
            "1. Ask Stan to check the service logs — the full traceback is there, with the "
            "file and line number.\n"
            "2. Rephrase so the request takes a different path (a different tool, or fewer "
            "files at once), if you need an answer before it is fixed.\n\n"
            "**Recommendation:** option 1 — this one needs a code change."
        )

    return (
        f"⚠️ **Problem:** the request failed with {detail}. I cannot tell whether this is "
        "transient or a bug.\n\n"
        "**Possible solutions:**\n"
        "1. Send the message once more — if it fails identically, it is not transient.\n"
        "2. Start a new thread.\n"
        "3. Ask Stan to check the service logs.\n\n"
        "**Recommendation:** option 1 once, then option 3 — do not keep resending."
    )


def system_prompt(model_spec: str | None = None) -> str:
    """Full system prompt as one string (stable part + volatile part)."""
    stable, volatile = system_prompt_parts(model_spec)
    return f"{stable}\n\n{volatile}"


def model_identity(model_spec: str | None = None) -> str:
    """One sentence telling Nikki which model she is actually running on.

    Without it she answered "what version are you" from stale training data
    ("Claude 3.5 Sonnet") while running on something else entirely. The spec is fixed
    for a given graph, so this belongs in the cached stable half - and the prompt
    cache is per model anyway, so a routed model change costs nothing extra here.
    """
    spec = model_spec or settings.model
    provider, _, name = spec.partition(":")
    return (f"You are currently running on the model `{name}` (provider: {provider}). "
            "If asked which model or version you are, say exactly that. ")


# Nikki's training data predates the model lineup she runs on. She once told Stan that
# Opus 5.5 "doesn't exist" while it was Anthropic's recommended default.
FRESHNESS_RULE = (
    "Your built-in knowledge of AI models, prices, product versions and other fast-moving facts is "
    "out of date. Before stating that one exists, does not exist, or costs a given amount, check a "
    "primary source - for Claude models, https://platform.claude.com/docs/en/models/overview - and "
    "report what it says. Never tell the user something does not exist based on memory alone; if you "
    "cannot verify it, say you could not confirm it. "
)


def system_prompt_parts(model_spec: str | None = None) -> tuple[str, str]:
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
        f"{persona}\n\n{model_identity(model_spec)}{FRESHNESS_RULE}{common}{extra}"
        "Tools marked as requiring approval will pause for the user's confirmation; explain briefly "
        "what you are about to do before calling them. Answer in plain, well-structured Markdown. "
        "Say plainly when you are unsure, and distinguish what you verified against a tool or document "
        "from what you are inferring. When a request is ambiguous in a way that changes what you would "
        "do, and the work is slow or hard to undo, ask one clarifying question before starting instead "
        "of guessing. When you need several independent reads or lookups, request them together "
        "in one step rather than one per step - each step re-sends the whole conversation. "
        "Whenever you encounter an error (a failed tool call, an API refusal, missing access), never just report "
        "the raw error: state the problem in plain words, list the possible solutions, and give your recommendation."
    )
    # Date only. This block sits in front of the whole conversation, so anything that changes
    # here invalidates the prompt cache for every message after it. It used to carry HH:MM,
    # which changed every minute: no user turn could ever read the previous turn's cached
    # history, and each one paid full price for all of it. The clock now rides on the
    # latest user message instead (see stamp_latest_user_message).
    volatile = f"{who}\nToday's date: {datetime.now(timezone.utc):%A %Y-%m-%d} (UTC).\n\n{memory_block}".strip()
    return stable, volatile


def system_message(model_spec: str | None = None) -> Any:
    """System prompt for the given model: cached content blocks on Anthropic, plain text elsewhere."""
    stable, volatile = system_prompt_parts(model_spec)
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

    Cache-stable: the cut may only land on a fixed set of "checkpoints" - the first user
    turn after every `budget // 2` tokens of history, measured from the START of the
    thread. Those positions never move as the thread grows, so the cut stays put for
    many turns and the prompt cache keeps hitting. The previous version moved the cut
    by one turn on every message once a thread was over budget, which changed the very
    first message sent and turned every turn of a long thread into a full-price cache miss.
    """
    budget = budget_tokens or settings.history_budget_tokens
    sizes = [_approx_tokens(m) for m in messages]
    total = sum(sizes)
    if total <= budget:
        return messages
    human_idx = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    last_human = human_idx[-1] if human_idx else 0
    step = max(budget // 2, 1)
    checkpoints: list[int] = []
    running, next_mark = 0, step
    for i, m in enumerate(messages):
        if running >= next_mark and isinstance(m, HumanMessage):
            checkpoints.append(i)
            while next_mark <= running:
                next_mark += step
        running += sizes[i]
    usable = [c for c in checkpoints if c <= last_human]
    cut = next((c for c in usable if sum(sizes[c:]) <= budget), None)
    if cut is None:
        # No checkpoint gets under budget (e.g. one enormous recent turn): fall back to
        # dropping whole turns one at a time, as before.
        cut = usable[-1] if usable else 0
        for i in human_idx[1:]:
            if i > cut and sum(sizes[cut:]) > budget:
                cut = i
    if cut:
        log.info("trimmed history: dropped %d messages, ~%d tokens remain", cut, sum(sizes[cut:]))
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


_STAMPS: dict[str, str] = {}


def stamp_latest_user_message(messages: list[Any]) -> list[Any]:
    """Prefix user messages (copies) with the UTC time they were first sent to the model.

    The clock used to live in the system prompt, where it broke the prompt cache every
    minute. Now the latest user message gets a stamp when first seen, and the stamp is
    remembered per message id and re-applied on every later call - so each request is a
    byte-identical extension of the previous one and the cache keeps hitting across tool
    rounds, approvals and later turns. Older messages this process never stamped (e.g.
    after a restart) are left alone rather than given a made-up time.
    """
    from datetime import datetime, timezone

    last = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)), None)
    if last is None:
        return messages
    out = list(messages)
    for i, m in enumerate(messages):
        if not isinstance(m, HumanMessage):
            continue
        key = getattr(m, "id", None)
        stamp = _STAMPS.get(key) if key else None
        if stamp is None and i == last:
            if len(_STAMPS) > 20000:
                _STAMPS.clear()
            stamp = f"[Current time: {datetime.now(timezone.utc):%A %Y-%m-%d %H:%M} UTC]"
            if key:
                _STAMPS[key] = stamp
        if stamp is None:
            continue
        content = m.content
        if isinstance(content, str):
            content = f"{stamp}\n{content}"
        elif isinstance(content, list):
            content = [{"type": "text", "text": stamp}, *content]
        else:
            continue
        out[i] = m.model_copy(update={"content": content})
    return out


def _prepare_messages(msgs: list[Any], anthropic: bool) -> list[Any]:
    budget = settings.history_budget_tokens_anthropic if anthropic else settings.history_budget_tokens
    out = stamp_latest_user_message(compact_old_tool_results(trim_history(repair_history(list(msgs)), budget)))
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
