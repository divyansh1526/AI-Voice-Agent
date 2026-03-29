/* ─────────────────────────────────────────────────────────────
   pages/agent-chat.js — Live voice interaction page
   ───────────────────────────────────────────────────────────── */

async function renderAgentChatPage(container, agentId) {
  if (!agentId) { Router.navigate('/dashboard'); return; }

  // Load agent info
  let agent;
  try {
    agent = await api.get(`/api/agents/${agentId}`);
  } catch (err) {
    Toast.error('Agent not found');
    Router.navigate('/dashboard');
    return;
  }

  container.innerHTML = '';
  const page = document.createElement('div');
  page.className = 'chat-page';
  renderNavbar(page);
  container.appendChild(page);

  const shortInstructions = agent.instructions || 'Default translation mode — no custom instructions.';

  // ── Layout ─────────────────────────────────────────────────────
  page.innerHTML += `
    <div class="chat-layout">
      <!-- Sidebar -->
      <aside class="chat-sidebar">
        <div>
          <div class="sidebar-agent-icon">🤖</div>
          <div class="sidebar-agent-name">${escHtml(agent.name)}</div>
          <div class="sidebar-agent-sub">Voice Agent</div>
        </div>

        <div>
          <div class="sidebar-section-title">Configuration</div>
          <div class="sidebar-info-row">
            <span class="sidebar-info-label">Source</span>
            <span class="sidebar-info-value">${escHtml(agent.source_language)}</span>
          </div>
          <div class="sidebar-info-row">
            <span class="sidebar-info-label">Target</span>
            <span class="sidebar-info-value">${escHtml(agent.target_language)}</span>
          </div>
          <div class="sidebar-info-row">
            <span class="sidebar-info-label">Voice</span>
            <span class="sidebar-info-value">🔊 ${escHtml(agent.voice)}</span>
          </div>
          <div class="sidebar-info-row">
            <span class="sidebar-info-label">Created</span>
            <span class="sidebar-info-value">${formatDate(agent.created_at)}</span>
          </div>
        </div>

        <div>
          <div class="sidebar-section-title">Instructions</div>
          <div class="sidebar-instructions">${escHtml(shortInstructions)}</div>
        </div>

        <div style="margin-top:auto">
          <button class="btn btn-secondary" style="width:100%" id="edit-agent-btn">✏️ Edit Agent</button>
        </div>
      </aside>

      <!-- Main chat area -->
      <main class="chat-main">
        <!-- Orb -->
        <div class="voice-orb-container">
          <div class="voice-orb-wrap" id="orb-wrap">
            <div class="voice-orb idle" id="voice-orb">🎙</div>
          </div>
          <div class="orb-status" id="orb-status">Ready to connect</div>
          <div id="conn-badge" class="connection-badge disconnected">Disconnected</div>
        </div>

        <!-- Transcript -->
        <div class="transcript-panel" id="transcript-panel">
          <div style="text-align:center;color:var(--text-muted);padding:2rem;font-size:0.88rem">
            Press the microphone button to start a live session.<br>
            Speak in <strong style="color:var(--text-accent)">${escHtml(agent.source_language)}</strong>
            and hear the response in <strong style="color:var(--accent-3)">${escHtml(agent.target_language)}</strong>.
          </div>
        </div>

        <!-- Controls -->
        <div class="chat-controls">
          <button class="btn btn-secondary" id="disconnect-btn" disabled title="Disconnect">✕ Disconnect</button>
          <button class="mic-btn" id="mic-btn" title="Toggle Microphone">🎙</button>
          <button class="btn btn-secondary" id="back-dashboard-btn" title="Back to Dashboard">← Dashboard</button>
        </div>
      </main>
    </div>
  `;

  // ── Element refs ───────────────────────────────────────────────
  const orbWrap      = page.querySelector('#orb-wrap');
  const orb          = page.querySelector('#voice-orb');
  const orbStatus    = page.querySelector('#orb-status');
  const connBadge    = page.querySelector('#conn-badge');
  const micBtn       = page.querySelector('#mic-btn');
  const discBtn      = page.querySelector('#disconnect-btn');
  const transcript   = page.querySelector('#transcript-panel');

  page.querySelector('#edit-agent-btn').onclick         = () => Router.navigate(`/agent/${agentId}`);
  page.querySelector('#back-dashboard-btn').onclick     = () => {
    cleanup();
    Router.navigate('/dashboard');
  };
  discBtn.onclick = cleanup;

  // ── State ──────────────────────────────────────────────────────
  let isConnected    = false;
  let isRecording    = false;
  let currentUserMsg = null;
  let currentAgtMsg  = null;

  const mediaHandler = new MediaHandler();

  // ── Session management ─────────────────────────────────────────
  const token = Auth.getToken();
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${location.host}/ws/${agentId}?token=${encodeURIComponent(token)}`;

  const geminiClient = new GeminiClient({
    wsUrl,
    onOpen: () => {
      isConnected = true;
      setConnectionState('connected');
      discBtn.disabled = false;
      Toast.info('Connected — you can speak now');
    },
    onMessage: handleMessage,
    onClose: () => {
      isConnected = false;
      setConnectionState('disconnected');
      stopRecording();
      discBtn.disabled = true;
    },
    onError: () => {
      isConnected = false;
      setConnectionState('error');
      Toast.error('Connection error — try reconnecting');
    },
  });

  // ── Mic button ─────────────────────────────────────────────────
  micBtn.onclick = async () => {
    if (!isConnected) {
      // Connect first
      await startSession();
    } else if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  async function startSession() {
    setConnectionState('connecting');
    orbStatus.textContent = 'Connecting…';
    try {
      await mediaHandler.initializeAudio();
      geminiClient.connect();
    } catch (err) {
      Toast.error('Could not start audio: ' + err.message);
      setConnectionState('disconnected');
    }
  }

  function startRecording() {
    mediaHandler.startAudio((data) => {
      if (geminiClient.isConnected()) geminiClient.send(data);
    }).then(() => {
      isRecording = true;
      micBtn.classList.add('active');
      micBtn.innerHTML = '⏹';
      micBtn.title = 'Stop Mic';
      setOrbState('listening');
    }).catch(err => {
      Toast.error('Mic access denied: ' + err.message);
    });
  }

  function stopRecording() {
    mediaHandler.stopAudio();
    isRecording = false;
    micBtn.classList.remove('active');
    micBtn.innerHTML = '🎙';
    micBtn.title = 'Start Mic';
    if (isConnected) setOrbState('idle-connected');
    else setOrbState('idle');
  }

  function cleanup() {
    stopRecording();
    mediaHandler.stopAudioPlayback();
    geminiClient.disconnect();
    isConnected = false;
    setConnectionState('disconnected');
  }

  // ── Handle messages from Gemini ─────────────────────────────────
  function handleMessage(event) {
    if (event.data instanceof ArrayBuffer) {
      mediaHandler.playAudio(event.data);
      setOrbState('speaking');
      return;
    }
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'user') {
        if (!currentUserMsg) {
          currentUserMsg = appendTranscript('user', `🎤 ${agent.source_language}`, msg.text);
        } else {
          currentUserMsg.querySelector('.msg-bubble').textContent += msg.text;
          transcript.scrollTop = transcript.scrollHeight;
        }
      } else if (msg.type === 'agent') {
        setOrbState('speaking');
        if (!currentAgtMsg) {
          currentAgtMsg = appendTranscript('agent', `🌐 ${agent.target_language}`, msg.text);
        } else {
          currentAgtMsg.querySelector('.msg-bubble').textContent += msg.text;
          transcript.scrollTop = transcript.scrollHeight;
        }
      } else if (msg.type === 'turn_complete') {
        currentUserMsg = null;
        currentAgtMsg  = null;
        if (isRecording) setOrbState('listening');
        else setOrbState('idle-connected');
      } else if (msg.type === 'interrupted') {
        mediaHandler.stopAudioPlayback();
        currentAgtMsg = null;
        if (isRecording) setOrbState('listening');
      } else if (msg.type === 'error') {
        Toast.error('Agent error: ' + msg.error);
        setConnectionState('error');
      }
    } catch { /* binary data was already handled above */ }
  }

  function appendTranscript(role, label, text) {
    // Clear placeholder if present
    const placeholder = transcript.querySelector('div[style*="text-align"]');
    if (placeholder) placeholder.remove();

    const div = document.createElement('div');
    div.className = `transcript-msg ${role}`;
    div.innerHTML = `
      <div class="msg-label">${label}</div>
      <div class="msg-bubble">${escHtml(text)}</div>
    `;
    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;
    return div;
  }

  // ── UI state helpers ────────────────────────────────────────────
  function setOrbState(state) {
    orb.className      = 'voice-orb';
    orbWrap.className  = 'voice-orb-wrap';
    orbStatus.className = 'orb-status';

    if (state === 'listening') {
      orb.classList.add('listening');
      orb.innerHTML = '🎤';
      orbWrap.classList.add('listening');
      orbStatus.classList.add('listening');
      orbStatus.textContent = 'Listening…';
    } else if (state === 'speaking') {
      orb.classList.add('speaking');
      orb.innerHTML = '🔊';
      orbWrap.classList.add('speaking');
      orbStatus.classList.add('speaking');
      orbStatus.textContent = 'Speaking…';
    } else if (state === 'idle-connected') {
      orb.classList.add('idle');
      orb.innerHTML = '⏸';
      orbStatus.textContent = 'Mic paused — press 🎙 to speak';
    } else {
      orb.classList.add('idle');
      orb.innerHTML = '🎙';
      orbStatus.textContent = 'Ready to connect';
    }
  }

  function setConnectionState(state) {
    connBadge.className = `connection-badge ${state}`;
    const labels = {
      connected:    '● Connected',
      disconnected: '○ Disconnected',
      connecting:   '◌ Connecting…',
      error:        '✕ Error',
    };
    connBadge.textContent = labels[state] || state;
    if (state !== 'connected' && state !== 'connecting') {
      setOrbState('idle');
    }
  }
}

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
