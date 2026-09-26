"""`escalate`: Nikki asks for the stronger model herself.

Keyword routing (agent.route_model) guesses from the user's words before a turn starts.
It cannot see that an innocent-looking request turned out to be hard, or that two fixes
in a row have already failed. This tool lets the model say so. The tool itself does
nothing but return a note; the turn loops in ui.py / headless.py see the call in the
round's pending tool calls and rebuild the graph on NIKKI_ENGINEER_MODEL (with its
deeper thinking) for the rest of the turn. Because the call stays in the thread's
history, route_model keeps the next few turns on that model too.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def escalate(reason: str) -> str:
    """Switch the rest of this task to the stronger engineering model with deeper thinking.
    Call it when the task turns out harder than it looked: a fix failed twice, a build or
    test keeps failing for reasons you don't understand, you are about to design something
    that will be hard to undo, or an analysis result looks too good to be true. Give a
    one-sentence reason. Don't call it for routine work, or when you are already on the
    engineering model. It has no side effects and needs no approval."""
    return ("Escalation noted: " + " ".join((reason or "").split())[:300] + ". Unless the user chose a model "
            "manually, your next step runs on the engineering model with deeper thinking. Re-check your "
            "assumptions before continuing rather than repeating the last approach.")


escalate.metadata = {"requires_approval": False}

TOOLS = [escalate]
