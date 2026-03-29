/* ─────────────────────────────────────────────────────────────
   pages/auth.js — Login & Signup page
   ───────────────────────────────────────────────────────────── */

async function renderAuthPage(container) {
  container.innerHTML = `
    <div class="auth-page">
      <div class="auth-container">
        <div class="auth-logo">
          <div class="brand-icon" style="margin:0 auto 1rem;">🌐</div>
          <h1>VoiceBridge</h1>
          <p>Real-time AI speech-to-speech translation</p>
        </div>

        <div class="auth-card">
          <div class="auth-tabs">
            <button class="auth-tab active" data-tab="login" id="tab-login">Sign In</button>
            <button class="auth-tab" data-tab="signup" id="tab-signup">Create Account</button>
          </div>

          <!-- Login Form -->
          <form id="login-form" class="auth-form" autocomplete="on">
            <div class="form-group">
              <label class="form-label" for="login-email">Email</label>
              <input id="login-email" class="form-control" type="email"
                placeholder="you@example.com" required autocomplete="email" />
            </div>
            <div class="form-group">
              <label class="form-label" for="login-password">Password</label>
              <input id="login-password" class="form-control" type="password"
                placeholder="••••••••" required autocomplete="current-password" />
            </div>
            <div id="login-error" class="form-error" style="display:none"></div>
            <button type="submit" class="btn btn-primary btn-lg" id="login-btn" style="width:100%">
              Sign In
            </button>
          </form>

          <!-- Signup Form (hidden initially) -->
          <form id="signup-form" class="auth-form" style="display:none" autocomplete="on">
            <div class="form-group">
              <label class="form-label" for="signup-name">Full Name</label>
              <input id="signup-name" class="form-control" type="text"
                placeholder="Jane Smith" required autocomplete="name" />
            </div>
            <div class="form-group">
              <label class="form-label" for="signup-email">Email</label>
              <input id="signup-email" class="form-control" type="email"
                placeholder="you@example.com" required autocomplete="email" />
            </div>
            <div class="form-group">
              <label class="form-label" for="signup-password">Password</label>
              <input id="signup-password" class="form-control" type="password"
                placeholder="Min. 6 characters" required autocomplete="new-password" minlength="6" />
            </div>
            <div id="signup-error" class="form-error" style="display:none"></div>
            <button type="submit" class="btn btn-primary btn-lg" id="signup-btn" style="width:100%">
              Create Account
            </button>
          </form>
        </div>

        <div class="auth-footer">
          Powered by <strong>Gemini Live API</strong> • Built with ❤️
        </div>
      </div>
    </div>
  `;

  // ── Tab switching ─────────────────────────────────────────────
  const loginForm  = container.querySelector('#login-form');
  const signupForm = container.querySelector('#signup-form');
  const loginTab   = container.querySelector('#tab-login');
  const signupTab  = container.querySelector('#tab-signup');

  function showTab(tab) {
    if (tab === 'login') {
      loginForm.style.display  = '';
      signupForm.style.display = 'none';
      loginTab.classList.add('active');
      signupTab.classList.remove('active');
    } else {
      loginForm.style.display  = 'none';
      signupForm.style.display = '';
      loginTab.classList.remove('active');
      signupTab.classList.add('active');
    }
  }

  loginTab.onclick  = () => showTab('login');
  signupTab.onclick = () => showTab('signup');

  // ── Login submit ───────────────────────────────────────────────
  loginForm.onsubmit = async (e) => {
    e.preventDefault();
    const errEl = container.querySelector('#login-error');
    const btn   = container.querySelector('#login-btn');
    errEl.style.display = 'none';
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Signing in…';

    try {
      const { token, user } = await api.post('/api/auth/login', {
        email:    container.querySelector('#login-email').value.trim(),
        password: container.querySelector('#login-password').value,
      });
      Auth.save(token, user);
      Toast.success(`Welcome back, ${user.name}!`);
      Router.navigate('/dashboard');
    } catch (err) {
      errEl.textContent = '⚠ ' + err.message;
      errEl.style.display = 'flex';
      btn.disabled = false;
      btn.textContent = 'Sign In';
    }
  };

  // ── Signup submit ──────────────────────────────────────────────
  signupForm.onsubmit = async (e) => {
    e.preventDefault();
    const errEl = container.querySelector('#signup-error');
    const btn   = container.querySelector('#signup-btn');
    errEl.style.display = 'none';
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Creating account…';

    try {
      const { token, user } = await api.post('/api/auth/signup', {
        name:     container.querySelector('#signup-name').value.trim(),
        email:    container.querySelector('#signup-email').value.trim(),
        password: container.querySelector('#signup-password').value,
      });
      Auth.save(token, user);
      Toast.success(`Account created! Welcome, ${user.name}!`);
      Router.navigate('/dashboard');
    } catch (err) {
      errEl.textContent = '⚠ ' + err.message;
      errEl.style.display = 'flex';
      btn.disabled = false;
      btn.textContent = 'Create Account';
    }
  };
}
