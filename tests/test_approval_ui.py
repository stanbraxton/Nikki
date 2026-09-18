"""Focused regression checks for the approval composer state.

Run directly with ``python tests/test_approval_ui.py``; no extra test runner needed.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "app" / "ui.py").read_text()


def test_streaming_reply_is_finalized_before_approval_prompt():
    """A live stream makes Chainlit show Stop instead of the send arrow."""
    start = UI.index("async def close_segment")
    end = UI.index("async def tool_planned", start)
    segment = UI[start:end]
    assert "await self.msg.send()" in segment
    assert "await self.msg.update()" not in segment


def test_approvals_stay_normal_messages_with_typed_fallback():
    """Never regress to Chainlit's reconnect-fragile ask-message implementation."""
    assert "cl.AskActionMessage(" not in UI
    assert 'cl.Action(name="approve"' in UI
    assert "def approval_intent" in UI


def test_approval_checkpoint_returns_instead_of_waiting_for_a_future():
    """An approval must end the handler so the normal send arrow returns."""
    assert "asyncio.Future" not in UI
    assert "asyncio.wait_for(" not in UI
    drive = UI[UI.index("async def _drive"):UI.index("async def _continue_approval")]
    ask_at = drive.index("await ask_approval(thread_id, gated)")
    assert "return" in drive[ask_at:]
    continuation = UI[UI.index("async def _continue_approval"):UI.index("async def attach_files")]
    assert "async with lock:" in continuation
    assert "pending_tool_calls" in continuation
    assert "await _drive(graph, cp, model, config, None, r, thread_id, True)" in continuation


if __name__ == "__main__":
    test_streaming_reply_is_finalized_before_approval_prompt()
    test_approvals_stay_normal_messages_with_typed_fallback()
    test_approval_checkpoint_returns_instead_of_waiting_for_a_future()
    print("approval UI regression checks passed")
