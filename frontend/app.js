/* ─────────────────────────────────────────────────────────────
   app.js — SPA Router, Auth State, API Client, Toast
   ───────────────────────────────────────────────────────────── */

// ── Constants ──────────────────────────────────────────────────
const API_BASE = '';
const TOKEN_KEY  = 'vb_token';
const USER_KEY   = 'vb_user';

// ── Auth state ─────────────────────────────────────────────────
const Auth = {
  getToken() { return localStorage.getItem(TOKEN_KEY); },
  getUser()  {
    try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
  },
  isLoggedIn() { return !!this.getToken(); },
  save(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
};

// ── API client ─────────────────────────────────────────────────
const api = {
  async request(method, path, body = null, isForm = false) {
    const headers = {};
    const token = Auth.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const opts = { method, headers };
    if (body && !isForm) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    } else if (isForm) {
      opts.body = body; // FormData
    }

    const res = await fetch(`${API_BASE}${path}`, opts);
    if (res.status === 204) return null;

    let json;
    try { json = await res.json(); } catch { json = {}; }

    if (!res.ok) {
      throw new Error(json.detail || json.message || `Error ${res.status}`);
    }
    return json;
  },

  get(path)           { return this.request('GET', path); },
  post(path, body)    { return this.request('POST', path, body); },
  put(path, body)     { return this.request('PUT', path, body); },
  delete(path)        { return this.request('DELETE', path); },
  upload(path, form)  { return this.request('POST', path, form, true); },
};

// ── Toast system ────────────────────────────────────────────────
const Toast = {
  show(msg, type = 'info', duration = 4000) {
    const icons = { success: '✅', error: '❌', info: 'ℹ️' };
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<span>${icons[type]}</span><span>${msg}</span>`;
    const container = document.getElementById('toast-container');
    container.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateX(20px)';
      setTimeout(() => el.remove(), 300);
    }, duration);
  },
  success(m) { this.show(m, 'success'); },
  error(m)   { this.show(m, 'error'); },
  info(m)    { this.show(m, 'info'); },
};

// ── Router ──────────────────────────────────────────────────────
const Router = {
  routes: {},
  currentPage: null,

  register(pattern, handler) {
    this.routes[pattern] = handler;
  },

  navigate(path) {
    window.location.hash = path;
  },

  async resolve() {
    const hash = window.location.hash.slice(1) || '/';
    const [basePath, ...params] = hash.split('/').filter(Boolean);
    const route = `/${basePath || ''}`;
    const app = document.getElementById('app');

    // Protect routes
    if (route !== '/' && !route.startsWith('/auth') && !Auth.isLoggedIn()) {
      return this.navigate('/auth');
    }
    if ((route === '/' || route === '/auth') && Auth.isLoggedIn()) {
      return this.navigate('/dashboard');
    }

    // Find and call handler
    if (this.routes[route]) {
      app.innerHTML = '';
      await this.routes[route](app, params);
    } else {
      app.innerHTML = `<div style="text-align:center;padding:4rem;">
        <h2>404 — Page not found</h2>
        <button class="btn btn-primary" onclick="Router.navigate('/dashboard')" style="margin-top:1rem">Go to Dashboard</button>
      </div>`;
    }
  },

  init() {
    window.addEventListener('hashchange', () => this.resolve());
    this.resolve();
  },
};

// ── Shared helpers ──────────────────────────────────────────────
function renderNavbar(container, { showLogout = true } = {}) {
  const user = Auth.getUser();
  const initials = user ? user.name.slice(0, 2).toUpperCase() : '??';
  const nav = document.createElement('nav');
  nav.className = 'navbar';
  nav.innerHTML = `
    <a class="navbar-brand" href="#/dashboard">
      <div class="brand-icon">🌐</div>
      VoiceBridge
    </a>
    <div class="navbar-actions">
      ${user ? `<div class="user-badge">
        <div class="user-avatar">${initials}</div>
        <span>${user.name}</span>
      </div>` : ''}
      ${showLogout ? `<button class="btn btn-ghost btn-sm" id="logout-btn">Sign out</button>` : ''}
    </div>
  `;
  container.appendChild(nav);
  if (showLogout) {
    nav.querySelector('#logout-btn').onclick = () => {
      Auth.clear();
      Router.navigate('/auth');
    };
  }
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ── Register pages ──────────────────────────────────────────────
Router.register('/', (app) => Router.navigate('/auth'));
Router.register('/auth', renderAuthPage);
Router.register('/dashboard', renderDashboardPage);
Router.register('/agent', (app, params) => renderCreateAgentPage(app, params[0]));
Router.register('/chat', (app, params) => renderAgentChatPage(app, params[0]));

Router.init();
