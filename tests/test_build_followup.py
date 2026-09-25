"""deploy_app results are posted back into the chat that started the build.

2026-09-25: Nikki queued deploys and ended her turn; nothing woke her when Cloud Build
finished, so results only surfaced when Stan asked. Runs the real _capture_chat,
_build_result_text and _notify_build from app/tools/engineer.py (extracted with ast,
as CLAUDE.md prescribes) against a stubbed chainlit and a live event loop on another
thread — the same shape as a build thread posting into the chat's loop.

Run:  python3 tests/test_build_followup.py
"""
from __future__ import annotations

import ast
import asyncio
import logging
import sys
import threading
import types
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "app" / "tools" / "engineer.py").read_text()
NAMES = {"_capture_chat", "_build_result_text", "_notify_build"}
FAILED = 0


def ok(cond: bool, msg: str) -> None:
    global FAILED
    FAILED += 0 if cond else 1
    print(("PASS " if cond else "FAIL ") + msg)


posted: list[tuple[str, str]] = []  # (session thread id, text)
current = {"ctx": None}


class NoContext(Exception):
    pass


def install_chainlit(fail_send: bool = False) -> None:
    cl = types.ModuleType("chainlit")
    ctxmod = types.ModuleType("chainlit.context")

    def get_context():
        if current["ctx"] is None:
            raise NoContext()
        return current["ctx"]

    def init_ws_context(session):
        current["ctx"] = types.SimpleNamespace(session=session, loop=asyncio.get_running_loop())

    class Message:
        def __init__(self, content: str):
            self.content = content

        async def send(self):
            if fail_send:
                raise RuntimeError("socket gone")
            posted.append((current["ctx"].session.thread_id, self.content))

    cl.Message = Message
    ctxmod.get_context = get_context
    ctxmod.init_ws_context = init_ws_context
    sys.modules["chainlit"] = cl
    sys.modules["chainlit.context"] = ctxmod


def load(rows: dict) -> dict:
    tree = ast.parse(SRC)
    ns: dict = {"asyncio": asyncio, "log": logging.getLogger("t"), "_build_chats": {},
                "_build_row": lambda bid: rows.get(bid)}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "engineer.py", "exec"), ns)
    return ns


# A chat's event loop running on its own thread.
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
chat_session = types.SimpleNamespace(thread_id="thread-123")

install_chainlit()
rows = {
    "gp-ok": {"status": "success", "url": "https://golden-picks.web.app", "log": ""},
    "gp-bad": {"status": "failed", "url": None,
               "log": "creating archive\nuploading\n--- Cloud Build errors ---\nconvex/mlb.ts(28,3): error TS6133\n✖ 'bun run build' failed\n"},
}
ns = load(rows)

# capture
current["ctx"] = None
ok(ns["_capture_chat"]() is None, "no chat context (headless run): nothing captured")
current["ctx"] = types.SimpleNamespace(session=types.SimpleNamespace(thread_id=None), loop=loop)
ok(ns["_capture_chat"]() is None, "session without a thread: nothing captured")
current["ctx"] = types.SimpleNamespace(session=chat_session, loop=loop)
cap = ns["_capture_chat"]()
ok(cap == (chat_session, loop), "chat turn: session and loop captured")

# success posts once, into the right thread
current["ctx"] = None  # the build thread has no chat context of its own
ns["_build_chats"]["gp-ok"] = cap
ns["_notify_build"]("gp-ok", "golden-picks")
ok(len(posted) == 1 and posted[0][0] == "thread-123", "success is posted into the conversation that started it")
ok("succeeded" in posted[0][1] and "https://golden-picks.web.app" in posted[0][1], "success message has the live URL")
ns["_notify_build"]("gp-ok", "golden-picks")
ok(len(posted) == 1, "posted only once")

# failure posts the errors, not the whole log
ns["_build_chats"]["gp-bad"] = cap
ns["_notify_build"]("gp-bad", "golden-picks")
ok(len(posted) == 2 and "failed" in posted[1][1] and "TS6133" in posted[1][1], "failure message carries the build errors")
ok("uploading" not in posted[1][1], "failure message shows only the error section")

# a build nobody is waiting on posts nothing
ns["_notify_build"]("gp-headless", "golden-picks")
ok(len(posted) == 2, "headless build: no message")

# a broken socket never raises into the build thread
install_chainlit(fail_send=True)
ns["_build_chats"]["gp-ok"] = cap
try:
    ns["_notify_build"]("gp-ok", "golden-picks")
    ok(True, "send failure is swallowed (build thread keeps going)")
except Exception as e:  # noqa: BLE001
    ok(False, f"send failure escaped: {e!r}")

# deploy_app must hand out the promise only when a chat was captured
ok("_build_chats[bid] = chat" in SRC and "posted in this conversation" in SRC, "deploy_app registers the chat and tells the model not to poll")

loop.call_soon_threadsafe(loop.stop)
if FAILED:
    raise SystemExit(f"{FAILED} check(s) failed")
