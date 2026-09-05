// Nikki UI extras: "Create account" link on the login page; Admin/Spaces links in the top header for platform admins.
(function () {
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
    // Put the links in the Chainlit header (top-right, before the theme/settings buttons).
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
    } else {
      return; // header not rendered yet; observer will retry
    }
    bar.appendChild(linkEl('/admin', 'Admin'));
    bar.appendChild(linkEl('/spaces', 'Spaces'));
  }

  // ============ VOICE CONTROLS ============
  var mediaRecorder = null;
  var audioChunks = [];
  var isRecording = false;

  function initVoiceControls() {
    if (location.pathname.startsWith('/login') || location.pathname.startsWith('/signup')) return;
    if (document.getElementById('nikki-voice-controls')) return;

    // Find the chat input container
    var inputContainer = document.querySelector('#chat-input');
    if (!inputContainer) return;

    // Find the input textarea
    var textarea = inputContainer.querySelector('textarea');
    if (!textarea) return;

    // Create voice controls container
    var voiceContainer = document.createElement('div');
    voiceContainer.id = 'nikki-voice-controls';
    voiceContainer.style.cssText = 'display:flex;align-items:center;gap:8px;position:absolute;right:60px;bottom:12px;z-index:10';

    // Microphone button
    var micButton = document.createElement('button');
    micButton.id = 'nikki-mic-button';
    micButton.innerHTML = '🎤';
    micButton.title = 'Record voice message';
    micButton.style.cssText = 'background:#2d2d40;border:2px solid #444;border-radius:50%;width:36px;height:36px;cursor:pointer;font-size:18px;display:flex;align-items:center;justify-content:center;transition:all 0.2s';
    
    micButton.addEventListener('click', toggleRecording);
    
    // Speaker button (toggle auto-play)
    var speakerButton = document.createElement('button');
    speakerButton.id = 'nikki-speaker-button';
    speakerButton.innerHTML = '🔊';
    speakerButton.title = 'Voice responses: ON';
    speakerButton.style.cssText = 'background:#2d2d40;border:2px solid #444;border-radius:50%;width:36px;height:36px;cursor:pointer;font-size:18px;display:flex;align-items:center;justify-content:center;transition:all 0.2s';
    
    var voiceEnabled = localStorage.getItem('nikki-voice-enabled') !== 'false';
    updateSpeakerButton(speakerButton, voiceEnabled);
    
    speakerButton.addEventListener('click', function() {
      voiceEnabled = !voiceEnabled;
      localStorage.setItem('nikki-voice-enabled', voiceEnabled);
      updateSpeakerButton(speakerButton, voiceEnabled);
    });

    voiceContainer.appendChild(micButton);
    voiceContainer.appendChild(speakerButton);
    inputContainer.appendChild(voiceContainer);

    // Listen for new messages to auto-play
    observeMessages();
  }

  function updateSpeakerButton(button, enabled) {
    button.innerHTML = enabled ? '🔊' : '🔇';
    button.title = 'Voice responses: ' + (enabled ? 'ON' : 'OFF');
    button.style.borderColor = enabled ? '#6366f1' : '#444';
  }

  async function toggleRecording() {
    var micButton = document.getElementById('nikki-mic-button');
    
    if (isRecording) {
      // Stop recording
      if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
      }
      isRecording = false;
      micButton.innerHTML = '🎤';
      micButton.style.background = '#2d2d40';
      micButton.style.borderColor = '#444';
    } else {
      // Start recording
      try {
        var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        
        mediaRecorder = new MediaRecorder(stream);
        
        mediaRecorder.ondataavailable = function(event) {
          audioChunks.push(event.data);
        };
        
        mediaRecorder.onstop = async function() {
          var audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
          stream.getTracks().forEach(function(track) { track.stop(); });
          
          // Transcribe the audio
          await transcribeAudio(audioBlob);
        };
        
        mediaRecorder.start();
        isRecording = true;
        micButton.innerHTML = '⏹️';
        micButton.style.background = '#dc2626';
        micButton.style.borderColor = '#ef4444';
        
      } catch (err) {
        console.error('Microphone access denied:', err);
        alert('Microphone access denied. Please allow microphone access to use voice input.');
      }
    }
  }

  async function transcribeAudio(audioBlob) {
    var formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');
    
    try {
      var response = await fetch('/api/voice/transcribe', {
        method: 'POST',
        body: formData
      });
      
      if (!response.ok) {
        throw new Error('Transcription failed');
      }
      
      var data = await response.json();
      var textarea = document.querySelector('#chat-input textarea');
      
      if (textarea && data.text) {
        // Insert the transcribed text
        textarea.value = data.text;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        textarea.focus();
      }
      
    } catch (err) {
      console.error('Transcription error:', err);
      alert('Failed to transcribe audio. Please try again.');
    }
  }

  var processedMessages = new Set();
  
  function observeMessages() {
    // Watch for new assistant messages and auto-play them
    var observer = new MutationObserver(function() {
      var voiceEnabled = localStorage.getItem('nikki-voice-enabled') !== 'false';
      if (!voiceEnabled) return;
      
      var messages = document.querySelectorAll('.step[data-author="assistant"]');
      messages.forEach(function(msg) {
        // Use a unique identifier for each message
        var msgId = msg.getAttribute('data-id') || msg.textContent.substring(0, 50);
        if (processedMessages.has(msgId)) return;
        
        // Check if this message already has a speaker button
        if (msg.querySelector('.nikki-speaker-btn')) return;
        
        // Add speaker button to this message
        var contentDiv = msg.querySelector('.MuiBox-root');
        if (!contentDiv) return;
        
        processedMessages.add(msgId);
        
        var speakerBtn = document.createElement('button');
        speakerBtn.className = 'nikki-speaker-btn';
        speakerBtn.innerHTML = '▶️';
        speakerBtn.title = 'Play audio';
        speakerBtn.style.cssText = 'background:#2d2d40;border:1px solid #444;border-radius:4px;padding:4px 8px;cursor:pointer;font-size:14px;margin-top:8px;transition:all 0.2s';
        
        speakerBtn.addEventListener('click', async function() {
          var text = contentDiv.textContent || contentDiv.innerText;
          await speakText(text, speakerBtn);
        });
        
        contentDiv.appendChild(speakerBtn);
      });
    });
    
    observer.observe(document.body, { childList: true, subtree: true });
  }

  async function speakText(text, button) {
    if (!text || text.trim().length === 0) return;
    
    // Clean the text (remove code blocks, excessive formatting)
    var cleanText = text.replace(/```[\s\S]*?```/g, '')
                        .replace(/`[^`]+`/g, '')
                        .replace(/#{1,6}\s/g, '')
                        .trim()
                        .substring(0, 4000); // Limit to 4000 chars
    
    if (cleanText.length === 0) return;
    
    var originalContent = button.innerHTML;
    button.innerHTML = '⏸️';
    button.disabled = true;
    
    try {
      var formData = new FormData();
      formData.append('text', cleanText);
      formData.append('voice', 'nova'); // You can make this configurable
      
      var response = await fetch('/api/voice/speak', {
        method: 'POST',
        body: formData
      });
      
      if (!response.ok) {
        throw new Error('TTS failed');
      }
      
      var audioBlob = await response.blob();
      var audioUrl = URL.createObjectURL(audioBlob);
      var audio = new Audio(audioUrl);
      
      audio.onended = function() {
        button.innerHTML = originalContent;
        button.disabled = false;
        URL.revokeObjectURL(audioUrl);
      };
      
      audio.onerror = function() {
        button.innerHTML = originalContent;
        button.disabled = false;
        URL.revokeObjectURL(audioUrl);
      };
      
      await audio.play();
      
    } catch (err) {
      console.error('TTS error:', err);
      button.innerHTML = originalContent;
      button.disabled = false;
      alert('Failed to generate speech. Please try again.');
    }
  }

  new MutationObserver(function () { 
    onLogin(); 
    adminLinks();
    initVoiceControls();
  }).observe(document.documentElement, { childList: true, subtree: true });
  
  onLogin(); 
  adminLinks();
  initVoiceControls();
})();
