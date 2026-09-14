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

    // Try multiple strategies to find the right place to insert controls
    var actionsContainer = null;
    
    // Strategy 1: Look for the header right section (where theme/settings are)
    var header = document.getElementById('header');
    if (header) {
      var headerRight = header.querySelector(':scope > div.flex.items-center.gap-1');
      if (headerRight) actionsContainer = headerRight;
    }
    
    // Strategy 2: Look for chat input area
    if (!actionsContainer) {
      var chatInput = document.querySelector('#chat-input');
      if (chatInput) {
        // Find parent form
        var form = chatInput.closest('form');
        if (form) {
          // Look for action buttons container
          var buttons = form.querySelector('div[class*="flex"]');
          if (buttons) actionsContainer = buttons;
        }
      }
    }
    
    if (!actionsContainer) return;

    // Create voice controls container
    var voiceContainer = document.createElement('div');
    voiceContainer.id = 'nikki-voice-controls';
    voiceContainer.style.cssText = 'display:inline-flex;align-items:center;gap:6px;margin-right:8px';

    // Microphone button
    var micButton = document.createElement('button');
    micButton.id = 'nikki-mic-button';
    micButton.type = 'button';
    micButton.innerHTML = '🎤';
    micButton.title = 'Click to record voice message';
    micButton.style.cssText = 'background:#2d2d40;border:1px solid #444;border-radius:6px;cursor:pointer;font-size:18px;padding:8px 12px;display:flex;align-items:center;justify-content:center;transition:all 0.2s;opacity:0.85;min-width:44px;height:36px';
    
    micButton.addEventListener('mouseenter', function() { 
      this.style.opacity = '1'; 
      this.style.background = '#3d3d50';
    });
    micButton.addEventListener('mouseleave', function() { 
      if (!isRecording) {
        this.style.opacity = '0.85'; 
        this.style.background = '#2d2d40';
      }
    });
    micButton.addEventListener('click', toggleRecording);
    
    // Speaker button (toggle auto-play)
    var speakerButton = document.createElement('button');
    speakerButton.id = 'nikki-speaker-button';
    speakerButton.type = 'button';
    speakerButton.innerHTML = '🔊';
    speakerButton.title = 'Voice responses: ON (click to toggle)';
    speakerButton.style.cssText = 'background:#2d2d40;border:1px solid #444;border-radius:6px;cursor:pointer;font-size:18px;padding:8px 12px;display:flex;align-items:center;justify-content:center;transition:all 0.2s;opacity:0.85;min-width:44px;height:36px';
    
    var voiceEnabled = localStorage.getItem('nikki-voice-enabled') !== 'false';
    updateSpeakerButton(speakerButton, voiceEnabled);
    
    speakerButton.addEventListener('mouseenter', function() { 
      this.style.opacity = '1'; 
      this.style.background = '#3d3d50';
    });
    speakerButton.addEventListener('mouseleave', function() { 
      this.style.opacity = '0.85'; 
      this.style.background = '#2d2d40';
    });
    speakerButton.addEventListener('click', function() {
      voiceEnabled = !voiceEnabled;
      localStorage.setItem('nikki-voice-enabled', voiceEnabled);
      updateSpeakerButton(speakerButton, voiceEnabled);
    });

    voiceContainer.appendChild(micButton);
    voiceContainer.appendChild(speakerButton);
    
    // Insert at the beginning of the container
    actionsContainer.insertBefore(voiceContainer, actionsContainer.firstChild);

    // Listen for new messages to auto-play
    observeMessages();
  }

  function updateSpeakerButton(button, enabled) {
    button.innerHTML = enabled ? '🔊' : '🔇';
    button.title = 'Voice responses: ' + (enabled ? 'ON' : 'OFF') + ' (click to toggle)';
    if (enabled) {
      button.style.opacity = '0.85';
      button.style.borderColor = '#4a9eff';
    } else {
      button.style.opacity = '0.5';
      button.style.borderColor = '#444';
    }
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
      micButton.style.opacity = '0.85';
      micButton.style.background = '#2d2d40';
      micButton.style.borderColor = '#444';
      micButton.title = 'Click to record voice message';
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
        micButton.style.opacity = '1';
        micButton.style.background = '#ff4444';
        micButton.style.borderColor = '#ff6666';
        micButton.title = 'Recording... Click to stop';
        
      } catch (err) {
        console.error('Microphone access denied:', err);
        alert('⚠️ Microphone access denied.\n\nPlease allow microphone access in your browser settings to use voice input.');
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
        speakerBtn.innerHTML = '🔊 Play';
        speakerBtn.title = 'Listen to this message';
        speakerBtn.style.cssText = 'background:#2d2d40;border:1px solid #4a9eff;border-radius:6px;padding:6px 12px;cursor:pointer;font-size:13px;margin-top:10px;transition:all 0.2s;color:#c9bdff;font-weight:500;display:inline-flex;align-items:center;gap:4px';
        
        speakerBtn.addEventListener('mouseenter', function() {
          this.style.background = '#3d3d50';
          this.style.borderColor = '#5aafff';
        });
        
        speakerBtn.addEventListener('mouseleave', function() {
          if (!this.disabled) {
            this.style.background = '#2d2d40';
            this.style.borderColor = '#4a9eff';
          }
        });
        
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
    button.innerHTML = '⏸️ Playing...';
    button.disabled = true;
    button.style.opacity = '0.6';
    button.style.cursor = 'wait';
    
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
        button.style.opacity = '1';
        button.style.cursor = 'pointer';
        URL.revokeObjectURL(audioUrl);
      };
      
      audio.onerror = function() {
        button.innerHTML = originalContent;
        button.disabled = false;
        button.style.opacity = '1';
        button.style.cursor = 'pointer';
        URL.revokeObjectURL(audioUrl);
      };
      
      await audio.play();
      
    } catch (err) {
      console.error('TTS error:', err);
      button.innerHTML = originalContent;
      button.disabled = false;
      button.style.opacity = '1';
      button.style.cursor = 'pointer';
      alert('⚠️ Failed to generate speech.\n\nPlease try again or check your connection.');
    }
  }

  // ============ APPROVAL BUTTON FIX ============
  function ensureActionButtons() {
    // Find all messages with actions that might not be rendering properly
    var actionContainers = document.querySelectorAll('.step-actions, .message-actions');
    actionContainers.forEach(function(container) {
      if (!container || container.getAttribute('data-fixed')) return;
      container.setAttribute('data-fixed', 'true');
      
      // Ensure the container is visible and interactive
      container.style.display = 'flex';
      container.style.gap = '8px';
      container.style.marginTop = '12px';
      container.style.pointerEvents = 'auto';
      container.style.zIndex = '10';
      
      // Fix each button inside
      var buttons = container.querySelectorAll('button');
      buttons.forEach(function(btn) {
        btn.style.pointerEvents = 'auto';
        btn.style.cursor = 'pointer';
        btn.style.opacity = '1';
        btn.style.visibility = 'visible';
        btn.style.display = 'inline-flex';
        
        // Ensure click events are working
        if (!btn.getAttribute('data-click-fixed')) {
          btn.setAttribute('data-click-fixed', 'true');
          // Re-attach the click handler to ensure it fires
          btn.addEventListener('click', function(e) {
            e.stopPropagation();
            e.preventDefault();
            // Let the original handler fire
          }, true);
        }
      });
    });
  }

  new MutationObserver(function () { 
    onLogin(); 
    adminLinks();
    initVoiceControls();
    ensureActionButtons();
  }).observe(document.documentElement, { childList: true, subtree: true });
  
  onLogin(); 
  adminLinks();
  initVoiceControls();
  ensureActionButtons();
  
  // Also run periodically for the first 10 seconds after page load
  var fixAttempts = 0;
  var fixInterval = setInterval(function() {
    ensureActionButtons();
    fixAttempts++;
    if (fixAttempts >= 20) {
      clearInterval(fixInterval);
    }
  }, 500);
})();
