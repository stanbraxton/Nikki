"""Nikki's Chainlit UI: login, streamed tokens, visible tool/reasoning steps, and
human-in-the-loop approval gates. Mounted into FastAPI by app/main.py."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.input_widget import Select
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from app import persistence
from app.agent import build_graph, fallback_model_for, friendly_error, is_billing_error, pending_tool_calls, rejection_messages, text_of
from app.tools.images import IMAGE_MARK
from app.config import settings
from app.tenancy import Principal, set_principal
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
    return SQLAlchemyDataLayer(conninfo=settings.sqlalchemy_async_url)


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
async def ask_approval(tool_calls: list[dict]) -> bool:
    lines = ["**Nikki wants to run an action that needs your approval:**", ""]
    for tc in tool_calls:
        args = tc.get("args") or {}
        preview = "\n".join(f"  - `{k}`: {str(v)[:400]}" for k, v in args.items()) or "  (no arguments)"
        lines.append(f"- **{tc['name']}**\n{preview}")
    res = await cl.AskActionMessage(
        content="\n".join(lines),
        actions=[
            cl.Action(name="approve", payload={"v": "approve"}, label="✅ Approve"),
            cl.Action(name="reject", payload={"v": "reject"}, label="❌ Reject"),
        ],
        timeout=900,
    ).send()
    return bool(res and res.get("payload", {}).get("v") == "approve")


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
                await self.msg.update()
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
    lock = _turn_locks.setdefault(thread_id, asyncio.Lock())
    if lock.locked():
        # Two turns on one thread would interleave checkpoints and corrupt the history.
        await cl.Message(content="⏳ I'm still working on your previous message — please wait for it to finish (or approve/reject the pending action) and send that again.").send()
        return
    async with lock:
        await _run_turn(message, thread_id)
    if not lock.locked():
        _turn_locks.pop(thread_id, None)


async def _drive(graph: Any, cp: Any, model: str, config: dict, inp: Any, r: "TurnRenderer", thread_id: str) -> None:
    """Run one turn to completion, pausing for approvals on gated tools."""
    while True:
        await stream_segment(graph, config, inp, r)
        await r.close_segment()
        state = await graph.aget_state(config)
        if not state.next:
            break
        calls = pending_tool_calls(state)
        gated = [tc for tc in calls if registry.requires_approval(tc["name"])]
        if gated:
            approved = await ask_approval(gated)
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


async def _run_turn(message: cl.Message, thread_id: str) -> None:
    model = cl.user_session.get("model") or settings.model
    await persistence.trace(thread_id, "user", {"text": message.content, "model": model})
    r = TurnRenderer(thread_id)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.recursion_limit}

    # Handle file attachments: images (vision), PDFs, DOCX, TXT
    user_content = message.content
    if message.elements:
        import base64
        from pathlib import Path
        
        file_contents = []
        
        for el in message.elements:
            try:
                el_path = Path(el.path)
                suffix = el_path.suffix.lower()
                
                # Images: use vision
                if el.mime and "image" in el.mime:
                    img_data = el_path.read_bytes()
                    b64 = base64.b64encode(img_data).decode('utf-8')
                    mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', 
                                '.gif': 'image/gif', '.webp': 'image/webp'}
                    mime = mime_map.get(suffix, el.mime or 'image/png')
                    
                    if settings.openai_api_key:
                        from openai import OpenAI
                        client = OpenAI(api_key=settings.openai_api_key)
                        response = client.chat.completions.create(
                            model="gpt-4o",
                            messages=[{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Describe this image in detail."},
                                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                                ]
                            }],
                            max_tokens=1000
                        )
                        desc = response.choices[0].message.content or "(no description)"
                        file_contents.append(f"[Image: {el.name}]\n{desc}")
                
                # PDFs: extract text
                elif suffix == '.pdf':
                    try:
                        import pypdf
                        reader = pypdf.PdfReader(el_path)
                        text_parts = []
                        for i, page in enumerate(reader.pages[:100], 1):  # limit to 100 pages
                            page_text = page.extract_text()
                            if page_text:
                                text_parts.append(f"--- Page {i} ---\n{page_text}")
                        extracted = "\n\n".join(text_parts)
                        if extracted:
                            file_contents.append(f"[PDF: {el.name}]\n{extracted[:50000]}")  # limit to 50k chars
                        else:
                            file_contents.append(f"[PDF: {el.name}] (no text extracted)")
                    except ImportError:
                        file_contents.append(f"[PDF: {el.name}] (pypdf not available; install with: pip install pypdf)")
                
                # DOCX: extract text
                elif suffix in ('.docx', '.doc'):
                    try:
                        import docx
                        doc = docx.Document(el_path)
                        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                        extracted = "\n\n".join(paragraphs)
                        if extracted:
                            file_contents.append(f"[DOCX: {el.name}]\n{extracted[:50000]}")
                        else:
                            file_contents.append(f"[DOCX: {el.name}] (no text extracted)")
                    except ImportError:
                        file_contents.append(f"[DOCX: {el.name}] (python-docx not available; install with: pip install python-docx)")
                
                # Plain text files
                elif suffix in ('.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml'):
                    text = el_path.read_text(encoding='utf-8', errors='ignore')
                    file_contents.append(f"[{suffix.upper().lstrip('.')}: {el.name}]\n{text[:50000]}")
                
                else:
                    # Unsupported file type
                    file_contents.append(f"[File: {el.name}] (unsupported format: {suffix})")
                    
            except Exception as e:
                log.exception("failed to process file %s", el.name)
                file_contents.append(f"[File: {el.name}] (failed to process: {e})")
        
        if file_contents:
            user_content = f"{user_content}\n\n" + "\n\n".join(file_contents) if user_content else "\n\n".join(file_contents)

    try:
        async with persistence.checkpointer() as cp:
            graph = build_graph(cp, model)
            # A previous turn may have been abandoned mid-approval; settle its pending
            # tool calls as rejected so the chat history stays valid for the provider.
            stale = pending_tool_calls(await graph.aget_state(config))
            if stale:
                await graph.aupdate_state(config, {"messages": rejection_messages(stale, "Superseded by a new user message.")}, as_node="tools")
            inp: Any = {"messages": [HumanMessage(content=user_content)]}
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
