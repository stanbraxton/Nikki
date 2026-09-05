"""Voice API: OpenAI Whisper (speech-to-text) and TTS (text-to-speech)."""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    """Lazy OpenAI client (uses OPENAI_API_KEY) so importing the app never fails without a key."""
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
) -> dict[str, str]:
    """
    Transcribe audio to text using OpenAI Whisper.
    Accepts audio files in formats: flac, m4a, mp3, mp4, mpeg, mpga, oga, ogg, wav, webm
    """
    try:
        # Read the audio file
        audio_data = await audio.read()
        
        # Create a file-like object with a proper filename
        audio_file = BytesIO(audio_data)
        audio_file.name = audio.filename or "audio.webm"
        
        # Call OpenAI Whisper API
        transcript = await client().audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="text"
        )
        
        log.info(f"Transcribed {len(audio_data)} bytes -> {len(transcript)} chars")
        return {"text": transcript}
        
    except Exception as e:
        log.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


@router.post("/speak")
async def speak(
    text: Annotated[str, Form()],
    voice: Annotated[str, Form()] = "nova",
) -> StreamingResponse:
    """
    Convert text to speech using OpenAI TTS.
    Voices: alloy, echo, fable, onyx, nova, shimmer
    Returns MP3 audio stream.
    """
    try:
        # Call OpenAI TTS API
        response = await client().audio.speech.create(
            model="tts-1",  # or "tts-1-hd" for higher quality
            voice=voice,
            input=text,
            response_format="mp3"
        )
        
        log.info(f"Generated speech for {len(text)} chars with voice={voice}")
        
        # Stream the audio back
        return StreamingResponse(
            response.iter_bytes(),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline; filename=speech.mp3",
                "Cache-Control": "no-cache"
            }
        )
        
    except Exception as e:
        log.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"Text-to-speech failed: {str(e)}")
