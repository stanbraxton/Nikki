"""Run the agent without the chat UI (scheduled tasks, API calls). Gated tools are auto-
rejected unless `auto_approve=True` — which only a schedule created through the approval
gate can set. Returns the assistant's final text; traces are written like a normal turn."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app import persistence
from app.agent import build_graph, fallback_model_for, is_billing_error, pending_tool_calls, rejection_messages, text_of
from app.config import settings
from app.tools import registry

log = logging.getLogger("nikki.headless")


async def run_prompt(prompt: str, thread_id: str, auto_approve: bool = False, model: str | None = None) -> str:
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.recursion_limit}
    await persistence.trace(thread_id, "user", {"text": prompt, "model": model or settings.model, "headless": True})
    async with persistence.checkpointer() as cp:
        graph = build_graph(cp, model)
        stale = pending_tool_calls(await graph.aget_state(config))
        if stale:
            await graph.aupdate_state(config, {"messages": rejection_messages(stale, "Superseded by a new run.")}, as_node="tools")
        inp: Any = {"messages": [HumanMessage(content=prompt)]}
        for _ in range(settings.recursion_limit):
            try:
                await graph.ainvoke(inp, config)
            except Exception as e:  # noqa: BLE001
                fb = fallback_model_for(model)
                if not (fb and is_billing_error(e)):
                    raise
                log.warning("billing error on %s; continuing headless run on fallback %s: %r", model or settings.model, fb, e)
                await persistence.trace(thread_id, "fallback", {"from": model or settings.model, "to": fb, "error": repr(e)})
                model = fb
                graph = build_graph(cp, model)
                state = await graph.aget_state(config)
                await graph.ainvoke(None if state.next else inp, config)
            state = await graph.aget_state(config)
            if not state.next:
                break
            calls = pending_tool_calls(state)
            for tc in calls:
                await persistence.trace(thread_id, "tool_call", tc)
            gated = [tc for tc in calls if registry.requires_approval(tc["name"], tc.get("args"))]
            if gated and not auto_approve:
                await persistence.trace(thread_id, "approval", {"approved": False, "tools": [tc["name"] for tc in gated], "reason": "headless"})
                await graph.aupdate_state(
                    config,
                    {"messages": rejection_messages(calls, "Rejected: this is an unattended scheduled run and the action needs human approval. Report what you would have done instead.")},
                    as_node="tools",
                )
            elif gated:
                await persistence.trace(thread_id, "approval", {"approved": True, "tools": [tc["name"] for tc in gated], "reason": "schedule auto_approve"})
            inp = None
            graph = build_graph(cp, model)
        state = await graph.aget_state(config)
    msgs = state.values.get("messages", [])
    final = next((text_of(m.content) for m in reversed(msgs) if isinstance(m, AIMessage) and text_of(m.content).strip()), "")
    await persistence.trace(thread_id, "assistant", {"text": final[:8000], "headless": True})
    return final
