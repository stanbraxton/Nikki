"""Canvas API: OpenAI Realtime API integration for voice + visual workspace.

WebSocket endpoint at /api/canvas/session that:
1. Accepts client audio (browser mic)
2. Forwards to OpenAI Realtime API (GPT-4 with audio)
3. Streams back audio + transcripts + tool calls
4. Executes tools and emits canvas update events
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Cookie, HTTPException, Depends
from fastapi.responses import HTMLResponse
from openai import AsyncOpenAI
import base64

from app.tenancy import Principal
from app.auth import principal_of, require_principal
from app.tools import registry
from app.agent import system_prompt
from app import persistence

log = logging.getLogger(__name__)

router = APIRouter(prefix="/canvas", tags=["canvas"])

# --- Cost guardrails -------------------------------------------------------
# Realtime bills by wall-clock audio, so an open mic with nobody talking costs
# the same as a live conversation. A forgotten browser tab is a real bill.
# Both caps are env-tunable; set either to 0 to disable it.
MAX_SESSION_SECONDS = int(os.environ.get("CANVAS_MAX_SESSION_SECONDS", "900"))
IDLE_SECONDS = int(os.environ.get("CANVAS_IDLE_SECONDS", "180"))
_WATCHDOG_TICK_SECONDS = 5

# Events that prove a human is still in the room. Client audio frames do NOT
# count: the mic streams silence just as steadily as speech, so counting them
# would defeat the idle timer entirely.
_ACTIVITY_EVENTS = frozenset({
    "input_audio_buffer.speech_started",
    "input_audio_buffer.committed",
    "conversation.item.input_audio_transcription.completed",
    "response.done",
})

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    """Lazy OpenAI client."""
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client


class CanvasSession:
    """Manages a single Canvas session: user WebSocket + OpenAI Realtime connection."""
    
    def __init__(self, user_ws: WebSocket, principal: Principal):
        self.user_ws = user_ws
        self.principal = principal
        self.openai_ws = None
        self.session_id: str = ""
        self.running = False
        self.tools = registry.tools(admin=principal.is_admin)
        self.started_at = 0.0
        self.last_activity_at = 0.0
        self.close_reason: str | None = None
        
    async def start(self):
        """Connect to OpenAI Realtime API and start bidirectional streaming."""
        self.running = True
        self.started_at = self.last_activity_at = time.monotonic()
        
        # Create Realtime session
        # https://platform.openai.com/docs/api-reference/realtime
        try:
            import websockets
            
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not configured")
            
            # Connect to OpenAI Realtime API (GA shape - beta retired May 2026)
            uri = "wss://api.openai.com/v1/realtime?model=gpt-realtime"
            headers = {
                "Authorization": f"Bearer {api_key}"
            }
            
            self.openai_ws = await websockets.connect(uri, additional_headers=headers)
            log.info(f"Canvas session started for {self.principal.email}")
            
            # Send session configuration
            await self._configure_session()
            
            # Start bidirectional relay
            await asyncio.gather(
                self._relay_client_to_openai(),
                self._relay_openai_to_client(),
                self._watchdog(),
                return_exceptions=True
            )
            
        except Exception as e:
            log.error(f"Canvas session error: {e}")
            await self.user_ws.send_json({
                "type": "error",
                "error": str(e)
            })
        finally:
            self.running = False
            if self.openai_ws:
                await self.openai_ws.close()
    
    async def _configure_session(self):
        """Send initial configuration to OpenAI Realtime API."""
        # Get system instructions
        instructions = system_prompt("openai:gpt-realtime")  # voice runs on OpenAI Realtime, not the chat model
        
        # Convert tools to OpenAI function format
        tool_schemas = []
        for tool in self.tools:
            model = tool.get_input_schema()
            schema = (model.model_json_schema()
                      if hasattr(model, "model_json_schema") else model.schema())
            schema.pop("title", None)
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})
            tool_schemas.append({
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": schema
            })
        
        config = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": "gpt-realtime",
                "instructions": instructions,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {"model": "whisper-1"},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 500
                        }
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": "nova"
                    }
                },
                "tools": tool_schemas,
                "tool_choice": "auto",
                "max_output_tokens": 4096
            }
        }
        
        await self.openai_ws.send(json.dumps(config))
        log.info(f"Configured Canvas session with {len(tool_schemas)} tools")
    
    async def _relay_client_to_openai(self):
        """Forward client audio/messages to OpenAI."""
        try:
            while self.running:
                data = await self.user_ws.receive_text()
                msg = json.loads(data)
                
                # Client sends: {"type": "audio", "audio": "<base64>"} or text messages
                if msg.get("type") == "audio":
                    # Forward audio chunk to OpenAI
                    await self.openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": msg["audio"]
                    }))
                elif msg.get("type") == "text":
                    self.last_activity_at = time.monotonic()
                    # Text message (for canvas context or user typing)
                    await self.openai_ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{
                                "type": "input_text",
                                "text": msg["text"]
                            }]
                        }
                    }))
                    # Trigger response
                    await self.openai_ws.send(json.dumps({
                        "type": "response.create"
                    }))
                elif msg.get("type") == "commit_audio":
                    self.last_activity_at = time.monotonic()
                    # User stopped speaking, commit the audio buffer
                    await self.openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.commit"
                    }))
                    # Create response
                    await self.openai_ws.send(json.dumps({
                        "type": "response.create"
                    }))
                    
        except WebSocketDisconnect:
            log.info(f"Canvas client disconnected: {self.principal.email}")
        except Exception as e:
            log.error(f"Client relay error: {e}")
    
    async def _relay_openai_to_client(self):
        """Forward OpenAI events to client + execute tool calls."""
        try:
            async for raw_msg in self.openai_ws:
                msg = json.loads(raw_msg)
                event_type = msg.get("type")

                if event_type in _ACTIVITY_EVENTS:
                    self.last_activity_at = time.monotonic()
                
                # Forward transcript and audio to client
                if event_type in [
                    # GA names (the beta event shape was retired in May 2026)
                    "response.output_audio.delta",
                    "response.output_audio.done",
                    "response.output_audio_transcript.delta",
                    "response.output_text.delta",
                    "conversation.item.added",
                    "conversation.item.done",
                    # legacy beta names, kept so either shape still relays
                    "response.audio.delta",
                    "response.audio_transcript.delta",
                    "response.text.delta",
                    "response.audio.done",
                    "conversation.item.created",
                    "response.done",
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped",
                    "input_audio_buffer.committed",
                    "conversation.item.input_audio_transcription.completed"
                ]:
                    await self.user_ws.send_json(msg)
                
                # Handle tool calls
                elif event_type == "response.function_call_arguments.done":
                    await self._execute_tool_call(msg)
                
                # Session errors
                elif event_type == "error":
                    log.error(f"OpenAI Realtime error: {msg}")
                    await self.user_ws.send_json(msg)
                
        except Exception as e:
            log.error(f"OpenAI relay error: {e}")
    
    async def _watchdog(self):
        """Hang up on sessions that run too long or go quiet.

        Without this a Canvas tab left open bills for its entire lifetime,
        which is how a voice feature quietly drains an API balance.
        """
        try:
            while self.running:
                await asyncio.sleep(_WATCHDOG_TICK_SECONDS)
                if not self.running:
                    return
                now = time.monotonic()

                if MAX_SESSION_SECONDS and now - self.started_at >= MAX_SESSION_SECONDS:
                    await self._close_with_reason(
                        "session_limit",
                        f"Voice session ended after {MAX_SESSION_SECONDS // 60} minutes. "
                        "Open Canvas again to keep going.",
                    )
                    return

                if IDLE_SECONDS and now - self.last_activity_at >= IDLE_SECONDS:
                    await self._close_with_reason(
                        "idle_timeout",
                        f"Voice session ended after {IDLE_SECONDS // 60} minutes of silence.",
                    )
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"Canvas watchdog error: {e}")

    async def _close_with_reason(self, reason: str, message: str):
        """Tell the client why we are hanging up, then tear both sockets down."""
        self.running = False
        self.close_reason = reason
        log.info(
            "Canvas session closed (%s) for %s after %.0fs",
            reason, self.principal.email, time.monotonic() - self.started_at,
        )
        try:
            await self.user_ws.send_json({
                "type": "session.closed",
                "reason": reason,
                "message": message,
            })
        except Exception:
            pass
        for sock in (self.openai_ws, self.user_ws):
            if sock is None:
                continue
            try:
                await sock.close()
            except Exception:
                pass

    async def _execute_tool_call(self, msg: dict):
        """Execute a tool call and send results back to OpenAI + canvas update to client."""
        try:
            call_id = msg.get("call_id")
            name = msg.get("name")
            arguments = json.loads(msg.get("arguments", "{}"))
            
            log.info(f"Canvas tool call: {name}({arguments})")
            
            # Find the tool
            tool = next((t for t in self.tools if t.name == name), None)
            if not tool:
                result = {"error": f"Tool {name} not found"}
            else:
                # Execute tool (sync tools run in executor)
                from app.agent import text_of
                try:
                    if asyncio.iscoroutinefunction(tool.func):
                        output = await tool.func(**arguments)
                    else:
                        output = await asyncio.to_thread(tool.func, **arguments)
                    result = {"output": text_of(output)}
                except Exception as e:
                    log.error(f"Tool execution error: {e}")
                    result = {"error": str(e)}
            
            # Send canvas update to client (visual artifact)
            await self.user_ws.send_json({
                "type": "canvas.update",
                "tool": name,
                "arguments": arguments,
                "result": result
            })
            
            # Send result back to OpenAI
            await self.openai_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result)
                }
            }))
            
            # Continue response
            await self.openai_ws.send(json.dumps({
                "type": "response.create"
            }))
            
        except Exception as e:
            log.error(f"Tool execution failed: {e}")
            await self.user_ws.send_json({
                "type": "error",
                "error": f"Tool execution failed: {str(e)}"
            })


async def get_ws_principal(websocket: WebSocket) -> Principal | None:
    """Authenticate a Canvas WebSocket with the same session cookie the UI uses.

    Returns None when the caller is not signed in; the caller MUST then close
    the socket. There is deliberately no anonymous fallback: this endpoint
    exposes the full tool set over voice, including the admin-only file, SQL
    and self-maintenance tools.
    """
    import inspect

    from app.auth import principal_of

    token = websocket.cookies.get("access_token")
    if not token:
        log.warning("Canvas WebSocket: no access_token cookie - rejecting")
        return None

    try:
        try:
            from chainlit.auth import decode_jwt
        except ImportError:
            from chainlit.auth.jwt import decode_jwt

        user = decode_jwt(token)
        if inspect.isawaitable(user):
            user = await user
    except Exception as exc:
        log.warning("Canvas WebSocket: could not decode session token (%s)", exc)
        return None

    principal = principal_of(user)
    if principal is None:
        log.warning("Canvas WebSocket: session token carried no usable principal")
        return None

    return principal


@router.websocket("/session")
async def canvas_session(websocket: WebSocket):
    """
    Canvas WebSocket session: bidirectional audio + tool execution + canvas updates.
    
    Client sends:
    - {"type": "audio", "audio": "<base64 PCM16>"}
    - {"type": "text", "text": "<message>"}
    - {"type": "commit_audio"}
    
    Client receives:
    - OpenAI Realtime events (audio deltas, transcripts, etc.)
    - {"type": "canvas.update", "tool": "<name>", "arguments": {...}, "result": {...}}
    - {"type": "error", "error": "<message>"}
    """
    # Accept first, so we can send a readable reason before closing.
    await websocket.accept()

    principal = await get_ws_principal(websocket)
    if principal is None:
        await websocket.send_json({
            "type": "error",
            "error": "Not signed in. Reload the page, sign in, then open Canvas again.",
        })
        await websocket.close(code=4401)
        return

    log.info(
        "Canvas WebSocket connected: %s (tenant=%s role=%s)",
        principal.email, principal.tenant_id, principal.role,
    )
    
    session = CanvasSession(websocket, principal)
    
    try:
        await session.start()
    except Exception as e:
        log.error(f"Canvas session failed: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass


@router.get("/", response_class=HTMLResponse)
async def canvas_page(principal: Principal = Depends(require_principal)) -> HTMLResponse:
    """Canvas UI: voice + visual workspace."""
    return HTMLResponse(CANVAS_HTML)


CANVAS_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canvas · Nikki</title>
<link rel="icon" type="image/png" href="/public/favicon.png">
<style>
:root{--bg:#0f1014;--canvas:#171922;--line:#262a38;--text:#e8e9f0;--muted:#8b90a5;--primary:#7c5cff;--ok:#2fbf71;--bad:#ff5c72}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;height:100vh;overflow:hidden}
#container{display:flex;height:100vh}
#sidebar{width:320px;background:var(--canvas);border-right:1px solid var(--line);display:flex;flex-direction:column;overflow-y:auto}
#canvas{flex:1;display:flex;flex-direction:column;overflow:hidden}
header{padding:20px;border-bottom:1px solid var(--line)}
header h1{margin:0;font-size:18px;font-weight:600}
header .status{font-size:13px;color:var(--muted);margin-top:8px}
.status.connected{color:var(--ok)}
.status.disconnected{color:var(--bad)}
#controls{padding:20px;border-top:1px solid var(--line)}
.btn{display:block;width:100%;padding:12px;border-radius:10px;border:none;font:600 14px/1.4 inherit;cursor:pointer;transition:all .2s}
.btn.primary{background:var(--primary);color:#fff}
.btn.primary:hover{background:#8a6cff}
.btn.danger{background:var(--bad);color:#fff}
.btn.danger:hover{background:#ff7285}
.btn:disabled{opacity:.5;cursor:not-allowed}
#workspace{flex:1;overflow-y:auto;padding:24px}
.artifact{background:var(--canvas);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}
.artifact h3{margin:0 0 12px;font-size:15px;color:var(--muted);font-weight:500}
.artifact pre{background:#0c0d12;padding:12px;border-radius:8px;font-size:13px;overflow-x:auto;margin:0}
.artifact table{width:100%;border-collapse:collapse;font-size:13px}
.artifact td,.artifact th{padding:8px;text-align:left;border-bottom:1px solid var(--line)}
.artifact th{color:var(--muted);font-weight:500}
#transcript{padding:20px;border-top:1px solid var(--line);flex-shrink:0;max-height:180px;overflow-y:auto}
#transcript .msg{margin-bottom:12px;font-size:13px}
#transcript .user{color:var(--primary)}
#transcript .assistant{color:var(--ok)}
#transcript .error{color:var(--bad)}
.visualizer{width:100%;height:60px;background:#0c0d12;border-radius:8px;margin:12px 0;position:relative;overflow:hidden}
canvas{width:100%;height:100%}
</style>
</head><body>
<div id="container">
  <div id="sidebar">
    <header>
      <h1>🎙️ Canvas</h1>
      <div class="status" id="status">Disconnected</div>
    </header>
    <div id="controls">
      <button id="startBtn" class="btn primary">Start Session</button>
      <button id="stopBtn" class="btn danger" style="display:none">Stop Session</button>
      <div class="visualizer" style="margin-top:20px">
        <canvas id="visualizer"></canvas>
      </div>
    </div>
    <div id="transcript"></div>
  </div>
  <div id="canvas">
    <header>
      <h1>Workspace</h1>
      <a href="/" style="color:var(--muted);text-decoration:none;font-size:14px">← Back to Chat</a>
    </header>
    <div id="workspace"></div>
  </div>
</div>
<script>
let ws = null;
let audioContext = null;
let mediaStream = null;
let audioWorklet = null;
let isRecording = false;

const status = document.getElementById('status');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const workspace = document.getElementById('workspace');
const transcript = document.getElementById('transcript');
const visualizer = document.getElementById('visualizer');
const vizCtx = visualizer.getContext('2d');

function setStatus(text, className) {
  status.textContent = text;
  status.className = 'status ' + className;
}

function addTranscript(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = `${role}: ${text}`;
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
}

function addArtifact(toolName, args, result) {
  const div = document.createElement('div');
  div.className = 'artifact';
  
  let content = '';
  
  // Tool-specific rendering
  if (toolName === 'web_search') {
    content = '<h3>🔍 Web Search</h3>';
    if (result.output) {
      try {
        const data = JSON.parse(result.output);
        content += '<div>';
        data.forEach(item => {
          content += `<p><strong>${escapeHtml(item.title || '')}</strong><br>
                      <span style="color:var(--muted);font-size:12px">${escapeHtml(item.url || '')}</span><br>
                      ${escapeHtml(item.snippet || '')}</p>`;
        });
        content += '</div>';
      } catch {
        content += `<pre>${escapeHtml(result.output || '')}</pre>`;
      }
    }
  } else if (result.output && typeof result.output === 'object') {
    content = `<h3>🔧 ${escapeHtml(toolName)}</h3><pre>${escapeHtml(JSON.stringify(result.output, null, 2))}</pre>`;
  } else if (result.output) {
    content = `<h3>🔧 ${escapeHtml(toolName)}</h3><pre>${escapeHtml(String(result.output))}</pre>`;
  } else if (result.error) {
    content = `<h3 style="color:var(--bad)">❌ ${escapeHtml(toolName)}</h3><pre style="color:var(--bad)">${escapeHtml(result.error)}</pre>`;
  }
  
  div.innerHTML = content;
  workspace.appendChild(div);
  workspace.scrollTop = workspace.scrollHeight;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

async function startSession() {
  try {
    setStatus('Connecting...', 'connecting');
    
    // Get mic access
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    
    // Create audio context
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    const source = audioContext.createMediaStreamSource(mediaStream);
    
    // Connect WebSocket
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/canvas/session`);
    
    ws.onopen = async () => {
      setStatus('Connected', 'connected');
      startBtn.style.display = 'none';
      stopBtn.style.display = 'block';
      isRecording = true;
      
      // Start audio capture with ScriptProcessor (simple, works everywhere)
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = (e) => {
        if (!isRecording || !ws || ws.readyState !== WebSocket.OPEN) return;
        
        const inputData = e.inputBuffer.getChannelData(0);
        const pcm16 = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          pcm16[i] = Math.max(-32768, Math.min(32767, Math.floor(inputData[i] * 32768)));
        }
        
        // Send audio chunk
        const base64 = btoa(String.fromCharCode(...new Uint8Array(pcm16.buffer)));
        ws.send(JSON.stringify({ type: 'audio', audio: base64 }));
        
        // Update visualizer
        updateVisualizer(inputData);
      };
      
      source.connect(processor);
      processor.connect(audioContext.destination);
    };
    
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      
      if (msg.type === 'response.audio.delta') {
        // Play audio response (TODO: queue and play)
      } else if (msg.type === 'response.audio_transcript.delta') {
        addTranscript('assistant', msg.delta || '');
      } else if (msg.type === 'conversation.item.input_audio_transcription.completed') {
        addTranscript('user', msg.transcript || '');
      } else if (msg.type === 'canvas.update') {
        addArtifact(msg.tool, msg.arguments, msg.result);
      } else if (msg.type === 'error') {
        addTranscript('error', msg.error || 'Unknown error');
      }
    };
    
    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
      setStatus('Error', 'disconnected');
    };
    
    ws.onclose = () => {
      setStatus('Disconnected', 'disconnected');
      stopSession();
    };
    
  } catch (err) {
    console.error('Failed to start session:', err);
    alert('Failed to start: ' + err.message);
    setStatus('Error', 'disconnected');
  }
}

function stopSession() {
  isRecording = false;
  if (ws) {
    ws.close();
    ws = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  startBtn.style.display = 'block';
  stopBtn.style.display = 'none';
  setStatus('Disconnected', 'disconnected');
}

function updateVisualizer(audioData) {
  const width = visualizer.width;
  const height = visualizer.height;
  
  vizCtx.fillStyle = '#0c0d12';
  vizCtx.fillRect(0, 0, width, height);
  
  vizCtx.strokeStyle = '#7c5cff';
  vizCtx.lineWidth = 2;
  vizCtx.beginPath();
  
  const sliceWidth = width / audioData.length;
  let x = 0;
  
  for (let i = 0; i < audioData.length; i++) {
    const v = audioData[i] * 0.5 + 0.5;
    const y = v * height;
    
    if (i === 0) {
      vizCtx.moveTo(x, y);
    } else {
      vizCtx.lineTo(x, y);
    }
    
    x += sliceWidth;
  }
  
  vizCtx.stroke();
}

startBtn.addEventListener('click', startSession);
stopBtn.addEventListener('click', stopSession);

// Set canvas size
visualizer.width = visualizer.offsetWidth;
visualizer.height = visualizer.offsetHeight;
</script>
</body></html>
"""
