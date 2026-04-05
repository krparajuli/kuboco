'use strict';

/* ═══════════════════════════════════════════════════════════════════════════
   API CLIENT
   ═══════════════════════════════════════════════════════════════════════════ */

const API = {
  async _req(method, path, body) {
    const opts = {
      method,
      credentials: 'include',
      headers: {},
    };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch('/api' + path, opts);
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!res.ok) throw new APIError(res.status, data.detail || 'Request failed');
    return data;
  },

  register:    (username, password)  => API._req('POST', '/auth/register', { username, password }),
  login:       (username, password)  => API._req('POST', '/auth/login',    { username, password }),
  logout:      ()                    => API._req('POST', '/auth/logout'),
  me:          ()                    => API._req('GET',  '/auth/me'),
  getContainers: ()                  => API._req('GET',  '/containers'),
  createContainer: (name, image)     => API._req('POST', '/containers', { name, image: image || undefined }),
  getContainer:  id                  => API._req('GET',  `/containers/${id}`),
  deleteContainer: id                => API._req('DELETE', `/containers/${id}`),
};

class APIError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   SESSION HELPERS
   ═══════════════════════════════════════════════════════════════════════════ */

const Session = {
  set(user, token) {
    sessionStorage.setItem('kuboco_user', JSON.stringify(user));
    sessionStorage.setItem('kuboco_token', token);
  },
  clear() {
    sessionStorage.removeItem('kuboco_user');
    sessionStorage.removeItem('kuboco_token');
  },
  getUser() {
    try { return JSON.parse(sessionStorage.getItem('kuboco_user')); } catch { return null; }
  },
  getToken() {
    return sessionStorage.getItem('kuboco_token') || '';
  },
};

/* ═══════════════════════════════════════════════════════════════════════════
   ROUTER
   ═══════════════════════════════════════════════════════════════════════════ */

const Router = {
  go(path) {
    window.location.hash = '#' + path;
  },
  current() {
    return window.location.hash.slice(1) || '/';
  },
};

/* ═══════════════════════════════════════════════════════════════════════════
   TERMINAL MANAGER
   ═══════════════════════════════════════════════════════════════════════════ */

const TerminalMgr = {
  term: null,
  fitAddon: null,
  socket: null,
  _resizeObserver: null,
  _resizeTimer: null,

  init(mountEl, containerId) {
    this.dispose();

    this.term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: '"Fira Code", "Cascadia Code", "Consolas", monospace',
      theme: {
        background: '#0d1117',
        foreground: '#e6edf3',
        cursor:     '#58a6ff',
        selection:  'rgba(88,166,255,0.25)',
        black:      '#484f58', red:     '#ff7b72', green:  '#3fb950',
        yellow:     '#d29922', blue:    '#58a6ff', magenta:'#d2a8ff',
        cyan:       '#39d353', white:   '#b1bac4',
        brightBlack: '#6e7681', brightRed: '#ffa198', brightGreen: '#56d364',
        brightYellow: '#e3b341', brightBlue: '#79c0ff', brightMagenta: '#f0abff',
        brightCyan: '#56d364',  brightWhite: '#ffffff',
      },
      scrollback: 10000,
      allowTransparency: false,
    });

    this.fitAddon = new FitAddon.FitAddon();
    this.term.loadAddon(this.fitAddon);
    this.term.open(mountEl);
    this.fitAddon.fit();

    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    const token   = Session.getToken();
    const wsUrl   = `${wsProto}://${location.host}/api/ws/terminal/${containerId}?token=${token}`;

    this.socket = new WebSocket(wsUrl, ['tty']);
    this.socket.binaryType = 'arraybuffer';

    this.socket.onopen = () => {
      // ttyd protocol: send auth message first (empty token — backend handles auth)
      this.socket.send(JSON.stringify({ AuthToken: '' }));
    };

    this.socket.onmessage = (event) => {
      // ttyd frames: first byte/char is the command type ('0'=data, '1'=title, '2'=prefs)
      let cmd, payload;
      if (event.data instanceof ArrayBuffer) {
        const arr = new Uint8Array(event.data);
        cmd     = String.fromCharCode(arr[0]);
        payload = arr.slice(1);
      } else {
        cmd     = event.data[0];
        payload = event.data.slice(1);
      }

      if (cmd === '0') {
        this.term.write(payload);
      } else if (cmd === '1') {
        const title = typeof payload === 'string' ? payload : new TextDecoder().decode(payload);
        document.title = `${title || 'shell'} — Kuboco`;
      }
      // cmd '2' = preferences JSON (ignored)
    };

    this.socket.onclose = () => {
      this.term.write('\r\n\x1b[2m[Connection closed]\x1b[0m\r\n');
    };

    this.socket.onerror = () => {
      this.term.write('\r\n\x1b[31m[Connection error]\x1b[0m\r\n');
    };

    this.term.onData((data) => {
      if (this.socket.readyState === WebSocket.OPEN) {
        this.socket.send('0' + data);  // prefix '0' = stdin
      }
    });

    this.term.onResize(({ cols, rows }) => {
      if (this.socket.readyState === WebSocket.OPEN) {
        this.socket.send('1' + JSON.stringify({ columns: cols, rows }));
      }
    });

    // Resize observer for responsive terminal
    this._resizeObserver = new ResizeObserver(() => {
      clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => {
        try { this.fitAddon.fit(); } catch {}
      }, 50);
    });
    this._resizeObserver.observe(mountEl);
  },

  dispose() {
    if (this._resizeObserver) { this._resizeObserver.disconnect(); this._resizeObserver = null; }
    if (this._resizeTimer)    { clearTimeout(this._resizeTimer); }
    if (this.socket)          { this.socket.close(); this.socket = null; }
    if (this.term)            { this.term.dispose(); this.term = null; }
    this.fitAddon = null;
    document.title = 'Kuboco';
  },
};

/* ═══════════════════════════════════════════════════════════════════════════
   STATUS HELPERS
   ═══════════════════════════════════════════════════════════════════════════ */

function statusBadge(status) {
  return `<span class="status-badge status-${status}">${status}</span>`;
}

function fmt(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/* ═══════════════════════════════════════════════════════════════════════════
   VIEWS
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Login / Register ──────────────────────────────────────────────────── */

function renderAuth(mode) {
  const isLogin = mode === 'login';
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="auth-wrapper">
      <div class="auth-card">
        <div class="text-center mb-4">
          <div class="auth-logo">KUBOCO</div>
          <div class="auth-tagline">Kubernetes Container Runner</div>
        </div>
        <div id="auth-error" class="alert alert-danger d-none" role="alert"></div>
        <form id="auth-form" autocomplete="on">
          <div class="mb-3">
            <label for="username" class="form-label fw-semibold">Username</label>
            <input type="text" id="username" name="username" class="form-control bg-dark border-secondary text-light"
              placeholder="Enter username" autocomplete="username" required autofocus />
          </div>
          <div class="mb-4">
            <label for="password" class="form-label fw-semibold">Password</label>
            <input type="password" id="password" name="password" class="form-control bg-dark border-secondary text-light"
              placeholder="Enter password" autocomplete="${isLogin ? 'current-password' : 'new-password'}" required />
          </div>
          <button type="submit" id="auth-btn" class="btn btn-primary w-100 fw-semibold py-2">
            ${isLogin ? 'Sign in' : 'Create account'}
          </button>
        </form>
        <div class="text-center mt-3">
          <small class="text-secondary">
            ${isLogin
              ? 'No account? <a href="#/register" class="text-info text-decoration-none">Register</a>'
              : 'Already have an account? <a href="#/login" class="text-info text-decoration-none">Sign in</a>'}
          </small>
        </div>
      </div>
    </div>`;

  document.getElementById('auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('auth-btn');
    const errEl = document.getElementById('auth-error');
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    btn.disabled = true;
    btn.textContent = isLogin ? 'Signing in…' : 'Creating account…';
    errEl.classList.add('d-none');
    try {
      const data = isLogin
        ? await API.login(username, password)
        : await API.register(username, password);
      Session.set({ id: data.id, username: data.username }, data.access_token);
      Router.go('/dashboard');
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('d-none');
      btn.disabled = false;
      btn.textContent = isLogin ? 'Sign in' : 'Create account';
    }
  });
}

/* ── Dashboard ─────────────────────────────────────────────────────────── */

let _dashboardPoll = null;

async function renderDashboard() {
  if (_dashboardPoll) { clearInterval(_dashboardPoll); _dashboardPoll = null; }

  const user = Session.getUser();
  const app = document.getElementById('app');

  app.innerHTML = `
    ${navbar(user, 'dashboard')}
    <div class="container py-4" style="max-width:860px">
      <div class="d-flex align-items-center justify-content-between mb-4">
        <h4 class="mb-0 fw-bold">My Containers</h4>
        <button id="new-container-btn" class="btn btn-primary btn-sm px-3">
          <i class="bi bi-plus-lg me-1"></i> New Container
        </button>
      </div>
      <div id="containers-list">
        <div class="d-flex justify-content-center py-5">
          <div class="spinner-border spinner-border-sm text-secondary" role="status"></div>
        </div>
      </div>
    </div>

    <!-- New container modal -->
    <div class="modal fade" id="new-container-modal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content border-secondary" style="background:#161b22">
          <div class="modal-header border-secondary">
            <h5 class="modal-title fw-bold">New Container</h5>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body">
            <div id="create-error" class="alert alert-danger d-none"></div>
            <div class="mb-3">
              <label for="container-name" class="form-label fw-semibold">Name</label>
              <input type="text" id="container-name" class="form-control bg-dark border-secondary text-light"
                placeholder="e.g. my-dev" pattern="[a-z0-9][a-z0-9\\-]{0,30}[a-z0-9]?" required />
              <div class="form-text text-secondary">Lowercase letters, numbers, hyphens (2-32 chars)</div>
            </div>
            <div class="mb-1">
              <label for="container-image" class="form-label fw-semibold">Image <span class="text-secondary fw-normal">(optional)</span></label>
              <input type="text" id="container-image" class="form-control bg-dark border-secondary text-light"
                placeholder="Default: kuboco/ubuntu-ttyd:latest" />
            </div>
          </div>
          <div class="modal-footer border-secondary">
            <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
            <button type="button" id="create-container-btn" class="btn btn-primary fw-semibold">
              <i class="bi bi-play-circle me-1"></i> Launch
            </button>
          </div>
        </div>
      </div>
    </div>`;

  await loadContainerList();

  // Poll for status updates every 5 seconds
  _dashboardPoll = setInterval(loadContainerList, 5000);

  document.getElementById('new-container-btn').addEventListener('click', () => {
    const modal = new bootstrap.Modal(document.getElementById('new-container-modal'));
    modal.show();
  });

  document.getElementById('create-container-btn').addEventListener('click', async () => {
    const nameEl  = document.getElementById('container-name');
    const imageEl = document.getElementById('container-image');
    const errEl   = document.getElementById('create-error');
    const btn     = document.getElementById('create-container-btn');

    const name  = nameEl.value.trim().toLowerCase();
    const image = imageEl.value.trim() || null;

    if (!name) { nameEl.focus(); return; }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Launching…';
    errEl.classList.add('d-none');

    try {
      await API.createContainer(name, image);
      bootstrap.Modal.getInstance(document.getElementById('new-container-modal')).hide();
      nameEl.value = '';
      imageEl.value = '';
      await loadContainerList();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('d-none');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-play-circle me-1"></i> Launch';
    }
  });
}

async function loadContainerList() {
  const listEl = document.getElementById('containers-list');
  if (!listEl) return;

  let containers;
  try {
    containers = await API.getContainers();
  } catch {
    listEl.innerHTML = '<div class="alert alert-danger">Failed to load containers</div>';
    return;
  }

  if (containers.length === 0) {
    listEl.innerHTML = `
      <div class="empty-state">
        <i class="bi bi-box-seam"></i>
        <p class="mb-3">No containers yet.</p>
        <button class="btn btn-outline-primary btn-sm" onclick="document.getElementById('new-container-btn').click()">
          Launch your first container
        </button>
      </div>`;
    return;
  }

  listEl.innerHTML = containers.map(c => `
    <div class="container-card p-3 mb-3" data-id="${c.id}">
      <div class="d-flex align-items-center gap-3 flex-wrap">
        <div class="flex-grow-1">
          <div class="d-flex align-items-center gap-2 mb-1">
            <span class="fw-semibold">${escHtml(c.name)}</span>
            ${statusBadge(c.status)}
          </div>
          <div class="text-secondary" style="font-size:0.8rem">
            <code class="text-info">${escHtml(c.image)}</code>
            &nbsp;·&nbsp; Created ${fmt(c.created_at)}
          </div>
        </div>
        <div class="d-flex gap-2">
          ${c.status !== 'stopped' ? `
            <button class="btn btn-sm btn-outline-info open-btn" data-id="${c.id}" title="Open terminal">
              <i class="bi bi-terminal me-1"></i>Open
            </button>` : ''}
          <button class="btn btn-sm btn-outline-danger delete-btn" data-id="${c.id}" title="Delete container">
            <i class="bi bi-trash"></i>
          </button>
        </div>
      </div>
    </div>`).join('');

  listEl.querySelectorAll('.open-btn').forEach(btn => {
    btn.addEventListener('click', () => Router.go(`/container/${btn.dataset.id}`));
  });

  listEl.querySelectorAll('.delete-btn').forEach(btn => {
    btn.addEventListener('click', () => confirmDelete(parseInt(btn.dataset.id)));
  });
}

async function confirmDelete(id) {
  if (!confirm('Stop and delete this container?')) return;
  try {
    await API.deleteContainer(id);
    await loadContainerList();
  } catch (err) {
    alert('Failed to delete: ' + err.message);
  }
}

/* ── Container Detail ──────────────────────────────────────────────────── */

let _containerPoll = null;

async function renderContainer(id) {
  if (_containerPoll) { clearInterval(_containerPoll); _containerPoll = null; }
  TerminalMgr.dispose();

  const user = Session.getUser();
  const app = document.getElementById('app');

  app.innerHTML = `
    ${navbar(user, 'container', id)}
    <div class="container-detail-layout">
      <div class="terminal-panel">
        <div id="terminal-overlay" class="terminal-overlay">
          <div class="spinner-border text-primary" role="status"></div>
          <div class="overlay-text" id="overlay-msg">Loading container…</div>
        </div>
        <div id="terminal-mount"></div>
      </div>
      <div class="port-panel">
        <div class="d-flex align-items-center justify-content-between mb-2">
          <span class="fw-semibold" style="font-size:0.9rem">
            <i class="bi bi-plug me-1 text-info"></i> Port Proxy
          </span>
          <small class="text-secondary">Access services running inside the container</small>
        </div>
        <div class="d-flex gap-2 mb-1">
          <input type="number" id="port-input" class="form-control form-control-sm bg-dark border-secondary text-light"
            placeholder="Port number" min="1" max="65535" style="max-width:140px" />
          <input type="text" id="port-path-input" class="form-control form-control-sm bg-dark border-secondary text-light"
            placeholder="Path (optional, e.g. /app)" style="max-width:220px" />
          <button id="port-connect-btn" class="btn btn-sm btn-outline-info px-3">
            <i class="bi bi-globe me-1"></i>Connect
          </button>
          <button id="port-newtab-btn" class="btn btn-sm btn-outline-secondary px-2" title="Open in new tab" style="display:none">
            <i class="bi bi-box-arrow-up-right"></i>
          </button>
        </div>
        <div id="port-proxy-area"></div>
      </div>
    </div>`;

  document.getElementById('port-connect-btn').addEventListener('click', () => openPortProxy(id));
  document.getElementById('port-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') openPortProxy(id);
  });

  // Start polling/loading the container
  await pollAndInitContainer(id);
}

async function pollAndInitContainer(id) {
  const overlayEl = document.getElementById('terminal-overlay');
  const msgEl     = document.getElementById('overlay-msg');
  if (!overlayEl) return;

  let container;
  try {
    container = await API.getContainer(id);
  } catch (err) {
    if (msgEl) msgEl.textContent = 'Error: ' + err.message;
    return;
  }

  if (container.status === 'running') {
    initTerminal(id, overlayEl);
    return;
  }

  if (container.status === 'stopped') {
    if (msgEl) {
      msgEl.textContent = 'This container is stopped.';
      if (overlayEl) overlayEl.querySelector('.spinner-border')?.remove();
    }
    return;
  }

  // Starting/pending — poll until running
  if (msgEl) msgEl.textContent = `Container is ${container.status}…`;

  _containerPoll = setInterval(async () => {
    if (!document.getElementById('terminal-overlay')) {
      clearInterval(_containerPoll);
      return;
    }
    try {
      const c = await API.getContainer(id);
      if (msgEl) msgEl.textContent = `Container is ${c.status}…`;
      if (c.status === 'running') {
        clearInterval(_containerPoll);
        initTerminal(id, document.getElementById('terminal-overlay'));
      } else if (c.status === 'stopped' || c.status === 'error') {
        clearInterval(_containerPoll);
        if (msgEl) msgEl.textContent = `Container ${c.status}.`;
        const spinner = document.getElementById('terminal-overlay')?.querySelector('.spinner-border');
        if (spinner) spinner.remove();
      }
    } catch { /* keep polling */ }
  }, 2000);
}

function initTerminal(containerId, overlayEl) {
  const mountEl = document.getElementById('terminal-mount');
  if (!mountEl) return;

  // Hide overlay
  if (overlayEl) overlayEl.style.display = 'none';

  TerminalMgr.init(mountEl, containerId);
  setTimeout(() => { try { TerminalMgr.fitAddon?.fit(); } catch {} }, 100);
}

function openPortProxy(containerId) {
  const portInput = document.getElementById('port-input');
  const pathInput = document.getElementById('port-path-input');
  const areaEl    = document.getElementById('port-proxy-area');
  const tabBtn    = document.getElementById('port-newtab-btn');

  const port = parseInt(portInput.value, 10);
  if (!port || port < 1 || port > 65535) {
    portInput.classList.add('is-invalid');
    setTimeout(() => portInput.classList.remove('is-invalid'), 2000);
    return;
  }

  const extraPath = (pathInput.value.trim().replace(/^\/?/, '') || '');
  const proxyUrl  = `/api/proxy/${containerId}/${port}/${extraPath}`;

  // Show new-tab button
  tabBtn.style.display = '';
  tabBtn.onclick = () => window.open(proxyUrl, '_blank');

  areaEl.innerHTML = `
    <div class="d-flex align-items-center justify-content-between mb-1">
      <small class="text-secondary">
        <i class="bi bi-arrow-right-circle me-1"></i>
        Proxying <strong>port ${port}</strong>${extraPath ? ' at <code>' + escHtml('/' + extraPath) + '</code>' : ''} &nbsp;
        <a href="${escHtml(proxyUrl)}" target="_blank" class="text-info text-decoration-none">
          <i class="bi bi-box-arrow-up-right"></i> new tab
        </a>
      </small>
      <button class="btn btn-link btn-sm text-secondary p-0" onclick="document.getElementById('port-proxy-area').innerHTML='';document.getElementById('port-newtab-btn').style.display='none'">
        <i class="bi bi-x-lg"></i>
      </button>
    </div>
    <div class="port-iframe-wrapper">
      <iframe
        id="port-iframe"
        src="${escHtml(proxyUrl)}"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
        title="Port ${port} proxy"
      ></iframe>
    </div>`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   SHARED COMPONENTS
   ═══════════════════════════════════════════════════════════════════════════ */

function navbar(user, view, containerId) {
  return `
    <nav class="navbar navbar-expand navbar-dark border-bottom border-secondary px-3" style="background:#161b22; height:56px">
      <a class="navbar-brand py-0" href="#/dashboard">
        <span>KUBOCO</span>
      </a>
      <div class="navbar-nav ms-3">
        ${view === 'container' && containerId ? `
          <a class="nav-link text-secondary" href="#/dashboard">
            <i class="bi bi-arrow-left me-1"></i>Dashboard
          </a>` : ''}
      </div>
      <div class="navbar-nav ms-auto d-flex align-items-center gap-2">
        <span class="nav-link text-secondary" style="font-size:0.85rem">
          <i class="bi bi-person-circle me-1"></i>${escHtml(user?.username || '')}
        </span>
        <button id="logout-btn" class="btn btn-outline-secondary btn-sm px-2">
          <i class="bi bi-box-arrow-right"></i>
        </button>
      </div>
    </nav>`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ═══════════════════════════════════════════════════════════════════════════
   MAIN ROUTER / INIT
   ═══════════════════════════════════════════════════════════════════════════ */

async function route() {
  // Cleanup before each navigation
  if (_dashboardPoll)  { clearInterval(_dashboardPoll);  _dashboardPoll  = null; }
  if (_containerPoll)  { clearInterval(_containerPoll);  _containerPoll  = null; }

  const path = window.location.hash.slice(1) || '/';

  // Public routes
  if (path === '/login')    { return renderAuth('login'); }
  if (path === '/register') { return renderAuth('register'); }

  // Auth check
  const user = Session.getUser();
  if (!user) {
    // Try to restore session from backend cookie
    try {
      const me = await API.me();
      const storedToken = Session.getToken();
      Session.set(me, storedToken);
    } catch {
      return Router.go('/login');
    }
  }

  // Attach logout handler after rendering
  document.addEventListener('click', (e) => {
    if (e.target.id === 'logout-btn' || e.target.closest('#logout-btn')) {
      e.preventDefault();
      API.logout().finally(() => {
        Session.clear();
        TerminalMgr.dispose();
        Router.go('/login');
      });
    }
  }, { once: true });

  if (path === '/dashboard' || path === '/') {
    return renderDashboard();
  }

  const containerMatch = path.match(/^\/container\/(\d+)$/);
  if (containerMatch) {
    return renderContainer(parseInt(containerMatch[1], 10));
  }

  // Fallback
  Router.go('/dashboard');
}

window.addEventListener('hashchange', route);

// Expose for testing/debugging
window._TerminalMgr = TerminalMgr;
window.addEventListener('load', route);
