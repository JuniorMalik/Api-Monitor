// ── State ──────────────────────────────────────────────────────────────────
let endpoints = [];
let editingId = null;
let latencyChart = null;
let ws = null;

// ── DOM refs ────────────────────────────────────────────────────────────────
const grid = document.getElementById('endpoints-grid');
const modalAdd = document.getElementById('modal-add');
const modalHistory = document.getElementById('modal-history');
const modalUsers = document.getElementById('modal-users');
const wsDot = document.getElementById('ws-dot');
const wsLabel = document.getElementById('ws-label');
const loginScreen = document.getElementById('login-screen');
const loginForm = document.getElementById('login-form');

// ── Auth ─────────────────────────────────────────────────────────────────────
function getToken() {
  return localStorage.getItem('token');
}

function authHeaders() {
  return { 'Authorization': `Bearer ${getToken()}` };
}

function handleUnauthorized(res) {
  if (res.status === 401) {
    localStorage.removeItem('token');
    loginScreen.classList.remove('hidden');
    if (ws) { ws.close(); ws = null; }
    endpoints = [];
    renderAll();
    return true;
  }
  return false;
}

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const u = document.getElementById('login-user').value;
  const p = document.getElementById('login-pass').value;

  const params = new URLSearchParams();
  params.append('username', u);
  params.append('password', p);

  const res = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params
  });

  if (res.ok) {
    const data = await res.json();
    localStorage.setItem('token', data.access_token);
    loginScreen.classList.add('hidden');
    document.getElementById('login-pass').value = '';
    initApp();
  } else {
    showToast('Falha no login. Verifique usuário e senha.', 'down');
  }
});

// ── WebSocket ───────────────────────────────────────────────────────────────
function connectWS() {
  if (!getToken()) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'auth', token: getToken() }));
    wsDot.classList.add('connected');
    wsLabel.textContent = 'Conectado';
  };

  ws.onclose = (e) => {
    wsDot.classList.remove('connected');
    wsLabel.textContent = 'Reconectando...';
    if (e.code === 1008) {
      handleUnauthorized({ status: 401 });
      return;
    }
    setTimeout(connectWS, 3000);
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'check_result') handleCheckResult(msg);
    } catch (_) {}
  };
}

function handleCheckResult(msg) {
  const ep = endpoints.find(e => e.id === msg.endpoint_id);
  if (!ep) return;

  const prevStatus = ep.ultimo_status;
  ep.ultimo_status = msg.status;
  ep.ultima_latencia = msg.latencia_ms;

  renderCard(ep);
  updateStats();

  if (prevStatus && prevStatus !== msg.status) {
    if (msg.status === 'down') {
      showToast(`Atenção: ${ep.nome} caiu!`, 'down');
      document.title = `⚠️ ALERTA — ApiMonitor`;
    } else if (prevStatus === 'down' && msg.status === 'up') {
      showToast(`Recuperado: ${ep.nome} voltou`, 'up');
      document.title = 'ApiMonitor';
    }
  }
}

// ── API calls ────────────────────────────────────────────────────────────────
async function fetchEndpoints() {
  const res = await fetch('/api/endpoints', { headers: authHeaders() });
  if (handleUnauthorized(res)) return;
  endpoints = await res.json();
  renderAll();
}

async function saveEndpoint() {
  const nome = document.getElementById('input-nome').value.trim();
  const url = document.getElementById('input-url').value.trim();
  const intervalo = parseInt(document.getElementById('input-interval').value);

  if (!nome || !url) { showToast('Nome e URL são obrigatórios', 'down'); return; }

  const body = { nome, url, intervalo_minutos: intervalo };
  const method = editingId ? 'PUT' : 'POST';
  const endpoint_url = editingId ? `/api/endpoints/${editingId}` : '/api/endpoints';

  const res = await fetch(endpoint_url, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });

  if (handleUnauthorized(res)) return;
  if (!res.ok) { showToast('Erro ao salvar na API', 'down'); return; }

  const saved = await res.json();

  if (editingId) {
    const idx = endpoints.findIndex(e => e.id === editingId);
    if (idx >= 0) endpoints[idx] = saved;
  } else {
    endpoints.unshift(saved);
  }

  closeModal();
  renderAll();
  showToast('Endpoint salvo com sucesso', 'up');
}

async function deleteEndpoint(id) {
  if (!confirm('Remover endpoint permanentemente?')) return;
  const res = await fetch(`/api/endpoints/${id}`, { method: 'DELETE', headers: authHeaders() });
  if (handleUnauthorized(res)) return;
  endpoints = endpoints.filter(e => e.id !== id);
  renderAll();
  showToast('Endpoint deletado', 'up');
}

async function checkNow(id) {
  const res = await fetch(`/api/endpoints/${id}/check-now`, { method: 'POST', headers: authHeaders() });
  if (handleUnauthorized(res)) return;
  showToast('Ping enviado...', 'up');
}

async function toggleActive(id) {
  const ep = endpoints.find(e => e.id === id);
  if (!ep) return;
  const res = await fetch(`/api/endpoints/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ ativo: !ep.ativo }),
  });
  if (handleUnauthorized(res)) return;
  const updated = await res.json();
  const idx = endpoints.findIndex(e => e.id === id);
  if (idx >= 0) endpoints[idx] = updated;
  renderAll();
}

async function openHistory(id) {
  const ep = endpoints.find(e => e.id === id);
  document.getElementById('history-title').textContent = `Métricas: ${ep?.nome || ''}`;
  modalHistory.classList.add('active');

  const res = await fetch(`/api/endpoints/${id}/history?limit=50`, { headers: authHeaders() });
  if (handleUnauthorized(res)) return;
  const checks = await res.json();

  renderHistoryList(checks);
  renderLatencyChart(checks);
}

// ── Users ────────────────────────────────────────────────────────────────────
async function fetchUsers() {
  const res = await fetch('/api/users', { headers: authHeaders() });
  if (handleUnauthorized(res)) return;
  const users = await res.json();
  renderUsersList(users);
}

function renderUsersList(users) {
  const list = document.getElementById('users-list');
  if (!users.length) {
    list.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-secondary);">Sem usuários.</div>';
    return;
  }
  
  list.innerHTML = users.map(u => `
    <div class="hist-item" style="justify-content: space-between;">
      <div style="display: flex; gap: 16px; align-items: center;">
        <span class="badge" style="color: var(--text-secondary); border-color: var(--border-subtle)">ID: ${u.id}</span>
        <span style="font-weight: 500; color: var(--text-white)">${escHtml(u.username)}</span>
      </div>
      <button class="btn-icon danger" title="Excluir Usuário" onclick="deleteUser(${u.id})">${SVG.trash}</button>
    </div>
  `).join('');
}

async function saveUser() {
  const u = document.getElementById('input-new-user').value.trim();
  const p = document.getElementById('input-new-pass').value.trim();
  
  if (!u || !p) { showToast('Preencha usuário e senha', 'down'); return; }
  
  const res = await fetch('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ username: u, password: p })
  });
  
  if (handleUnauthorized(res)) return;
  if (!res.ok) {
    const data = await res.json();
    showToast(data.detail || 'Erro ao criar usuário', 'down');
    return;
  }
  
  showToast('Usuário criado com sucesso', 'up');
  document.getElementById('input-new-user').value = '';
  document.getElementById('input-new-pass').value = '';
  fetchUsers();
}

async function deleteUser(id) {
  if (!confirm('Remover este usuário permanentemente?')) return;
  
  const res = await fetch(`/api/users/${id}`, { method: 'DELETE', headers: authHeaders() });
  if (handleUnauthorized(res)) return;
  
  if (!res.ok) {
    const data = await res.json();
    showToast(data.detail || 'Erro ao deletar', 'down');
    return;
  }
  
  showToast('Usuário removido', 'up');
  fetchUsers();
}

// ── Render ───────────────────────────────────────────────────────────────────
function renderAll() {
  grid.innerHTML = '';

  if (endpoints.length === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <h3>Nenhum monitoramento</h3>
        <p>Cadastre uma API para começar.</p>
      </div>`;
    return;
  }

  endpoints.forEach(ep => renderCard(ep));
  updateStats();
  document.getElementById('last-update').textContent = `Atualizado às ${new Date().toLocaleTimeString('pt-BR')}`;
}

const SVG = {
  refresh: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 1 0 2.81-6.5L21 8"></path></svg>`,
  chart: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>`,
  play: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>`,
  pause: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>`,
  edit: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>`,
  trash: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`
};

function renderCard(ep) {
  let status = ep.ultimo_status || 'unknown';
  if (!ep.ativo) status = 'paused';
  
  const statusLabel = { up: 'OPERACIONAL', down: 'FALHA', degraded: 'LENTO', unknown: 'AGUARDANDO', paused: 'PAUSADO' };
  
  const latLabel = ep.ultima_latencia != null ? `${Math.round(ep.ultima_latencia)}ms` : '—';
  const latClass = ep.ultima_latencia == null ? '' : ep.ultima_latencia > 2000 ? 'slow' : 'good';
  const uptimeLabel = ep.uptime_percent != null ? `${ep.uptime_percent}%` : '—';

  const html = `
    <div class="card ${!ep.ativo ? 'inactive' : ''}" id="card-${ep.id}">
      
      <div class="card-top">
        <div>
          <div class="card-name" title="${escHtml(ep.nome)}">${escHtml(ep.nome)}</div>
          <div class="card-url" title="${escHtml(ep.url)}">${escHtml(ep.url)}</div>
        </div>
        <div class="badge ${status}">${statusLabel[status]}</div>
      </div>

      <div class="card-data">
        <div class="data-point">
          <span class="data-label">Latência</span>
          <span class="data-val ${latClass}">${latLabel}</span>
        </div>
        <div class="data-point">
          <span class="data-label">Uptime</span>
          <span class="data-val ${ep.uptime_percent >= 99 ? 'good' : ep.uptime_percent >= 90 ? '' : 'bad'}">${uptimeLabel}</span>
        </div>
        <div class="data-point">
          <span class="data-label">Check</span>
          <span class="data-val">${ep.intervalo_minutos}m</span>
        </div>
      </div>

      <div class="card-bottom">
        <span class="card-id">ID: ${ep.id}</span>
        <div class="actions">
          <button class="btn-icon" title="Forçar Ping" onclick="checkNow(${ep.id})">${SVG.refresh}</button>
          <button class="btn-icon" title="Ver Gráficos" onclick="openHistory(${ep.id})">${SVG.chart}</button>
          <button class="btn-icon" title="${ep.ativo ? 'Pausar' : 'Ativar'}" onclick="toggleActive(${ep.id})">${ep.ativo ? SVG.pause : SVG.play}</button>
          <button class="btn-icon" title="Editar" onclick="openEditModal(${ep.id})">${SVG.edit}</button>
          <button class="btn-icon danger" title="Excluir" onclick="deleteEndpoint(${ep.id})">${SVG.trash}</button>
        </div>
      </div>
      
    </div>`;

  const existing = document.getElementById(`card-${ep.id}`);
  if (existing) {
    existing.outerHTML = html;
  } else {
    grid.insertAdjacentHTML('beforeend', html);
  }
}

function updateStats() {
  const active = endpoints.filter(e => e.ativo);
  const upCount = active.filter(e => e.ultimo_status === 'up').length;
  const downCount = active.filter(e => e.ultimo_status === 'down').length;
  const uptimes = endpoints.filter(e => e.uptime_percent != null).map(e => e.uptime_percent);
  const avgUptime = uptimes.length ? (uptimes.reduce((a, b) => a + b, 0) / uptimes.length).toFixed(1) + '%' : '0%';

  document.getElementById('stat-total').textContent = endpoints.length;
  document.getElementById('stat-up').textContent = upCount;
  document.getElementById('stat-down').textContent = downCount;
  document.getElementById('stat-uptime').textContent = avgUptime;
}

// ── History ──────────────────────────────────────────────────────────────────
function renderHistoryList(checks) {
  const list = document.getElementById('history-list');
  if (!checks.length) {
    list.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-secondary);">Sem registros ainda.</div>';
    return;
  }

  list.innerHTML = checks.map(c => {
    const t = new Date(c.checado_em + 'Z').toLocaleTimeString('pt-BR');
    const lat = c.latencia_ms != null ? `${Math.round(c.latencia_ms)}ms` : 'ERR';
    let statusClass = 'unknown';
    if(c.status === 'up') statusClass = 'up';
    if(c.status === 'down') statusClass = 'down';
    if(c.status === 'degraded') statusClass = 'degraded';

    return `
      <div class="hist-item">
        <div class="h-stat"><span class="badge ${statusClass}">${c.status}</span></div>
        <div class="h-lat">${lat}</div>
        <div class="h-code">${c.http_status_code || '---'}</div>
        <div class="h-time">${t}</div>
      </div>`;
  }).join('');
}

function renderLatencyChart(checks) {
  const ctx = document.getElementById('latency-chart').getContext('2d');
  const data = [...checks].reverse().filter(c => c.latencia_ms != null).slice(-30);

  if (latencyChart) latencyChart.destroy();

  latencyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.map(c => new Date(c.checado_em + 'Z').toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })),
      datasets: [{
        label: 'Latência (ms)',
        data: data.map(c => Math.round(c.latencia_ms)),
        borderColor: '#58A6FF',
        backgroundColor: 'rgba(88, 166, 255, 0.1)',
        borderWidth: 2,
        tension: 0.1,
        fill: true,
        pointRadius: 2,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { 
          ticks: { color: '#8B949E', font: { family: "'Roboto Mono', monospace", size: 10 } }, 
          grid: { color: '#30363D' }, 
          beginAtZero: true 
        },
      },
    },
  });
}

// ── Modals ───────────────────────────────────────────────────────────────────
function openAddModal() {
  editingId = null;
  document.getElementById('modal-add-title').textContent = 'Novo Endpoint';
  document.getElementById('input-nome').value = '';
  document.getElementById('input-url').value = '';
  document.getElementById('input-interval').value = '5';
  modalAdd.classList.add('active');
  setTimeout(() => document.getElementById('input-nome').focus(), 100);
}

function openEditModal(id) {
  const ep = endpoints.find(e => e.id === id);
  if (!ep) return;
  editingId = id;
  document.getElementById('modal-add-title').textContent = 'Editar Endpoint';
  document.getElementById('input-nome').value = ep.nome;
  document.getElementById('input-url').value = ep.url;
  document.getElementById('input-interval').value = ep.intervalo_minutos;
  modalAdd.classList.add('active');
}

function closeModal() {
  modalAdd.classList.remove('active');
  editingId = null;
}

// ── Toast ────────────────────────────────────────────────────────────────────
function showToast(msg, type = 'up') {
  const c = document.getElementById('toast-container');
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `<span>${msg}</span>`;
  c.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ── Utils ────────────────────────────────────────────────────────────────────
function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Event Listeners ──────────────────────────────────────────────────────────
document.getElementById('btn-add-endpoint').addEventListener('click', openAddModal);
document.getElementById('btn-close-modal').addEventListener('click', closeModal);
document.getElementById('btn-cancel-modal').addEventListener('click', closeModal);
document.getElementById('btn-save-endpoint').addEventListener('click', saveEndpoint);
document.getElementById('btn-close-history').addEventListener('click', () => modalHistory.classList.remove('active'));

document.getElementById('btn-users').addEventListener('click', () => {
  modalUsers.classList.add('active');
  fetchUsers();
});
document.getElementById('btn-close-users').addEventListener('click', () => modalUsers.classList.remove('active'));
document.getElementById('btn-save-user').addEventListener('click', saveUser);

modalAdd.addEventListener('click', e => { if (e.target === modalAdd) closeModal(); });
modalHistory.addEventListener('click', e => { if (e.target === modalHistory) modalHistory.classList.remove('active'); });
modalUsers.addEventListener('click', e => { if (e.target === modalUsers) modalUsers.classList.remove('active'); });

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { 
    closeModal(); 
    modalHistory.classList.remove('active'); 
    modalUsers.classList.remove('active');
  }
});

// ── Init ─────────────────────────────────────────────────────────────────────
let pollInterval = null;

function initApp() {
  connectWS();
  fetchEndpoints();
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(fetchEndpoints, 30000);
}

if (getToken()) {
  loginScreen.classList.add('hidden');
  initApp();
} else {
  loginScreen.classList.remove('hidden');
}
