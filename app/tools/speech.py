"""Speech tools: transcribe audio files (OpenAI Whisper) and turn text into an MP3 (OpenAI TTS).
Both save/read files in the workspace; the UI attaches produced audio with a player."""
from __future__ import annotations

import time
from pathlib import Path

from langchain_core.tools import tool

from app.config import settings
from app.tools.artifacts import out_dir, safe_name, saved, workspace_path

VOICES = {"alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"}


def _client():
    from openai import OpenAI

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=settings.openai_api_key)


def transcribe_path(p: Path) -> str:
    with p.open("rb") as fh:
        r = _client().audio.transcriptions.create(model="whisper-1", file=fh, response_format="text")
    return str(r).strip() or "(no speech detected)"


@tool
def transcribe_audio(path: str) -> str:
    """Transcribe an audio or video file from the workspace to text (mp3, m4a, wav, webm, ogg, mp4, flac;
    up to 25 MB). Voice memos the user attaches in chat are saved under uploads/."""
    try:
        p = workspace_path(path, must_exist=True)
        if p.stat().st_size > 25 * 1024 * 1024:
            return "error: file is over Whisper's 25 MB limit"
        return transcribe_path(p)
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


@tool
def text_to_speech(text: str, voice: str = "nova", name: str = "") -> str:
    """Read text aloud: generate an MP3 from `text` (up to ~4000 characters) and attach it to the chat.
    voice: alloy, ash, coral, echo, fable, onyx, nova, sage or shimmer."""
    try:
        if voice not in VOICES:
            return f"error: voice must be one of {sorted(VOICES)}"
        if not text.strip():
            return "error: nothing to say"
        text = text[:4096]
        r = _client().audio.speech.create(model="tts-1", voice=voice, input=text, response_format="mp3")
        fname = safe_name(name, "") or f"speech-{time.strftime('%Y%m%d-%H%M%S')}"
        if not fname.lower().endswith(".mp3"):
            fname += ".mp3"
        p = out_dir("audio") / fname
        p.write_bytes(r.content)
        return saved(p, f"voice {voice}, {len(text)} chars")
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"


for _t in (transcribe_audio, text_to_speech):
    _t.metadata = {"requires_approval": False}

TOOLS = [transcribe_audio, text_to_speech]
