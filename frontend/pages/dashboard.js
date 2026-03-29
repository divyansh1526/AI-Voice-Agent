/* ─────────────────────────────────────────────────────────────
   pages/dashboard.js — Agent management dashboard
   ───────────────────────────────────────────────────────────── */

async function renderDashboardPage(container) {
  // Scaffold page
  container.innerHTML = '';
  const page = document.createElement('div');
  page.className = 'dashboard-page';
  renderNavbar(page);
  container.appendChild(page);

  const content = document.createElement('div');
  content.className = 'dashboard-content';
  page.appendChild(content);

  // Loading state
  content.innerHTML = `
    <div style="text-align:center;padding:5rem;color:var(--text-muted)">
      <span class="spinner" style="width:32px;height:32px;border-width:3px"></span>
      <p style="margin-top:1rem">Loading your agents…</p>
    </div>
  `;

  let agents = [];
  try {
    agents = await api.get('/api/agents');
  } catch (err) {
    if (err.message.includes('401') || err.message.includes('authenticated')) {
      Auth.clear();
      return Router.navigate('/auth');
    }
    Toast.error('Failed to load agents: ' + err.message);
  }

  const user = Auth.getUser();
  renderContent(content, agents, user);
}

function renderContent(content, agents, user) {
  const langPairs = new Set(agents.map(a => `${a.source_language}→${a.target_language}`));

  content.innerHTML = `
    <div class="dashboard-header">
      <div>
        <h2>Your Voice Agents</h2>
        <p>${agents.length ? `${agents.length} agent${agents.length !== 1 ? 's' : ''} created` : 'No agents yet — create your first one!'}</p>
      </div>
      <button class="btn btn-primary" id="create-btn">
        ＋ New Agent
      </button>
    </div>

    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-value">${agents.length}</div>
        <div class="stat-label">Agents</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${langPairs.size}</div>
        <div class="stat-label">Language Pairs</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${new Set(agents.map(a => a.voice)).size || 0}</div>
        <div class="stat-label">Voices Used</div>
      </div>
    </div>

    <div class="agents-grid" id="agents-grid">
      ${agents.length === 0 ? renderEmptyState() : agents.map(renderAgentCard).join('')}
    </div>
  `;

  content.querySelector('#create-btn').onclick = () => Router.navigate('/agent/new');

  // Bind agent action buttons
  content.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const { action, id } = btn.dataset;
      handleAgentAction(action, id, content, agents);
    });
  });

  // Click card to open chat
  content.querySelectorAll('.agent-card[data-agent-id]').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      Router.navigate(`/chat/${card.dataset.agentId}`);
    });
  });
}

function renderEmptyState() {
  return `
    <div class="empty-state">
      <span class="empty-state-icon">🤖</span>
      <h3>No Agents Yet</h3>
      <p>Create your first AI voice agent to start translating speech in real-time across languages.</p>
      <button class="btn btn-primary" onclick="Router.navigate('/agent/new')">Create First Agent</button>
    </div>
  `;
}

function renderAgentCard(agent) {
  const shortInstructions = agent.instructions
    ? agent.instructions.slice(0, 120) + (agent.instructions.length > 120 ? '…' : '')
    : 'Default translation mode';

  return `
    <div class="agent-card" data-agent-id="${agent.id}" style="cursor:pointer" title="Click to start session">
      <div class="agent-card-header">
        <div class="agent-icon">🤖</div>
        <div class="agent-meta">
          <div class="agent-name">${escHtml(agent.name)}</div>
          <div class="agent-date">Created ${formatDate(agent.created_at)}</div>
        </div>
      </div>
      <div class="agent-langs">
        <span class="lang-badge">${escHtml(agent.source_language)}</span>
        <span class="lang-arrow">→</span>
        <span class="lang-badge">${escHtml(agent.target_language)}</span>
      </div>
      <div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap">
        <span class="voice-chip">🔊 ${escHtml(agent.voice)}</span>
      </div>
      <p class="agent-instructions">${escHtml(shortInstructions)}</p>
      <div class="agent-actions">
        <button class="btn btn-primary" data-action="launch" data-id="${agent.id}">▶ Launch</button>
        <button class="btn btn-secondary btn-icon" data-action="edit"   data-id="${agent.id}" title="Edit">✏️</button>
        <button class="btn btn-danger   btn-icon" data-action="delete" data-id="${agent.id}" title="Delete">🗑</button>
      </div>
    </div>
  `;
}

async function handleAgentAction(action, id, content, agents) {
  if (action === 'launch') {
    Router.navigate(`/chat/${id}`);
  } else if (action === 'edit') {
    Router.navigate(`/agent/${id}`);
  } else if (action === 'delete') {
    showDeleteConfirm(id, content, agents);
  }
}

function showDeleteConfirm(agentId, content, agents) {
  const agent = agents.find(a => a.id === agentId);
  if (!agent) return;

  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h3 class="modal-title">Delete Agent</h3>
        <button class="btn btn-ghost btn-icon" id="modal-close">✕</button>
      </div>
      <p style="color:var(--text-secondary);margin-bottom:1.5rem">
        Are you sure you want to delete <strong style="color:var(--text-primary)">${escHtml(agent.name)}</strong>?
        This action cannot be undone.
      </p>
      <div style="display:flex;gap:0.75rem;justify-content:flex-end">
        <button class="btn btn-secondary" id="modal-cancel">Cancel</button>
        <button class="btn btn-danger" id="modal-confirm">Delete Agent</button>
      </div>
    </div>
  `;

  document.body.appendChild(backdrop);

  backdrop.querySelector('#modal-close').onclick  = () => backdrop.remove();
  backdrop.querySelector('#modal-cancel').onclick = () => backdrop.remove();
  backdrop.querySelector('#modal-confirm').onclick = async () => {
    const btn = backdrop.querySelector('#modal-confirm');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
    try {
      await api.delete(`/api/agents/${agentId}`);
      backdrop.remove();
      Toast.success(`"${agent.name}" deleted`);
      // Re-fetch and re-render
      const fresh = await api.get('/api/agents');
      renderContent(content, fresh, Auth.getUser());
    } catch (err) {
      Toast.error('Failed to delete: ' + err.message);
      backdrop.remove();
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
