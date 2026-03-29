/* ─────────────────────────────────────────────────────────────
   pages/create-agent.js — Agent creation & editing form
   ───────────────────────────────────────────────────────────── */

const VOICES = [
  'Puck', 'Charon', 'Kore', 'Fenrir', 'Aoede',
  'Leda', 'Orus', 'Zephyr', 'Autonoe', 'Umbriel',
  'Erinome', 'Laomedeia', 'Schedar', 'Achird',
];

const LANGUAGES = [
  'English', 'Spanish', 'French', 'German', 'Italian',
  'Portuguese', 'Russian', 'Japanese', 'Korean', 'Chinese (Mandarin)',
  'Hindi', 'Arabic', 'Turkish', 'Dutch', 'Polish',
  'Swedish', 'Thai', 'Vietnamese', 'Indonesian', 'Bengali',
];

async function renderCreateAgentPage(container, agentId) {
  const isEdit = agentId && agentId !== 'new';
  let existingAgent = null;

  if (isEdit) {
    try {
      existingAgent = await api.get(`/api/agents/${agentId}`);
    } catch (err) {
      Toast.error('Could not load agent: ' + err.message);
      Router.navigate('/dashboard');
      return;
    }
  }

  container.innerHTML = '';
  const page = document.createElement('div');
  page.className = 'create-agent-page';
  renderNavbar(page);
  container.appendChild(page);

  const content = document.createElement('div');
  content.className = 'create-agent-content';
  page.appendChild(content);

  // Current state
  let selectedVoice = existingAgent?.voice || 'Puck';
  let instructionsText = existingAgent?.instructions || '';

  const langOptions = LANGUAGES.map(l => `<option value="${l}">${l}</option>`).join('');

  content.innerHTML = `
    <div class="create-agent-header">
      <button class="btn btn-ghost btn-icon" id="back-btn" title="Back">←</button>
      <div>
        <h2>${isEdit ? 'Edit Agent' : 'Create New Agent'}</h2>
      </div>
    </div>

    <form id="agent-form" class="create-form" autocomplete="off">
      <!-- Agent Name -->
      <div class="form-group">
        <label class="form-label" for="agent-name">Agent Name</label>
        <input id="agent-name" class="form-control" type="text"
          placeholder="e.g. My Spanish Translator"
          value="${escHtml(existingAgent?.name || '')}"
          required maxlength="80" />
        <span class="form-hint">Give your agent a memorable name</span>
      </div>

      <!-- Language pair -->
      <div class="form-row">
        <div class="form-group">
          <label class="form-label" for="source-lang">Source Language</label>
          <select id="source-lang" class="form-control">
            ${LANGUAGES.map(l => `<option value="${l}" ${(existingAgent?.source_language||'English')===l?'selected':''}>${l}</option>`).join('')}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label" for="target-lang">Target Language</label>
          <select id="target-lang" class="form-control">
            ${LANGUAGES.map(l => `<option value="${l}" ${(existingAgent?.target_language||'Spanish')===l?'selected':''}>${l}</option>`).join('')}
          </select>
        </div>
      </div>

      <!-- Voice selection -->
      <div class="form-group">
        <label class="form-label">Voice</label>
        <div class="voice-grid" id="voice-grid">
          ${VOICES.map(v => `
            <button type="button" class="voice-option ${v === selectedVoice ? 'selected' : ''}" data-voice="${v}">
              ${v}
            </button>
          `).join('')}
        </div>
        <span class="form-hint">Selected: <strong id="selected-voice-name">${selectedVoice}</strong></span>
      </div>

      <!-- Instructions -->
      <div class="form-group">
        <label class="form-label" for="instructions">Custom Instructions</label>
        <textarea id="instructions" class="form-control" rows="5"
          placeholder="Add any custom behavior instructions for your agent (e.g. 'Keep responses concise and professional'). Leave empty for default translation mode."
          maxlength="7000">${escHtml(instructionsText)}</textarea>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:0.25rem">
          <span class="form-hint">Tip: Use a formal/informal tone based on your use case</span>
          <span class="word-count" id="word-count">0 / 1000 words</span>
        </div>
      </div>

      <!-- File upload -->
      <div class="form-group">
        <label class="form-label">Upload Instructions (.txt)</label>
        <div class="upload-area" id="upload-area">
          <div class="upload-area-icon">📄</div>
          <div class="upload-area-text">Drag & drop a .txt file, or <u>click to browse</u></div>
          <input type="file" id="file-input" accept=".txt" style="display:none" />
        </div>
        <span class="form-hint" id="upload-status"></span>
      </div>

      <!-- Form error -->
      <div id="form-error" class="form-error" style="display:none"></div>

      <!-- Actions -->
      <div class="form-actions">
        <button type="button" class="btn btn-secondary" id="cancel-btn">Cancel</button>
        <button type="submit" class="btn btn-primary" id="save-btn">
          ${isEdit ? '💾 Save Changes' : '✨ Create Agent'}
        </button>
      </div>
    </form>
  `;

  // ── Back / Cancel ──────────────────────────────────────────────
  const goBack = () => Router.navigate('/dashboard');
  content.querySelector('#back-btn').onclick   = goBack;
  content.querySelector('#cancel-btn').onclick = goBack;

  // ── Voice selection ────────────────────────────────────────────
  const voiceGrid = content.querySelector('#voice-grid');
  voiceGrid.querySelectorAll('.voice-option').forEach(btn => {
    btn.onclick = () => {
      voiceGrid.querySelectorAll('.voice-option').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      selectedVoice = btn.dataset.voice;
      content.querySelector('#selected-voice-name').textContent = selectedVoice;
    };
  });

  // ── Word count ────────────────────────────────────────────────
  const instructionsEl = content.querySelector('#instructions');
  const wordCountEl = content.querySelector('#word-count');

  function updateWordCount() {
    const words = instructionsEl.value.trim().split(/\s+/).filter(Boolean).length;
    wordCountEl.textContent = `${words} / 1000 words`;
    wordCountEl.className = 'word-count' + (words > 950 ? ' at-limit' : words > 800 ? ' near-limit' : '');
  }

  instructionsEl.addEventListener('input', updateWordCount);
  updateWordCount();

  // ── File upload ────────────────────────────────────────────────
  const uploadArea   = content.querySelector('#upload-area');
  const fileInput    = content.querySelector('#file-input');
  const uploadStatus = content.querySelector('#upload-status');

  uploadArea.onclick = () => fileInput.click();

  uploadArea.addEventListener('dragover', e => {
    e.preventDefault();
    uploadArea.style.borderColor = 'var(--border-focus)';
  });
  uploadArea.addEventListener('dragleave', () => {
    uploadArea.style.borderColor = '';
  });
  uploadArea.addEventListener('drop', e => {
    e.preventDefault();
    uploadArea.style.borderColor = '';
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) handleFileUpload(fileInput.files[0]);
  });

  async function handleFileUpload(file) {
    if (!file.name.endsWith('.txt')) {
      Toast.error('Only .txt files are accepted');
      return;
    }
    uploadStatus.textContent = '⏳ Uploading…';
    uploadStatus.style.color = 'var(--text-muted)';
    try {
      const form = new FormData();
      form.append('file', file);
      const { text, word_count } = await api.upload('/api/upload-instructions', form);
      instructionsEl.value = text;
      updateWordCount();
      uploadStatus.textContent = `✅ Loaded "${file.name}" (${word_count} words)`;
      uploadStatus.style.color = 'var(--success)';
    } catch (err) {
      uploadStatus.textContent = '❌ Upload failed: ' + err.message;
      uploadStatus.style.color = 'var(--danger)';
    }
  }

  // ── Form submit ────────────────────────────────────────────────
  const form    = content.querySelector('#agent-form');
  const errEl   = content.querySelector('#form-error');
  const saveBtn = content.querySelector('#save-btn');

  form.onsubmit = async (e) => {
    e.preventDefault();
    errEl.style.display = 'none';

    const name        = content.querySelector('#agent-name').value.trim();
    const source_language = content.querySelector('#source-lang').value;
    const target_language = content.querySelector('#target-lang').value;
    const instructions = instructionsEl.value.trim();

    if (!name) {
      errEl.textContent = '⚠ Agent name is required';
      errEl.style.display = 'flex';
      return;
    }

    const words = instructions.split(/\s+/).filter(Boolean).length;
    if (words > 1000) {
      errEl.textContent = '⚠ Instructions exceed 1000 words. Please trim them.';
      errEl.style.display = 'flex';
      return;
    }

    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="spinner"></span> Saving…';

    const payload = { name, instructions, voice: selectedVoice, source_language, target_language };

    try {
      if (isEdit) {
        await api.put(`/api/agents/${agentId}`, payload);
        Toast.success('Agent updated successfully!');
      } else {
        const created = await api.post('/api/agents', payload);
        Toast.success(`"${created.name}" created!`);
      }
      Router.navigate('/dashboard');
    } catch (err) {
      errEl.textContent = '⚠ ' + err.message;
      errEl.style.display = 'flex';
      saveBtn.disabled = false;
      saveBtn.textContent = isEdit ? '💾 Save Changes' : '✨ Create Agent';
    }
  };
}

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
