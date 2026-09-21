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
from app.guards import TurnBudget, UsageMeter
from app.tools import registry

log = logging.getLogger("nikki.headless")


async def run_prompt(prompt: str, thread_id: str, auto_approve: bool = False, model: str | None = None) -> str:
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.recursion_limit}
    # The same per-turn budget the interactive path uses. This loop is the one nobody
    # is watching -- and a schedule created through the approval gate may carry
    # auto_approve=True, so an unguarded loop here can push and deploy unattended.
    budget = TurnBudget(
        max_tool_rounds=settings.max_tool_rounds,
        max_repeats=settings.max_repeated_tool_calls,
        token_ceiling=settings.turn_token_ceiling,
    )
    usage = UsageMeter()
    stopped: str | None = None
    await persistence.trace(thread_id, "user", {"text": prompt, "model": model or settings.model, "headless": True})
    async with persistence.checkpointer() as cp:
        graph = build_graph(cp, model)
        stale = pending_tool_calls(await graph.aget_state(config))
        if stale:
            await graph.aupdate_state(config, {"messages": rejection_messages(stale, "Superseded by a new run.")}, as_node="tools")
        inp: Any = {"messages": [HumanMessage(content=prompt)]}
        while True:
            budget.start_round()
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

            # Token accounting. ainvoke does not stream, so every AIMessage in the
            # checkpoint is authoritative; UsageMeter dedupes on message id, so
            # re-walking the whole history each round counts each call once.
            for m in state.values.get("messages", []):
                if isinstance(m, AIMessage):
                    usage.note(m, final=True)

            stop = budget.stop_reason(usage.input_tokens, usage.output_tokens)
            if stop:
                log.warning("headless run %s stopped by budget: %s", thread_id,
                            budget.summary(usage.input_tokens, usage.output_tokens))
                await persistence.trace(thread_id, "budget_stop", {
                    "rounds": budget.rounds, "headless": True,
                    "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                })
                stopped = stop
                break

            if not state.next:
                break
            calls = pending_tool_calls(state)

            repeats = [tc for tc in calls if budget.would_repeat(tc["name"], tc.get("args"))]
            if repeats:
                log.warning("headless run %s blocked repeated calls: %s", thread_id, [tc["name"] for tc in repeats])
                await persistence.trace(thread_id, "repeat_blocked",
                                        {"tools": [tc["name"] for tc in repeats], "headless": True})
                await graph.aupdate_state(
                    config,
                    {"messages": rejection_messages(calls, budget.repeat_message(repeats[0]["name"]))},
                    as_node="tools",
                )
                inp = None
                graph = build_graph(cp, model)
                continue

            for tc in calls:
                budget.record(tc["name"], tc.get("args"))
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
    if stopped:
        final = (final + "\n\n" + stopped).strip() if final else stopped
    if usage.total_tokens:
        await persistence.log_token_usage(thread_id, model or settings.model, usage.input_tokens, usage.output_tokens)
    await persistence.trace(thread_id, "assistant", {"text": final[:8000], "headless": True})
    return final
