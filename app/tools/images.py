"""Image generation: `generate_image` renders a picture from a text prompt with OpenAI's
gpt-image-1 model and saves it under the workspace `images/` folder. The UI shows any
image a tool saved there inline in the chat (see ui.py `tool_result`). Read-only from
the user's point of view (it only creates a new file), so no approval is required.
"""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path

import httpx
from langchain_core.tools import tool

from app.config import settings

API_URL = "https://api.openai.com/v1/images/generations"
MODEL = "gpt-image-1"
SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
QUALITIES = {"low", "medium", "high", "auto"}
IMAGE_MARK = "saved image: "


def images_dir() -> Path:
    d = settings.workspace_dir / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40] or "image").rstrip("-")


@tool
def generate_image(prompt: str, size: str = "1024x1024", quality: str = "medium") -> str:
    """Generate an image from a text description and save it to the workspace (images/ folder).
    The picture is shown to the user automatically. size: 1024x1024 (square), 1536x1024 (landscape),
    1024x1536 (portrait) or auto. quality: low, medium, high or auto (higher = slower and costlier).
    Describe subject, style, composition, lighting and any text to render in the prompt."""
    if not settings.openai_api_key:
        return "error: OPENAI_API_KEY is not configured"
    if size not in SIZES:
        return f"error: size must be one of {sorted(SIZES)}"
    if quality not in QUALITIES:
        return f"error: quality must be one of {sorted(QUALITIES)}"
    payload = {"model": MODEL, "prompt": prompt, "n": 1, "size": size, "quality": quality, "output_format": "png"}
    try:
        with httpx.Client(timeout=180) as c:
            r = c.post(API_URL, json=payload, headers={"Authorization": f"Bearer {settings.openai_api_key}"})
    except httpx.HTTPError as e:
        return f"error: {type(e).__name__}: {e}"
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            msg = r.text
        return f"error: image API {r.status_code}: {msg[:500]}"
    data = r.json().get("data") or []
    if not data or not data[0].get("b64_json"):
        return "error: image API returned no image"
    raw = base64.b64decode(data[0]["b64_json"])
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(prompt)}.png"
    path = images_dir() / name
    path.write_bytes(raw)
    rel = path.relative_to(settings.workspace_dir)
    return f"{IMAGE_MARK}{rel} ({len(raw) // 1024} KB, {size}, {quality}). The image is displayed to the user; describe it briefly."


TOOLS = [generate_image]
