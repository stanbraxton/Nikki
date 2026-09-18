"""Nikki's Chainlit UI: login, streamed tokens, visible tool/reasoning steps, and
human-in-the-loop approval gates. Mounted into FastAPI by app/main.py."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import chainlit as cl
from chainlit.input_widget import Select
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from app import persistence
from app.agent import build_graph, fallback_model_for, friendly_error, is_billing_error, pending_tool_calls, rejection_messages, text_of
from app.tools.artifacts import FILE_MARK, marks_in
from app.tools.images import IMAGE_MARK
from app.config import settings
from app.tenancy import Principal, reset_principal, set_principal
from app.uploads import ingest_uploads
from app.tools import registry

log = logging.getLogger("nikki.ui")

SPACE_STAGES = ["building", "pushing", "provisioning", "live"]
SPACE_LABELS = {"queued": "Queued", "building": "Building Container", "pushing": "Pushing to Registry",
                "provisioning": "Provisioning Cloud Run", "live": "Live", "failed": "Failed"}


def _space_chips(status: str) -> str:
    if status == "failed":
        return "🔴 Failed"
    idx = SPACE_STAGES.index(status) if status in SPACE_STAGES else -1
    parts = []
    for i, st in enumerate(SPACE_STAGES):
        mark = "✅" if i < idx or (i == idx and st == "live") else "🔵" if i == idx else "⚪"
        parts.append(f"{mark} {SPACE_LABELS[st]}")
    return "  →  ".join(parts)


async def watch_space(slug: str) -> None:
    """Live status card for a Space deploy; refreshes until the deploy is live or failed."""
    from sqlalchemy import select

    card = cl.Message(content=f"🚀 **Space `{slug}`** — deploying…")
    await card.send()
    for _ in range(240):  # 20 min at 5 s
        await asyncio.sleep(5)
        async with persistence.engine().connect() as conn:
            r = (await conn.execute(select(persistence.spaces).where(persistence.spaces.c.slug == slug))).first()
        if not r:
            card.content = f"🚀 **Space `{slug}`** — record vanished (deleted?)"
            await card.update()
            return
        row = dict(r._mapping)
        body = f"🚀 **Space `{slug}`** — {row['title']}\n\n{_space_chips(row['status'])}"
        if row["status"] == "live":
            body += f"\n\n**Live:** {row['url']}  ·  [Spaces Gallery](/spaces)"
        elif row["status"] == "failed":
            err = (row.get("last_error") or "").strip().splitlines()
            body += "\n\n```\n" + "\n".join(err[-6:])[-1200:] + "\n```"
        if body != card.content:
            card.content = body
            await card.update()
        if row["status"] in ("live", "failed"):
            return
    card.content += "\n\n⏱️ still not finished after 20 minutes — check the Spaces Gallery."
    await card.update()

MODEL_CHOICES = [
    "anthropic:claude-sonnet-4-5",
    "anthropic:claude-opus-4-1",
    "anthropic:claude-haiku-4-5",
    "openai:gpt-4.1",
    "openai:gpt-4.1-mini",
    "openai:o4-mini",
]
if settings.model not in MODEL_CHOICES:
    MODEL_CHOICES.insert(0, settings.model)


# ---------------------------------------------------------------- persistence
@cl.data_layer
def _data_layer():
    return persistence.chainlit_data_layer()


# ---------------------------------------------------------------- auth
@cl.password_auth_callback
async def auth(username: str, password: str) -> cl.User | None:
    from app.auth import authenticate

    p = await authenticate(username, password)
    if p is None:
        return None
    return cl.User(identifier=p.email, metadata={"role": p.role, "tenant_id": p.tenant_id, "email": p.email})


def _principal() -> Principal:
    from app.auth import principal_of

    p = principal_of(cl.user_session.get("user"))
    if p is None:
        raise RuntimeError("no authenticated user in session")
    return p


# ---------------------------------------------------------------- session
async def _settings_panel() -> None:
    await cl.ChatSettings(
        [Select(id="model", label="Model", values=MODEL_CHOICES, initial_index=MODEL_CHOICES.index(settings.model))]
    ).send()


@cl.on_chat_start
async def on_start() -> None:
    set_principal(_principal())
    cl.user_session.set("model", settings.model)
    await _settings_panel()


@cl.on_chat_resume
async def on_resume(thread: dict) -> None:
    set_principal(_principal())
    cl.user_session.set("model", (thread.get("metadata") or {}).get("model", settings.model))
    await _settings_panel()


@cl.on_settings_update
async def on_settings(s: dict) -> None:
    cl.user_session.set("model", s.get("model", settings.model))


# ---------------------------------------------------------------- approval gate
# Approval requests are normal persisted chat messages.  Crucially, no Chainlit message
# handler waits for a decision: awaiting one leaves Chainlit's Stop control active and
# disables the composer. A later action click or typed reply starts a fresh continuation.
_pending_approvals: set[str] = set()
_approval_msgs: dict[str, cl.Message] = {}

_APPROVE_WORDS = r"(approve[d]?|approval|yes|yep|yeah|ok(ay)?|go( ahead)?|do it|proceed|confirm(ed)?|sure|please|y|👍|✅)"
_REJECT_WORDS = r"(reject(ed)?|no|nope|cancel|stop|don'?t|deny|denied|decline[d]?|n|👎|❌)"
APPROVE_RE = re.compile(rf"^(\W*{_APPROVE_WORDS})+\W*$", re.I)
REJECT_RE = re.compile(rf"^(\W*{_REJECT_WORDS})+\W*$", re.I)


def approval_intent(text: str) -> bool | None:
    """True = approve, False = reject, None = not an approval reply."""
    t = (text or "").strip()
    if len(t) > 40:
        return None
    if APPROVE_RE.match(t):
        return True
    if REJECT_RE.match(t):
        return False
    return None


def _approval_text(tool_calls: list[dict]) -> str:
    lines = ["**Nikki wants to run an action that needs your approval:**", ""]
    for tc in tool_calls:
        args = tc.get("args") or {}
        preview = "\n".join(f"  - `{k}`: {str(v)[:400]}" for k, v in args.items()) or "  (no arguments)"
        lines.append(f"- **{tc['name']}**\n{preview}")
    lines += ["", "_Tap a button, or just reply **approve** / **reject**._"]
    return "\n".join(lines)


async def _finish_approval_msg(approval_id: str, verdict: str) -> None:
    msg = _approval_msgs.pop(approval_id, None)
    if not msg:
        return
    try:
        await msg.remove_actions()
        msg.content = msg.content.split("\n\n_Tap a button")[0] + f"\n\n{verdict}"
        await msg.update()
    except Exception:  # noqa: BLE001
        log.debug("could not update approval message", exc_info=True)


async def ask_approval(thread_id: str, tool_calls: list[dict]) -> None:
    """Render an approval checkpoint, then return so the normal composer is usable."""
    if thread_id in _pending_approvals:
        return
    principal = _principal()
    approval_id = await persistence.create_pending_approval(
        thread_id,
        principal.tenant_id,
        principal.email,
        [tc["name"] for tc in tool_calls],
    )
    _pending_approvals.add(thread_id)
    msg = cl.Message(
        content=_approval_text(tool_calls),
        actions=[
            cl.Action(name="approve", payload={"thread_id": thread_id, "approval_id": approval_id}, label="✅ Approve"),
            cl.Action(name="reject", payload={"thread_id": thread_id, "approval_id": approval_id}, label="❌ Reject"),
        ],
    )
    _approval_msgs[approval_id] = msg
    await msg.send()


def _action_target(action: cl.Action) -> tuple[str, str]:
    """Accept an action only in the thread it was rendered for.

    The durable approval record performs the principal check as well; this immediate
    check rejects a copied or forged action payload before it reaches the graph.
    """
    payload = action.payload or {}
    thread_id = str(payload.get("thread_id") or "")
    approval_id = str(payload.get("approval_id") or "")
    session_thread_id = str(getattr(cl.context.session, "thread_id", "") or "")
    if not thread_id or not approval_id or (session_thread_id and session_thread_id != thread_id):
        raise ValueError("approval action does not belong to this conversation")
    return thread_id, approval_id


@cl.action_callback("approve")
async def _on_approve(action: cl.Action) -> None:
    try:
        tid, approval_id = _action_target(action)
    except ValueError:
        log.warning("rejected mismatched approval action")
        await cl.Message(content="That approval button does not belong to this conversation.").send()
        return
    await _continue_approval(tid, True, approval_id)


@cl.action_callback("reject")
async def _on_reject(action: cl.Action) -> None:
    try:
        tid, approval_id = _action_target(action)
    except ValueError:
        log.warning("rejected mismatched rejection action")
        await cl.Message(content="That approval button does not belong to this conversation.").send()
        return
    await _continue_approval(tid, False, approval_id)


# ---------------------------------------------------------------- streaming
class TurnRenderer:
    """Turns LangGraph stream events into Chainlit messages and steps."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.msg: cl.Message | None = None
        self.reasoning: cl.Step | None = None
        self.steps: dict[str, cl.Step] = {}
        self.final_text: list[str] = []
        self.input_tokens: int = 0
        self.output_tokens: int = 0

    async def token(self, text: str) -> None:
        if self.msg is None:
            self.msg = cl.Message(content="")
            await self.msg.send()
        await self.msg.stream_token(text)

    async def thinking(self, text: str) -> None:
        if self.reasoning is None:
            self.reasoning = cl.Step(name="Reasoning", type="llm")
            await self.reasoning.send()
        await self.reasoning.stream_token(text)

    async def close_segment(self) -> None:
        if self.reasoning is not None:
            await self.reasoning.update()
            self.reasoning = None
        if self.msg is not None:
            if self.msg.content.strip():
                self.final_text.append(self.msg.content)
                # `send` finalizes a streamed message. `update` leaves the client turn
                # in its active/Stop state when the next event is an approval checkpoint.
                await self.msg.send()
            else:
                await self.msg.remove()
            self.msg = None

    async def tool_planned(self, tc: dict) -> None:
        step = cl.Step(name=tc["name"], type="tool")
        step.input = tc.get("args") or {}
        await step.send()
        self.steps[tc["id"]] = step
        await persistence.trace(self.thread_id, "tool_call", tc)

    async def show_image(self, out: str) -> None:
        """Render an image a tool saved in the workspace inline in the chat."""
        rel = out[len(IMAGE_MARK):].split(" (", 1)[0].strip()
        path = (settings.workspace_dir / rel).resolve()
        if not path.is_file() or settings.workspace_dir.resolve() not in path.parents:
            return
        img = cl.Image(name=path.name, path=str(path), display="inline", size="large")
        await cl.Message(content="", elements=[img]).send()

    async def tool_result(self, tm: ToolMessage) -> None:
        step = self.steps.pop(tm.tool_call_id, None)
        out = text_of(tm.content)
        if step is None:
            step = cl.Step(name=tm.name or "tool", type="tool")
            await step.send()
        step.output = out
        step.is_error = getattr(tm, "status", "success") == "error"
        await step.update()
        if tm.name == "generate_image" and out.startswith(IMAGE_MARK):
            await self.show_image(out)
        elif FILE_MARK in out:
            await attach_files(marks_in(out))
        if tm.name == "deploy_space" and out.startswith("queued deploy of Space"):
            m = re.search(r"Space '([a-z0-9-]+)'", out)
            if m:
                asyncio.create_task(watch_space(m.group(1)))
        await persistence.trace(self.thread_id, "tool_result", {"tool_call_id": tm.tool_call_id, "name": tm.name, "output": out[:4000]})


async def stream_segment(graph, config: dict, inp: Any, r: TurnRenderer) -> None:
    async for mode, chunk in graph.astream(inp, config, stream_mode=["messages", "updates"]):
        if mode == "messages":
            msg, meta = chunk
            if meta.get("langgraph_node") != "agent" or not isinstance(msg, AIMessageChunk):
                continue
            # Capture token usage from message metadata
            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                usage = msg.usage_metadata
                r.input_tokens = usage.get("input_tokens", 0)
                r.output_tokens = usage.get("output_tokens", 0)
            content = msg.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "thinking" and block.get("thinking"):
                        await r.thinking(block["thinking"])
            text = text_of(content)
            if text:
                await r.token(text)
        else:  # updates
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                for m in update.get("messages", []):
                    if node == "agent" and isinstance(m, AIMessage):
                        # Capture usage from complete AIMessage too
                        if hasattr(m, "usage_metadata") and m.usage_metadata:
                            usage = m.usage_metadata
                            r.input_tokens = usage.get("input_tokens", 0)
                            r.output_tokens = usage.get("output_tokens", 0)
                        if m.tool_calls:
                            await r.close_segment()
                            for tc in m.tool_calls:
                                await r.tool_planned(tc)
                    elif node == "tools" and isinstance(m, ToolMessage):
                        await r.tool_result(m)


# ---------------------------------------------------------------- main turn
_turn_locks: dict[str, asyncio.Lock] = {}


@cl.on_message
async def on_message(message: cl.Message) -> None:
    set_principal(_principal())
    thread_id = cl.context.session.thread_id
    intent = approval_intent(message.content) if not message.elements else None
    if intent is not None:
        await _continue_approval(thread_id, intent)
        return  # typed/spoken answer never becomes a new ordinary request
    lock = _turn_locks.setdefault(thread_id, asyncio.Lock())
    if lock.locked():
        # Two turns on one thread would interleave checkpoints and corrupt the history.
        await cl.Message(content="⏳ I'm still working on your previous message — please wait for it to finish (or approve/reject the pending action) and send that again.").send()
        return
    async with lock:
        await _run_turn(message, thread_id)
    if not lock.locked():
        _turn_locks.pop(thread_id, None)


async def _drive(graph: Any, cp: Any, model: str, config: dict, inp: Any, r: "TurnRenderer", thread_id: str, preapproved: bool | None = None) -> None:
    """Run one turn until complete or until an approval checkpoint is rendered."""
    while True:
        await stream_segment(graph, config, inp, r)
        await r.close_segment()
        state = await graph.aget_state(config)
        if not state.next:
            break
        calls = pending_tool_calls(state)
        gated = [tc for tc in calls if registry.requires_approval(tc["name"], tc.get("args"))]
        if gated:
            if preapproved is None:
                await ask_approval(thread_id, gated)
                return
            else:
                approved, preapproved = preapproved, None
            await persistence.trace(thread_id, "approval", {"approved": approved, "tools": [tc["name"] for tc in gated]})
            if not approved:
                for tc in calls:
                    step = r.steps.pop(tc["id"], None)
                    if step:
                        step.output = "rejected by user"
                        step.is_error = True
                        await step.update()
                await graph.aupdate_state(config, {"messages": rejection_messages(calls)}, as_node="tools")
        # resume from the interrupt; rebuild so hot-loaded skills are bound to the model
        inp = None
        graph = build_graph(cp, model)


async def _continue_approval(thread_id: str, approved: bool, approval_id: str | None = None) -> bool:
    """Resume a paused tool checkpoint in a fresh Chainlit turn.

    This deliberately does not rely on in-memory state: a page reload, a Cloud Run
    revision, or a different instance may serve the user's button click/reply.
    """
    principal = _principal()
    principal_token = set_principal(principal)
    lock = _turn_locks.setdefault(thread_id, asyncio.Lock())
    if lock.locked():
        reset_principal(principal_token)
        return False
    try:
        async with lock:
            model = cl.user_session.get("model") or settings.model
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.recursion_limit}
            r = TurnRenderer(thread_id)
            try:
                approval_id, claim = await persistence.claim_pending_approval(
                    thread_id, principal.tenant_id, principal.email, approved, approval_id
                )
                if claim != "claimed":
                    messages = {
                        "no_pending": "There is no approval waiting in this conversation.",
                        "expired": "That approval expired without running anything. Please send the request again if you still want it done.",
                        "not_owner": "That approval is not available in this conversation.",
                        "approved_running": "That approval is already being processed.",
                        "rejected_running": "That rejection is already being processed.",
                        "completed": "That approval has already been handled.",
                        "superseded": "That approval was replaced by a newer message.",
                        "failed": "That approval did not finish and was left safely blocked. Send a new request rather than retrying the old action.",
                    }
                    await cl.Message(content=messages.get(claim, "That approval is no longer pending.")).send()
                    return True
                async with persistence.checkpointer() as cp:
                    graph = build_graph(cp, model)
                    calls = pending_tool_calls(await graph.aget_state(config))
                    gated = [tc for tc in calls if registry.requires_approval(tc["name"], tc.get("args"))]
                    authorized_tools = await persistence.approval_tool_names(approval_id)
                    if not gated or authorized_tools != [tc["name"] for tc in gated]:
                        await persistence.finish_approval(approval_id, "failed")
                        await cl.Message(
                            content="That approval no longer matches the action that was shown, so nothing was run. Please send the request again."
                        ).send()
                        return True
                    _pending_approvals.discard(thread_id)
                    await _finish_approval_msg(approval_id, "✅ **Approved**" if approved else "❌ **Rejected**")
                    await persistence.trace(thread_id, "approval", {"approved": approved, "tools": [tc["name"] for tc in gated]})
                    if approved:
                        await cl.Message(content=f"✅ Approved — running **{', '.join(tc['name'] for tc in gated)}**.").send()
                        await _drive(graph, cp, model, config, None, r, thread_id, True)
                    else:
                        for tc in calls:
                            step = r.steps.pop(tc["id"], None)
                            if step:
                                step.output = "rejected by user"
                                step.is_error = True
                                await step.update()
                        await graph.aupdate_state(config, {"messages": rejection_messages(calls)}, as_node="tools")
                        await _drive(build_graph(cp, model), cp, model, config, None, r, thread_id)
                await persistence.finish_approval(approval_id)
            except Exception as e:  # noqa: BLE001
                log.exception("approval continuation failed")
                if approval_id:
                    await persistence.finish_approval(approval_id, "failed")
                await r.close_segment()
                await cl.Message(content=friendly_error(e)).send()
                await persistence.trace(thread_id, "error", {"error": repr(e)})
                return False
    finally:
        if not lock.locked():
            _turn_locks.pop(thread_id, None)
        reset_principal(principal_token)
    if r.input_tokens > 0 or r.output_tokens > 0:
        await persistence.log_token_usage(thread_id, model, r.input_tokens, r.output_tokens)
    return True


async def attach_files(rels: list[str]) -> None:
    """Attach workspace files a tool produced: images inline, audio with a player, others as downloads."""
    elements = []
    for rel in rels:
        path = (settings.workspace_dir / rel).resolve()
        if not path.is_file() or settings.workspace_dir.resolve() not in path.parents:
            continue
        ext = path.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            elements.append(cl.Image(name=path.name, path=str(path), display="inline", size="large"))
        elif ext in (".mp3", ".wav", ".m4a", ".ogg", ".webm"):
            elements.append(cl.Audio(name=path.name, path=str(path), display="inline"))
        else:
            elements.append(cl.File(name=path.name, path=str(path), display="inline"))
    if elements:
        await cl.Message(content="", elements=elements).send()


async def _run_turn(message: cl.Message, thread_id: str) -> None:
    model = cl.user_session.get("model") or settings.model
    principal = _principal()
    await persistence.trace(thread_id, "user", {"text": message.content, "model": model})
    r = TurnRenderer(thread_id)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.recursion_limit}

    # Handle file attachments: save each one into the workspace (uploads/) so tools can act on it,
    # and inline a text rendering (PDF/DOCX/XLSX/PPTX/CSV text, image transcription, audio transcript).
    user_content = message.content
    if message.elements:
        user_content = await asyncio.to_thread(ingest_uploads, message.content, message.elements)

    try:
        async with persistence.checkpointer() as cp:
            graph = build_graph(cp, model)
            approval_state = await persistence.supersede_pending_approval(
                thread_id, principal.tenant_id, principal.email
            )
            if approval_state == "running":
                await cl.Message(
                    content="⏳ Your previous approved action is already being processed. Please wait for it to finish before sending another request."
                ).send()
                return
            _pending_approvals.discard(thread_id)
            stale = pending_tool_calls(await graph.aget_state(config))
            inp: Any = {"messages": [HumanMessage(content=user_content)]}
            if stale:
                # A non-approval message supersedes an orphaned checkpoint. This prevents
                # a stale action from ever being executed after the user changes direction.
                await graph.aupdate_state(config, {"messages": rejection_messages(stale, "Superseded by a new user message.")}, as_node="tools")
                graph = build_graph(cp, model)
            try:
                await _drive(graph, cp, model, config, inp, r, thread_id)
            except Exception as e:  # noqa: BLE001
                fb = fallback_model_for(model)
                if not (fb and is_billing_error(e)):
                    raise
                log.warning("billing error on %s; retrying turn on fallback %s: %r", model, fb, e)
                await r.close_segment()
                await cl.Message(
                    content=f"⚠️ My primary model's API credit is exhausted (Stan: top up at console.anthropic.com → Plans & Billing). Continuing on the backup model `{fb}` for now."
                ).send()
                await persistence.trace(thread_id, "fallback", {"from": model, "to": fb, "error": repr(e)})
                graph = build_graph(cp, fb)
                state = await graph.aget_state(config)
                # If the failed call had already checkpointed the user message, resume; else replay it.
                await _drive(graph, cp, fb, config, None if state.next else inp, r, thread_id)
    except Exception as e:  # noqa: BLE001
        log.exception("turn failed")
        await r.close_segment()
        await cl.Message(content=friendly_error(e)).send()
        await persistence.trace(thread_id, "error", {"error": repr(e)})
        return

    await persistence.trace(thread_id, "assistant", {"text": "\n\n".join(r.final_text)[:8000]})
    
    # Log token usage for this turn
    if r.input_tokens > 0 or r.output_tokens > 0:
        await persistence.log_token_usage(thread_id, model, r.input_tokens, r.output_tokens)
