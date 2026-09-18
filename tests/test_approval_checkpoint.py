"""Exercise Nikki's real graph approval checkpoint on local SQLite.

Run directly with ``python tests/test_approval_checkpoint.py``.
"""
import asyncio
import os
import tempfile


async def main() -> None:
    # app.config reads configuration at import time, so set an isolated SQLite
    # database before importing the application.
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/checkpoint.db"
        os.environ["NIKKI_WORKSPACE_DIR"] = f"{tmp}/workspace"
        os.environ["NIKKI_SKILLS_DIR"] = f"{tmp}/skills"

        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        from app import persistence
        from app.agent import pending_tool_calls, rejection_messages
        from langgraph.graph import END, START, StateGraph
        from langgraph.graph.message import MessagesState

        config = {
            "configurable": {"thread_id": "approval-regression"},
            "recursion_limit": 8,
        }
        call = {
            "name": "memory_remember",
            "args": {"content": "approval regression fixture"},
            "id": "call-approval-regression",
            "type": "tool_call",
        }

        async with persistence.checkpointer() as cp:
            # A compact graph recreates the same interrupt and checkpoint
            # semantics without importing an LLM provider in a unit test.
            builder = StateGraph(MessagesState)
            builder.add_node("agent", lambda _: {})
            builder.add_node("tools", lambda _: {})
            builder.add_edge(START, "agent")
            builder.add_edge("agent", "tools")
            builder.add_edge("tools", END)
            graph = builder.compile(checkpointer=cp, interrupt_before=["tools"])
            # Seed the precise graph state that the UI sees after the agent has
            # proposed an approval-gated action and interrupted before tools.
            await graph.aupdate_state(
                config,
                {
                    "messages": [
                        HumanMessage(content="remember this"),
                        AIMessage(content="I can do that.", tool_calls=[call]),
                    ]
                },
                as_node="agent",
            )
            paused = await graph.aget_state(config)
            calls = pending_tool_calls(paused)
            assert paused.next == ("tools",)
            assert calls == [call]

            # Rejection resumes from the checkpoint with a matching ToolMessage;
            # this is the state mutation used by the fresh approval continuation.
            await graph.aupdate_state(
                config,
                {"messages": rejection_messages(calls, "Rejected by regression test.")},
                as_node="tools",
            )
            resumed = await graph.aget_state(config)
            final = resumed.values["messages"][-1]
            assert isinstance(final, ToolMessage)
            assert final.tool_call_id == call["id"]
            assert final.content == "Rejected by regression test."

        await persistence.close_db()


if __name__ == "__main__":
    asyncio.run(main())
    print("approval checkpoint regression checks passed")
