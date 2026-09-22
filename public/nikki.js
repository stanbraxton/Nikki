// Nikki UI extras
//   - "Create account" link on the login page
//   - Admin / Spaces / Canvas links in the top header for platform admins
//   - Canvas: realtime voice client for the WebSocket at /canvas/session
//   - Chat voice: push-to-talk dictation + per-message playback
//
// Canvas talks to app/canvas.py, which relays to the OpenAI Realtime API.
// Protocol, from that file:
//   client -> server : {"type":"audio","audio":"<base64 pcm16>"}
//                      {"type":"text","text":"..."}
//   server -> client : raw OpenAI Realtime events, forwarded verbatim
//                      (response.audio.delta, response.audio_transcript.delta,
//                       input_audio_buffer.speech_started/stopped,
//                       conversation.item.input_audio_transcription.completed,
//                       response.done, error)
//
// Audio is pcm16 @ 24 kHz mono, in both directions. Turn detection is
// server_vad on the OpenAI side, so the client streams continuously and does
// NOT do its own voice-activity detection.
//
// No emoji literals: the previous build was served as latin-1 and rendered
// them as mojibake. Icons are inline SVG.
(function () {
  'use strict';

  var WS_PATH = '/canvas/session';
  var SAMPLE_RATE = 24000;      // must match input/output_audio_format pcm16
  var FRAME = 4096;             // ~170ms per chunk at 24kHz
  var STT_URL = '/api/voice/transcribe';
  var TTS_URL = '/api/voice/speak';

  // ---------------------------------------------------------------- icons
  var ICON_MIC = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>';
  var ICON_STOP = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
  var ICON_SPK_ON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>';
  var ICON_SPK_OFF = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><line x1="16" y1="9" x2="22" y2="15"/><line x1="22" y1="9" x2="16" y2="15"/></svg>';
  var ICON_WAVE = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="10" x2="4" y2="14"/><line x1="8" y1="6" x2="8" y2="18"/><line x1="12" y1="3" x2="12" y2="21"/><line x1="16" y1="6" x2="16" y2="18"/><line x1="20" y1="10" x2="20" y2="14"/></svg>';
  var ICON_X = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';

  // ------------------------------------------------------------ PCM codecs
  function b64ToInt16(b64) {
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Int16Array(bytes.buffer);
  }

  function float32ToB64Pcm16(f32) {
    var i16 = new Int16Array(f32.length);
    for (var i = 0; i < f32.length; i++) {
      var s = f32[i];
      if (s > 1) s = 1; else if (s < -1) s = -1;
      i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    var bytes = new Uint8Array(i16.buffer);
    var bin = '';
    // chunked so we do not blow the argument limit on large buffers
    for (var j = 0; j < bytes.length; j += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(j, j + 0x8000));
    }
    return btoa(bin);
  }

  // --------------------------------------------------------- DOM utilities
  function setReactValue(el, value) {
    // Chainlit's textarea is React-controlled; assigning .value directly does
    // not update React's value tracker and the change is silently discarded.
    var proto = el.tagName === 'TEXTAREA'
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function chatInput() { return document.getElementById('chat-input'); }   // IS the textarea
  function submitBtn() { return document.getElementById('chat-submit'); }
  function isBusy() {
    if (document.getElementById('stop-button')) return true;
    var b = submitBtn();
    return !b || b.disabled;
  }
  function assistantSteps() {
    // Chainlit marks messages with data-step-type, not data-author.
    return document.querySelectorAll('.step[data-step-type="assistant_message"]');
  }
  function stepText(step) {
    if (!step) return '';
    var c = step.querySelector('.message-content') || step;
    return (c.innerText || c.textContent || '').trim();
  }
  function voiceEnabled() { return localStorage.getItem('nikki-voice-enabled') !== 'false'; }

  // =====================================================================
  //  CANVAS - realtime voice
  // =====================================================================
  var C = {
    open: false, ws: null, micCtx: null, playCtx: null, stream: null,
    proc: null, sources: [], nextTime: 0, state: 'idle', level: 0,
    userLine: '', nikkiLine: ''
  };

  function playPcm(int16) {
    if (!C.playCtx) {
      C.playCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
      C.nextTime = C.playCtx.currentTime;
    }
    if (C.playCtx.state === 'suspended') C.playCtx.resume();

    var f32 = new Float32Array(int16.length);
    for (var i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;

    var buf = C.playCtx.createBuffer(1, f32.length, SAMPLE_RATE);
    buf.copyToChannel(f32, 0);
    var src = C.playCtx.createBufferSource();
    src.buffer = buf;
    src.connect(C.playCtx.destination);

    // Schedule back-to-back so consecutive deltas play gaplessly.
    var at = Math.max(C.playCtx.currentTime + 0.02, C.nextTime);
    src.start(at);
    C.nextTime = at + buf.duration;
    C.sources.push(src);
    src.onended = function () {
      C.sources = C.sources.filter(function (s) { return s !== src; });
    };
  }

  function stopPlayback() {
    C.sources.forEach(function (s) { try { s.stop(); } catch (e) {} });
    C.sources = [];
    if (C.playCtx) C.nextTime = C.playCtx.currentTime;
  }

  function wsUrl() {
    var scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
    return scheme + location.host + WS_PATH;
  }

  function openCanvas() {
    if (C.open) return;
    buildOverlay();
    injectStyles();
    document.getElementById('nikki-canvas').classList.add('nk-visible');
    C.open = true;
    document.addEventListener('keydown', onEsc);
    setStatus('Connecting...');
    setMode('connecting');

    navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,   // keeps Nikki's own voice out of the mic
        noiseSuppression: true,
        autoGainControl: true
      }
    }).then(function (stream) {
      C.stream = stream;
      connect();
    }).catch(function (err) {
      console.error('[nikki] mic denied', err);
      setStatus('Microphone blocked. Allow mic access for this site, then reopen Canvas.');
      setMode('error');
    });
  }

  function connect() {
    var ws;
    try { ws = new WebSocket(wsUrl()); } catch (e) {
      setStatus('Could not open the Canvas connection: ' + e.message);
      setMode('error');
      return;
    }
    C.ws = ws;

    ws.onopen = function () {
      setStatus('Connected - just start talking');
      setMode('listening');
      startMic();
    };

    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      handleEvent(msg);
    };

    ws.onerror = function () {
      setStatus('Connection error. Check that OPENAI_API_KEY is configured on the server.');
      setMode('error');
    };

    ws.onclose = function (ev) {
      if (!C.open) return;
      setMode('error');
      setStatus(ev.code === 1008 || ev.code === 1011
        ? 'Server closed the session (' + ev.code + '). Check the Canvas logs.'
        : 'Disconnected (' + ev.code + ').');
      stopMic();
    };
  }

  function startMic() {
    if (!C.stream) return;
    var AC = window.AudioContext || window.webkitAudioContext;
    C.micCtx = new AC({ sampleRate: SAMPLE_RATE });
    var source = C.micCtx.createMediaStreamSource(C.stream);

    // ScriptProcessor is deprecated but needs no separate worklet file and is
    // supported everywhere. 4096 frames @ 24kHz is ~170ms per send.
    C.proc = C.micCtx.createScriptProcessor(FRAME, 1, 1);
    C.proc.onaudioprocess = function (e) {
      if (!C.ws || C.ws.readyState !== 1) return;
      var f32 = e.inputBuffer.getChannelData(0);

      var peak = 0;
      for (var i = 0; i < f32.length; i += 16) {
        var a = f32[i] < 0 ? -f32[i] : f32[i];
        if (a > peak) peak = a;
      }
      C.level = peak;
      var ring = document.querySelector('#nikki-canvas .nk-orb-ring');
      if (ring) ring.style.transform = 'scale(' + (1 + Math.min(peak * 5, 1.1)) + ')';

      C.ws.send(JSON.stringify({ type: 'audio', audio: float32ToB64Pcm16(f32) }));
    };

    // Route through a silent gain node: connecting straight to destination
    // would echo the raw mic back out of the speakers.
    var mute = C.micCtx.createGain();
    mute.gain.value = 0;
    source.connect(C.proc);
    C.proc.connect(mute);
    mute.connect(C.micCtx.destination);
  }

  function stopMic() {
    if (C.proc) { try { C.proc.disconnect(); C.proc.onaudioprocess = null; } catch (e) {} C.proc = null; }
    if (C.micCtx) { try { C.micCtx.close(); } catch (e) {} C.micCtx = null; }
  }

  function handleEvent(msg) {
    switch (msg.type) {
      case 'input_audio_buffer.speech_started':
        // Server VAD heard the user. Cut Nikki off mid-sentence.
        stopPlayback();
        C.nikkiLine = '';
        setMode('recording');
        setStatus('Listening...');
        break;

      case 'input_audio_buffer.speech_stopped':
        setMode('thinking');
        setStatus('Thinking...');
        break;

      case 'conversation.item.input_audio_transcription.completed':
        C.userLine = msg.transcript || '';
        renderTranscript();
        break;

      case 'response.output_audio.delta':
      case 'response.audio.delta':
        if (msg.delta) {
          setMode('speaking');
          setStatus('Nikki is speaking...');
          playPcm(b64ToInt16(msg.delta));
        }
        break;

      case 'response.output_audio_transcript.delta':
      case 'response.output_text.delta':
      case 'response.audio_transcript.delta':
      case 'response.text.delta':
        C.nikkiLine += (msg.delta || '');
        renderTranscript();
        break;

      case 'response.done':
        setMode('listening');
        setStatus('Listening...');
        break;

      case 'error':
        setMode('error');
        setStatus('Error: ' + (msg.error || (msg.error && msg.error.message) || 'unknown'));
        break;

      default:
        break;
    }
  }

  function renderTranscript() {
    var el = document.querySelector('#nikki-canvas .nk-transcript');
    if (!el) return;
    var html = '';
    if (C.userLine) html += '<div class="nk-you"><b>You</b> ' + esc(C.userLine) + '</div>';
    if (C.nikkiLine) html += '<div class="nk-her"><b>Nikki</b> ' + esc(C.nikkiLine) + '</div>';
    el.innerHTML = html;
    el.scrollTop = el.scrollHeight;
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function closeCanvas() {
    if (!C.open) return;
    C.open = false;
    document.removeEventListener('keydown', onEsc);
    stopMic();
    stopPlayback();
    if (C.playCtx) { try { C.playCtx.close(); } catch (e) {} C.playCtx = null; }
    if (C.ws) { try { C.ws.close(1000); } catch (e) {} C.ws = null; }
    if (C.stream) { C.stream.getTracks().forEach(function (t) { t.stop(); }); C.stream = null; }
    C.userLine = C.nikkiLine = '';
    var el = document.getElementById('nikki-canvas');
    if (el) el.classList.remove('nk-visible');
    if (location.pathname === '/canvas') history.replaceState({}, '', '/');
  }

  function onEsc(e) { if (e.key === 'Escape') closeCanvas(); }

  // ------------------------------------------------------------- overlay UI
  function buildOverlay() {
    if (document.getElementById('nikki-canvas')) return;
    var el = document.createElement('div');
    el.id = 'nikki-canvas';
    el.innerHTML =
      '<div class="nk-backdrop"></div>' +
      '<div class="nk-panel">' +
        '<button class="nk-close" title="Close Canvas">' + ICON_X + '</button>' +
        '<div class="nk-orb"><div class="nk-orb-ring"></div><div class="nk-orb-core">' + ICON_WAVE + '</div></div>' +
        '<div class="nk-status">Starting...</div>' +
        '<div class="nk-transcript"></div>' +
        '<div class="nk-actions"><button class="nk-btn nk-mute">Mute mic</button></div>' +
        '<div class="nk-hint">Speak naturally - interrupt any time. Esc to exit.</div>' +
      '</div>';
    document.body.appendChild(el);

    el.querySelector('.nk-close').addEventListener('click', closeCanvas);
    el.querySelector('.nk-backdrop').addEventListener('click', closeCanvas);

    var mute = el.querySelector('.nk-mute');
    mute.addEventListener('click', function () {
      if (!C.stream) return;
      var track = C.stream.getAudioTracks()[0];
      if (!track) return;
      track.enabled = !track.enabled;
      mute.textContent = track.enabled ? 'Mute mic' : 'Unmute mic';
      setStatus(track.enabled ? 'Listening...' : 'Mic muted');
    });
  }

  function setStatus(s) {
    var el = document.querySelector('#nikki-canvas .nk-status');
    if (el) el.textContent = s;
  }
  function setMode(m) {
    var el = document.getElementById('nikki-canvas');
    if (el) el.setAttribute('data-mode', m);
  }

  function injectStyles() {
    if (document.getElementById('nikki-canvas-style')) return;
    var s = document.createElement('style');
    s.id = 'nikki-canvas-style';
    s.textContent = [
      '#nikki-canvas{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center}',
      '#nikki-canvas.nk-visible{display:flex}',
      '#nikki-canvas .nk-backdrop{position:absolute;inset:0;background:rgba(10,10,16,.74);backdrop-filter:blur(6px)}',
      '#nikki-canvas .nk-panel{position:relative;width:min(460px,92vw);padding:34px 28px 24px;border-radius:20px;background:#15161f;border:1px solid #2c2e3d;box-shadow:0 24px 70px rgba(0,0,0,.55);text-align:center;color:#e8e6f5;font:14px/1.5 system-ui,-apple-system,sans-serif}',
      '#nikki-canvas .nk-close{position:absolute;top:12px;right:12px;background:none;border:0;color:#8b8ba3;cursor:pointer;padding:6px;border-radius:8px}',
      '#nikki-canvas .nk-close:hover{background:#23242f;color:#e8e6f5}',
      '#nikki-canvas .nk-orb{position:relative;width:104px;height:104px;margin:4px auto 20px;display:flex;align-items:center;justify-content:center}',
      '#nikki-canvas .nk-orb-ring{position:absolute;inset:0;border-radius:50%;background:radial-gradient(circle,rgba(201,189,255,.30),rgba(201,189,255,0) 70%);transition:transform .08s ease-out}',
      '#nikki-canvas .nk-orb-core{position:relative;width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:rgba(35,36,49,.5);border:2px solid #4a4d66;color:#c9bdff;transition:all .25s}',
      '#nikki-canvas[data-mode="recording"] .nk-orb-core{border-color:#5ad18f;color:#5ad18f;box-shadow:0 0 0 6px rgba(90,209,143,.12)}',
      '#nikki-canvas[data-mode="thinking"] .nk-orb-core{border-color:#f2c14e;color:#f2c14e}',
      '#nikki-canvas[data-mode="speaking"] .nk-orb-core{border-color:#4a9eff;color:#4a9eff;box-shadow:0 0 0 6px rgba(74,158,255,.14)}',
      '#nikki-canvas[data-mode="error"] .nk-orb-core{border-color:#ff6b6b;color:#ff6b6b}',
      '#nikki-canvas .nk-status{font-size:15px;font-weight:500;min-height:22px}',
      '#nikki-canvas .nk-transcript{margin-top:12px;font-size:13px;color:#a9a7c0;max-height:140px;overflow:auto;text-align:left}',
      '#nikki-canvas .nk-transcript b{color:#c9bdff;margin-right:6px}',
      '#nikki-canvas .nk-you,#nikki-canvas .nk-her{margin:4px 0}',
      '#nikki-canvas .nk-actions{margin-top:18px;display:flex;gap:8px;justify-content:center}',
      '#nikki-canvas .nk-btn{background:#23242f;border:1px solid #3a3c4e;color:#c9c7dd;border-radius:9px;padding:8px 14px;font-size:13px;cursor:pointer}',
      '#nikki-canvas .nk-btn:hover{background:#2d2f3d;color:#fff}',
      '#nikki-canvas .nk-hint{margin-top:16px;font-size:11.5px;color:#6f7089}',
      '.nikki-speaker-btn{background:#2d2d40;border:1px solid #4a9eff;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px;margin-top:8px;color:#c9bdff;display:inline-flex;align-items:center;gap:5px}',
      '.nikki-speaker-btn:hover{background:#3d3d50}'
    ].join('\n');
    document.head.appendChild(s);
  }

  // Intercept every route into /canvas. The server mounts only a WebSocket at
  // /canvas/session - there is no GET /canvas - so navigating there falls
  // through to the Chainlit catch-all and renders an empty chat.
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href$="/canvas"]');
    var b = !a && e.target.closest && e.target.closest('button');
    if (!a && !(b && b.textContent.trim() === 'Canvas')) return;
    e.preventDefault();
    e.stopPropagation();
    openCanvas();
  }, true);

  // =====================================================================
  //  Header links
  // =====================================================================
  function onLogin() {
    if (!location.pathname.startsWith('/login') || document.getElementById('nikki-signup')) return;
    var form = document.querySelector('form');
    if (!form) return;
    var p = document.createElement('p');
    p.id = 'nikki-signup';
    p.style.cssText = 'text-align:center;margin-top:14px;font-size:13px;opacity:.8';
    p.innerHTML = 'New to Nikki? <a href="/signup" style="color:#c9bdff">Create an account</a>';
    form.parentNode.appendChild(p);
  }

  var me = null, meLoading = false;

  function linkEl(href, label) {
    var a = document.createElement('a');
    a.href = href; a.textContent = label; a.className = 'nikki-admin-link';
    a.style.cssText = 'color:#c9bdff;text-decoration:none;border:1px solid #333;padding:3px 10px;border-radius:999px;background:#171922;font:12px system-ui;line-height:18px;margin-right:4px;white-space:nowrap';
    return a;
  }

  function adminLinks() {
    if (location.pathname.startsWith('/login') || location.pathname.startsWith('/signup')) return;
    if (!me) {
      if (meLoading) return;
      meLoading = true;
      fetch('/api/me').then(function (r) { return r.ok ? r.json() : null; }).then(function (m) {
        meLoading = false; me = m || { role: 'none' }; adminLinks();
      }).catch(function () { meLoading = false; });
      return;
    }
    if (me.role !== 'admin' || document.getElementById('nikki-admin-links')) return;
    var header = document.getElementById('header');
    var right = header ? header.querySelector(':scope > div.flex.items-center.gap-1') : null;
    var bar = document.createElement('div');
    bar.id = 'nikki-admin-links';
    if (right) {
      bar.style.cssText = 'display:flex;align-items:center;gap:4px;margin-right:6px';
      right.insertBefore(bar, right.firstChild);
    } else if (header) {
      bar.style.cssText = 'display:flex;align-items:center;gap:4px;margin-left:12px';
      header.appendChild(bar);
    } else { return; }
    bar.appendChild(linkEl('/admin', 'Admin'));
    bar.appendChild(linkEl('/spaces', 'Spaces'));
    bar.appendChild(linkEl('/canvas', 'Canvas'));
  }

  // =====================================================================
  //  Chat voice: dictation + per-message playback
  // =====================================================================
  var mediaRecorder = null, audioChunks = [], isRecording = false;

  function initVoiceControls() {
    if (location.pathname.startsWith('/login') || location.pathname.startsWith('/signup')) return;
    if (document.getElementById('nikki-voice-controls')) return;
    var header = document.getElementById('header');
    var container = header ? header.querySelector(':scope > div.flex.items-center.gap-1') : null;
    if (!container) return;

    var wrap = document.createElement('div');
    wrap.id = 'nikki-voice-controls';
    wrap.style.cssText = 'display:inline-flex;align-items:center;gap:6px;margin-right:8px';
    var css = 'background:#2d2d40;border:1px solid #444;border-radius:6px;cursor:pointer;padding:7px 11px;display:flex;align-items:center;justify-content:center;color:#c9bdff;transition:all .2s;min-width:40px;height:34px';

    var mic = document.createElement('button');
    mic.id = 'nikki-mic-button'; mic.type = 'button';
    mic.innerHTML = ICON_MIC; mic.title = 'Dictate into the message box';
    mic.style.cssText = css;
    mic.addEventListener('click', toggleRecording);

    var spk = document.createElement('button');
    spk.id = 'nikki-speaker-button'; spk.type = 'button';
    spk.style.cssText = css;
    paintSpeaker(spk);
    spk.addEventListener('click', function () {
      var on = !voiceEnabled();
      localStorage.setItem('nikki-voice-enabled', on ? 'true' : 'false');
      if (!on) stopSpeaking();   // turning replies off also silences what is playing
      paintSpeaker(spk);
    });

    // Explicit stop: only visible while audio is actually playing.
    var stopBtn = document.createElement("button");
    stopBtn.id = 'nikki-stop-audio'; stopBtn.type = 'button';
    stopBtn.style.cssText = css + ';display:none;border-color:#ff6b6b;color:#ff6b6b';
    stopBtn.title = 'Stop speaking (Esc)';
    stopBtn.innerHTML = ICON_STOP;
    stopBtn.addEventListener('click', stopSpeaking);

    wrap.appendChild(mic); wrap.appendChild(spk); wrap.appendChild(stopBtn);
    container.insertBefore(wrap, container.firstChild);
    observeMessages();
  }

  function paintSpeaker(btn) {
    var on = voiceEnabled();
    btn.innerHTML = on ? ICON_SPK_ON : ICON_SPK_OFF;
    btn.title = 'Spoken replies: ' + (on ? 'ON' : 'OFF');
    btn.style.borderColor = on ? '#4a9eff' : '#444';
    btn.style.opacity = on ? '1' : '.55';
  }

  function transcribe(blob) {
    var fd = new FormData();
    fd.append('audio', blob, 'recording.webm');
    return fetch(STT_URL, { method: 'POST', body: fd }).then(function (r) {
      if (!r.ok) throw new Error('transcribe returned ' + r.status);
      return r.json();
    }).then(function (d) { return (d && d.text ? d.text : '').trim(); });
  }

  var currentAudio = null;
  var speakSeq = 0;

  function setStopVisible(on) {
    var b = document.getElementById('nikki-stop-audio');
    if (b) b.style.display = on ? 'flex' : 'none';
  }

  // Single source of truth for stopping audio: cancels what is playing AND
  // invalidates any TTS request still in flight, so a slow response cannot
  // start a second voice after you already hit stop.
  function stopSpeaking() {
    speakSeq++;
    if (currentAudio) {
      try { currentAudio.pause(); } catch (e) {}
      try { URL.revokeObjectURL(currentAudio.src); } catch (e) {}
      currentAudio = null;
    }
    setStopVisible(false);
  }

  function speak(text) {
    var clean = String(text || '')
      .replace(/```[\s\S]*?```/g, ' code block omitted. ')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/^\s*[#>*\-]+\s*/gm, '')
      .replace(/\s+/g, ' ').trim().slice(0, 4000);
    if (!clean) return Promise.resolve();

    // Never overlap: whatever is playing or pending loses to this call.
    stopSpeaking();
    var mine = speakSeq;

    var fd = new FormData();
    fd.append('text', clean);
    fd.append('voice', 'nova');
    return fetch(TTS_URL, { method: 'POST', body: fd }).then(function (r) {
      if (!r.ok) throw new Error('speak returned ' + r.status);
      return r.blob();
    }).then(function (blob) {
      if (mine !== speakSeq) return;   // superseded or stopped while fetching
      var url = URL.createObjectURL(blob);
      var audio = new Audio(url);
      currentAudio = audio;
      setStopVisible(true);
      audio.onended = audio.onerror = function () {
        URL.revokeObjectURL(url);
        if (currentAudio === audio) { currentAudio = null; setStopVisible(false); }
      };
      return audio.play();
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && currentAudio) stopSpeaking();
  });

  function toggleRecording() {
    var mic = document.getElementById('nikki-mic-button');
    if (isRecording) {
      if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
      isRecording = false;
      mic.innerHTML = ICON_MIC;
      mic.style.background = '#2d2d40'; mic.style.borderColor = '#444'; mic.style.color = '#c9bdff';
      return;
    }
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      .then(function (stream) {
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = function (e) { if (e.data && e.data.size) audioChunks.push(e.data); };
        mediaRecorder.onstop = function () {
          var blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
          stream.getTracks().forEach(function (t) { t.stop(); });
          transcribe(blob).then(function (text) {
            var ta = chatInput();
            if (ta && text) { setReactValue(ta, text); ta.focus(); }
          }).catch(function (err) {
            console.error('[nikki] transcription failed', err);
            alert('Could not transcribe that: ' + err.message);
          });
        };
        mediaRecorder.start();
        isRecording = true;
        mic.innerHTML = ICON_STOP;
        mic.style.background = '#ff4444'; mic.style.borderColor = '#ff6666'; mic.style.color = '#fff';
      })
      .catch(function (err) {
        console.error('[nikki] mic denied', err);
        alert('Microphone access denied. Allow mic access for this site to use voice input.');
      });
  }

  var seen = new Set();
  var primed = false;
  var messageObserver = null;

  // A long thread paints one message at a time on load, and each newly painted
  // message is briefly "the newest" - which fired one autoplay per message, all
  // overlapping. Nothing autoplays until the page has settled.
  setTimeout(function () { primed = true; }, 3000);

  function observeMessages() {
    if (messageObserver) return;
    messageObserver = new MutationObserver(function () {
      if (isBusy()) return;
      var steps = assistantSteps();
      for (var i = 0; i < steps.length; i++) {
        var step = steps[i];
        var content = step.querySelector('.message-content');
        if (!content) continue;
        var text = stepText(step);
        if (!text) continue;
        var key = text.slice(0, 80);
        if (seen.has(key)) continue;

        if (!content.querySelector('.nikki-speaker-btn')) {
          var btn = document.createElement('button');
          btn.className = 'nikki-speaker-btn';
          btn.innerHTML = ICON_SPK_ON + '<span>Play</span>';
          btn.addEventListener('click', function (ev) {
            speak(stepText(ev.currentTarget.closest('.step'))).catch(function (e) {
              alert('Could not generate speech: ' + e.message);
            });
          });
          content.appendChild(btn);
        }

        seen.add(key);
        // Autoplay the newest reply only, and never on the first pass, so the
        // whole backlog is not read aloud on page load.
        if (i === steps.length - 1 && primed && voiceEnabled() && !C.open) {
          speak(text).catch(function (e) { console.warn('[nikki] autoplay blocked', e.message); });
        }
      }
      // primed is set by the settle timer above, not here.
    });
    messageObserver.observe(document.body, { childList: true, subtree: true });
  }

  // --------------------------------------------------------------- bootstrap
  function boot() {
    injectStyles();
    onLogin();
    adminLinks();
    initVoiceControls();
    if (location.pathname === '/canvas' && !C.open) {
      history.replaceState({}, '', '/');
      setTimeout(openCanvas, 400);
    }
  }

  new MutationObserver(boot).observe(document.documentElement, { childList: true, subtree: true });
  boot();
})();
