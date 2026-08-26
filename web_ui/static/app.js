/* ======================================================
   Sherry Console v2 — app.js
   完整重寫：Log 動態載入、Settings 分組渲染、
   Model Picker（下拉+手動）、分頁、自動刷新、Toast、
   外掛生態 (SherryUI Plugin Ecosystem) 被動動態加載
   ====================================================== */

// ── State ────────────────────────────────────────────────
const state = {
  currentView: 'dashboard',
  currentLogFile: 'runtime_log.jsonl',
  currentPage: 1,
  totalPages: 1,
  totalEntries: 0,
  eventFilter: 'all',
  autoRefreshTimer: null,
  metricsTimer: null,
  serviceCheckTimer: null,
  botStatusTimer: null,
  dashboardTimer: null,
  autoRefreshEnabled: true,
  currentSettingsTab: 'tab-ai',
  settingsData: {},       // { key: { value, label, description, type, ... } }
  dirtyKeys: new Set(),   // keys that have been changed but not saved
  ollamaModels: [],
  lmsModels: {},           // keyed by model prefix for caching
  adminToken: localStorage.getItem('sherry_admin_token') || null,
  isAdmin: false,
  lastActivityTime: Date.now(),
  inactivityCheckTimer: null,
};

// ── SherryUI Ecosystem for Dynamic Mod Plugins ───────────
window.SherryUI = {
  plugins: {},
  activePluginId: null,
  registerPlugin(id, config) {
    this.plugins[id] = config;
    console.log(`[SherryUI] Registered plugin: ${id}`, config);
    // If user is currently looking at this mod view, trigger onActivate immediately
    if (state.currentView === (config.viewId || id) && typeof config.onActivate === 'function') {
      try { config.onActivate(); } catch (e) { console.error(e); }
    }
  },
  getAuthHeaders,
  showToast,
  switchView,
  escapeHtml,
};

// ── Init ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await checkAdminAuth();
  startInactivityTracker();
  await checkDynamicModules();
  await loadLogFiles();
  switchView('dashboard');
  startAutoRefresh();
});

/**
 * Dynamically discover and mount mods from /api/modules.
 * Builds sidebar navigation buttons, injects view HTML, and executes mod scripts.
 */
async function checkDynamicModules() {
  try {
    const res = await fetch('/api/modules');
    const data = await res.json();
    const mods = data.modules || {};
    const dynamicNavGroup = document.getElementById('sidebar-dynamic-mods');
    const dynamicNavWrap = document.getElementById('sidebar-dynamic-mods-wrap');
    const dynamicViewsContainer = document.getElementById('dynamic-mod-views');

    if (dynamicNavGroup) dynamicNavGroup.innerHTML = '';

    const modEntries = Object.values(mods).sort((a, b) => (a.order || 100) - (b.order || 100));
    let mountedModsCount = 0;

    for (const mod of modEntries) {
      if (!mod.enabled || !mod.has_web_ui) continue;
      mountedModsCount++;

      const navId = `nav-${mod.nav_id || mod.id}`;
      const viewId = `view-${mod.nav_id || mod.id}`;

      // 1. Sidebar navigation button
      if (dynamicNavGroup) {
        let btn = document.getElementById(navId);
        if (!btn) {
          btn = document.createElement('button');
          btn.className = 'nav-btn' + (mod.admin_only ? ' admin-only' : '');
          btn.id = navId;
          btn.onclick = () => switchView(mod.nav_id || mod.id);
          btn.innerHTML = `<span class="nav-icon">${mod.nav_icon || '🧩'}</span><span>${mod.nav_title || mod.display_name}</span>`;
          dynamicNavGroup.appendChild(btn);
        }
      }

      // 2. Fetch and mount dynamic view snippet if has view
      if (dynamicViewsContainer && mod.has_view && mod.view_url) {
        let existingView = document.getElementById(viewId);
        if (!existingView) {
          try {
            const viewRes = await fetch(mod.view_url);
            if (viewRes.ok) {
              const html = await viewRes.text();
              const tempWrapper = document.createElement('div');
              tempWrapper.innerHTML = html.trim();
              while (tempWrapper.firstChild) {
                const node = tempWrapper.firstChild;
                if (node.nodeType === Node.ELEMENT_NODE && node.classList && node.classList.contains('view')) {
                  if (!node.id) node.id = viewId;
                  node.classList.add('hidden');
                }
                dynamicViewsContainer.appendChild(node);
              }
            }
          } catch (err) {
            console.error(`Failed to load view for mod [${mod.id}]:`, err);
          }
        }
      }

      // 3. Dynamically inject script tag if has script
      if (mod.script_url) {
        const scriptId = `script-mod-${mod.id}`;
        if (!document.getElementById(scriptId)) {
          const script = document.createElement('script');
          script.id = scriptId;
          script.src = `${mod.script_url}?v=${Date.now()}`;
          script.async = true;
          document.body.appendChild(script);
        }
      }
    }

    if (dynamicNavWrap) {
      dynamicNavWrap.style.display = mountedModsCount > 0 ? 'flex' : 'none';
    }
  } catch (e) {
    console.warn('Failed to discover & mount dynamic modules:', e);
  }
}


// =========================================================
// VIEW SWITCHING
// =========================================================
function switchView(view) {
  // Notify previous plugin of deactivation
  if (window.SherryUI && window.SherryUI.activePluginId) {
    const prevPlugin = window.SherryUI.plugins[window.SherryUI.activePluginId];
    if (prevPlugin && typeof prevPlugin.onDeactivate === 'function') {
      try { prevPlugin.onDeactivate(); } catch (e) { console.error(e); }
    }
    window.SherryUI.activePluginId = null;
  }

  state.currentView = view;

  document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
  document.querySelectorAll('[id$="-toolbar"]').forEach(tb => tb.classList.add('hidden'));

  if (state.metricsTimer) {
    clearInterval(state.metricsTimer);
    state.metricsTimer = null;
  }
  if (state.serviceCheckTimer) {
    clearInterval(state.serviceCheckTimer);
    state.serviceCheckTimer = null;
  }
  if (state.botStatusTimer) {
    clearInterval(state.botStatusTimer);
    state.botStatusTimer = null;
  }
  if (state.dashboardTimer) {
    clearInterval(state.dashboardTimer);
    state.dashboardTimer = null;
  }

  if (view === 'dashboard') {
    const navDb = document.getElementById('nav-dashboard');
    if (navDb) navDb.classList.add('active');
    document.getElementById('view-dashboard').classList.remove('hidden');
    const dTb = document.getElementById('dashboard-toolbar');
    if (dTb) dTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '📊 總覽主控台';
    loadDashboard();
    state.dashboardTimer = setInterval(loadDashboard, 5000);
  } else if (view === 'logs') {
    document.getElementById('nav-logs').classList.add('active');
    document.getElementById('view-logs').classList.remove('hidden');
    document.getElementById('logs-toolbar').classList.remove('hidden');
    document.getElementById('view-title').textContent = '即時日誌';
    if (state.totalEntries === 0) loadLogs();
  } else if (view === 'metrics') {
    document.getElementById('nav-metrics').classList.add('active');
    document.getElementById('view-metrics').classList.remove('hidden');
    document.getElementById('metrics-toolbar').classList.remove('hidden');
    document.getElementById('view-title').textContent = '💻 系統資源監控';
    loadMetrics();
    state.metricsTimer = setInterval(loadMetrics, 2000);
  } else if (view === 'tasks') {
    document.getElementById('nav-tasks').classList.add('active');
    document.getElementById('view-tasks').classList.remove('hidden');
    document.getElementById('tasks-toolbar').classList.remove('hidden');
    document.getElementById('view-title').textContent = '⏰ 任務排程管理';
    loadTasks();
  } else if (view === 'chat') {
    document.getElementById('nav-chat').classList.add('active');
    document.getElementById('view-chat').classList.remove('hidden');
    document.getElementById('view-title').textContent = '💬 橘雪莉穩定性測試室 (必定觸發 CHAT)';
    setTimeout(() => {
      const inputEl = document.getElementById('chat-input');
      if (inputEl && !inputEl.classList.contains('hidden')) inputEl.focus();
    }, 100);
  } else if (view === 'services') {
    document.getElementById('nav-services').classList.add('active');
    document.getElementById('view-services').classList.remove('hidden');
    const sTb = document.getElementById('services-toolbar');
    if (sTb) sTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '📡 地端 AI 服務即時監測';
    loadServiceStatus();
    state.serviceCheckTimer = setInterval(loadServiceStatus, 5000);
  } else if (view === 'bot-status') {
    const navBs = document.getElementById('nav-bot-status');
    if (navBs) navBs.classList.add('active');
    document.getElementById('view-bot-status').classList.remove('hidden');
    const bTb = document.getElementById('bot-status-toolbar');
    if (bTb) bTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '🎵 雪莉語音與音樂動態監控';
    loadBotStatus();
    state.botStatusTimer = setInterval(loadBotStatus, 3000);
  } else if (view === 'audit') {
    const navAudit = document.getElementById('nav-audit');
    if (navAudit) navAudit.classList.add('active');
    document.getElementById('view-audit').classList.remove('hidden');
    const aTb = document.getElementById('audit-toolbar');
    if (aTb) aTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '🛡️ 管理員審計紀錄';
    loadAuditLogs();
  } else if (view === 'mod-manager') {
    const navMM = document.getElementById('nav-mod-manager');
    if (navMM) navMM.classList.add('active');
    const viewMM = document.getElementById('view-mod-manager');
    if (viewMM) viewMM.classList.remove('hidden');
    const mmTb = document.getElementById('mod-manager-toolbar');
    if (mmTb) mmTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '🧩 功能模組管理中心';
    loadModManager();
  } else if (view === 'roast') {
    const navRoast = document.getElementById('nav-roast');
    if (navRoast) navRoast.classList.add('active');
    const viewRoast = document.getElementById('view-roast');
    if (viewRoast) viewRoast.classList.remove('hidden');
    const rTb = document.getElementById('roast-toolbar');
    if (rTb) rTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '🎯 關鍵字吐槽管理中心';
    loadRoastConfig();
  } else if (view === 'settings') {
    document.getElementById('nav-settings').classList.add('active');
    document.getElementById('view-settings').classList.remove('hidden');
    document.getElementById('settings-toolbar').classList.remove('hidden');
    document.getElementById('view-title').textContent = '系統設定';
    if (Object.keys(state.settingsData).length === 0) loadSettings();
  } else {
    // Dynamic Mod Plugin View activation
    const navBtn = document.getElementById(`nav-${view}`) || document.getElementById(`nav-mod-${view}`);
    if (navBtn) navBtn.classList.add('active');
    const viewEl = document.getElementById(`view-${view}`);
    if (viewEl) viewEl.classList.remove('hidden');
    const tbEl = document.getElementById(`${view}-toolbar`);
    if (tbEl) tbEl.classList.remove('hidden');

    if (window.SherryUI && window.SherryUI.plugins) {
      for (const [pluginId, pConfig] of Object.entries(window.SherryUI.plugins)) {
        if (pConfig.viewId === view || pluginId === view) {
          window.SherryUI.activePluginId = pluginId;
          if (pConfig.title) document.getElementById('view-title').textContent = pConfig.title;
          if (typeof pConfig.onActivate === 'function') {
            try { pConfig.onActivate(); } catch (e) { console.error(e); }
          }
          break;
        }
      }
    }
  }
}

// =========================================================
// STATUS INDICATOR
// =========================================================
function setStatus(online, text) {
  const dot = document.getElementById('status-dot');
  const label = document.getElementById('status-text');
  dot.className = 'status-dot ' + (online ? 'online' : 'error');
  label.textContent = text;
}

// =========================================================
// LOGS
// =========================================================

/** Load list of available log files from server */
async function loadLogFiles() {
  try {
    const resp = await fetch('/api/logs/files', { headers: getAuthHeaders() });
    if (!resp.ok) return;
    const files = await resp.json();
    const sel = document.getElementById('log-file-select');
    sel.innerHTML = '<option value="">— 選擇日誌檔案 —</option>';
    files.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = `${f.name} (${formatBytes(f.size_bytes)})`;
      if (f.name === state.currentLogFile) opt.selected = true;
      sel.appendChild(opt);
    });
    // If selected file is no longer in allowed list for guest, fallback to first available
    if (files.length > 0 && !files.some(f => f.name === state.currentLogFile)) {
      state.currentLogFile = files[0].name;
      loadLogs();
    }
  } catch (e) {
    console.warn('Failed to load log files:', e);
  }
}

function onLogFileChange() {
  const sel = document.getElementById('log-file-select');
  if (sel.value) {
    state.currentLogFile = sel.value;
    state.currentPage = 1;
    loadLogs();
  }
}

function reloadLogs() {
  state.currentPage = 1;
  loadLogs();
}

async function loadLogs() {
  if (state.currentView !== 'logs') return;
  const sizeSel = document.getElementById('log-size-select');
  const size = parseInt(sizeSel.value || '50');

  try {
    const url = `/api/logs?file=${encodeURIComponent(state.currentLogFile)}&page=${state.currentPage}&size=${size}`;
    const resp = await fetch(url, { headers: getAuthHeaders() });
    if (resp.status === 401 || resp.status === 403) {
      const errData = await resp.json().catch(() => ({}));
      showToast(errData.message || '無法讀取此日誌檔，請確認權限', 'warning');
    }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);

    const data = await resp.json();
    state.totalPages = data.pages || 1;
    state.totalEntries = data.total || 0;
    setStatus(true, `Online · ${state.totalEntries} 筆`);

    renderLogs(data.entries || []);
    updatePagination();
  } catch (e) {
    setStatus(false, '連線錯誤');
    console.error('Failed to load logs:', e);
  }
}

function renderLogs(entries) {
  const container = document.getElementById('logs-container');
  container.innerHTML = '';

  const filtered = state.eventFilter === 'all'
    ? entries
    : entries.filter(e => (e.event || 'raw') === state.eventFilter);

  if (filtered.length === 0) {
    container.innerHTML = '<div class="placeholder-msg">沒有符合條件的日誌記錄。</div>';
    return;
  }

  filtered.forEach(log => {
    const el = document.createElement('div');
    const event = log.event || 'raw';
    el.className = 'log-entry';
    el.dataset.event = event;
    el.innerHTML = buildLogHTML(log, event);
    el.addEventListener('click', () => showLogDetail(log));
    container.appendChild(el);
  });
}

function buildLogHTML(log, event) {
  const ts = log.ts || log.timestamp || '—';
  const badgeClass = {
    chat_reply:        'badge-chat',
    react_agent_done:  'badge-chat',
    interject:         'badge-interject',
    error:             'badge-error',
  }[event] || 'badge-default';
  const badgeLabel = {
    chat_reply:        log.intent || 'CHAT',
    react_agent_done:  log.intent || 'REACT',
    interject:         'INTERJECT',
    error:             'ERROR',
  }[event] || event.toUpperCase();

  let body = '';
  if (event === 'chat_reply' || event === 'react_agent_done') {
    body += `<div class="log-user-text">👤 ${escHtml(log.raw_input || '')}</div>`;
    const latStr = log.latency_ms ? ` • ⚡ 耗時 ${log.latency_ms} ms` : '';
    const modelStr = log.model_used ? ` • 🤖 ${escHtml(log.model_used)}` : '';
    const toolsStr = (log.tools_invoked && log.tools_invoked.length) ? ` • 🛠️ [${log.tools_invoked.join(', ')}]` : '';
    if (log.intent) body += `<div class="log-intent">Intent: ${escHtml(log.intent)}${latStr}${modelStr}${toolsStr}</div>`;
    if (log.reply_preview) body += `<div class="log-reply-text">🤖 ${escHtml(log.reply_preview)}</div>`;
    if (log.cot_thought) {
      const formattedThought = escHtml(log.cot_thought).replace(/\n/g, '<br>');
      body += `<div class="log-thought-text">💭 ${formattedThought}</div>`;
    }
  } else if (event === 'interject') {
    const score = log.extra?.extra_score ?? log.score ?? '';
    const reason = log.extra?.extra_reasoning ?? log.reasoning ?? '';
    const reply = log.extra?.extra_reply ?? '';
    if (score !== '') body += `<div class="log-intent">Score: ${score}</div>`;
    if (reason) body += `<div class="log-thought-text">💭 ${escHtml(reason)}</div>`;
    if (reply) body += `<div class="log-reply-text">🤖 ${escHtml(reply)}</div>`;
  } else {
    // Generic: show message or first string field
    const msg = log.message || log.msg || log.text || log.error || '';
    if (msg) body += `<div class="log-user-text">${escHtml(msg)}</div>`;
    else body += `<div class="log-thought-text">${escHtml(JSON.stringify(log, null, 2).slice(0, 300))}</div>`;
  }

  return `
    <div class="log-header">
      <span class="log-time">${escHtml(ts)}</span>
      <span class="log-badge ${badgeClass}">${badgeLabel}</span>
    </div>
    ${body}
  `;
}

// ── Pagination ─────────────────────────────────────────
function updatePagination() {
  document.getElementById('page-info').textContent =
    `第 ${state.currentPage} / ${state.totalPages} 頁  (共 ${state.totalEntries} 筆)`;
  document.getElementById('prev-btn').disabled = state.currentPage <= 1;
  document.getElementById('next-btn').disabled = state.currentPage >= state.totalPages;
}

function changePage(delta) {
  const newPage = state.currentPage + delta;
  if (newPage < 1 || newPage > state.totalPages) return;
  state.currentPage = newPage;
  loadLogs();
}

// ── Event Filter ───────────────────────────────────────
function setEventFilter(event) {
  state.eventFilter = event;
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.event === event);
  });
  loadLogs();
}

// ── Auto Refresh ───────────────────────────────────────
function startAutoRefresh() {
  if (state.autoRefreshTimer) clearInterval(state.autoRefreshTimer);
  state.autoRefreshTimer = setInterval(() => {
    if (state.autoRefreshEnabled && state.currentView === 'logs') {
      loadLogs();
    }
  }, 5000);
}

function toggleAutoRefresh() {
  state.autoRefreshEnabled = document.getElementById('auto-refresh-chk').checked;
}

// ── Log Detail Modal ────────────────────────────────────
function showLogDetail(log) {
  document.getElementById('modal-body').textContent = JSON.stringify(log, null, 2);
  document.getElementById('modal-backdrop').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('modal-backdrop').classList.add('hidden');
}

// =========================================================
// SETTINGS
// =========================================================

async function loadSettings() {
  try {
    const resp = await fetch('/api/settings');
    if (resp.status === 401) return;
    state.settingsData = await resp.json();
    state.dirtyKeys.clear();
    state.dirtyValues = {};
    updateChangedHint();
    renderSettings();
  } catch (e) {
    console.error('Failed to load settings:', e);
  }
}

const TAB_CATEGORY_MAP = {
  'tab-ai': ['模型設定', 'LLM 參數', 'Chain-of-Thought'],
  'tab-persona': ['雪莉人設與心跳', '插話引擎', '打字模擬', '記憶與上下文'],
  'tab-voice': ['語音辨識與合成'],
  'tab-art': ['圖片生成', '二次元圖庫 API'],
  'tab-search': ['Web 爬蟲', '外部資訊檢索', 'YouTube 爬蟲'],
  'tab-advanced': ['任務佇列優先級', '音樂播放與隊列', '系統與網路', '深夜記憶整合', '健康監控', '氛圍系統', '速率限制', '後端連線設定', '其他']
};

function switchSettingsTab(tabId) {
  state.currentSettingsTab = tabId;
  document.querySelectorAll('.settings-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
  });
  renderSettings();
}

function renderSettings() {
  const wrapper = document.getElementById('settings-wrapper');
  wrapper.innerHTML = '';

  const allowedCategories = TAB_CATEGORY_MAP[state.currentSettingsTab || 'tab-ai'] || TAB_CATEGORY_MAP['tab-ai'];
  const allMappedCategories = new Set(Object.values(TAB_CATEGORY_MAP).flat());

  // Group by category
  const categories = {};
  for (const [key, meta] of Object.entries(state.settingsData)) {
    const cat = meta.category || '其他';
    let isAllowed = allowedCategories.includes(cat);
    // 未對應到現有頁籤的任何分類一律放進系統與進階
    if (!isAllowed && (state.currentSettingsTab === 'tab-advanced') && !allMappedCategories.has(cat)) {
      isAllowed = true;
    }
    if (!isAllowed) continue;

    if (!categories[cat]) categories[cat] = [];
    const currentValue = (state.dirtyValues && key in state.dirtyValues) ? state.dirtyValues[key] : meta.value;
    categories[cat].push({ key, ...meta, value: currentValue });
  }

  // Category display order preference
  const ORDER = [
    '模型設定', 'LLM 參數', 'Chain-of-Thought',
    '雪莉人設與心跳', '插話引擎', '打字模擬', '記憶與上下文',
    '語音辨識與合成',
    '圖片生成', '二次元圖庫 API',
    'Web 爬蟲', '外部資訊檢索', 'YouTube 爬蟲',
    '任務佇列優先級', '音樂播放與隊列', '系統與網路', '深夜記憶整合',
    '健康監控', '氛圍系統', '速率限制', '後端連線設定', '其他',
  ];
  const sortedCats = Object.keys(categories).sort((a, b) => {
    const ai = ORDER.indexOf(a); const bi = ORDER.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });

  sortedCats.forEach((cat, idx) => {
    const items = categories[cat];
    const section = document.createElement('div');
    // 頁籤切換時，各分組預設直接展開
    section.className = 'settings-category open';
    section.id = `cat-${idx}`;

    section.innerHTML = `
      <div class="category-header" onclick="toggleCategory('cat-${idx}')">
        <div class="category-title">
          ${getCatIcon(cat)} ${escHtml(cat)}
          <span class="category-count">${items.length}</span>
        </div>
        <span class="category-chevron">▼</span>
      </div>
      <div class="category-body" id="body-${idx}">
        ${items.map(item => buildSettingItem(item)).join('')}
      </div>
    `;
    wrapper.appendChild(section);
  });
}

function buildSettingItem({ key, value, label, description, type, min, max, step, options }) {
  const keyAttr = `data-key="${escAttr(key)}"`;
  const descHtml = description
    ? `<div class="setting-desc">${escHtml(description)}</div>`
    : '';

  let control = '';

  if (type === 'bool') {
    const checked = value ? 'checked' : '';
    control = `
      <div class="bool-row">
        <label class="toggle-switch">
          <input type="checkbox" id="inp-${escAttr(key)}" ${keyAttr} ${checked}
            onchange="markDirty('${escAttr(key)}', this.checked)">
          <span class="toggle-track"></span>
        </label>
        <span class="bool-value-label" id="bool-label-${escAttr(key)}">${value ? 'ON' : 'OFF'}</span>
      </div>`;
  } else if (type === 'select') {
    const opts = options || [];
    const optHtml = opts.map(opt => {
      const optVal = typeof opt === 'object' ? opt.value : opt;
      const optLabel = typeof opt === 'object' ? opt.label : opt;
      const selected = String(optVal).toUpperCase() === String(value).toUpperCase() ? 'selected' : '';
      return `<option value="${escAttr(optVal)}" ${selected}>${escHtml(optLabel)}</option>`;
    }).join('');
    control = `
      <select class="setting-input setting-select" id="inp-${escAttr(key)}" ${keyAttr}
        onchange="markDirty('${escAttr(key)}', this.value)">
        ${optHtml}
      </select>`;
  } else if (type === 'model') {
    control = buildModelPicker(key, value);
  } else if (type === 'float' || type === 'int') {
    const stepVal = step || (type === 'float' ? 0.1 : 1);
    const minVal = min !== undefined ? min : '';
    const maxVal = max !== undefined ? max : '';
    const hasRange = minVal !== '' && maxVal !== '';
    control = `
      <div class="setting-number-row">
        ${hasRange ? `<input type="range" class="setting-slider" id="slider-${escAttr(key)}"
          min="${minVal}" max="${maxVal}" step="${stepVal}" value="${value}"
          oninput="syncNumberFromSlider('${escAttr(key)}')">` : ''}
        <input type="number" class="setting-number-input" id="inp-${escAttr(key)}" ${keyAttr}
          value="${value}" step="${stepVal}"
          ${minVal !== '' ? `min="${minVal}"` : ''}
          ${maxVal !== '' ? `max="${maxVal}"` : ''}
          oninput="syncSliderFromNumber('${escAttr(key)}'); markDirty('${escAttr(key)}', parseFloat(this.value) || 0)">
      </div>`;
  } else {
    // str / url
    const valStr = String(value ?? '');
    const isMultiline = valStr.length > 50 || valStr.includes('\n') || key === 'CORE_PERSONA' || key.includes('PROMPT') || key.includes('PERSONA');
    if (isMultiline) {
      control = `
        <textarea class="setting-textarea" rows="5"
          id="inp-${escAttr(key)}" ${keyAttr}
          oninput="markDirty('${escAttr(key)}', this.value)">${escHtml(valStr)}</textarea>`;
    } else {
      control = `
        <input type="${type === 'url' ? 'url' : 'text'}" class="setting-input"
          id="inp-${escAttr(key)}" ${keyAttr} value="${escAttr(valStr)}"
          oninput="markDirty('${escAttr(key)}', this.value)">`;
    }
  }

  return `
    <div class="setting-item" id="si-${escAttr(key)}">
      <div class="setting-label-row">
        <label class="setting-label" for="inp-${escAttr(key)}">${escHtml(label)}</label>
        <span class="setting-key">${escHtml(key)}</span>
      </div>
      ${descHtml}
      ${control}
    </div>`;
}

function buildModelPicker(key, value) {
  const PREFIXES = ['freetoken', 'ollama', 'lms', 'vllm', 'openai', 'deepseek', 'gemini', 'openrouter', 'claude'];
  // Parse current value
  const colonIdx = (value || '').indexOf(':');
  const currentPrefix = colonIdx > -1 ? (value || '').slice(0, colonIdx) : 'freetoken';
  const currentModel  = colonIdx > -1 ? (value || '').slice(colonIdx + 1) : (value || '');

  const prefixOptions = PREFIXES.map(p =>
    `<option value="${p}" ${p === currentPrefix ? 'selected' : ''}>${p}:</option>`
  ).join('');

  const placeholderText = key === 'VISION_MODEL'
    ? '手動輸入模型名稱（留空：使用主力模型原生看圖）'
    : '手動輸入模型名稱';

  return `
    <div class="model-picker" id="picker-${escAttr(key)}" data-key="${escAttr(key)}">
      <select class="model-prefix-select" id="prefix-${escAttr(key)}"
        onchange="onPrefixChange('${escAttr(key)}')">
        ${prefixOptions}
      </select>
      <div class="model-name-wrap">
        <select class="model-name-select hidden" id="sel-${escAttr(key)}"
          onchange="onModelSelectChange('${escAttr(key)}')">
          <option value="${escAttr(currentModel)}">${escHtml(currentModel) || (key === 'VISION_MODEL' ? '（留空：使用主力模型原生看圖）' : '載入中...')}</option>
        </select>
        <input type="text" class="model-name-input" id="inp-${escAttr(key)}"
          placeholder="${escAttr(placeholderText)}" value="${escAttr(currentModel)}"
          oninput="markDirty('${escAttr(key)}', getModelValue('${escAttr(key)}'))">
      </div>
      <button class="model-fetch-btn" title="從伺服器取得模型列表"
        onclick="fetchModelsForPicker('${escAttr(key)}')">⟳</button>
    </div>`;
}

// ── Category Accordion ──────────────────────────────────
function toggleCategory(id) {
  document.getElementById(id)?.classList.toggle('open');
}

// ── Model Picker Handlers ───────────────────────────────
function onPrefixChange(key) {
  markDirty(key, getModelValue(key));
  fetchModelsForPicker(key);
}

function onModelSelectChange(key) {
  const sel = document.getElementById(`sel-${key}`);
  const inp = document.getElementById(`inp-${key}`);
  if (sel && sel.value === '__manual__') {
    sel.classList.add('hidden');
    if (inp) {
      inp.classList.remove('hidden');
      inp.focus();
    }
  } else if (inp && sel) {
    inp.value = sel.value;
  }
  markDirty(key, getModelValue(key));
}

function getModelValue(key) {
  const prefix = document.getElementById(`prefix-${key}`)?.value || '';
  const sel    = document.getElementById(`sel-${key}`);
  const inp    = document.getElementById(`inp-${key}`);

  let modelName = '';
  if (sel && !sel.classList.contains('hidden') && sel.value && sel.value !== '__manual__') {
    modelName = sel.value;
  } else {
    modelName = inp?.value || '';
  }
  modelName = modelName.trim();
  if (!modelName) {
    return '';
  }
  return `${prefix}:${modelName}`;
}

async function fetchModelsForPicker(key) {
  const prefix = document.getElementById(`prefix-${key}`)?.value || '';
  const sel    = document.getElementById(`sel-${key}`);
  const inp    = document.getElementById(`inp-${key}`);
  if (!sel) return;

  const isLocal = prefix === 'freetoken' || prefix === 'ollama' || prefix === 'lms' || prefix === 'vllm';
  if (!isLocal) {
    // Cloud providers: hide select, show text input only
    sel.classList.add('hidden');
    inp?.classList.remove('hidden');
    return;
  }

  sel.innerHTML = '<option value="">載入中...</option>';
  sel.classList.remove('hidden');

  try {
    let endpoint = '/api/models/freetoken';
    if (prefix === 'ollama') endpoint = '/api/models/ollama';
    else if (prefix === 'lms') endpoint = '/api/models/lms';
    else if (prefix === 'vllm') endpoint = '/api/models/vllm';
    const resp = await fetch(endpoint);
    const data = await resp.json();
    const models = data.models || [];

    if (models.length === 0) {
      sel.innerHTML = '<option value="">（未偵測到模型）</option>';
      sel.classList.add('hidden');
      inp?.classList.remove('hidden');
      showToast(`未偵測到 ${prefix} 模型，請確認服務是否運行`, 'info');
      return;
    }

    const currentVal = inp?.value || '';
    let optionsHtml = '';
    if (key === 'VISION_MODEL') {
      optionsHtml += `<option value="" ${currentVal === '' ? 'selected' : ''}>⚡ 留空（啟用主力模型端到端原生多模態）</option>`;
    }
    optionsHtml += models.map(m =>
      `<option value="${escAttr(m)}" ${m === currentVal ? 'selected' : ''}>${escHtml(m)}</option>`
    ).join('');
    // Also add manual option at end
    optionsHtml += `<option value="__manual__">✏️ 手動輸入...</option>`;
    sel.innerHTML = optionsHtml;

    // Sync input with selected
    if (sel.value !== undefined && sel.value !== '__manual__' && inp) {
      inp.value = sel.value;
    }

    showToast(`已載入 ${models.length} 個 ${prefix} 模型`, 'success');
  } catch (e) {
    sel.innerHTML = '<option value="">（連線失敗）</option>';
    sel.classList.add('hidden');
    inp?.classList.remove('hidden');
    showToast(`無法連線到 ${prefix} 服務`, 'error');
  }
}

// ── Sync Slider ↔ Number ────────────────────────────────
function syncNumberFromSlider(key) {
  const slider = document.getElementById(`slider-${key}`);
  const inp    = document.getElementById(`inp-${key}`);
  if (slider && inp) {
    inp.value = slider.value;
    markDirty(key, parseFloat(slider.value));
  }
}

function syncSliderFromNumber(key) {
  const slider = document.getElementById(`slider-${key}`);
  const inp    = document.getElementById(`inp-${key}`);
  if (slider && inp) slider.value = inp.value;
}

// ── Dirty Tracking ──────────────────────────────────────
function markDirty(key, newValue) {
  if (!state.dirtyValues) state.dirtyValues = {};
  state.dirtyValues[key] = newValue;

  const original = state.settingsData[key]?.value;
  const el = document.getElementById(`inp-${key}`);

  // Bool labels
  const meta = state.settingsData[key];
  if (meta?.type === 'bool') {
    const lbl = document.getElementById(`bool-label-${key}`);
    if (lbl) lbl.textContent = newValue ? 'ON' : 'OFF';
  }

  if (newValue !== original) {
    state.dirtyKeys.add(key);
    if (el) el.classList.add('dirty');
    const slider = document.getElementById(`slider-${key}`);
    if (slider) slider.classList.add('dirty');
  } else {
    state.dirtyKeys.delete(key);
    delete state.dirtyValues[key];
    if (el) el.classList.remove('dirty');
    const slider = document.getElementById(`slider-${key}`);
    if (slider) slider.classList.remove('dirty');
  }
  updateChangedHint();
}

function updateChangedHint() {
  const hint = document.getElementById('changed-hint');
  if (!hint) return;
  hint.classList.toggle('hidden', state.dirtyKeys.size === 0);
}

// ── Save ────────────────────────────────────────────────
async function saveSettings() {
  const payload = {};
  const allSettings = state.settingsData;
  if (!state.dirtyValues) state.dirtyValues = {};

  // First sync any currently visible DOM inputs into dirtyValues
  for (const [key, meta] of Object.entries(allSettings)) {
    const type = meta.type;
    let currentVal = undefined;

    if (type === 'bool') {
      const el = document.getElementById(`inp-${key}`);
      if (el) currentVal = el.checked;
    } else if (type === 'model') {
      const picker = document.getElementById(`picker-${key}`);
      if (picker) {
        currentVal = getModelValue(key);
      }
    } else if (type === 'float') {
      const el = document.getElementById(`inp-${key}`);
      if (el) currentVal = parseFloat(el.value) || 0;
    } else if (type === 'int') {
      const el = document.getElementById(`inp-${key}`);
      if (el) currentVal = parseInt(el.value) || 0;
    } else {
      const el = document.getElementById(`inp-${key}`);
      if (el) currentVal = el.value;
    }

    if (currentVal !== undefined) {
      state.dirtyValues[key] = currentVal;
    }

    // Use dirtyValues if set / edited, otherwise keep original value from settingsData
    if (key in state.dirtyValues) {
      payload[key] = state.dirtyValues[key];
    } else if (meta.value !== undefined) {
      payload[key] = meta.value;
    }
  }

  try {
    const resp = await fetch('/api/settings', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      showToast('設定已儲存！請點擊「🔄 重啟雪莉」套用變更', 'success');
      await loadSettings(); // reload to sync
    } else {
      const err = await resp.json().catch(() => ({}));
      const failedKeys = (err.failed_keys || []).join(', ');
      showToast('部分設定儲存失敗: ' + failedKeys, 'error');
    }
  } catch (e) {
    showToast('儲存時發生錯誤', 'error');
    console.error(e);
  }
}

// =========================================================
// RESTART BOT
// =========================================================
function requestRestart() {
  document.getElementById('restart-modal-backdrop').classList.remove('hidden');
}

function closeRestartModal() {
  document.getElementById('restart-modal-backdrop').classList.add('hidden');
}

async function executeRestart() {
  closeRestartModal();
  _showRestartOverlay('重啟指令已發送，正在等待雪莉重新上線...', false);
  try {
    await fetch('/api/restart', { method: 'POST', headers: getAuthHeaders() });
  } catch (e) {}
  _pollForRestart(35, '✅ 重啟成功！頁面即將重新載入...', '⚠️ 重啟時間較長，請手動重新整理頁面。');
}

// =========================================================
// UPDATE (GIT PULL) + RESTART
// =========================================================
function requestUpdate() {
  if (!confirm('確定要從 dev 分支拉取最新程式碼並重新啟動雪莉嗎？\n\n這將執行：git pull origin dev → 重啟雪莉\n過程約需 15~25 秒。')) return;
  _showRestartOverlay('正在執行 Git Pull 拉取最新代碼，請稍候...', true);
  fetch('/api/update', { method: 'POST', headers: getAuthHeaders() })
    .catch(() => {});
  _pollForRestart(50, '✅ 更新完成！頁面即將重新載入...', '⚠️ 更新時間較長，請手動重新整理頁面。');
}

// ── Shared Overlay & Polling ─────────────────────────────
function _showRestartOverlay(initialMsg, isUpdate) {
  const overlay = document.getElementById('restart-overlay');
  const title = overlay.querySelector('h2');
  const desc = document.getElementById('restart-overlay-desc');
  if (title) title.textContent = isUpdate ? '📥 雪莉正在更新並重新啟動中...' : '🚀 雪莉正在重新啟動中...';
  if (desc) desc.textContent = initialMsg;
  overlay.classList.remove('hidden');
}

function _pollForRestart(maxAttempts, successMsg, timeoutMsg) {
  const desc = document.getElementById('restart-overlay-desc');
  const overlay = document.getElementById('restart-overlay');
  let attempts = 0;
  const pollInterval = setInterval(async () => {
    attempts++;
    if (desc) desc.textContent = `等待雪莉重新上線... (${attempts}s)`;
    try {
      const check = await fetch('/api/logs/files', { cache: 'no-store' });
      if (check.ok && attempts >= 4) {
        clearInterval(pollInterval);
        if (desc) desc.textContent = successMsg;
        setTimeout(() => window.location.reload(), 1000);
      }
    } catch (_) {}
    if (attempts >= maxAttempts) {
      clearInterval(pollInterval);
      if (desc) desc.textContent = timeoutMsg;
      setTimeout(() => overlay.classList.add('hidden'), 3000);
    }
  }, 1000);
}

// =========================================================
// TOAST
// =========================================================
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'fade-out 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// =========================================================
// HELPERS
// =========================================================
function escHtml(str) {
  return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function escAttr(str) {
  return String(str ?? '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function sanitizeId(str) {
  return String(str).replace(/[^a-zA-Z0-9_-]/g, '_');
}
function formatBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(1) + ' MB';
}

// =========================================================
// SYSTEM METRICS & TASKS
// =========================================================
async function loadMetrics() {
  try {
    const resp = await fetch('/api/system/metrics');
    if (!resp.ok) return;
    const data = await resp.json();

    // 1. CPU
    const cpu = data.cpu || {};
    const cpuP = Math.round(cpu.percent || 0);
    document.getElementById('cpu-percent-val').textContent = cpuP + '%';
    document.getElementById('cpu-percent-badge').textContent = cpuP + '%';
    document.getElementById('cpu-bar').style.width = Math.min(100, cpuP) + '%';
    document.getElementById('cpu-cores-text').textContent = `核心數: ${cpu.count || '--'}`;
    document.getElementById('cpu-freq-text').textContent = `時脈: ${cpu.freq_mhz || '--'} MHz`;

    // 2. RAM
    const ram = data.ram || {};
    const ramP = Math.round(ram.percent || 0);
    document.getElementById('ram-usage-val').textContent = `${ram.used_gb || 0} GB / ${ram.total_gb || 0} GB`;
    document.getElementById('ram-percent-badge').textContent = ramP + '%';
    document.getElementById('ram-percent-text').textContent = `使用率: ${ramP}%`;
    document.getElementById('ram-bar').style.width = Math.min(100, ramP) + '%';

    // 3. Network
    const net = data.network || {};
    const sentRate = net.sent_rate_kbps > 1024 ? (net.sent_rate_kbps / 1024).toFixed(2) + ' MB/s' : (net.sent_rate_kbps || 0) + ' KB/s';
    const recvRate = net.recv_rate_kbps > 1024 ? (net.recv_rate_kbps / 1024).toFixed(2) + ' MB/s' : (net.recv_rate_kbps || 0) + ' KB/s';
    document.getElementById('net-sent-rate').textContent = sentRate;
    document.getElementById('net-recv-rate').textContent = recvRate;
    document.getElementById('net-total-sent').textContent = `總發送: ${net.total_sent_mb || 0} MB`;
    document.getElementById('net-total-recv').textContent = `總接收: ${net.total_recv_mb || 0} MB`;

    // 4. GPU
    const gpus = data.gpu || [];
    const gpuBadge = document.getElementById('gpu-status-badge');
    const gpuBody = document.getElementById('gpu-card-body');

    if (gpus.length === 0) {
      gpuBadge.textContent = '無獨顯 / 未偵測到';
      gpuBadge.className = 'metric-badge';
      gpuBody.innerHTML = '<div class="placeholder-msg" style="padding: 1rem 0;">系統以 CPU/NPU 運算，未偵測到獨立 NVIDIA 顯示卡。</div>';
    } else {
      gpuBadge.textContent = `${gpus.length} 顯卡上線`;
      gpuBadge.className = 'metric-badge badge-gpu';
      let html = '';
      gpus.forEach(g => {
        html += `
          <div class="gpu-item">
            <div class="gpu-name">🎮 ${escHtml(g.name)} (${g.temperature_c}°C)</div>
            <div class="progress-bar-bg" style="margin-bottom: 0.5rem;">
              <div class="progress-bar-fill bar-gpu" style="width: ${Math.min(100, g.utilization)}%"></div>
            </div>
            <div class="gpu-stats">
              <div class="gpu-stat-box">
                <div class="gpu-stat-lbl">GPU 核心算力使用率</div>
                <div class="gpu-stat-num">${g.utilization}%</div>
              </div>
              <div class="gpu-stat-box">
                <div class="gpu-stat-lbl">VRAM 顯存使用量</div>
                <div class="gpu-stat-num">${g.memory_used_mb} MB / ${g.memory_total_mb} MB (${g.memory_percent}%)</div>
              </div>
            </div>
          </div>
        `;
      });
      gpuBody.innerHTML = html;
    }

  } catch (e) {
    console.warn('Failed to fetch system metrics:', e);
  }
}

async function loadTasks() {
  const bContainer = document.getElementById('brain-queue-container');
  const sContainer = document.getElementById('tasks-container');

  // 1. Fetch Brain Inference Queue
  if (bContainer) {
    try {
      const bResp = await fetch('/api/brain/queue');
      if (bResp.ok) {
        const bData = await bResp.json();
        const bQueue = bData.queue || [];
        if (bQueue.length === 0) {
          bContainer.innerHTML = '<div class="placeholder-msg" style="padding: 1.5rem 1rem;">目前大腦空閒中，無佇列等待中的推理任務 ✨</div>';
        } else {
          let bHtml = '';
          bQueue.forEach(item => {
            bHtml += `
              <div class="task-card" style="border-left: 4px solid #00e5ff;">
                <div class="task-left">
                  <div class="task-badge-row">
                    <span class="task-type-badge badge-once" style="background: rgba(0, 229, 255, 0.15); color: #00e5ff; border-color: rgba(0, 229, 255, 0.3);">Priority: ${item.priority} (Seq: #${item.sequence_id})</span>
                    <span class="task-id">Task ID: ${escHtml(item.task_id)}</span>
                    <span style="font-size: 0.8rem; background: rgba(255,255,255,0.06); padding: 2px 8px; border-radius: 4px; color: var(--text-muted);">🤖 ${escHtml(item.model)}</span>
                  </div>
                  <div class="task-prompt" style="margin-top: 6px; font-family: monospace;">${escHtml(item.prompt_preview || '(無文字預覽)')}</div>
                </div>
              </div>
            `;
          });
          bContainer.innerHTML = bHtml;
        }
      }
    } catch (e) {
      bContainer.innerHTML = '<div class="placeholder-msg">載入大腦佇列時發生錯誤。</div>';
    }
  }

  // 2. Fetch Scheduled Reminders
  if (sContainer) {
    try {
      const resp = await fetch('/api/system/tasks');
      if (!resp.ok) return;
      const data = await resp.json();
      const tasks = data.tasks || [];

      if (tasks.length === 0) {
        sContainer.innerHTML = '<div class="placeholder-msg" style="padding: 1.5rem 1rem;">目前沒有正在監控中的排程或提醒事項 ✨</div>';
        return;
      }

      let html = '';
      tasks.forEach(t => {
        const isCron = !!t.cron_expression;
        const typeBadge = isCron
          ? `<span class="task-type-badge badge-cron">CRON 週期 (${t.cron_expression})</span>`
          : `<span class="task-type-badge badge-once">單次提醒</span>`;

        let timeText = '--';
        if (t.trigger_timestamp) {
          timeText = new Date(t.trigger_timestamp * 1000).toLocaleString('zh-TW');
        }

        html += `
          <div class="task-card">
            <div class="task-left">
              <div class="task-badge-row">
                ${typeBadge}
                <span class="task-id">ID: ${escHtml(t.task_id)}</span>
              </div>
              <div class="task-prompt">${escHtml(t.prompt)}</div>
              <div class="task-meta">
                預計觸發: ${timeText} | 頻道 ID: ${t.target_channel_id || '預設'}
              </div>
            </div>
            <button class="btn-delete-task" onclick="deleteTask('${escAttr(t.task_id)}')">✕ 取消任務</button>
          </div>
        `;
      });
      sContainer.innerHTML = html;
    } catch (e) {
      sContainer.innerHTML = '<div class="placeholder-msg">載入排程提醒時發生錯誤。</div>';
      console.error(e);
    }
  }
}

async function deleteTask(taskId) {
  if (!confirm(`確定要取消排程任務 [${taskId}] 嗎？`)) return;
  try {
    const resp = await fetch(`/api/system/tasks/${encodeURIComponent(taskId)}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    });
    if (resp.ok) {
      showToast(`已成功取消任務 ${taskId}`, 'success');
      loadTasks();
    } else {
      showToast('取消任務失敗！權限不足或 Session 已過期', 'error');
    }
  } catch (e) {
    showToast('刪除任務時發生錯誤', 'error');
    console.error(e);
  }
}

function getCatIcon(cat) {
  const icons = {
    '模型設定': '🤖', 'LLM 參數': '🧠', '後端連線設定': '🔌',
    '記憶與上下文': '💾', '氛圍系統': '🌊', '插話引擎': '💬',
    '速率限制': '⏱️', '打字模擬': '⌨️', 'Chain-of-Thought': '🔍',
    '圖片生成': '🎨', '健康監控': '❤️', 'Web 爬蟲': '🕷️',
    '深夜記憶整合': '🌙', '雪莉人設與心跳': '👧', '語音辨識與合成': '🎙️',
    '二次元圖庫 API': '🖼️', '外部資訊檢索': '🌐', 'YouTube 爬蟲': '📺',
    '任務佇列優先級': '🚦', '音樂播放與隊列': '🎵', '系統與網路': '🔧',
    '其他': '⚙️',
  };
  return icons[cat] || '📦';
}

// =========================================================
// AUTH & 10-MIN INACTIVITY TRACKER
// =========================================================
function getAuthHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  if (state.adminToken) {
    headers['Authorization'] = `Bearer ${state.adminToken}`;
  }
  return headers;
}

async function checkAdminAuth() {
  try {
    const resp = await fetch('/api/auth/check', {
      headers: getAuthHeaders(),
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (resp.ok && data.is_admin) {
      if (!state.adminToken && localStorage.getItem('sherry_admin_token')) {
        state.adminToken = localStorage.getItem('sherry_admin_token');
      }
      updateAuthUI(true);
    } else {
      state.adminToken = null;
      localStorage.removeItem('sherry_admin_token');
      updateAuthUI(false);
    }
  } catch (e) {
    updateAuthUI(false);
  }
}

function resetInactivityTimer() {
  state.lastActivityTime = Date.now();
}

function startInactivityTracker() {
  ['mousemove', 'click', 'keydown', 'touchstart', 'scroll'].forEach(evt => {
    window.addEventListener(evt, resetInactivityTimer, { passive: true });
  });

  if (state.inactivityCheckTimer) clearInterval(state.inactivityCheckTimer);

  state.inactivityCheckTimer = setInterval(() => {
    if (!state.adminToken) return;

    const inactiveSec = Math.floor((Date.now() - state.lastActivityTime) / 1000);
    const remainingSec = Math.max(0, 600 - inactiveSec);

    const timerEl = document.getElementById('admin-timer');
    if (timerEl) {
      const m = Math.floor(remainingSec / 60);
      const s = remainingSec % 60;
      timerEl.textContent = `(${m}m ${s < 10 ? '0' : ''}${s}s)`;
    }

    if (inactiveSec >= 600) {
      logoutAdmin(true); // 超過 10 分鐘無動作自動登出
    }
  }, 1000);
}

function openLoginModal() {
  const backdrop = document.getElementById('login-modal-backdrop');
  if (backdrop) backdrop.classList.remove('hidden');
  setTimeout(() => {
    const inp = document.getElementById('login-password-input');
    if (inp) inp.focus();
  }, 100);
}

function closeLoginModal() {
  const backdrop = document.getElementById('login-modal-backdrop');
  if (backdrop) backdrop.classList.add('hidden');
}

async function executeLogin() {
  const usernameInput = document.getElementById('login-username-input');
  const passwordInput = document.getElementById('login-password-input');
  const username = usernameInput ? usernameInput.value.trim() : 'admin';
  const password = passwordInput ? passwordInput.value : '';
  if (!password) {
    showToast('請輸入密碼', 'warning');
    return;
  }

  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ username, password }),
    });
    const data = await resp.json();

    if (resp.ok && data.token) {
      state.adminToken = data.token;
      localStorage.setItem('sherry_admin_token', data.token);
      state.lastActivityTime = Date.now();
      closeLoginModal();
      if (passwordInput) passwordInput.value = '';
      showToast(`🔐 管理員 ${escHtml(data.username || username)} 登入成功！已解鎖系統控制 (10分鐘無動作自動登出)`, 'success');
      updateAuthUI(true);
      await loadLogFiles();
      if (state.currentView === 'logs') loadLogs();
      if (state.currentView === 'settings') loadSettings();
      if (state.currentView === 'tasks') loadTasks();
      if (state.currentView === 'audit') loadAuditLogs();
    } else if (resp.status === 429) {
      showToast(`⛔ ${data.message || '登入失敗次數過多，該 IP 已暫時鎖定！'}`, 'error');
    } else {
      showToast(`❌ ${data.message || '密碼錯誤！'}`, 'error');
    }
  } catch (e) {
    showToast('登入失敗，請檢查伺服器連線', 'error');
    console.error(e);
  }
}

async function logoutAdmin(auto = false) {
  if (state.adminToken || document.cookie.includes('admin_session')) {
    try {
      fetch('/api/auth/logout', {
        method: 'POST',
        headers: getAuthHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({ token: state.adminToken }),
      });
    } catch (e) {}
  }

  state.adminToken = null;
  localStorage.removeItem('sherry_admin_token');
  updateAuthUI(false);
  await loadLogFiles();

  if (auto) {
    showToast('⏳ 超過 10 分鐘無動作，已自動切回訪客唯讀模式', 'info');
  } else {
    showToast('🚪 已成功登出管理員模式', 'info');
  }

  if (state.currentView === 'logs') loadLogs();
  if (state.currentView === 'settings') loadSettings();
  if (state.currentView === 'tasks') loadTasks();
}

function updateAuthUI(isAdmin) {
  state.isAdmin = isAdmin;
  const loginBtn = document.getElementById('login-nav-btn');
  const adminPill = document.getElementById('admin-pill');
  const logoutBtn = document.getElementById('logout-nav-btn');
  const adminOnlyEls = document.querySelectorAll('.admin-only');
  const guestNotice = document.getElementById('chat-guest-notice');

  if (isAdmin) {
    if (loginBtn) loginBtn.classList.add('hidden');
    if (adminPill) adminPill.classList.remove('hidden');
    if (logoutBtn) logoutBtn.classList.remove('hidden');
    adminOnlyEls.forEach(el => el.classList.remove('hidden'));
    if (guestNotice) guestNotice.style.display = 'none';
  } else {
    if (loginBtn) loginBtn.classList.remove('hidden');
    if (adminPill) adminPill.classList.add('hidden');
    if (logoutBtn) logoutBtn.classList.add('hidden');
    adminOnlyEls.forEach(el => el.classList.add('hidden'));
    if (guestNotice) guestNotice.style.display = 'block';
  }
}

// =========================================================
// CHAT TEST ROOM (跟雪莉聊天 穩定性測試)
// =========================================================

async function sendTestChat() {
  if (!state.isAdmin && !state.adminToken) {
    showToast('為維持系統穩定與資安，測試與聊天功能僅限管理員使用，請先登入！', 'warning');
    return;
  }
  const inputEl = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send-btn');
  if (!inputEl) return;

  const msgText = inputEl.value.trim();
  if (!msgText) return;

  inputEl.value = '';
  appendChatMessage('測試員 (你)', '👤', msgText, 'user-msg');

  inputEl.disabled = true;
  if (sendBtn) {
    sendBtn.disabled = true;
    sendBtn.innerHTML = '<span>⏳</span> 生成中...';
  }

  const typingId = 'typing-' + Date.now();
  appendChatMessage('橘雪莉 Sherry', '🍊', '正在透過 BotBrain 推理回覆... ✨', 'bot-msg typing-bubble', typingId);

  try {
    const resp = await fetch('/api/chat/test', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({ message: msgText })
    });
    const data = await resp.json();

    const typingEl = document.getElementById(typingId);
    if (typingEl) typingEl.remove();

    if (resp.status === 401 || resp.status === 403) {
      showToast(data.message || '請先登入管理員', 'error');
      appendChatMessage('系統警告 ⚠️', '🤖', data.message || '無法執行對話測試：未授權或未登入。', 'bot-msg error-msg');
    } else if (!resp.ok || data.status === 'error') {
      showToast(data.message || '回覆生成錯誤', 'error');
      appendChatMessage('橘雪莉 Sherry 🍊 (錯誤訊息)', '❌', `發生錯誤：${data.message || '未知錯誤'}`, 'bot-msg error-msg');
    } else {
      const latInfo = data.latency_ms ? ` • ⚡ 耗時 ${data.latency_ms} ms` : '';
      appendChatMessage('橘雪莉 Sherry', '🍊', data.reply, 'bot-msg', null, `由 ${data.model || 'BotBrain'} 回覆 • 觸發 ${data.intent || 'CHAT'}${latInfo}`);
    }
  } catch (e) {
    const typingEl = document.getElementById(typingId);
    if (typingEl) typingEl.remove();
    showToast('網路請求異常：' + e.message, 'error');
    appendChatMessage('系統通知 ⚠️', '🤖', '網路連線或請求發生錯誤，請檢查主伺服器狀態。', 'bot-msg error-msg');
  } finally {
    inputEl.disabled = false;
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.innerHTML = '<span>🚀</span> 發送';
    }
    inputEl.focus();
  }
}

function renderChatMessageContent(text) {
  if (!text) return '';

  const hasThought = /<thought>[\s\S]*?<\/thought>/i.test(text);
  const hasAction = /<action>[\s\S]*?<\/action>/i.test(text);
  const hasFinal = /<final_answer>[\s\S]*?<\/final_answer>/i.test(text);
  const hasObs = /<observation>[\s\S]*?<\/observation>/i.test(text);

  if (!hasThought && !hasAction && !hasFinal && !hasObs) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;")
      .replace(/\n/g, "<br>");
  }

  let stepsHtml = '';
  let speechText = text;

  // 1. 萃取 Thought
  const thoughtMatches = [...text.matchAll(/<thought>([\s\S]*?)<\/thought>/gi)];
  thoughtMatches.forEach((m, idx) => {
    const content = m[1].trim()
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
    stepsHtml += `<div class="react-thought-step"><span class="react-step-badge react-badge-thought">🧠 思考 #${idx+1}</span> ${content}</div>`;
  });

  // 2. 萃取 Action & Action Input
  const actionMatches = [...text.matchAll(/<action>([\s\S]*?)<\/action>(?:\s*<action_input>([\s\S]*?)<\/action_input>)?/gi)];
  actionMatches.forEach((m) => {
    const act = m[1].trim();
    const actIn = (m[2] || '').trim();
    stepsHtml += `<div class="react-thought-step"><span class="react-step-badge react-badge-action">⚡ 發動工具 [${act}]</span> 指令: <code>${actIn || '無參數'}</code></div>`;
  });

  // 3. 萃取 Observation
  const obsMatches = [...text.matchAll(/<observation>([\s\S]*?)<\/observation>/gi)];
  obsMatches.forEach((m) => {
    const obsContent = m[1].trim()
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
    stepsHtml += `<div class="react-thought-step"><span class="react-step-badge react-badge-obs">👁️ 觀察結果</span> ${obsContent.slice(0, 300)}</div>`;
  });

  // 4. 萃取 Final Answer
  const finalMatch = text.match(/<final_answer>([\s\S]*?)<\/final_answer>/i);
  if (finalMatch) {
    speechText = finalMatch[1].trim();
  } else {
    // 剝離所有 XML 標籤取得乾淨回覆文字
    speechText = text
      .replace(/<thought>[\s\S]*?<\/thought>/gi, '')
      .replace(/<action>[\s\S]*?<\/action>/gi, '')
      .replace(/<action_input>[\s\S]*?<\/action_input>/gi, '')
      .replace(/<observation>[\s\S]*?<\/observation>/gi, '')
      .replace(/<\/?final_answer>/gi, '')
      .trim();
  }

  const cleanSpeechEscaped = speechText
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;")
    .replace(/\n/g, "<br>");

  let outputHtml = '';
  if (stepsHtml) {
    outputHtml += `
      <details class="react-thought-block" open>
        <summary class="react-thought-summary">🧠 ReAct 推理與工具調度歷程</summary>
        <div class="react-thought-content">${stepsHtml}</div>
      </details>
    `;
  }

  if (cleanSpeechEscaped) {
    outputHtml += `<div class="react-final-speech">${cleanSpeechEscaped}</div>`;
  } else if (!stepsHtml) {
    outputHtml += `<div class="react-final-speech">（無文字回覆）</div>`;
  }

  return outputHtml;
}

function appendChatMessage(senderName, avatar, text, extraClass = '', elementId = null, subTag = '') {
  const container = document.getElementById('chat-messages');
  if (!container) return;

  const msgDiv = document.createElement('div');
  msgDiv.className = 'chat-msg ' + extraClass;
  if (elementId) msgDiv.id = elementId;

  const tagHtml = subTag ? `<span class="chat-tag">${subTag}</span>` : '';
  const renderedContent = renderChatMessageContent(text);

  msgDiv.innerHTML = `
    <div class="chat-avatar">${avatar}</div>
    <div class="chat-bubble">
      <div class="chat-sender">${senderName} ${tagHtml}</div>
      <div class="chat-text">${renderedContent}</div>
    </div>
  `;

  container.appendChild(msgDiv);
  container.scrollTop = container.scrollHeight;
}

function clearChatHistory() {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  container.innerHTML = `
    <div class="chat-msg bot-msg">
      <div class="chat-avatar">🍊</div>
      <div class="chat-bubble">
        <div class="chat-sender">橘雪莉 Sherry <span class="chat-tag">System Ready</span></div>
        <div class="chat-text">紀錄已清空！隨時輸入訊息重新開始測試對話喔～✨</div>
      </div>
    </div>
  `;
}

// =========================================================
// SERVICES MONITOR (地端 AI 服務即時監測)
// =========================================================
async function loadServiceStatus() {
  const grid = document.getElementById('services-grid');
  if (!grid) return;

  try {
    const resp = await fetch('/api/services/status');
    const data = await resp.json();
    const services = data.services || [];

    const timeEl = document.getElementById('services-checked-time');
    if (timeEl && data.checked_at) timeEl.textContent = `最後檢測 ${data.checked_at} (5 秒自動刷新)`;

    if (services.length === 0) {
      grid.innerHTML = '<div class="placeholder-msg">未偵測到任何地端服務設定</div>';
      return;
    }

    grid.innerHTML = services.map(s => {
      const isOnline = s.status === 'online';
      const badgeClass = isOnline ? 'badge-online' : 'badge-offline';
      const pingClass = s.ping_ms < 60 ? 'ping-fast' : (s.ping_ms < 200 ? 'ping-mid' : 'ping-slow');
      const pingLabel = isOnline ? `<span class="ping-badge ${pingClass}">⚡ ${s.ping_ms} ms</span>` : '';
      const modelsHtml = (s.models || []).map(m => `<span class="service-model-tag">${escHtml(m)}</span>`).join('');

      return `
        <div class="service-card ${isOnline ? 'service-online' : 'service-offline'}">
          <div class="service-card-header">
            <div class="service-title-row">
              <span class="service-icon">${s.icon || '📡'}</span>
              <div>
                <div class="service-name">${escHtml(s.name)}</div>
                <div class="service-url">${escHtml(s.url)}</div>
              </div>
            </div>
            <div class="service-badges">
              ${pingLabel}
              <span class="status-badge ${badgeClass}">${isOnline ? 'ONLINE 在線' : 'OFFLINE 離線'}</span>
            </div>
          </div>
          <div class="service-card-body">
            <div class="service-detail-text">${escHtml(s.details)}</div>
            ${modelsHtml ? `<div class="service-models-title">載入模型清單：</div><div class="service-models-row">${modelsHtml}</div>` : ''}
          </div>
        </div>`;
    }).join('');
  } catch (e) {
    grid.innerHTML = '<div class="placeholder-msg">無法連線至服務檢測 API</div>';
  }
}

// =========================================================
// ADMIN AUDIT LOGS (管理員審計紀錄)
// =========================================================
async function loadAuditLogs() {
  const container = document.getElementById('audit-container');
  if (!container) return;

  try {
    const resp = await fetch('/api/audit/logs', { headers: getAuthHeaders(), credentials: 'same-origin' });
    if (!resp.ok) {
      container.innerHTML = '<div class="placeholder-msg">🔒 審計紀錄僅限登入管理員查看</div>';
      return;
    }
    const data = await resp.json();
    const history = data.history || [];

    if (history.length === 0) {
      container.innerHTML = '<div class="placeholder-msg">尚無管理員登入或操作歷史紀錄</div>';
      return;
    }

    container.innerHTML = `
      <table class="audit-table">
        <thead>
          <tr>
            <th>管理員帳號</th>
            <th>登入時間</th>
            <th>狀態 / 登出時間</th>
            <th>來源 IP</th>
            <th>網路位置標籤</th>
            <th>裝置與瀏覽器</th>
            <th>操作履歷歷程</th>
          </tr>
        </thead>
        <tbody>
          ${history.map(rec => {
            const isLoggedOut = rec.status === 'logged_out' || rec.logout_time;
            const statusBadge = isLoggedOut
              ? `<span class="badge-logout">已登出 (${escHtml(rec.logout_time)})</span>`
              : `<span class="badge-active">● 線上中 (Active)</span>`;
            const actionsCount = (rec.actions || []).length;
            const actionsJson = escAttr(JSON.stringify(rec.actions || []));

            return `
              <tr>
                <td><span class="user-badge">👤 ${escHtml(rec.username || 'admin')}</span></td>
                <td><span class="audit-time">${escHtml(rec.login_time)}</span></td>
                <td>${statusBadge}</td>
                <td><code class="ip-code">${escHtml(rec.ip)}</code></td>
                <td><span class="location-tag">${escHtml(rec.location)}</span></td>
                <td><span class="device-text">${escHtml(rec.device)}</span></td>
                <td>
                  ${actionsCount > 0
                    ? `<button class="btn-audit-detail" onclick="showAuditDetailModal(${actionsJson})">📋 查看履歷 (${actionsCount}條)</button>`
                    : `<span style="color: var(--text-muted); font-size: 0.85rem;">尚無動作紀錄</span>`}
                </td>
              </tr>`;
          }).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    container.innerHTML = '<div class="placeholder-msg">無法連線至審計紀錄 API</div>';
  }
}

function showAuditDetailModal(actions) {
  const backdrop = document.getElementById('audit-detail-modal-backdrop');
  const body = document.getElementById('audit-detail-modal-body');
  if (!backdrop || !body) return;

  if (!actions || actions.length === 0) {
    body.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-muted);">本 Session 無操作項目記錄</div>';
  } else {
    body.innerHTML = `
      <div class="audit-timeline">
        ${actions.map(act => `
          <div class="timeline-item">
            <div class="timeline-dot"></div>
            <div class="timeline-content">
              <div class="timeline-time">${escHtml(act.timestamp)}</div>
              <div class="timeline-action">${escHtml(act.action)}</div>
              <div class="timeline-detail">${escHtml(act.detail)}</div>
            </div>
          </div>
        `).join('')}
      </div>`;
  }
  backdrop.classList.remove('hidden');
}

function closeAuditDetailModal() {
  const backdrop = document.getElementById('audit-detail-modal-backdrop');
  if (backdrop) backdrop.classList.add('hidden');
}

// ── Bot Voice & Music Status Monitoring ────────────────
async function loadBotStatus() {
  const container = document.getElementById('bot-status-container');
  if (!container) return;
  try {
    const resp = await fetch('/api/bot/status');
    if (!resp.ok) return;
    const data = await resp.json();

    if (!data.ready) {
      container.innerHTML = `
        <div class="placeholder-msg" style="padding: 2.5rem 1rem;">
          🤖 雪莉目前尚未就緒或連線中...<br>
          <span style="font-size: 0.85rem; color: var(--text-muted); margin-top: 8px; display: inline-block;">請確認 Discord Bot Token 正確且主服務已啟動。</span>
        </div>
      `;
      return;
    }

    let html = `
      <div style="display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap;">
        <div class="metric-card" style="flex: 1; min-width: 200px;">
          <div class="metric-title">🤖 Bot 狀態</div>
          <div style="font-size: 1.15rem; font-weight: 700; color: #00e5ff; margin-top: 4px; word-break: break-all;">${escHtml(data.bot_name)}</div>
          <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 4px;">已連線 ${data.guild_count} 個 Discord 伺服器</div>
        </div>
        <div class="metric-card" style="flex: 1; min-width: 200px;">
          <div class="metric-title">⚡ Discord API 延遲</div>
          <div style="font-size: 1.2rem; font-weight: 700; color: #00ffaa; margin-top: 4px;">${data.ping_ms} ms</div>
          <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 4px;">WebSocket 心跳延遲</div>
        </div>
        <div class="metric-card" style="flex: 1; min-width: 200px;">
          <div class="metric-title">🎙️ 語音頻道連線數</div>
          <div style="font-size: 1.2rem; font-weight: 700; color: #ff007f; margin-top: 4px;">${data.voice_connections.length} 個語音房</div>
          <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 4px;">即時監控語音通話與留聲機</div>
        </div>
      </div>
    `;

    if (data.voice_connections.length === 0) {
      html += `
        <div class="placeholder-msg" style="padding: 3rem 1rem;">
          🔊 雪莉目前沒有加入任何 Discord 語音頻道。<br>
          <span style="font-size: 0.85rem; color: var(--text-muted); margin-top: 8px; display: inline-block;">在 Discord 頻道輸入 <code>/join</code> 或 <code>@雪莉 播音樂</code>，雪莉就會進房為大家高歌喔！🎵</span>
        </div>
      `;
    } else {
      data.voice_connections.forEach(vc => {
        const isPlaying = vc.is_playing;
        const isPaused = vc.is_paused;
        const statusBadge = isPlaying
          ? `<span class="service-badge service-online">▶️ 播放中</span>`
          : isPaused
          ? `<span class="service-badge service-offline" style="background: rgba(255, 170, 0, 0.15); color: #ffaa00; border-color: rgba(255, 170, 0, 0.3);">⏸️ 暫停中</span>`
          : `<span class="service-badge service-offline">⏹️ 空閒中</span>`;

        let membersHtml = '';
        if (vc.members && vc.members.length > 0) {
          membersHtml = vc.members.map(m => `
            <span style="display: inline-flex; align-items: center; gap: 6px; background: rgba(255,255,255,0.06); border: 1px solid var(--border-color); padding: 4px 10px; border-radius: 20px; font-size: 0.82rem;">
              👤 ${escHtml(m.name)}
            </span>
          `).join(' ');
        } else {
          membersHtml = '<span style="color: var(--text-muted); font-size: 0.85rem;">目前房間內無其他成員</span>';
        }

        let songHtml = '';
        if (vc.current_song) {
          const song = vc.current_song;
          songHtml = `
            <div style="background: rgba(0, 229, 255, 0.05); border: 1px solid rgba(0, 229, 255, 0.2); border-radius: 8px; padding: 12px; margin-top: 12px;">
              <div style="font-size: 0.85rem; color: #00e5ff; font-weight: 600;">🎵 當前播放曲目：</div>
              <div style="font-weight: 700; font-size: 1rem; margin-top: 4px;">
                ${song.url ? `<a href="${escAttr(song.url)}" target="_blank" style="color: var(--text-main); text-decoration: underline;">${escHtml(song.title)}</a>` : escHtml(song.title)}
              </div>
              <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">
                點歌者: ${escHtml(song.requester)} ${song.duration ? `• 總長: ${Math.floor(song.duration/60)}分${song.duration%60}秒` : ''}
              </div>
            </div>
          `;
        }

        let queueHtml = '';
        if (vc.queue && vc.queue.length > 0) {
          queueHtml = `
            <div style="margin-top: 10px; font-size: 0.82rem; color: var(--text-muted);">
              📋 歌單隊列 (剩餘 ${vc.queue_count} 首)：
              <ol style="margin-top: 4px; padding-left: 20px;">
                ${vc.queue.map(q => `<li>${escHtml(q.title)} <span style="opacity:0.7;">(點歌: ${escHtml(q.requester)})</span></li>`).join('')}
              </ol>
            </div>
          `;
        }

        html += `
          <div class="service-card" style="margin-bottom: 16px;">
            <div class="service-header">
              <div class="service-title-row">
                <span style="font-size: 1.3rem;">🏰</span>
                <div>
                  <div style="font-weight: 700; font-size: 1.05rem;">${escHtml(vc.guild_name)}</div>
                  <div style="font-size: 0.82rem; color: var(--text-muted);">語音房: <strong style="color: #00e5ff;">#${escHtml(vc.channel_name)}</strong> (${vc.member_count} 人)</div>
                </div>
              </div>
              ${statusBadge}
            </div>
            <div style="margin-top: 10px;">
              <div style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: 6px;">🎙️ 在線通話成員：</div>
              <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                ${membersHtml}
              </div>
            </div>
            ${songHtml}
            ${queueHtml}
          </div>
        `;
      });
    }

    container.innerHTML = html;
  } catch (e) {
    console.error('Failed to load bot status:', e);
    container.innerHTML = '<div class="placeholder-msg">載入語音與音樂動態時發生錯誤。</div>';
  }
}

// ── Dashboard Overview & Git Version Live Monitor ────────
async function loadDashboard() {
  const gitContainer = document.getElementById('dashboard-git-container');
  const coreGrid = document.getElementById('dashboard-core-grid');
  if (!gitContainer || !coreGrid) return;

  try {
    const resp = await fetch('/api/system/dashboard');
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.status !== 'success') return;

    const versionTag = document.getElementById('dashboard-version-tag');
    if (versionTag) versionTag.textContent = data.version || 'v1.4.1';

    const git = data.git || {};
    const running = git.running || {};
    const disk = git.disk || {};
    const needsRestart = git.needs_restart || false;

    let syncBadge = needsRestart
      ? `<span class="service-badge service-offline" style="background: rgba(255, 170, 0, 0.15); color: #ffaa00; border-color: rgba(255, 170, 0, 0.3);">⚠️ 檢測到新版本已 Pull，請重啟套用</span>`
      : `<span class="service-badge service-online">🟢 執行中版本為最新狀態</span>`;

    let gitAlert = '';
    if (needsRestart) {
      gitAlert = `
        <div style="background: rgba(255, 170, 0, 0.1); border: 1px solid rgba(255, 170, 0, 0.3); border-radius: 8px; padding: 12px 16px; margin-top: 12px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
          <div style="font-size: 0.88rem; color: #ffaa00;">
            <strong>⚠️ 版本升級提醒：</strong> 本地磁碟代碼已更新至 <code>${escHtml(disk.commit_hash)}</code> (${escHtml(disk.commit_msg)})，但雪莉目前仍於記憶體執行舊版 <code>${escHtml(running.commit_hash)}</code>。
          </div>
          <button class="btn-restart admin-only" onclick="requestRestart()" style="padding: 6px 14px; font-size: 0.85rem;">🚀 立即重啟雪莉</button>
        </div>
      `;
    }

    gitContainer.innerHTML = `
      <div class="service-card" style="border-color: rgba(0, 229, 255, 0.25); background: rgba(0, 229, 255, 0.02);">
        <div class="service-header">
          <div class="service-title-row">
            <span style="font-size: 1.4rem;">🌿</span>
            <div>
              <div style="font-weight: 700; font-size: 1.1rem; color: #00e5ff;">Git 代碼庫與實時運作版本</div>
              <div style="font-size: 0.82rem; color: var(--text-muted);">分支: <strong style="color:var(--text-main);">${escHtml(disk.branch || 'dev')}</strong> | 標籤: <strong>${escHtml(data.version || 'v1.4.1')}</strong></div>
            </div>
          </div>
          ${syncBadge}
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-top: 14px; font-size: 0.85rem;">
          <div style="background: rgba(0,0,0,0.25); padding: 10px 14px; border-radius: 6px; border: 1px solid var(--border-color);">
            <div style="color: var(--text-muted); font-size: 0.78rem;">🤖 實時運作中 Commit (Bot Process):</div>
            <div style="font-weight: 700; font-family: monospace; font-size: 0.95rem; color: #00ffaa; margin-top: 2px;">
              ${escHtml(running.commit_hash || 'unknown')}
            </div>
            <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escAttr(running.commit_msg)}">
              ${escHtml(running.commit_msg || '啟動時 Commit 快照')}
            </div>
          </div>
          <div style="background: rgba(0,0,0,0.25); padding: 10px 14px; border-radius: 6px; border: 1px solid var(--border-color);">
            <div style="color: var(--text-muted); font-size: 0.78rem;">📦 本地磁碟 Commit (Latest Code):</div>
            <div style="font-weight: 700; font-family: monospace; font-size: 0.95rem; color: #00e5ff; margin-top: 2px;">
              ${escHtml(disk.commit_hash || 'unknown')}
            </div>
            <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escAttr(disk.commit_msg)}">
              ${escHtml(disk.commit_msg || '無提交紀錄')}
            </div>
          </div>
          <div style="background: rgba(0,0,0,0.25); padding: 10px 14px; border-radius: 6px; border: 1px solid var(--border-color);">
            <div style="color: var(--text-muted); font-size: 0.78rem;">👤 最新提交作者與時間:</div>
            <div style="font-weight: 600; margin-top: 2px;">${escHtml(disk.commit_author || '未知')}</div>
            <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 4px;">${escHtml(disk.commit_date || '--')}</div>
          </div>
        </div>
        ${gitAlert}
      </div>
    `;

    const bot = data.bot || {};
    const sys = data.system || {};
    const vram = sys.vram || {};
    const queues = data.queues || {};

    coreGrid.innerHTML = `
      <div class="metric-card">
        <div class="metric-title">🤖 Bot 狀態與連線</div>
        <div style="font-size: 1.2rem; font-weight: 700; color: ${bot.ready ? '#00ffaa' : '#ff4444'}; margin-top: 4px;">
          ${bot.ready ? 'ONLINE (已上線)' : 'DISCONNECTED'}
        </div>
        <div style="font-size: 0.82rem; color: var(--text-muted); margin-top: 6px;">
          Delay: <strong>${bot.ping_ms} ms</strong> | 伺服器: <strong>${bot.guild_count}</strong> | 語音房: <strong>${bot.voice_count}</strong>
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-title">💻 伺服器 CPU & RAM</div>
        <div style="font-size: 1.2rem; font-weight: 700; color: #00e5ff; margin-top: 4px;">
          CPU ${sys.cpu_percent}% | RAM ${sys.mem_used_gb} GB
        </div>
        <div style="font-size: 0.82rem; color: var(--text-muted); margin-top: 6px;">
          系統總記憶體: ${sys.mem_total_gb} GB (${sys.mem_percent}%)
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-title">🎮 GPU 溫度 & 顯存</div>
        <div style="font-size: 1.2rem; font-weight: 700; color: #ff007f; margin-top: 4px;">
          ${vram.gpu_temp ? vram.gpu_temp + '°C' : 'N/A'} | ${vram.used_gb} / ${vram.total_gb} GB
        </div>
        <div style="font-size: 0.82rem; color: var(--text-muted); margin-top: 6px;">
          顯示卡: ${escHtml(vram.gpu_name || 'GPU')} (${vram.percent}%)
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-title">🧠 大腦與排程佇列</div>
        <div style="font-size: 1.2rem; font-weight: 700; color: #ffaa00; margin-top: 4px;">
          ${queues.brain_llm} 個排隊推理
        </div>
        <div style="font-size: 0.82rem; color: var(--text-muted); margin-top: 6px;">
          背景監控排程提醒數: <strong>${queues.schedule}</strong> 個
        </div>
      </div>
    `;

  } catch (e) {
    console.error('Failed to load dashboard data:', e);
    if (gitContainer) gitContainer.innerHTML = '<div class="placeholder-msg">載入 Dashboard 時發生錯誤。</div>';
  }
}






// =========================================================
// MOD MANAGER
// =========================================================

/**
 * Load and render the Mod Manager view.
 * Fetches /api/modules and builds the module card grid.
 */
async function loadModManager() {
  const grid = document.getElementById('mod-grid');
  if (!grid) return;
  grid.innerHTML = `<div class="mod-loading"><div class="mod-loading-spinner"></div><span>正在掃描模組清單...</span></div>`;
  try {
    const res = await fetch('/api/modules', { headers: getAuthHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderModManager(data.modules || {});
  } catch (e) {
    grid.innerHTML = `<div class="mod-empty">❌ 載入模組清單失敗: ${e}</div>`;
  }
}

/**
 * Render module cards from the modules dict.
 * @param {Object} modules - keyed by mod_key, value has enabled/display_name/description/category
 */
function renderModManager(modules) {
  const grid = document.getElementById('mod-grid');
  if (!grid) return;

  const keys = Object.keys(modules);
  const statsContainer = document.getElementById('mod-manager-stats');

  if (keys.length === 0) {
    grid.innerHTML = '<div class="mod-empty">尚未發現任何可用模組。</div>';
    if (statsContainer) statsContainer.innerHTML = '';
    return;
  }

  const totalCount = keys.length;
  const enabledCount = keys.filter(k => modules[k].enabled !== false).length;
  if (statsContainer) {
    statsContainer.innerHTML = `
      <span class="mod-stats-chip mod-stats-chip-highlight">⚡ 啟用中：${enabledCount} / ${totalCount} 個模組</span>
    `;
  }

  grid.innerHTML = '';
  for (const key of keys) {
    const mod = modules[key];
    const enabled = mod.enabled !== false;
    const card = document.createElement('div');
    card.className = `mod-card ${enabled ? 'mod-card-enabled' : 'mod-card-disabled'}`;
    card.id = `mod-card-${key}`;

    const badgeClass = enabled ? 'mod-badge mod-badge-enabled' : 'mod-badge mod-badge-disabled';
    const badgeText = enabled ? '🟢 啟用中' : '🔴 已停用';

    const gamesCount = (mod.info && Array.isArray(mod.info.games) && mod.info.games.length > 0)
      ? `<div class="mod-meta-badge">🎮 內包 ${mod.info.games.length} 款小遊戲</div>` : '';

    card.innerHTML = `
      <div>
        <div class="mod-card-header">
          <div class="mod-card-name">${mod.display_name || key}</div>
          <span class="${badgeClass}">${badgeText}</span>
        </div>
        <div class="mod-card-desc">${mod.description || '未登記的自訂模組。'}</div>
        ${gamesCount ? `<div style="margin-bottom: 0.6rem;">${gamesCount}</div>` : ''}
      </div>
      <div>
        <div class="mod-card-meta">
          <span class="mod-category-tag">${mod.category || '自訂'}</span>
          <span class="mod-key-tag"><code>${key}</code></span>
        </div>
        <div class="mod-card-actions">
          <button
            class="mod-toggle-btn ${enabled ? 'mod-toggle-disable' : 'mod-toggle-enable'}"
            onclick="toggleModuleStatus('${key}', ${!enabled})"
            id="toggle-btn-${key}"
          >
            ${enabled ? '🔴 停用此模組' : '🟢 啟用此模組'}
          </button>
        </div>
      </div>
    `;
    grid.appendChild(card);
  }
}

/**
 * Toggle a module's enabled/disabled state via POST /api/modules/toggle.
 * Shows a toast and reveals the notice bar prompting restart.
 * @param {string} modKey - The mod identifier
 * @param {boolean} enable - Whether to enable (true) or disable (false)
 */
async function toggleModuleStatus(modKey, enable) {
  if (!state.isAdmin) {
    showToast('請先登入管理員才能操作模組！', 'error');
    openLoginModal();
    return;
  }

  const btn = document.getElementById(`toggle-btn-${modKey}`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = '處理中...';
  }

  try {
    const res = await fetch('/api/modules/toggle', {
      method: 'POST',
      headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ mod_key: modKey, enabled: enable }),
    });
    const data = await res.json();

    if (res.ok && data.status === 'success') {
      const actionZh = enable ? '啟用' : '停用';
      showToast(`✅ 模組「${modKey}」已${actionZh}，請重啟雪莉生效！`, 'success');

      // Show pending changes notice bar
      const noticeBar = document.getElementById('mod-notice-bar');
      if (noticeBar) noticeBar.style.display = 'flex';

      // Dynamically update sidebar navigation & views for all mods
      await checkDynamicModules();
      await loadModManager();
    } else {
      showToast(`❌ ${data.message || '操作失敗'}`, 'error');
      if (btn) {
        btn.disabled = false;
        btn.textContent = enable ? '🟢 啟用此模組' : '🔴 停用此模組';
      }
    }
  } catch (e) {
    showToast(`❌ 網路錯誤: ${e}`, 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = enable ? '🟢 啟用此模組' : '🔴 停用此模組';
    }
  }
}



// =========================================================
// STOP BOT (關閉/停止雪莉)
// =========================================================
function requestStop() {
  if (!state.isAdmin) {
    showToast('關閉雪莉僅限管理員使用，請先登入！', 'warning');
    openLoginModal();
    return;
  }
  const backdrop = document.getElementById('stop-modal-backdrop');
  if (backdrop) backdrop.classList.remove('hidden');
}

function closeStopModal() {
  const backdrop = document.getElementById('stop-modal-backdrop');
  if (backdrop) backdrop.classList.add('hidden');
}

async function executeStop() {
  closeStopModal();
  const overlay = document.getElementById('stop-overlay');
  if (overlay) overlay.classList.remove('hidden');

  try {
    await fetch('/api/stop', {
      method: 'POST',
      headers: getAuthHeaders(),
    });
  } catch (e) {}

  showToast('🛑 雪莉已傳送關閉指令並停止運行。', 'info');
}




// =========================================================
// 🎯 ROAST / TSUKKOMI SYSTEM MANAGEMENT
// =========================================================

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

state.roastConfig = null;
state.roastDebounceTimers = {};
state.roastCurrentPage = 1;
state.roastPageSize = 10;

function setModalProb(val) {
  const probEl = document.getElementById('roast-modal-prob');
  const probDisplay = document.getElementById('roast-modal-prob-display');
  if (probEl) probEl.value = val;
  if (probDisplay) probDisplay.textContent = `${val}%`;
}

async function loadRoastConfig() {
  const container = document.getElementById('roast-rules-container');
  if (!container) return;

  try {
    const res = await fetch('/api/roast/config');
    if (!res.ok) {
      showToast('❌ 載入吐槽設定失敗', 'error');
      return;
    }
    const data = await res.json();
    state.roastConfig = data;

    // Update Master Switch & Cooldown UI
    const toggleEl = document.getElementById('roast-master-toggle');
    const badgeEl = document.getElementById('roast-master-badge');
    const cooldownEl = document.getElementById('roast-cooldown-input');

    if (toggleEl) toggleEl.checked = !!data.enabled;
    if (badgeEl) {
      if (data.enabled) {
        badgeEl.textContent = '🟢 運作中';
        badgeEl.style.background = 'rgba(0,255,136,0.15)';
        badgeEl.style.color = '#00ff88';
        badgeEl.style.border = '1px solid #00ff88';
      } else {
        badgeEl.textContent = '🔴 已停用';
        badgeEl.style.background = 'rgba(255,68,68,0.15)';
        badgeEl.style.color = '#ff4444';
        badgeEl.style.border = '1px solid #ff4444';
      }
    }
    if (cooldownEl) cooldownEl.value = data.global_cooldown_seconds || 8;

    // Update Stats
    const rules = data.rules || [];
    const totalCount = rules.length;
    const enabledCount = rules.filter(r => r.enabled).length;
    const avgProb = totalCount > 0
      ? Math.round(rules.reduce((acc, r) => acc + (r.probability || 0), 0) / totalCount)
      : 100;

    const totalEl = document.getElementById('roast-stat-total');
    const enabledEl = document.getElementById('roast-stat-enabled');
    const avgEl = document.getElementById('roast-stat-avg-prob');

    if (totalEl) totalEl.textContent = totalCount;
    if (enabledEl) enabledEl.textContent = enabledCount;
    if (avgEl) avgEl.textContent = `${avgProb}%`;

    renderRoastList();
  } catch (e) {
    console.error('Failed to load roast config:', e);
    showToast(`❌ 載入吐槽設定失敗: ${e.message}`, 'error');
  }
}

function renderRoastList() {
  const container = document.getElementById('roast-rules-container');
  const pageInfoEl = document.getElementById('roast-page-info');
  const prevBtn = document.getElementById('roast-prev-btn');
  const nextBtn = document.getElementById('roast-next-btn');

  if (!container || !state.roastConfig) return;

  const rawRules = state.roastConfig.rules || [];
  const searchInput = document.getElementById('roast-search-input');
  const query = (searchInput ? searchInput.value : '').trim().toLowerCase();
  const filterMode = document.getElementById('roast-filter-mode')?.value || 'all';

  const filtered = rawRules.filter(r => {
    if (filterMode === 'enabled' && !r.enabled) return false;
    if (filterMode === 'disabled' && r.enabled) return false;
    if (query) {
      const matchKw = (r.keyword || '').toLowerCase().includes(query);
      const matchGuide = (r.reply_guide || '').toLowerCase().includes(query);
      return matchKw || matchGuide;
    }
    return true;
  });

  const totalFiltered = filtered.length;

  if (totalFiltered === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 48px; color: var(--text-muted); background: var(--bg-card); border-radius: 12px; border: 1px dashed var(--border-color);">
        <div style="font-size: 2.5rem; margin-bottom: 10px;">🔍</div>
        <div style="font-size: 1.05rem; font-weight: 600; color: var(--text-main); margin-bottom: 6px;">尚無符合條件的吐槽規則</div>
        <div style="font-size: 0.85rem;">點擊右上角「＋ 新增吐槽規則」即可自訂關鍵字與名偵探回覆導引！</div>
      </div>
    `;
    if (pageInfoEl) pageInfoEl.textContent = '第 0 / 0 頁 (共 0 筆)';
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    return;
  }

  // Pagination calculation
  let displayRules = filtered;
  let totalPages = 1;

  if (state.roastPageSize !== 'all') {
    const size = parseInt(state.roastPageSize, 10) || 10;
    totalPages = Math.max(1, Math.ceil(totalFiltered / size));
    if (state.roastCurrentPage > totalPages) state.roastCurrentPage = totalPages;
    if (state.roastCurrentPage < 1) state.roastCurrentPage = 1;

    const start = (state.roastCurrentPage - 1) * size;
    displayRules = filtered.slice(start, start + size);

    if (pageInfoEl) {
      pageInfoEl.textContent = `第 ${state.roastCurrentPage} / ${totalPages} 頁 (共 ${totalFiltered} 筆)`;
    }
    if (prevBtn) prevBtn.disabled = state.roastCurrentPage <= 1;
    if (nextBtn) nextBtn.disabled = state.roastCurrentPage >= totalPages;
  } else {
    if (pageInfoEl) {
      pageInfoEl.textContent = `全部顯示 (共 ${totalFiltered} 筆)`;
    }
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
  }

  container.innerHTML = displayRules.map(rule => {
    const prob = rule.probability ?? 100;
    const isEn = !!rule.enabled;
    const modeBadge = rule.match_mode === 'exact' ? '完全一致' : rule.match_mode === 'regex' ? '正則表達' : '包含關鍵字';
    const modeColor = rule.match_mode === 'exact' ? '#ffab00' : rule.match_mode === 'regex' ? '#00e5ff' : '#a855f7';

    return `
      <div class="roast-card" style="background: var(--bg-card); border: 1px solid ${isEn ? 'rgba(251, 191, 36, 0.25)' : 'var(--border-color)'}; border-radius: 12px; padding: 18px 22px; transition: all 0.2s ease; opacity: ${isEn ? '1' : '0.65'};">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 12px;">
          <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
            <span style="font-size: 1.15rem; font-weight: 800; color: #fbbf24; background: rgba(251, 191, 36, 0.12); border: 1px solid rgba(251, 191, 36, 0.4); padding: 4px 12px; border-radius: 8px; font-family: 'JetBrains Mono', monospace;">
              ${escapeHtml(rule.keyword)}
            </span>
            <span style="font-size: 0.72rem; font-weight: 700; color: ${modeColor}; background: rgba(0,0,0,0.3); border: 1px solid ${modeColor}; padding: 2px 8px; border-radius: 6px;">
              ${modeBadge}
            </span>
            <span style="font-size: 0.75rem; color: var(--text-muted);">
              ID: ${rule.id}
            </span>
          </div>
          <div style="display: flex; align-items: center; gap: 10px;">
            <label style="display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.82rem; font-weight: 600; color: ${isEn ? '#00ff88' : 'var(--text-muted)'};">
              <input type="checkbox" ${isEn ? 'checked' : ''} onchange="toggleRoastRule('${rule.id}', this.checked)" style="transform: scale(1.15); cursor: pointer;">
              <span>${isEn ? '已啟用' : '已停用'}</span>
            </label>
            <button onclick="openRoastModal('${rule.id}')" title="編輯規則" style="background: rgba(255,255,255,0.06); border: 1px solid var(--border-color); color: var(--text-main); padding: 5px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer;">
              ✏️ 編輯
            </button>
            <button onclick="deleteRoastRule('${rule.id}')" title="刪除規則" style="background: rgba(255,68,68,0.1); border: 1px solid rgba(255,68,68,0.3); color: #ff5555; padding: 5px 10px; border-radius: 6px; font-size: 0.8rem; cursor: pointer;">
              🗑️
            </button>
          </div>
        </div>

        <div style="background: rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.05); border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; font-size: 0.9rem; line-height: 1.5; color: #e2e8f0;">
          <span style="color: #fbbf24; font-weight: 700; margin-right: 6px;">💬 回覆導引:</span>
          ${escapeHtml(rule.reply_guide)}
        </div>

        <div style="display: flex; align-items: center; justify-content: space-between; gap: 20px;">
          <div style="display: flex; align-items: center; gap: 12px; flex: 1;">
            <span style="font-size: 0.82rem; font-weight: 600; color: var(--text-muted); white-space: nowrap;">
              🎲 獨立觸發機率:
            </span>
            <input type="range" min="0" max="100" value="${prob}" style="flex: 1; cursor: pointer;" oninput="onRoastProbSlide('${rule.id}', this.value)" onchange="onRoastProbChange('${rule.id}', this.value)">
            <span id="roast-prob-badge-${rule.id}" style="font-size: 0.95rem; font-weight: 800; color: ${prob === 100 ? '#00ff88' : prob > 50 ? '#fbbf24' : '#ffab00'}; min-width: 45px; text-align: right;">
              ${prob}%
            </span>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function filterRoastRules() {
  state.roastCurrentPage = 1;
  renderRoastList();
}

function changeRoastPage(delta) {
  state.roastCurrentPage += delta;
  renderRoastList();
  const listEl = document.getElementById('roast-rules-container');
  if (listEl) listEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function onRoastPageSizeChange(val) {
  state.roastPageSize = val === 'all' ? 'all' : (parseInt(val, 10) || 10);
  state.roastCurrentPage = 1;
  renderRoastList();
}

function onRoastProbSlide(ruleId, val) {
  const badge = document.getElementById(`roast-prob-badge-${ruleId}`);
  if (badge) badge.textContent = `${val}%`;
}

function onRoastProbChange(ruleId, val) {
  const prob = parseInt(val, 10);
  if (isNaN(prob)) return;

  if (state.roastDebounceTimers[ruleId]) {
    clearTimeout(state.roastDebounceTimers[ruleId]);
  }

  state.roastDebounceTimers[ruleId] = setTimeout(async () => {
    try {
      const res = await fetch(`/api/roast/rules/${ruleId}`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify({ probability: prob })
      });
      if (res.ok) {
        showToast(`✅ 已將規則機率更新為 ${prob}%`, 'success');
        if (state.roastConfig && state.roastConfig.rules) {
          const target = state.roastConfig.rules.find(r => r.id === ruleId);
          if (target) target.probability = prob;
        }
      } else {
        const err = await res.json();
        showToast(`❌ 更新機率失敗: ${err.message}`, 'error');
        loadRoastConfig();
      }
    } catch (e) {
      showToast(`❌ 更新機率發生錯誤: ${e.message}`, 'error');
    }
  }, 200);
}

async function toggleRoastMaster(enabled) {
  if (!state.isAdmin) {
    showToast('修改吐槽開關需要管理員身份，請先登入！', 'warning');
    openLoginModal();
    const toggleEl = document.getElementById('roast-master-toggle');
    if (toggleEl) toggleEl.checked = !enabled;
    return;
  }

  try {
    const res = await fetch('/api/roast/config', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ enabled: enabled })
    });
    if (res.ok) {
      showToast(enabled ? '🟢 吐槽功能已啟用' : '🔴 吐槽功能已停用', 'success');
      loadRoastConfig();
    } else {
      const err = await res.json();
      showToast(`❌ 更新失敗: ${err.message}`, 'error');
    }
  } catch (e) {
    showToast(`❌ 發生異常: ${e.message}`, 'error');
  }
}

async function updateRoastCooldown(cooldownVal) {
  if (!state.isAdmin) {
    showToast('修改冷卻時間需要管理員身份，請先登入！', 'warning');
    openLoginModal();
    return;
  }

  const sec = parseInt(cooldownVal, 10);
  if (isNaN(sec) || sec < 0) return;

  try {
    const res = await fetch('/api/roast/config', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ global_cooldown_seconds: sec })
    });
    if (res.ok) {
      showToast(`✅ 頻道冷卻時間已設定為 ${sec} 秒`, 'success');
    } else {
      const err = await res.json();
      showToast(`❌ 設定冷卻失敗: ${err.message}`, 'error');
    }
  } catch (e) {
    showToast(`❌ 發生異常: ${e.message}`, 'error');
  }
}

async function toggleRoastRule(ruleId, enabled) {
  if (!state.isAdmin) {
    showToast('切換規則狀態需要管理員身份，請先登入！', 'warning');
    openLoginModal();
    loadRoastConfig();
    return;
  }

  try {
    const res = await fetch(`/api/roast/rules/${ruleId}`, {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify({ enabled: enabled })
    });
    if (res.ok) {
      showToast(enabled ? '✅ 規則已啟用' : '⏸️ 規則已停用', 'success');
      if (state.roastConfig && state.roastConfig.rules) {
        const target = state.roastConfig.rules.find(r => r.id === ruleId);
        if (target) target.enabled = enabled;
      }
      renderRoastList();
    } else {
      const err = await res.json();
      showToast(`❌ 更新失敗: ${err.message}`, 'error');
      loadRoastConfig();
    }
  } catch (e) {
    showToast(`❌ 發生異常: ${e.message}`, 'error');
  }
}

function onRoastModalModeChange(mode) {
  const kwEl = document.getElementById('roast-modal-kw');
  const hintEl = document.getElementById('roast-modal-kw-hint');
  if (!kwEl || !hintEl) return;

  if (mode === 'regex') {
    kwEl.placeholder = '例如：小奶|扁奶|ㄌㄌ|蘿莉|酪梨 （多組關鍵字請用 | 隔開）';
    hintEl.innerHTML = '💡 <strong>正則模式</strong>：支援完整 Regex 語法。若要符合多個詞彙任一命中，請使用垂直線 <code>|</code> 隔開（例如：<code>小奶|扁奶|ㄌㄌ|蘿莉</code>）。';
  } else if (mode === 'exact') {
    kwEl.placeholder = '例如：笑死 （整句話必須完全等於此關鍵字）';
    hintEl.innerHTML = '💡 <strong>完全一致</strong>：群友整句發言必須與關鍵字一模一樣（不含多餘字詞）才會觸發。';
  } else {
    kwEl.placeholder = '例如：== 或 你是笨蛋';
    hintEl.innerHTML = '💡 <strong>包含模式</strong>：只要群友發言中含有該關鍵字即可觸發。';
  }
}

function openRoastModal(ruleId = null) {
  if (!state.isAdmin) {
    showToast('編輯或新增規則需要管理員身份，請先登入！', 'warning');
    openLoginModal();
    return;
  }

  const backdrop = document.getElementById('roast-modal-backdrop');
  const titleEl = document.getElementById('roast-modal-title');
  const idEl = document.getElementById('roast-modal-id');
  const kwEl = document.getElementById('roast-modal-kw');
  const modeEl = document.getElementById('roast-modal-mode');
  const guideEl = document.getElementById('roast-modal-guide');
  const probEl = document.getElementById('roast-modal-prob');
  const probDisplay = document.getElementById('roast-modal-prob-display');
  const enabledEl = document.getElementById('roast-modal-enabled');

  if (!backdrop) return;

  let currentMode = 'contains';
  if (ruleId && state.roastConfig && state.roastConfig.rules) {
    const rule = state.roastConfig.rules.find(r => r.id === ruleId);
    if (rule) {
      titleEl.textContent = '✏️ 編輯吐槽規則';
      idEl.value = rule.id;
      kwEl.value = rule.keyword || '';
      currentMode = rule.match_mode || 'contains';
      modeEl.value = currentMode;
      guideEl.value = rule.reply_guide || '';
      const p = rule.probability ?? 100;
      probEl.value = p;
      probDisplay.textContent = `${p}%`;
      enabledEl.checked = !!rule.enabled;
    }
  } else {
    titleEl.textContent = '🎯 新增吐槽規則';
    idEl.value = '';
    kwEl.value = '';
    currentMode = 'contains';
    modeEl.value = currentMode;
    guideEl.value = '';
    probEl.value = 100;
    probDisplay.textContent = '100%';
    enabledEl.checked = true;
  }

  onRoastModalModeChange(currentMode);
  backdrop.classList.remove('hidden');
  setTimeout(() => kwEl.focus(), 50);
}

function closeRoastModal() {
  const backdrop = document.getElementById('roast-modal-backdrop');
  if (backdrop) backdrop.classList.add('hidden');
}

async function saveRoastRuleFromModal() {
  const id = document.getElementById('roast-modal-id').value;
  const keyword = document.getElementById('roast-modal-kw').value.trim();
  const match_mode = document.getElementById('roast-modal-mode').value;
  const reply_guide = document.getElementById('roast-modal-guide').value.trim();
  const probability = parseInt(document.getElementById('roast-modal-prob').value, 10) || 100;
  const enabled = document.getElementById('roast-modal-enabled').checked;

  if (!keyword) {
    showToast('請輸入觸發關鍵字！', 'warning');
    return;
  }
  if (!reply_guide) {
    showToast('請輸入吐槽回覆導引！', 'warning');
    return;
  }

  const payload = {
    keyword,
    match_mode,
    reply_guide,
    probability,
    enabled
  };

  try {
    let res;
    if (id) {
      // Update
      res = await fetch(`/api/roast/rules/${id}`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify(payload)
      });
    } else {
      // Create
      res = await fetch('/api/roast/rules', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(payload)
      });
    }

    if (res.ok) {
      showToast(id ? '✅ 吐槽規則修改成功！' : '🎉 吐槽規則新增成功！', 'success');
      closeRoastModal();
      loadRoastConfig();
    } else {
      const err = await res.json();
      showToast(`❌ 儲存失敗: ${err.message}`, 'error');
    }
  } catch (e) {
    showToast(`❌ 發生錯誤: ${e.message}`, 'error');
  }
}

async function deleteRoastRule(ruleId) {
  if (!state.isAdmin) {
    showToast('刪除規則需要管理員身份，請先登入！', 'warning');
    openLoginModal();
    return;
  }

  if (!confirm('確定要刪除這條吐槽規則嗎？')) return;

  try {
    const res = await fetch(`/api/roast/rules/${ruleId}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    });
    if (res.ok) {
      showToast('🗑️ 吐槽規則已刪除', 'success');
      loadRoastConfig();
    } else {
      const err = await res.json();
      showToast(`❌ 刪除失敗: ${err.message}`, 'error');
    }
  } catch (e) {
    showToast(`❌ 發生異常: ${e.message}`, 'error');
  }
}

async function testRoastPreview() {
  const inputEl = document.getElementById('roast-test-msg');
  const forceEl = document.getElementById('roast-test-force');
  const resultBox = document.getElementById('roast-test-result');

  if (!inputEl) return;
  const msg = inputEl.value.trim();
  if (!msg) {
    showToast('請輸入要測試的發言內容！', 'warning');
    inputEl.focus();
    return;
  }

  resultBox.style.display = 'block';
  resultBox.innerHTML = '<span style="color: var(--text-muted);">🤖 雪莉正在分析關鍵字並構思吐槽...</span>';

  try {
    const res = await fetch('/api/roast/test', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({
        message: msg,
        force: forceEl ? forceEl.checked : true
      })
    });

    if (res.ok) {
      const data = await res.json();
      if (!data.matched) {
        resultBox.innerHTML = `
          <div style="color: #888;">
            ⚪ <strong>判定結果</strong>: 未命中任何已啟用的吐槽關鍵字
          </div>
        `;
      } else if (data.matched && !data.triggered) {
        resultBox.innerHTML = `
          <div style="color: #ffab00;">
            🟡 <strong>命中但未觸發</strong>: ${escapeHtml(data.message)}
          </div>
        `;
      } else {
        resultBox.innerHTML = `
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="background: rgba(0,255,136,0.15); color: #00ff88; border: 1px solid #00ff88; padding: 2px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 700;">
                🎯 命中關鍵字: ${escapeHtml(data.rule.keyword)} (${data.rule.probability}%)
              </span>
              <span style="font-size: 0.8rem; color: var(--text-muted);">
                導引: ${escapeHtml(data.rule.reply_guide)}
              </span>
            </div>
            <div style="background: rgba(251, 191, 36, 0.1); border: 1px solid rgba(251, 191, 36, 0.3); border-radius: 8px; padding: 12px; color: #fff; font-size: 1rem; line-height: 1.4;">
              <span style="color: #fbbf24; font-weight: 800;">🍊 雪莉 (名偵探):</span>
              <span style="margin-left: 6px;">${escapeHtml(data.roast_reply || '')}</span>
            </div>
          </div>
        `;
      }
    } else {
      resultBox.innerHTML = '<span style="color: #ff4444;">❌ 測試請求失敗</span>';
    }
  } catch (e) {
    resultBox.innerHTML = `<span style="color: #ff4444;">❌ 測試發生異常: ${escapeHtml(e.message)}</span>`;
  }
}




function renderSimpleMarkdown(md) {
  if (!md) return '';
  let html = escapeHtml(md);

  // Headers
  html = html.replace(/^### (.*$)/gim, '<h4 class="md-h4">$1</h4>');
  html = html.replace(/^## (.*$)/gim, '<h3 class="md-h3">$1</h3>');
  html = html.replace(/^# (.*$)/gim, '<h2 class="md-h2">$1</h2>');

  // Blockquotes
  html = html.replace(/^\> (.*$)/gim, '<blockquote class="md-quote">$1</blockquote>');

  // Bold & Italic
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

  // Inline Code
  html = html.replace(/`([^`]+)`/g, '<code class="md-code">$1</code>');

  // Links [text](url)
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="md-link">$1 🔗</a>');

  // Bullet points
  html = html.replace(/^\s*[-*•]\s+(.*$)/gim, '<li class="md-li">$1</li>');

  // Paragraph breaks
  html = html.replace(/\n\n+/g, '<br><br>');
  html = html.replace(/\n/g, '<br>');

  return html;
}


