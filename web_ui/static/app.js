/* ======================================================
   Sherry Console v2 — app.js
   完整重寫：Log 動態載入、Settings 分組渲染、
   Model Picker（下拉+手動）、分頁、自動刷新、Toast、
   外掛生態 (SherryUI Plugin Ecosystem) 被動動態加載
   ====================================================== */

// ── State ────────────────────────────────────────────────
const state = {
  currentView: 'dashboard',
  currentLogFile: 'chat_history.db [runtime_events]',
  currentPage: 1,
  totalPages: 1,
  totalEntries: 0,
  eventFilter: 'all',
  autoRefreshTimer: null,
  metricsTimer: null,
  serviceCheckTimer: null,
  botStatusTimer: null,
  dashboardTimer: null,
  inFlight: {
    dashboard: false,
    metrics: false,
    botStatus: false,
    services: false,
    logs: false,
    tasks: false,
    selfMonitoring: false,
  },
  tasksTimer: null,
  isPageVisible: !document.hidden,
  autoRefreshEnabled: true,
  currentSettingsTab: 'tab-ai',
  settingsSearch: '',
  settingsData: {},       // { key: { value, label, description, type, ... } }
  dirtyKeys: new Set(),   // keys that have been changed but not saved
  ollamaModels: [],
  lmsModels: {},           // keyed by model prefix for caching
  isAdmin: false,
  lastActivityTime: Date.now(),
  inactivityCheckTimer: null,
  availableMods: {},
  loadedModViews: new Set(),
};

// ── Anti-Stampede Self-Scheduling Helper ─────────────────
function scheduleNextPoll(timerKey, callback, intervalMs, viewRequired) {
  if (state[timerKey]) {
    clearTimeout(state[timerKey]);
    state[timerKey] = null;
  }
  if (!state.isPageVisible) return;
  if (viewRequired && state.currentView !== viewRequired) return;

  state[timerKey] = setTimeout(async () => {
    state[timerKey] = null;
    if (!state.isPageVisible) return;
    if (viewRequired && state.currentView !== viewRequired) return;
    await callback();
  }, intervalMs);
}

// ── SherryUI Ecosystem for Dynamic Mod Plugins ───────────
window.SherryUI = {
  plugins: {},
  activePluginId: null,
  registerPlugin(id, config) {
    this.plugins[id] = config;
    if (config.viewId && config.viewId !== id) {
      this.plugins[config.viewId] = config;
    }
    console.log(`[SherryUI] Registered plugin: ${id}`, config);
    // If user is currently looking at this mod view, trigger onActivate immediately
    if (state.currentView === (config.viewId || id) || state.currentView === id) {
      this.activePluginId = id;
      if (config.title) {
        const titleEl = document.getElementById('view-title');
        if (titleEl) titleEl.textContent = config.title;
      }
      if (typeof config.onActivate === 'function') {
        try { config.onActivate(); } catch (e) { console.error(e); }
      }
    }
  },
  getAuthHeaders,
  showToast,
  switchView,
  escapeHtml,
  sanitizeHtml,
};

// ── Init ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // 1. 即時秒開預設首頁（總覽主控台），消弭任何串行阻塞 (LCP 0ms 延遲)
  switchView('dashboard');

  // R20：主動關懷導覽按鈕以事件監聽綁定（避免新增 inline handler）。
  const navCare = document.getElementById('nav-care');
  if (navCare) navCare.addEventListener('click', () => switchView('care'));

  startInactivityTracker();
  startAutoRefresh();
  startUserHeartbeatTracker();

  // 2. 背景平行非同步處理權限驗證與動態外掛按鈕建置（日誌清單改為切換至日誌分頁時 Lazy 載入）
  Promise.allSettled([
    checkAdminAuth(),
    checkDynamicModules()
  ]).catch(err => console.warn('[Init] Background init warning:', err));
});

// ── Page Visibility Listener ─────────────────────────────
document.addEventListener('visibilitychange', () => {
  state.isPageVisible = !document.hidden;
  if (state.isPageVisible) {
    // 使用者切回分頁，立即喚醒當前視圖資料更新
    if (state.currentView === 'dashboard') loadDashboard();
    else if (state.currentView === 'metrics') loadMetrics();
    else if (state.currentView === 'bot-status') loadBotStatus();
    else if (state.currentView === 'services') loadServiceStatus();
    else if (state.currentView === 'logs' && state.autoRefreshEnabled) loadLogs();
    else if (state.currentView === 'audit') refreshCurrentAuditTab();
  } else {
    // 進入背景待機，清除所有高頻輪詢定時器以節省系統資源
    if (state.metricsTimer) { clearTimeout(state.metricsTimer); state.metricsTimer = null; }
    if (state.serviceCheckTimer) { clearTimeout(state.serviceCheckTimer); state.serviceCheckTimer = null; }
    if (state.botStatusTimer) { clearTimeout(state.botStatusTimer); state.botStatusTimer = null; }
    if (state.dashboardTimer) { clearTimeout(state.dashboardTimer); state.dashboardTimer = null; }
    if (state.autoRefreshTimer) { clearTimeout(state.autoRefreshTimer); state.autoRefreshTimer = null; }
  }
});

/**
 * Dynamically discover and mount mod buttons from /api/modules.
 * Builds sidebar navigation buttons without eager fetching of view HTML/scripts (Lazy Loading).
 */
async function checkDynamicModules() {
  try {
    const res = await fetch('/api/modules');
    const data = await res.json();
    const mods = data.modules || {};
    state.availableMods = mods;
    const dynamicNavGroup = document.getElementById('sidebar-dynamic-mods');
    const dynamicNavWrap = document.getElementById('sidebar-dynamic-mods-wrap');

    if (dynamicNavGroup) dynamicNavGroup.innerHTML = '';

    const modEntries = Object.values(mods).sort((a, b) => (a.order || 100) - (b.order || 100));
    let mountedModsCount = 0;

    for (const mod of modEntries) {
      if (!mod.enabled || !mod.has_web_ui) continue;
      mountedModsCount++;

      const navId = `nav-${mod.nav_id || mod.id}`;

      // 1. Sidebar navigation button
      if (dynamicNavGroup) {
        let btn = document.getElementById(navId);
        if (!btn) {
          btn = document.createElement('button');
          btn.className = 'nav-btn' + (mod.admin_only ? ' admin-only' : '');
          btn.id = navId;
          btn.onclick = () => switchView(mod.nav_id || mod.id);
          btn.title = mod.nav_title || mod.display_name || mod.id;
          btn.innerHTML = `<span class="nav-icon">${escHtml(mod.nav_icon || '🧩')}</span><span class="nav-label">${escHtml(mod.nav_title || mod.display_name || mod.id)}</span>`;
          dynamicNavGroup.appendChild(btn);
        }
      }
    }

    if (dynamicNavWrap) {
      dynamicNavWrap.style.display = mountedModsCount > 0 ? 'flex' : 'none';
    }
  } catch (e) {
    console.error('Failed to discover dynamic modules:', e);
  }
}

/**
 * Lazy-loads view snippet and script for a dynamic mod on-demand with LocalStorage 0ms caching.
 */
async function ensureModViewMounted(modKey) {
  const mod = Object.values(state.availableMods).find(m => (m.nav_id || m.id) === modKey || m.id === modKey);
  if (!mod || !mod.enabled || !mod.has_web_ui) return;

  const viewId = `view-${mod.nav_id || mod.id}`;
  const dynamicViewsContainer = document.getElementById('dynamic-mod-views');
  const cacheKey = `mod_view_${mod.id}_${mod.version || '1.0.0'}`;

  // Helper to mount HTML into DOM safely
  const mountHtml = (html) => {
    if (!html || !dynamicViewsContainer) return;
    const tempWrapper = document.createElement('div');
    tempWrapper.innerHTML = html.trim();
    while (tempWrapper.firstChild) {
      const node = tempWrapper.firstChild;
      if (node.nodeType === Node.ELEMENT_NODE && node.classList && node.classList.contains('view')) {
        if (!node.id) node.id = viewId;
        const isCurrent = (state.currentView === modKey || state.currentView === (mod.nav_id || mod.id));
        if (isCurrent) {
          node.classList.remove('hidden');
        } else {
          node.classList.add('hidden');
        }
      }
      dynamicViewsContainer.appendChild(node);
    }
    state.loadedModViews.add(viewId);
    // Remove loading placeholder
    const spinner = document.getElementById('mod-loading-placeholder');
    if (spinner) spinner.remove();
  };

  // 1. Check if already mounted in DOM
  let existingView = document.getElementById(viewId);
  if (existingView) {
    state.loadedModViews.add(viewId);
    if (state.currentView === modKey || state.currentView === (mod.nav_id || mod.id)) {
      existingView.classList.remove('hidden');
    }
  } else if (dynamicViewsContainer && mod.has_view && mod.view_url) {
    // 2. Check LocalStorage Cache for 0ms instant render
    let cachedHtml = null;
    try { cachedHtml = localStorage.getItem(cacheKey); } catch (e) {}

    if (cachedHtml) {
      mountHtml(cachedHtml);
    } else {
      // 3. Fetch from server with loading placeholder
      try {
        const viewRes = await fetch(mod.view_url);
        if (viewRes.ok) {
          const html = await viewRes.text();
          if (html) {
            try { localStorage.setItem(cacheKey, html); } catch (e) {}
            mountHtml(html);
          }
        }
      } catch (err) {
        console.error(`Failed to load view for mod [${mod.id}]:`, err);
      }
    }
  }

  // 4. Inject script tag if not already injected
  if (mod.script_url) {
    const scriptId = `script-mod-${mod.id}`;
    let script = document.getElementById(scriptId);
    if (!script) {
      script = document.createElement('script');
      script.id = scriptId;
      script.src = `${mod.script_url}?v=${Date.now()}`;
      script.async = true;
      script.onload = () => {
        if (window.SherryUI && window.SherryUI.plugins) {
          const plugin = window.SherryUI.plugins[modKey] || window.SherryUI.plugins[mod.nav_id || mod.id];
          if (plugin && (state.currentView === modKey || state.currentView === (mod.nav_id || mod.id))) {
            window.SherryUI.activePluginId = modKey;
            if (plugin.title) document.getElementById('view-title').textContent = plugin.title;
            if (typeof plugin.onActivate === 'function') {
              try { plugin.onActivate(); } catch (e) { console.error(e); }
            }
          }
        }
      };
      document.body.appendChild(script);
    }
  }

  // 5. Activate plugin if already registered
  const activePlugin = (window.SherryUI && window.SherryUI.plugins) ? (window.SherryUI.plugins[modKey] || window.SherryUI.plugins[mod.nav_id || mod.id]) : null;
  if (activePlugin) {
    if (state.currentView === modKey || state.currentView === (mod.nav_id || mod.id)) {
      window.SherryUI.activePluginId = modKey;
      if (activePlugin.title) document.getElementById('view-title').textContent = activePlugin.title;
      if (typeof activePlugin.onActivate === 'function') {
        try { activePlugin.onActivate(); } catch (e) { console.error(e); }
      }
    }
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

  // Notify Live2D Companion of view change
  if (window.sherryCompanion && typeof window.sherryCompanion.onViewChanged === 'function') {
    window.sherryCompanion.onViewChanged(view);
  }

  document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
  document.querySelectorAll('[id$="-toolbar"]').forEach(tb => tb.classList.add('hidden'));

  if (state.metricsTimer) {
    clearTimeout(state.metricsTimer);
    state.metricsTimer = null;
  }
  if (state.serviceCheckTimer) {
    clearTimeout(state.serviceCheckTimer);
    state.serviceCheckTimer = null;
  }
  if (state.botStatusTimer) {
    clearTimeout(state.botStatusTimer);
    state.botStatusTimer = null;
  }
  if (state.dashboardTimer) {
    clearTimeout(state.dashboardTimer);
    state.dashboardTimer = null;
  }
  if (state.tasksTimer) {
    clearTimeout(state.tasksTimer);
    state.tasksTimer = null;
  }

  // 延遲載入並啟動外掛視圖
  ensureModViewMounted(view);

  if (view === 'dashboard') {
    const navDb = document.getElementById('nav-dashboard');
    if (navDb) navDb.classList.add('active');
    document.getElementById('view-dashboard').classList.remove('hidden');
    const dTb = document.getElementById('dashboard-toolbar');
    if (dTb) dTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '總覽主控台';
    loadDashboard();
  } else if (view === 'logs') {
    document.getElementById('nav-logs').classList.add('active');
    document.getElementById('view-logs').classList.remove('hidden');
    document.getElementById('logs-toolbar').classList.remove('hidden');
    document.getElementById('view-title').textContent = '即時日誌';
    if (!state.logFilesLoaded) {
      loadLogFiles().then(() => { if (state.totalEntries === 0) loadLogs(); });
    } else if (state.totalEntries === 0) {
      loadLogs();
    }
  } else if (view === 'metrics') {
    document.getElementById('nav-metrics').classList.add('active');
    document.getElementById('view-metrics').classList.remove('hidden');
    document.getElementById('metrics-toolbar').classList.remove('hidden');
    document.getElementById('view-title').textContent = '系統資源監控';
    loadMetrics();
  } else if (view === 'tasks') {
    document.getElementById('nav-tasks').classList.add('active');
    document.getElementById('view-tasks').classList.remove('hidden');
    document.getElementById('tasks-toolbar').classList.remove('hidden');
    document.getElementById('view-title').textContent = '即時工作與自癒中樞';
    loadTasks();
  } else if (view === 'chat') {
    document.getElementById('nav-chat').classList.add('active');
    document.getElementById('view-chat').classList.remove('hidden');
    document.getElementById('view-title').textContent = '雪莉穩定性測試室';
    setTimeout(() => {
      const inputEl = document.getElementById('chat-input');
      if (inputEl && !inputEl.classList.contains('hidden')) inputEl.focus();
    }, 100);
  } else if (view === 'services') {
    document.getElementById('nav-services').classList.add('active');
    document.getElementById('view-services').classList.remove('hidden');
    const sTb = document.getElementById('services-toolbar');
    if (sTb) sTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '地端 AI 服務監測';
    loadServiceStatus();
  } else if (view === 'bot-status') {
    const navBs = document.getElementById('nav-bot-status');
    if (navBs) navBs.classList.add('active');
    document.getElementById('view-bot-status').classList.remove('hidden');
    const bTb = document.getElementById('bot-status-toolbar');
    if (bTb) bTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '語音與音樂監控';
    loadBotStatus();
  } else if (view === 'audit') {
    const navAudit = document.getElementById('nav-audit');
    if (navAudit) navAudit.classList.add('active');
    document.getElementById('view-audit').classList.remove('hidden');
    const aTb = document.getElementById('audit-toolbar');
    if (aTb) aTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '審計與連線監控';
    if (typeof currentAuditTab !== 'undefined' && currentAuditTab === 'user') {
      loadUserConnections();
    } else {
      loadAuditLogs();
    }
  } else if (view === 'mod-manager') {
    const navMM = document.getElementById('nav-mod-manager');
    if (navMM) navMM.classList.add('active');
    const viewMM = document.getElementById('view-mod-manager');
    if (viewMM) viewMM.classList.remove('hidden');
    const mmTb = document.getElementById('mod-manager-toolbar');
    if (mmTb) mmTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '功能模組管理';
    loadModManager();
  } else if (view === 'self-monitoring') {
    const navSM = document.getElementById('nav-self-monitoring');
    if (navSM) navSM.classList.add('active');
    const viewSM = document.getElementById('view-self-monitoring');
    if (viewSM) viewSM.classList.remove('hidden');
    document.getElementById('view-title').textContent = '自我監控與延遲稽核';
    loadSelfMonitoringData();
  } else if (view === 'roast') {
    const navRoast = document.getElementById('nav-roast');
    if (navRoast) navRoast.classList.add('active');
    const viewRoast = document.getElementById('view-roast');
    if (viewRoast) viewRoast.classList.remove('hidden');
    const rTb = document.getElementById('roast-toolbar');
    if (rTb) rTb.classList.remove('hidden');
    document.getElementById('view-title').textContent = '關鍵字吐槽管理';
    loadRoastConfig();
  } else if (view === 'spark') {
    const navSpark = document.getElementById('nav-spark');
    if (navSpark) navSpark.classList.add('active');
    const viewSpark = document.getElementById('view-spark');
    if (viewSpark) viewSpark.classList.remove('hidden');
    document.getElementById('view-title').textContent = 'Spark 條件推播';
    if (window.SparkApp) {
      SparkApp.init();
    }
  } else if (view === 'care') {
    const navCare = document.getElementById('nav-care');
    if (navCare) navCare.classList.add('active');
    const viewCare = document.getElementById('view-care');
    if (viewCare) viewCare.classList.remove('hidden');
    document.getElementById('view-title').textContent = '賽博群友與自我記憶';
    if (window.CareApp) {
      CareApp.init();
    }
  } else if (view === 'settings') {
    document.getElementById('nav-settings').classList.add('active');
    document.getElementById('view-settings').classList.remove('hidden');
    document.getElementById('settings-toolbar').classList.remove('hidden');
    document.getElementById('view-title').textContent = '系統設定';
    if (!state.settingsData || Object.keys(state.settingsData).length === 0) {
      loadSettings();
    } else {
      renderSettings();
    }
  } else {
    // Dynamic Mod Plugin View activation
    const navBtn = document.getElementById(`nav-${view}`) || document.getElementById(`nav-mod-${view}`);
    if (navBtn) navBtn.classList.add('active');

    // Immediate title synchronization from availableMods
    const mod = Object.values(state.availableMods || {}).find(m => (m.nav_id || m.id) === view || m.id === view);
    if (mod && mod.view_title) {
      const titleEl = document.getElementById('view-title');
      if (titleEl) titleEl.textContent = mod.view_title;
    }

    const viewEl = document.getElementById(`view-${view}`);
    if (viewEl) {
      viewEl.classList.remove('hidden');
    } else {
      // Show smooth loading placeholder if view is downloading
      const dynamicViewsContainer = document.getElementById('dynamic-mod-views');
      if (dynamicViewsContainer && !document.getElementById('mod-loading-placeholder')) {
        const placeholder = document.createElement('div');
        placeholder.id = 'mod-loading-placeholder';
        placeholder.className = 'view';
        placeholder.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:50vh;color:var(--text-muted);font-size:1.1rem;gap:16px;"><div class="spinner" style="width:36px;height:36px;border:3px solid rgba(56,189,248,0.2);border-top-color:#38bdf8;border-radius:50%;animation:spin 0.8s linear infinite;"></div><span>載入模組介面中...</span></div>';
        dynamicViewsContainer.appendChild(placeholder);
      }
    }
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
    state.logFilesLoaded = true;
    const sel = document.getElementById('log-file-select');
    sel.innerHTML = '<option value="">— 選擇日誌/資料庫 —</option>';
    files.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = f.label || `${f.name} (${formatBytes(f.size_bytes)})`;
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
  if (state.inFlight.logs) return;
  state.inFlight.logs = true;

  const sizeSel = document.getElementById('log-size-select');
  const size = parseInt(sizeSel?.value || '50');

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
  } finally {
    state.inFlight.logs = false;
    if (state.autoRefreshEnabled && state.currentView === 'logs') {
      scheduleNextPoll('autoRefreshTimer', loadLogs, 5000, 'logs');
    }
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
    el.innerHTML = sanitizeHtml(buildLogHTML(log, event));
    el.addEventListener('click', () => showLogDetail(log));
    container.appendChild(el);
  });
}

function buildLogHTML(log, event) {
  const ts = formatToUtc8(log.ts || log.timestamp || '—');
  const badgeClass = {
    chat_reply:            'badge-chat',
    react_agent_done:      'badge-chat',
    command_exec:          'badge-command',
    component_interaction: 'badge-command',
    modal_submit:          'badge-command',
    tool_exec:             'badge-tool',
    inference_done:        'badge-inference',
    interject:             'badge-interject',
    error:                 'badge-error',
  }[event] || 'badge-default';

  const badgeLabel = {
    chat_reply:            log.intent || 'CHAT',
    react_agent_done:      log.intent || 'REACT',
    command_exec:          log.intent || 'COMMAND',
    component_interaction: log.intent || 'COMPONENT',
    modal_submit:          log.intent || 'MODAL',
    tool_exec:             log.intent || 'TOOL',
    inference_done:        log.intent || 'LLM',
    interject:             'INTERJECT',
    error:                 'ERROR',
  }[event] || event.toUpperCase();

  let body = '';
  if (event === 'command_exec' || event === 'component_interaction' || event === 'modal_submit') {
    const u = log.user ? `👤 ${escHtml(log.user)}` : '';
    const ch = log.channel_id ? ` • 💬 #${escHtml(log.channel_id)}` : '';
    const lat = log.latency_ms ? ` • ⚡ ${log.latency_ms} ms` : '';
    const tid = log.trace_id ? ` • 🔗 <code>${escHtml(log.trace_id)}</code>` : '';
    body += `<div class="log-user-text">${u}${ch}${lat}${tid}</div>`;
    if (log.raw_input) body += `<div class="log-reply-text" style="color: #38bdf8; font-family: monospace;">⌨️ ${escHtml(log.raw_input)}</div>`;
    if (log.extra?.options && Object.keys(log.extra.options).length > 0) {
      body += `<div class="log-intent">⚙️ 參數: ${escHtml(JSON.stringify(log.extra.options))}</div>`;
    }
  } else if (event === 'tool_exec') {
    const u = log.user ? `👤 ${escHtml(log.user)}` : '';
    const lat = log.latency_ms ? ` • ⏱️ 耗時 ${log.latency_ms} ms` : '';
    const tid = log.trace_id ? ` • 🔗 <code>${escHtml(log.trace_id)}</code>` : '';
    body += `<div class="log-user-text">${u}${lat}${tid}</div>`;
    if (log.raw_input) body += `<div class="log-intent">📥 參數: ${escHtml(log.raw_input)}</div>`;
    const obs = log.extra?.observation || log.reply_preview || '';
    if (obs) body += `<div class="log-reply-text" style="color: #fde047;">📤 回傳: ${escHtml(obs)}</div>`;
  } else if (event === 'inference_done') {
    const lat = log.latency_ms ? ` • ⚡ 推論 ${log.latency_ms} ms` : '';
    const qLat = log.extra?.queue_latency_ms ? ` (排隊 ${log.extra.queue_latency_ms} ms)` : '';
    const tid = log.trace_id ? ` • 🔗 <code>${escHtml(log.trace_id)}</code>` : '';
    body += `<div class="log-user-text">🤖 ${escHtml(log.model_used || 'LLM')}${lat}${qLat}${tid}</div>`;
    if (log.raw_input) body += `<div class="log-intent">📝 Prompt: ${escHtml(log.raw_input)}</div>`;
    if (log.reply_preview) body += `<div class="log-reply-text">✨ 生成: ${escHtml(log.reply_preview)}</div>`;
  } else if (event === 'chat_reply' || event === 'react_agent_done') {
    body += `<div class="log-user-text">👤 ${escHtml(log.raw_input || '')}</div>`;
    const latStr = log.latency_ms ? ` • ⚡ 耗時 ${log.latency_ms} ms` : '';
    const modelStr = log.model_used ? ` • 🤖 ${escHtml(log.model_used)}` : '';
    const toolsStr = (log.tools_invoked && log.tools_invoked.length) ? ` • 🛠️ [${log.tools_invoked.map(t => escHtml(String(t))).join(', ')}]` : '';
    const tidStr = log.trace_id ? ` • 🔗 <code>${escHtml(log.trace_id)}</code>` : '';
    if (log.intent) body += `<div class="log-intent">Intent: ${escHtml(log.intent)}${latStr}${modelStr}${toolsStr}${tidStr}</div>`;
    if (log.reply_preview) body += `<div class="log-reply-text">🤖 ${escHtml(log.reply_preview)}</div>`;
    if (log.cot_thought) {
      const formattedThought = escHtml(log.cot_thought).replace(/\n/g, '<br>');
      body += `<div class="log-thought-text">💭 ${formattedThought}</div>`;
    }
  } else if (event === 'interject') {
    const score = log.extra?.extra_score ?? log.score ?? '';
    const reason = log.extra?.extra_reasoning ?? log.reasoning ?? '';
    const reply = log.extra?.extra_reply ?? '';
    if (score !== '') body += `<div class="log-intent">Score: ${escHtml(score)}</div>`;
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
      <span class="log-badge ${badgeClass}">${escHtml(badgeLabel)}</span>
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
  if (state.autoRefreshTimer) {
    clearTimeout(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
  if (state.autoRefreshEnabled && state.currentView === 'logs') {
    scheduleNextPoll('autoRefreshTimer', loadLogs, 5000, 'logs');
  }
}

function toggleAutoRefresh() {
  const chk = document.getElementById('auto-refresh-chk');
  state.autoRefreshEnabled = chk ? chk.checked : true;
  if (state.autoRefreshEnabled) {
    startAutoRefresh();
  } else if (state.autoRefreshTimer) {
    clearTimeout(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
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
  const wrapper = document.getElementById('settings-wrapper');
  try {
    const resp = await fetch('/api/settings', {
      headers: getAuthHeaders(),
      credentials: 'same-origin',
    });
    if (!resp.ok) {
      if (resp.status === 401) {
        state.settingsData = {};
        if (wrapper) {
          wrapper.innerHTML = `
            <div class="placeholder-msg" style="padding: 3.5rem 1rem; text-align: center;">
              <div style="font-size: 2.2rem; margin-bottom: 12px;">🔒</div>
              <div style="font-size: 1.15rem; font-weight: 600; color: #fff; margin-bottom: 8px;">系統設定僅限管理員查看與編輯</div>
              <div style="font-size: 0.88rem; color: var(--text-muted, #888); margin-bottom: 20px;">請先點擊右上角「管理員登入」或下方按鈕輸入管理員密碼以解鎖控制台。</div>
              <button class="btn-primary" onclick="openLoginModal()" style="padding: 8px 24px; font-size: 0.95rem; cursor: pointer;">
                🔑 立即登入管理員
              </button>
            </div>
          `;
        }
      } else {
        if (wrapper) wrapper.innerHTML = '<div class="placeholder-msg">載入設定失敗，請檢查伺服器狀態。</div>';
      }
      return;
    }
    state.settingsData = await resp.json();
    state.dirtyKeys.clear();
    state.dirtyValues = {};
    updateChangedHint();
    renderSettings();
  } catch (e) {
    console.error('Failed to load settings:', e);
    if (wrapper) wrapper.innerHTML = '<div class="placeholder-msg">無法連線至設定 API，請確認伺服器已正常啟動。</div>';
  }
}

const TAB_CATEGORY_MAP = {
  'tab-ai': ['模型設定', 'LLM 參數', 'Chain-of-Thought'],
  'tab-persona': ['雪莉人設與心跳', '插話引擎', '打字模擬', '記憶與上下文'],
  'tab-voice': ['語音辨識與合成'],
  'tab-art': ['圖片生成', '二次元圖庫 API'],
  'tab-search': ['Web 爬蟲', '外部資訊檢索', 'YouTube 爬蟲'],
  'tab-advanced': ['任務佇列優先級', '音樂播放與隊列', '系統與網路', '深夜記憶整合', '健康監控', '氛圍系統', '速率限制', '後端連線設定', '模組與排程', '其他']
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
  if (!wrapper) return;

  if (!state.settingsData || Object.keys(state.settingsData).length === 0) {
    if (!state.isAdmin) {
      wrapper.innerHTML = `
        <div class="placeholder-msg" style="padding: 3.5rem 1rem; text-align: center;">
          <div style="font-size: 2.2rem; margin-bottom: 12px;">🔒</div>
          <div style="font-size: 1.15rem; font-weight: 600; color: #fff; margin-bottom: 8px;">系統設定僅限管理員查看與編輯</div>
          <div style="font-size: 0.88rem; color: var(--text-muted, #888); margin-bottom: 20px;">請先點擊右上角「管理員登入」或下方按鈕輸入管理員密碼以解鎖控制台。</div>
          <button class="btn-primary" onclick="openLoginModal()" style="padding: 8px 24px; font-size: 0.95rem; cursor: pointer;">
            🔑 立即登入管理員
          </button>
        </div>
      `;
    } else {
      wrapper.innerHTML = '<div class="placeholder-msg">正在載入設定...</div>';
    }
    return;
  }

  wrapper.innerHTML = '';

  const search = (state.settingsSearch || '').trim().toLowerCase();
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
    // 搜尋模式：跨分頁比對鍵名 / 標籤 / 說明
    if (search) {
      const hay = `${key} ${meta.label || ''} ${meta.description || ''}`.toLowerCase();
      isAllowed = hay.includes(search);
    }
    if (!isAllowed) continue;

    if (!categories[cat]) categories[cat] = [];
    const currentValue = (state.dirtyValues && key in state.dirtyValues) ? state.dirtyValues[key] : meta.value;
    categories[cat].push({ key, ...meta, value: currentValue });
  }

  if (search) {
    const total = Object.values(categories).reduce((n, arr) => n + arr.length, 0);
    const info = document.createElement('div');
    info.className = 'settings-match-hint';
    info.textContent = total
      ? `🔎 找到 ${total} 項符合「${state.settingsSearch}」的設定`
      : `🔎 找不到符合「${state.settingsSearch}」的設定`;
    wrapper.appendChild(info);
  }

  // Category display order preference
  const ORDER = [
    '模型設定', 'LLM 參數', 'Chain-of-Thought',
    '雪莉人設與心跳', '插話引擎', '打字模擬', '記憶與上下文',
    '語音辨識與合成',
    '圖片生成', '二次元圖庫 API',
    'Web 爬蟲', '外部資訊檢索', 'YouTube 爬蟲',
    '任務佇列優先級', '音樂播放與隊列', '系統與網路', '深夜記憶整合',
    '健康監控', '氛圍系統', '速率限制', '後端連線設定', '模組與排程', '其他',
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

async function onSoulNameChange(newSoulName) {
  markDirty('CURRENT_SOUL_NAME', newSoulName);
  try {
    const resp = await fetch(`/api/soul/content?file=${encodeURIComponent(newSoulName)}`, {
      headers: getAuthHeaders()
    });
    if (!resp.ok) {
      console.warn('載入人設檔案失敗:', resp.statusText);
      return;
    }
    const data = await resp.json();
    if (data.status === 'success' && typeof data.content === 'string') {
      const textarea = document.getElementById('inp-SOUL_CONTENT');
      if (textarea) {
        textarea.value = data.content;
        markDirty('SOUL_CONTENT', data.content);
      }
      const labelEl = document.querySelector('#si-SOUL_CONTENT .setting-label');
      if (labelEl) {
        labelEl.textContent = `當前人設內容預覽與編輯 (${newSoulName})`;
      }
    }
  } catch (err) {
    console.error('onSoulNameChange error:', err);
  }
}

function buildSettingItem({ key, value, label, description, type, min, max, step, options, item_type, restart_required }) {
  const keyAttr = `data-key="${escAttr(key)}"`;
  const descHtml = description
    ? `<div class="setting-desc">${escHtml(description)}</div>`
    : '';
  const restartBadge = restart_required
    ? '<span class="setting-badge-restart" title="此設定需重新啟動雪莉才會生效">需重啟</span>'
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
    const onchangeAttr = key === 'CURRENT_SOUL_NAME'
      ? `onchange="onSoulNameChange(this.value)"`
      : `onchange="markDirty('${escAttr(key)}', this.value)"`;
    control = `
      <select class="setting-input setting-select" id="inp-${escAttr(key)}" ${keyAttr}
        ${onchangeAttr}>
        ${optHtml}
      </select>`;
  } else if (type === 'model') {
    control = buildModelPicker(key, value);
  } else if (type === 'list' || (typeof type === 'string' && type.startsWith('list'))) {
    control = buildListEditor(key, value, item_type);
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
    const isMultiline = valStr.length > 50 || valStr.includes('\n') || key === 'SOUL_CONTENT' || key === 'CORE_PERSONA' || key.includes('PROMPT') || key.includes('PERSONA') || key.includes('SOUL');
    if (isMultiline) {
      const rows = key === 'SOUL_CONTENT' ? 12 : 5;
      control = `
        <textarea class="setting-textarea" rows="${rows}"
          id="inp-${escAttr(key)}" ${keyAttr}
          oninput="markDirty('${escAttr(key)}', this.value)">${escHtml(valStr)}</textarea>`;
    } else {
      control = `
        <input type="${type === 'url' ? 'url' : 'text'}" class="setting-input"
          id="inp-${escAttr(key)}" ${keyAttr} value="${escAttr(valStr)}"
          oninput="markDirty('${escAttr(key)}', this.value)">`;
    }
  }

  let displayLabel = label;
  if (key === 'SOUL_CONTENT') {
    const activeSoul = (state.dirtyValues && state.dirtyValues.CURRENT_SOUL_NAME)
      || (state.settingsData?.CURRENT_SOUL_NAME?.value)
      || 'sherry_default.md';
    displayLabel = `當前人設內容預覽與編輯 (${activeSoul})`;
  }

  return `
    <div class="setting-item" id="si-${escAttr(key)}">
      <div class="setting-label-row">
        <label class="setting-label" for="inp-${escAttr(key)}">${escHtml(displayLabel)}</label>
        <span class="setting-label-badges">
          ${restartBadge}
          <span class="setting-key">${escHtml(key)}</span>
        </span>
      </div>
      ${descHtml}
      ${control}
    </div>`;
}

function buildModelPicker(key, value) {
  const PREFIXES = ['freetoken', 'ollama', 'lms', 'vllm', 'openai', 'deepseek', 'gemini', 'openrouter', 'claude', 'groq'];
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

// ── List Editor (channel / role ID arrays) ──────────────
function buildListEditor(key, value, itemType) {
  const items = Array.isArray(value) ? value : [];
  const chips = items.map(v => listChipHtml(v)).join('');
  const isInt = (itemType || 'str') === 'int';
  const placeholder = isInt ? '輸入數字 ID 後按 Enter' : '輸入內容後按 Enter';
  return `
    <div class="list-editor" id="list-${escAttr(key)}" data-item-type="${escAttr(itemType || 'str')}">
      <div class="list-chips" id="list-chips-${escAttr(key)}">${chips}</div>
      <div class="list-add-row">
        <input type="text" class="setting-input list-add-input" id="list-input-${escAttr(key)}"
          placeholder="${escAttr(placeholder)}"
          onkeydown="onListInputKeydown(event, '${escAttr(key)}')">
        <button type="button" class="list-add-btn" onclick="addListItem('${escAttr(key)}')">＋ 新增</button>
      </div>
    </div>`;
}

function listChipHtml(v) {
  return `<span class="list-chip" data-val="${escAttr(v)}">
    <span class="list-chip-text">${escHtml(v)}</span>
    <button type="button" class="list-chip-remove" onclick="removeListItem(this)" aria-label="移除">×</button>
  </span>`;
}

function onListInputKeydown(event, key) {
  if (event.key === 'Enter') {
    event.preventDefault();
    addListItem(key);
  }
}

function addListItem(key) {
  const input = document.getElementById(`list-input-${key}`);
  const editor = document.getElementById(`list-${key}`);
  const chips = document.getElementById(`list-chips-${key}`);
  if (!input || !editor || !chips) return;
  const raw = input.value.trim();
  if (raw === '') return;

  const itemType = editor.dataset.itemType || 'str';
  if (itemType === 'int' && !/^-?\d+$/.test(raw)) {
    showToast('此欄位僅接受整數 ID', 'error');
    return;
  }
  const exists = Array.from(chips.querySelectorAll('.list-chip')).some(c => c.dataset.val === raw);
  if (exists) {
    showToast('此項目已存在', 'info');
    input.value = '';
    return;
  }
  chips.insertAdjacentHTML('beforeend', listChipHtml(raw));
  input.value = '';
  input.focus();
  syncListDirty(key);
}

function removeListItem(btn) {
  const editor = btn.closest('.list-editor');
  if (!editor) return;
  btn.closest('.list-chip')?.remove();
  syncListDirty(editor.id.replace(/^list-/, ''));
}

function collectListValues(key) {
  const editor = document.getElementById(`list-${key}`);
  const itemType = editor?.dataset.itemType || 'str';
  const chips = document.querySelectorAll(`#list-chips-${key} .list-chip`);
  return Array.from(chips).map(c => {
    const val = c.dataset.val;
    if (itemType === 'int') {
      // 保留字串傳遞，避免 JS IEEE 754 雙精度浮點數截斷 64-bit Discord Snowflake ID
      return String(val ?? '').trim();
    }
    if (itemType === 'float') return parseFloat(val);
    if (itemType === 'bool') return val === 'true';
    return val;
  });
}

function syncListDirty(key) {
  markDirty(key, collectListValues(key));
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
    sel.innerHTML = sanitizeHtml(optionsHtml);

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

  const isChanged = (Array.isArray(original) || Array.isArray(newValue))
    ? JSON.stringify(original) !== JSON.stringify(newValue)
    : newValue !== original;
  if (isChanged) {
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
  const n = state.dirtyKeys.size;
  if (n === 0) {
    hint.classList.add('hidden');
    hint.classList.remove('needs-restart');
    hint.textContent = '';
    return;
  }
  const restartCount = Array.from(state.dirtyKeys)
    .filter(k => state.settingsData[k]?.restart_required).length;
  hint.classList.remove('hidden');
  hint.classList.toggle('needs-restart', restartCount > 0);
  hint.textContent = restartCount > 0
    ? `● 有 ${n} 項未儲存的變更（${restartCount} 項需重啟生效）`
    : `● 有 ${n} 項未儲存的變更`;
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
    } else if (type === 'list' || (typeof type === 'string' && type.startsWith('list'))) {
      const editor = document.getElementById(`list-${key}`);
      if (editor) currentVal = collectListValues(key);
    } else if (type === 'float') {
      const el = document.getElementById(`inp-${key}`);
      if (el) currentVal = parseFloat(el.value) || 0;
    } else if (type === 'int') {
      const el = document.getElementById(`inp-${key}`);
      if (el) {
        const strVal = el.value.trim();
        if (strVal.length > 15 && /^-?\d+$/.test(strVal)) {
          currentVal = strVal;
        } else {
          currentVal = parseInt(strVal, 10) || 0;
        }
      }
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
      credentials: 'same-origin',
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      const restartCount = Array.from(state.dirtyKeys)
        .filter(k => state.settingsData[k]?.restart_required).length;
      if (restartCount > 0) {
        showToast(`設定已儲存！有 ${restartCount} 項需點擊「🔄 重啟雪莉」才會生效`, 'info');
      } else {
        showToast('設定已儲存！', 'success');
      }
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

// ── Settings Search & Reset ─────────────────────────────
function onSettingsSearch(value) {
  state.settingsSearch = value || '';
  renderSettings();
}

function clearSettingsSearch() {
  const el = document.getElementById('settings-search-input');
  if (el) el.value = '';
  state.settingsSearch = '';
  renderSettings();
}

function resetSettingsChanges() {
  if (state.dirtyKeys.size === 0) {
    showToast('目前沒有未儲存的變更', 'info');
    return;
  }
  const n = state.dirtyKeys.size;
  state.dirtyKeys.clear();
  state.dirtyValues = {};
  updateChangedHint();
  renderSettings();
  showToast(`已還原 ${n} 項未儲存的變更`, 'info');
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
      // R3：/api/logs/files 已收斂為管理員專用（且重啟後舊 Session 必然失效），
      // 改以公開的 /api/version 作為存活探測。
      const check = await fetch('/api/version', { cache: 'no-store' });
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
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escAttr(str) {
  // R4：統一轉義工具——屬性情境沿用 escHtml 同一實作
  return escHtml(str);
}
function sanitizeId(str) {
  return String(str).replace(/[^a-zA-Z0-9_-]/g, '_');
}

// R5：DOMPurify 統一淨化入口（vendored：/static/vendor/purify.min.js；離線可用）
// 政策：動態 HTML 進 innerHTML 前，一律經 escHtml 轉義或 sanitizeHtml 淨化；
// 例外（必含 inline 事件屬性之模板）另見清點文件。DOMPurify 未載入時降級為保守過濾。
function sanitizeHtml(html) {
  if (typeof DOMPurify !== 'undefined' && typeof DOMPurify.sanitize === 'function') {
    return DOMPurify.sanitize(String(html ?? ''), { USE_PROFILES: { html: true } });
  }
  return String(html ?? '')
    .replace(/<\s*\/?\s*script/gi, '&lt;script')
    .replace(/\son\w+\s*=/gi, ' data-blocked-handler=');
}
function formatBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(1) + ' MB';
}

function formatToUtc8(val) {
  if (val === null || val === undefined || val === '' || val === '—' || val === '--') return '—';
  if (typeof val === 'number') {
    const ms = val > 1e11 ? val : val * 1000;
    const d = new Date(ms);
    if (!isNaN(d.getTime())) {
      return d.toLocaleString('zh-TW', {
        timeZone: 'Asia/Taipei',
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false
      }).replace(/\//g, '-');
    }
  }
  const str = String(val).trim();
  if (str.includes('T') || str.endsWith('Z') || str.includes('+00:00') || str.includes('+08:00')) {
    const d = new Date(str);
    if (!isNaN(d.getTime())) {
      return d.toLocaleString('zh-TW', {
        timeZone: 'Asia/Taipei',
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false
      }).replace(/\//g, '-');
    }
  }
  return str;
}

// =========================================================
// SYSTEM METRICS & TASKS
// =========================================================
async function loadMetrics() {
  if (state.currentView !== 'metrics') return;
  if (state.inFlight.metrics) return;
  state.inFlight.metrics = true;

  try {
    const resp = await fetch('/api/system/metrics');
    if (!resp.ok) return;
    const data = await resp.json();

    // 1. CPU
    const cpu = data.cpu || {};
    const cpuP = Math.round(cpu.percent || 0);
    const cpuValEl = document.getElementById('cpu-percent-val');
    const cpuBadgeEl = document.getElementById('cpu-percent-badge');
    const cpuBarEl = document.getElementById('cpu-bar');
    const cpuCoresEl = document.getElementById('cpu-cores-text');
    const cpuFreqEl = document.getElementById('cpu-freq-text');

    if (cpuValEl) cpuValEl.textContent = cpuP + '%';
    if (cpuBadgeEl) cpuBadgeEl.textContent = cpuP + '%';
    if (cpuBarEl) cpuBarEl.style.width = Math.min(100, cpuP) + '%';
    if (cpuCoresEl) cpuCoresEl.textContent = `核心數: ${cpu.count || '--'}`;
    if (cpuFreqEl) cpuFreqEl.textContent = `時脈: ${cpu.freq_mhz || '--'} MHz`;

    // 2. RAM
    const ram = data.ram || {};
    const ramP = Math.round(ram.percent || 0);
    const ramValEl = document.getElementById('ram-usage-val');
    const ramBadgeEl = document.getElementById('ram-percent-badge');
    const ramTextEl = document.getElementById('ram-percent-text');
    const ramBarEl = document.getElementById('ram-bar');

    if (ramValEl) ramValEl.textContent = `${ram.used_gb || 0} GB / ${ram.total_gb || 0} GB`;
    if (ramBadgeEl) ramBadgeEl.textContent = ramP + '%';
    if (ramTextEl) ramTextEl.textContent = `使用率: ${ramP}%`;
    if (ramBarEl) ramBarEl.style.width = Math.min(100, ramP) + '%';

    // 3. Network
    const net = data.network || {};
    const sentRate = net.sent_rate_kbps > 1024 ? (net.sent_rate_kbps / 1024).toFixed(2) + ' MB/s' : (net.sent_rate_kbps || 0) + ' KB/s';
    const recvRate = net.recv_rate_kbps > 1024 ? (net.recv_rate_kbps / 1024).toFixed(2) + ' MB/s' : (net.recv_rate_kbps || 0) + ' KB/s';
    const netSentEl = document.getElementById('net-sent-rate');
    const netRecvEl = document.getElementById('net-recv-rate');
    const netTotSentEl = document.getElementById('net-total-sent');
    const netTotRecvEl = document.getElementById('net-total-recv');

    if (netSentEl) netSentEl.textContent = sentRate;
    if (netRecvEl) netRecvEl.textContent = recvRate;
    if (netTotSentEl) netTotSentEl.textContent = `總發送: ${net.total_sent_mb || 0} MB`;
    if (netTotRecvEl) netTotRecvEl.textContent = `總接收: ${net.total_recv_mb || 0} MB`;

    // 4. GPU
    const gpus = data.gpu || [];
    const gpuBadge = document.getElementById('gpu-status-badge');
    const gpuBody = document.getElementById('gpu-card-body');

    if (gpuBadge && gpuBody) {
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
        gpuBody.innerHTML = sanitizeHtml(html);
      }
    }

    // 5. Component Metrics (Admin Only)
    await loadComponentMetrics();
  } catch (e) {
    console.warn('Failed to fetch system metrics:', e);
  } finally {
    state.inFlight.metrics = false;
    if (state.currentView === 'metrics') {
      scheduleNextPoll('metricsTimer', loadMetrics, 2000, 'metrics');
    }
  }
}

// =========================================================
// ENTERPRISE AI APM & OBSERVABILITY CENTER
// =========================================================

const apmState = {
  components: {},
  kpi: {},
  selectedCategory: 'all',
  hideInactive: false,
  searchQuery: '',
};

const _APM_ICONS = {
  BOT_BRAIN: '🧠',
  CHAT: '💬',
  SEARCH: '🔍',
  STOCK: '📈',
  PIXIV: '🎨',
  DRAW: '🖌️',
  MUSIC: '🎵',
  SCHEDULE: '📅',
  SYSTEM_INSPECT: '💻',
  SUMMARY: '📑',
  MEMORY: '💾',
  PAINTER: '🖼️',
  DEEP_SEARCH: '🌐',
  FEED_SENTINEL: '🛡️',
  LIVESTREAM: '🔴',
  DND: '🎲',
  ROAST: '🔥',
  MARKET_PLUGIN: '📊',
};

function formatLatency(ms) {
  if (ms === undefined || ms === null || ms <= 0) return '0 ms';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function getLatencyColor(ms) {
  if (!ms || ms <= 0) return 'var(--text-muted, #888)';
  if (ms < 1000) return '#10b981';
  if (ms < 5000) return '#38bdf8';
  if (ms < 15000) return '#f59e0b';
  return '#ef4444';
}

function renderSparklineHtml(trend) {
  if (!trend || trend.length === 0) {
    return '<span style="color: var(--text-muted); font-size: 0.78rem;">— 無採樣 —</span>';
  }
  const maxVal = Math.max(...trend, 1);
  let bars = '';
  for (const val of trend) {
    const heightPercent = Math.max(8, Math.round((val / maxVal) * 100));
    const hPx = Math.max(3, Math.round((heightPercent / 100) * 20));
    let cls = 'sparkline-bar';
    if (val >= 15000) cls += ' danger';
    else if (val >= 5000) cls += ' slow';
    bars += `<div class="${cls}" style="height: ${hPx}px;" title="${formatLatency(val)}"></div>`;
  }
  return `<div class="sparkline-container" title="近 ${trend.length} 次耗時微型走勢">${bars}</div>`;
}

function setApmCategory(cat) {
  apmState.selectedCategory = cat;
  document.querySelectorAll('.apm-pill-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-cat') === cat);
  });
  renderApmGrid();
}

function toggleApmHideInactive() {
  const toggle = document.getElementById('apm-hide-inactive-toggle');
  if (toggle) {
    apmState.hideInactive = toggle.checked;
    renderApmGrid();
  }
}

function filterApmSearch() {
  const input = document.getElementById('apm-search-input');
  if (input) {
    apmState.searchQuery = (input.value || '').trim().toLowerCase();
    renderApmGrid();
  }
}

async function loadComponentMetrics() {
  const container = document.getElementById('component-metrics-container');
  const notice = document.getElementById('component-metrics-auth-notice');
  const actions = document.getElementById('component-metrics-actions');
  const tbody = document.getElementById('component-metrics-tbody');
  if (!container || !tbody) return;

  if (!state.isAdmin) {
    if (notice) notice.classList.remove('hidden');
    container.classList.add('hidden');
    if (actions) actions.classList.add('hidden');
    return;
  }

  try {
    const headers = typeof getAuthHeaders === 'function' ? getAuthHeaders() : {};
    const resp = await fetch('/api/system/components/metrics', {
      headers,
      credentials: 'same-origin',
    });
    if (resp.status === 401) {
      updateAuthUI(false);
      return;
    }
    if (!resp.ok) return;

    const data = await resp.json();
    if (notice) notice.classList.add('hidden');
    container.classList.remove('hidden');
    if (actions) actions.classList.remove('hidden');

    apmState.components = data.components || {};
    apmState.kpi = data.kpi || {};

    // 更新頂部 5 大全景 KPI 卡片
    const kpi = apmState.kpi;
    const healthEl = document.getElementById('apm-kpi-health-val');
    const healthSubEl = document.getElementById('apm-kpi-health-sub');
    if (healthEl) healthEl.textContent = `${(kpi.health_score ?? 100.0).toFixed(1)}%`;
    if (healthSubEl) {
      const errs = kpi.total_errors || 0;
      healthSubEl.textContent = errs === 0 ? '全組件健康運行 (0 異常)' : `${errs} 次異常需關注`;
      healthSubEl.style.color = errs === 0 ? '#00e676' : '#ff5252';
    }

    const callsEl = document.getElementById('apm-kpi-calls-val');
    const callsSubEl = document.getElementById('apm-kpi-calls-sub');
    if (callsEl) callsEl.textContent = `${(kpi.total_calls || 0).toLocaleString()} 次`;
    if (callsSubEl) callsSubEl.textContent = `${kpi.total_rpm || 0.0} RPM · 活躍: ${kpi.active_components_count || 0} / ${kpi.total_components_count || 18}`;

    const latEl = document.getElementById('apm-kpi-latency-val');
    const latSubEl = document.getElementById('apm-kpi-latency-sub');
    if (latEl) {
      latEl.textContent = formatLatency(kpi.global_p95_ms);
      latEl.style.color = getLatencyColor(kpi.global_p95_ms);
    }
    if (latSubEl) latSubEl.textContent = `平均耗時: ${formatLatency(kpi.global_avg_ms)}`;

    const tokEl = document.getElementById('apm-kpi-tokens-val');
    const tokSubEl = document.getElementById('apm-kpi-tokens-sub');
    if (tokEl) tokEl.textContent = `${(kpi.total_tokens || 0).toLocaleString()}`;
    if (tokSubEl) tokSubEl.textContent = `P: ${(kpi.prompt_tokens || 0).toLocaleString()} | C: ${(kpi.completion_tokens || 0).toLocaleString()} (~$${(kpi.total_cost_usd || 0).toFixed(3)})`;

    const concEl = document.getElementById('apm-kpi-concurrency-val');
    const concSubEl = document.getElementById('apm-kpi-concurrency-sub');
    if (concEl) concEl.textContent = `${kpi.active_tasks || 0}`;
    if (concSubEl) concSubEl.textContent = (kpi.active_tasks || 0) > 0 ? `${kpi.active_tasks} 個任務並行處理中` : '系統工作執行緒閒置中';

    // 更新各分類數量計數器
    updateApmCategoryCounts();

    // 渲染表格
    renderApmGrid();
  } catch (err) {
    console.warn('Failed to load component metrics:', err);
  }
}

function updateApmCategoryCounts() {
  const comps = Object.values(apmState.components);
  const countAll = comps.length;
  const countCoreEngine = comps.filter(c => c.category === 'core_engine').length;
  const countCoreTool = comps.filter(c => c.category === 'core_tool').length;
  const countModPlugin = comps.filter(c => c.category === 'mod_plugin').length;

  const elAll = document.getElementById('count-all');
  const elEngine = document.getElementById('count-core_engine');
  const elTool = document.getElementById('count-core_tool');
  const elPlugin = document.getElementById('count-mod_plugin');

  if (elAll) elAll.textContent = countAll;
  if (elEngine) elEngine.textContent = countCoreEngine;
  if (elTool) elTool.textContent = countCoreTool;
  if (elPlugin) elPlugin.textContent = countModPlugin;
}

function renderApmGrid() {
  const tbody = document.getElementById('component-metrics-tbody');
  if (!tbody) return;

  const components = apmState.components || {};
  let keys = Object.keys(components);

  if (keys.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; padding: 2.5rem; color: var(--text-muted);">尚未記錄到任何組件執行紀錄。</td></tr>';
    return;
  }

  // 依調用次數降序排列
  keys.sort((a, b) => (components[b].call_count || 0) - (components[a].call_count || 0));

  // 過濾分類
  if (apmState.selectedCategory !== 'all') {
    keys = keys.filter(k => components[k].category === apmState.selectedCategory);
  }

  // 過濾閒置
  if (apmState.hideInactive) {
    keys = keys.filter(k => (components[k].call_count || 0) > 0);
  }

  // 搜尋過濾
  if (apmState.searchQuery) {
    keys = keys.filter(k => k.toLowerCase().includes(apmState.searchQuery));
  }

  if (keys.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; padding: 2rem; color: var(--text-muted);">無符合篩選條件的組件。</td></tr>';
    return;
  }

  let html = '';
  for (const key of keys) {
    const c = components[key];
    const icon = _APM_ICONS[key] || '⚙️';
    const failRate = c.call_count > 0 ? ((c.error_count / c.call_count) * 100).toFixed(1) + '%' : '0.0%';
    const p50Str = formatLatency(c.p50_latency_ms);
    const p95Str = formatLatency(c.p95_latency_ms);
    const maxStr = formatLatency(c.max_latency_ms);

    // 狀態點
    let dotColor = '#64748b'; // 灰色 idle
    let dotClass = '';
    if (c.active_tasks > 0) {
      dotColor = '#00e5ff';
      dotClass = 'pulse-dot';
    } else if (c.call_count > 0) {
      dotColor = '#10b981';
    }

    // 類別徽章
    let catText = '外掛';
    let catBadgeClass = 'badge-neutral';
    if (c.category === 'core_engine') {
      catText = 'AI 核心';
      catBadgeClass = 'badge-chat';
    } else if (c.category === 'core_tool') {
      catText = '工具';
      catBadgeClass = 'badge-system';
    }

    // Token 進度條
    const totTok = (c.prompt_tokens || 0) + (c.completion_tokens || 0);
    const pTok = c.prompt_tokens || 0;
    const cTok = c.completion_tokens || 0;
    let pWidth = 0;
    let cWidth = 0;
    if (totTok > 0) {
      pWidth = Math.round((pTok / totTok) * 100);
      cWidth = 100 - pWidth;
    }

    // SLO 健康條樣式
    const healthLvl = (c.health_level || 'HEALTHY').toLowerCase();
    const healthScore = c.health_score ?? 100.0;

    // 狀態標籤
    const statusText = c.active_tasks > 0 ? '執行中' : (c.call_count > 0 ? '在席' : '閒置');
    const badgeClass = c.active_tasks > 0 ? 'badge-primary' : (c.error_count > 0 ? 'badge-warn' : 'badge-ok');

    html += `
      <tr style="border-bottom: 1px solid rgba(255,255,255,0.04);">
        <!-- 組件名稱 -->
        <td style="padding: 12px 14px;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${dotColor};" class="${dotClass}"></span>
            <span style="font-size: 1.1rem;">${icon}</span>
            <div>
              <div style="font-weight: 700; color: #fff; font-size: 0.92rem;">${escHtml(key)}</div>
              <span class="task-type-badge ${catBadgeClass}" style="font-size: 0.68rem; padding: 1px 6px; margin-top: 2px;">${catText}</span>
            </div>
          </div>
        </td>

        <!-- 調用吞吐 -->
        <td style="padding: 12px 14px;">
          <div style="font-weight: 600; color: #eee;">${c.call_count} 次</div>
          <div style="font-size: 0.75rem; color: var(--text-muted);">${c.rpm || 0.0} RPM</div>
        </td>

        <!-- 延遲分佈 -->
        <td style="padding: 12px 14px;">
          <div style="display: flex; gap: 8px; font-size: 0.82rem;">
            <span title="P50 中位數" style="color: ${getLatencyColor(c.p50_latency_ms)}; font-weight: 600;">${p50Str}</span>
            <span style="color: var(--text-muted);">/</span>
            <span title="P95 尾部延遲" style="color: ${getLatencyColor(c.p95_latency_ms)}; font-weight: 600;">${p95Str}</span>
            <span style="color: var(--text-muted);">/</span>
            <span title="Max 極大耗時" style="color: ${getLatencyColor(c.max_latency_ms)}; font-size: 0.78rem; color: #888;">${maxStr}</span>
          </div>
          <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 2px;">P50 / P95 / Max</div>
        </td>

        <!-- 近 15 次走勢 Sparkline -->
        <td style="padding: 12px 14px;">
          ${renderSparklineHtml(c.recent_latencies_trend)}
        </td>

        <!-- Tokens -->
        <td style="padding: 12px 14px;">
          <div style="font-size: 0.84rem; font-weight: 600; color: #ffd740;">
            ${totTok > 0 ? totTok.toLocaleString() : '<span style="color:var(--text-muted);">0</span>'}
          </div>
          ${totTok > 0 ? `
            <div class="token-progress" title="Prompt: ${pTok.toLocaleString()} | Comp: ${cTok.toLocaleString()}">
              <div class="token-prompt-fill" style="width: ${pWidth}%;"></div>
              <div class="token-comp-fill" style="width: ${cWidth}%;"></div>
            </div>
            <div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 2px;">${pTok}P / ${cTok}C ${c.tps > 0 ? `· ${c.tps} t/s` : ''}</div>
          ` : '<div style="font-size: 0.7rem; color: var(--text-muted);">—</div>'}
        </td>

        <!-- 可用性 SLO -->
        <td style="padding: 12px 14px;">
          <div style="display: flex; justify-content: space-between; align-items: center; width: 95px;">
            <span style="font-weight: 600; font-size: 0.82rem; color: ${healthScore >= 98 ? '#10b981' : (healthScore >= 90 ? '#f59e0b' : '#ef4444')};">${healthScore.toFixed(1)}%</span>
            <span style="font-size: 0.72rem; color: ${c.error_count > 0 ? '#ef4444' : 'var(--text-muted)'};">${c.error_count > 0 ? `(${c.error_count} 錯)` : '健全'}</span>
          </div>
          <div class="slo-bar-bg" style="width: 95px;">
            <div class="slo-bar-fill ${healthLvl}" style="width: ${healthScore}%;"></div>
          </div>
        </td>

        <!-- 狀態 -->
        <td style="padding: 12px 14px;">
          <span class="task-type-badge ${badgeClass}" style="padding: 2px 8px; font-size: 0.75rem;">${statusText}</span>
          ${c.active_tasks > 0 ? `<div style="font-size: 0.72rem; color: #00e5ff; margin-top: 2px;">${c.active_tasks} 任務進行中</div>` : ''}
        </td>

        <!-- 鏈路診斷按鈕 -->
        <td style="padding: 12px 14px; text-align: center;">
          <button class="btn-update" onclick="openApmTraceModal('${escHtml(key)}')" style="padding: 3px 10px; font-size: 0.75rem; border-radius: 6px;">
            🔍 鏈路詳情
          </button>
        </td>
      </tr>
    `;
  }
  tbody.innerHTML = html;
}

function openApmTraceModal(componentKey) {
  const modal = document.getElementById('apm-trace-modal');
  if (!modal) return;

  const c = apmState.components[componentKey];
  if (!c) return;

  const icon = _APM_ICONS[componentKey] || '⚙️';
  document.getElementById('apm-modal-icon').textContent = icon;
  document.getElementById('apm-modal-title').textContent = componentKey;

  const catBadge = document.getElementById('apm-modal-cat-badge');
  if (catBadge) catBadge.textContent = c.category;

  const healthBadge = document.getElementById('apm-modal-health-badge');
  if (healthBadge) {
    healthBadge.textContent = c.health_level || 'HEALTHY';
    healthBadge.className = `task-type-badge ${c.health_level === 'HEALTHY' ? 'badge-ok' : 'badge-warn'}`;
  }

  // Mini stats in modal
  document.getElementById('apm-modal-calls').textContent = `${c.call_count} 次`;
  document.getElementById('apm-modal-avg').textContent = formatLatency(c.avg_latency_ms);
  document.getElementById('apm-modal-p50').textContent = formatLatency(c.p50_latency_ms);
  document.getElementById('apm-modal-p95').textContent = formatLatency(c.p95_latency_ms);
  document.getElementById('apm-modal-max').textContent = formatLatency(c.max_latency_ms);
  document.getElementById('apm-modal-tokens').textContent = `${(c.total_tokens || 0).toLocaleString()} (~$${(c.estimated_cost_usd || 0).toFixed(4)})`;

  // Traces table
  const tracesTbody = document.getElementById('apm-modal-traces-tbody');
  if (tracesTbody) {
    const traces = c.recent_traces || [];
    if (traces.length === 0) {
      tracesTbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 1.5rem; color: #888;">暫無近期執行鏈路資料。</td></tr>';
    } else {
      // 倒序
      const reversed = [...traces].reverse();
      let html = '';
      for (const tr of reversed) {
        const timeStr = tr.timestamp ? new Date(tr.timestamp * 1000).toLocaleTimeString() : '—';
        const statusBadge = tr.success ? '<span class="task-type-badge badge-ok" style="font-size:0.7rem; padding: 1px 6px;">成功</span>' : '<span class="task-type-badge badge-warn" style="font-size:0.7rem; padding: 1px 6px;">失敗</span>';
        const tidStr = tr.trace_id && tr.trace_id !== '-' ? `<span style="font-family: monospace; color: #38bdf8;">${escHtml(tr.trace_id)}</span>` : '<span style="color:#666;">-</span>';
        const tokStr = (tr.total_tokens > 0) ? `${tr.prompt_tokens} / ${tr.completion_tokens}` : '—';
        const errStr = tr.error_msg ? `<span style="color: #ef4444;" title="${escHtml(tr.error_msg)}">${escHtml(tr.error_msg.substring(0, 35))}...</span>` : '<span style="color:#666;">—</span>';

        html += `
          <tr style="border-bottom: 1px solid rgba(255,255,255,0.04);">
            <td style="padding: 7px 12px; color: #aaa;">${timeStr}</td>
            <td style="padding: 7px 12px;">${tidStr}</td>
            <td style="padding: 7px 12px; font-weight: 600; color: ${getLatencyColor(tr.latency_ms)};">${formatLatency(tr.latency_ms)}</td>
            <td style="padding: 7px 12px; color: #ffd740;">${tokStr}</td>
            <td style="padding: 7px 12px;">${statusBadge}</td>
            <td style="padding: 7px 12px;">${errStr}</td>
          </tr>
        `;
      }
      tracesTbody.innerHTML = html;
    }
  }

  modal.classList.remove('hidden');
}

function closeApmTraceModal() {
  const modal = document.getElementById('apm-trace-modal');
  if (modal) modal.classList.add('hidden');
}

async function resetComponentMetrics() {
  if (!confirm('確定要重置所有組件的效能統計數據嗎？')) return;
  try {
    const headers = typeof getAuthHeaders === 'function' ? getAuthHeaders() : {};
    const resp = await fetch('/api/system/components/metrics/reset', {
      method: 'POST',
      headers,
      credentials: 'same-origin',
    });
    if (resp.ok) {
      await loadComponentMetrics();
    } else {
      alert('重置失敗：權限不足或系統錯誤');
    }
  } catch (err) {
    alert('重置失敗：' + err);
  }
}

// =========================================================
// SELF MONITORING & DELAY AUDIT CONTROLLER
// =========================================================
let selfMonCurrentFilter = 'ALL';
let selfMonRecordsCache = [];

async function loadSelfMonitoringData() {
  const headers = typeof getAuthHeaders === 'function' ? getAuthHeaders() : {};

  // 1. 載入 KPI 統計
  try {
    const statsRes = await fetch('/api/system/audit/kpi', {
      headers,
      credentials: 'same-origin',
    });
    if (statsRes.ok) {
      const statsJson = await statsRes.json();
      const d = statsJson.kpi || statsJson.data;
      if (statsJson.status === 'success' && d) {
        const elTotal = document.getElementById('stat-total-audits');
        const elAvg = document.getElementById('stat-avg-latency');
        const elWarn = document.getElementById('stat-warning-count');
        const elCrit = document.getElementById('stat-critical-count');
        if (elTotal) elTotal.textContent = d.total_audits || 0;
        if (elAvg) elAvg.textContent = (d.avg_latency_ms / 1000.0).toFixed(1) + 's';
        if (elWarn) elWarn.textContent = d.warning_count || 0;
        if (elCrit) elCrit.textContent = (d.critical_count || 0) + (d.blocking_count || 0);
      }
    }
  } catch (err) {
    console.warn('[SelfMonitoring] 載入統計失敗:', err);
  }

  // 2. 載入稽核紀錄列表
  try {
    let url = '/api/system/audit/records?limit=60';
    if (selfMonCurrentFilter === 'WARNING') {
      url += '&severity=WARNING';
    } else if (selfMonCurrentFilter === 'CRITICAL') {
      url += '&severity=CRITICAL';
    } else if (selfMonCurrentFilter === 'SLOW_TTFT_WARNING') {
      url += '&type=SLOW_TTFT_WARNING';
    } else if (selfMonCurrentFilter === 'ACTION_LOOP') {
      url += '&type=ACTION_LOOP';
    } else if (selfMonCurrentFilter === 'QUALITY_ANOMALY') {
      url += '&type=QUALITY_ANOMALY';
    } else if (selfMonCurrentFilter === 'INFERENCE_FAILURE') {
      url += '&type=INFERENCE_FAILURE';
    } else if (selfMonCurrentFilter === 'SYSTEM_BLOCKING') {
      url += '&type=SYSTEM_BLOCKING';
    } else if (selfMonCurrentFilter === 'MANUAL_CHECK') {
      url += '&type=MANUAL_CHECK';
    }

    const recRes = await fetch(url, {
      headers,
      credentials: 'same-origin',
    });
    const tbody = document.getElementById('self-mon-table-body');
    const updatedEl = document.getElementById('self-mon-last-updated');

    if (updatedEl) {
      const now = new Date();
      updatedEl.textContent = `最後更新: ${now.toLocaleTimeString()}`;
    }

    if (recRes.ok) {
      const recJson = await recRes.json();
      const records = recJson.records || recJson.data || [];
      if (recJson.status === 'success' && Array.isArray(records)) {
        selfMonRecordsCache = records;
        renderSelfMonTable(selfMonRecordsCache);
      } else if (tbody) {
        tbody.innerHTML = `<tr><td colspan="7" style="padding: 30px; text-align: center; color: #f87171;">資料格式異常: ${escHtml(recJson.message || '無效的紀錄清單結構')}</td></tr>`;
      }
    } else if (tbody) {
      tbody.innerHTML = `<tr><td colspan="7" style="padding: 30px; text-align: center; color: #f87171;">載入紀錄失敗 (HTTP ${recRes.status})</td></tr>`;
    }
  } catch (err) {
    console.warn('[SelfMonitoring] 載入紀錄失敗:', err);
    const tbody = document.getElementById('self-mon-table-body');
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="7" style="padding: 30px; text-align: center; color: #f87171;">載入紀錄異常: ${escHtml(err.message || err)}</td></tr>`;
    }
  }
}

function renderSelfMonTable(records) {
  const tbody = document.getElementById('self-mon-table-body');
  if (!tbody) return;

  if (!records || records.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="padding: 35px; text-align: center; color: #64748b;">暫無符合條件之技術稽核紀錄</td></tr>`;
    return;
  }

  let html = '';
  records.forEach((r) => {
    let sevBadge = '';
    if (r.severity === 'CRITICAL') {
      sevBadge = '<span style="background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 700;">CRITICAL</span>';
    } else if (r.severity === 'WARNING') {
      sevBadge = '<span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 700;">WARNING</span>';
    } else {
      sevBadge = '<span style="background: rgba(45, 212, 191, 0.2); color: #2dd4bf; border: 1px solid rgba(45, 212, 191, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 700;">INFO</span>';
    }

    const latStr = r.latency_ms > 0 ? (r.latency_ms / 1000.0).toFixed(1) + 's' : '-';
    const preview = r.root_cause ? r.root_cause.slice(0, 60) + (r.root_cause.length > 60 ? '...' : '') : '無根因說明';

    html += `
      <tr style="border-bottom: 1px solid rgba(255,255,255,0.04);">
        <td style="padding: 12px 16px; white-space: nowrap; font-family: monospace; font-size: 0.8rem; color: #94a3b8;">${escHtml(r.created_at)}</td>
        <td style="padding: 12px 16px; white-space: nowrap;">${sevBadge}</td>
        <td style="padding: 12px 16px; white-space: nowrap; font-weight: 600; color: #f1f5f9;">${escHtml(r.incident_type)}</td>
        <td style="padding: 12px 16px; white-space: nowrap; color: #38bdf8; font-weight: 600;">${latStr}</td>
        <td style="padding: 12px 16px; white-space: nowrap;"><code style="background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px; color: #a5b4fc; font-size: 0.8rem;">${escHtml(r.bottleneck_category || 'UNKNOWN')}</code></td>
        <td style="padding: 12px 16px; color: #cbd5e1; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escHtml(preview)}</td>
        <td style="padding: 12px 16px; text-align: center; white-space: nowrap;">
          <button onclick="openAuditDetail(${r.id})" class="btn btn-secondary btn-sm" style="padding: 3px 8px; font-size: 0.75rem;">
            查看報告
          </button>
        </td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
}

function filterSelfMonRecords(filterType, btnEl) {
  selfMonCurrentFilter = filterType;
  const filterBtns = document.querySelectorAll('#self-mon-filters .filter-tab');
  filterBtns.forEach((b) => {
    b.style.background = '#334155';
    b.style.color = '#cbd5e1';
  });
  if (btnEl) {
    btnEl.style.background = '#3b82f6';
    btnEl.style.color = '#fff';
  }
  loadSelfMonitoringData();
}

async function triggerManualAudit() {
  const btn = document.getElementById('btn-self-mon-trigger');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '⏳ 正在執行稽核剖析...';
  }

  try {
    const headers = typeof getAuthHeaders === 'function' ? getAuthHeaders() : {};
    headers['Content-Type'] = 'application/json';

    const res = await fetch('/api/system/audit/diagnose', {
      method: 'POST',
      headers,
    });

    const json = await res.json();
    if (res.status === 401 || res.status === 403) {
      alert('🔒 手動觸發技術稽核需要管理員權限，請先登入管理員帳號。');
      return;
    }

    if (res.ok && json.status === 'success') {
      alert('✅ 技術稽核完成並已存入資料庫！');
      await loadSelfMonitoringData();
    } else {
      alert(`❌ 稽核失敗: ${json.message || '未知錯誤'}`);
    }
  } catch (err) {
    alert(`❌ 網路異常: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '🚀 立即執行技術稽核';
    }
  }
}

async function openAuditDetail(recordId) {
  const modal = document.getElementById('self-mon-detail-modal');
  const content = document.getElementById('modal-audit-content');
  if (!modal || !content) return;

  modal.style.display = 'flex';
  content.innerHTML = '<div style="padding: 30px; text-align: center; color: #94a3b8;">正在載入稽核報告...</div>';

  try {
    const headers = typeof getAuthHeaders === 'function' ? getAuthHeaders() : {};
    const res = await fetch(`/api/system/audit/records/${recordId}`, { headers });

    if (res.status === 401 || res.status === 403) {
      content.innerHTML = `
        <div style="padding: 35px 20px; text-align: center; color: #f87171;">
          <div style="font-size: 3rem; margin-bottom: 12px;">🔒</div>
          <h3 style="margin: 0 0 10px 0; color: #f87171; font-size: 1.25rem; font-weight: 700;">需要管理員登入權限</h3>
          <p style="color: #cbd5e1; font-size: 0.9rem; line-height: 1.6; max-width: 480px; margin: 0 auto 20px auto;">
            自我監控之完整根因剖析、關鍵日誌跡證與系統快照涉及後台核心管理與資安資訊。<br>
            一般訪客僅可檢視清單概覽，請登入管理員帳號以解鎖詳細報告。
          </p>
        </div>
      `;
      return;
    }

    if (!res.ok) {
      content.innerHTML = `<div style="color: #f87171; text-align: center; padding: 20px;">無法讀取紀錄 (HTTP ${res.status})</div>`;
      return;
    }
    const json = await res.json();
    const r = json.record;
    if (!r) {
      content.innerHTML = '<div style="color: #f87171; text-align: center; padding: 20px;">查無此紀錄。</div>';
      return;
    }

    const recsHtml = Array.isArray(r.recommendations) && r.recommendations.length > 0
      ? r.recommendations.map((item) => `<li style="margin-bottom: 4px;">${escHtml(item)}</li>`).join('')
      : '<li>無具體建議</li>';

    const evidenceHtml = Array.isArray(r.key_evidence) && r.key_evidence.length > 0
      ? r.key_evidence.map((item) => `<pre style="background: #0f172a; padding: 8px 12px; border-radius: 6px; margin: 4px 0; font-size: 0.8rem; overflow-x: auto; color: #fca5a5; border: 1px solid #334155;"><code>${escHtml(item)}</code></pre>`).join('')
      : '<span style="color: #64748b;">無特定關鍵日誌證據</span>';

    const snap = r.log_snapshot || {};
    const snapSnippet = (snap.bot_log_snippet && snap.runtime_event_snippet)
      ? `--- Bot Log ---\n${snap.bot_log_snippet}\n\n--- Runtime Events ---\n${snap.runtime_event_snippet}`
      : (snap.bot_log_snippet || snap.runtime_event_snippet || (typeof snap === 'string' ? snap : (Object.keys(snap).length > 0 ? JSON.stringify(snap, null, 2) : '(無日誌節錄)')));
    const extraMetrics = r.extra_metrics || {};
    let metricsHtml = '';
    const metricKeys = Object.keys(extraMetrics);
    if (metricKeys.length > 0) {
      const items = metricKeys.map((k) => `<li><strong>${escHtml(k)}</strong>: <code style="color: #38bdf8;">${escHtml(typeof extraMetrics[k] === 'object' ? JSON.stringify(extraMetrics[k]) : String(extraMetrics[k]))}</code></li>`).join('');
      metricsHtml = `
        <div style="background: rgba(30, 41, 59, 0.7); border-left: 4px solid #818cf8; border-radius: 6px; padding: 14px;">
          <h4 style="margin: 0 0 8px 0; color: #a5b4fc; font-size: 1rem;">📊 微觀耗時與度量指標 (Micro Metrics)</h4>
          <ul style="margin: 0; padding-left: 20px; color: #e2e8f0; line-height: 1.6;">
            ${items}
          </ul>
        </div>
      `;
    }

    content.innerHTML = `
      <div style="background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 14px; display: flex; flex-wrap: wrap; gap: 15px; font-size: 0.85rem;">
        <div><strong>時間</strong>：<span style="color: #cbd5e1;">${escHtml(r.created_at)}</span></div>
        <div><strong>類型</strong>：<span style="color: #f8fafc; font-weight: 600;">${escHtml(r.incident_type)}</span></div>
        <div><strong>耗時</strong>：<span style="color: #38bdf8; font-weight: 600;">${(r.latency_ms / 1000.0).toFixed(1)}s (${r.latency_ms}ms)</span></div>
        <div><strong>Trace ID</strong>：<code style="color: #a5b4fc;">${escHtml(r.trace_id || '-')}</code></div>
        <div><strong>觸發原因</strong>：<span style="color: #e2e8f0;">${escHtml(r.trigger_reason)}</span></div>
      </div>

      <div style="background: rgba(30, 41, 59, 0.7); border-left: 4px solid #3b82f6; border-radius: 6px; padding: 14px;">
        <h4 style="margin: 0 0 8px 0; color: #93c5fd; font-size: 1rem; display: flex; align-items: center; gap: 6px;">
          🧠 雲端模型技術稽核診斷 (${escHtml(r.model_name || 'Cloud Search Model')})
        </h4>
        <div style="margin-bottom: 6px;"><strong>瓶頸分類</strong>：<code style="background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #fde047;">${escHtml(r.bottleneck_category)}</code></div>
        <div><strong>根因剖析</strong>：<span style="color: #f1f5f9; line-height: 1.5;">${escHtml(r.root_cause)}</span></div>
      </div>

      ${metricsHtml}

      <div style="background: rgba(30, 41, 59, 0.7); border-left: 4px solid #10b981; border-radius: 6px; padding: 14px;">
        <h4 style="margin: 0 0 8px 0; color: #6ee7b7; font-size: 1rem;">💡 稽核改善與處置建議</h4>
        <ul style="margin: 0; padding-left: 20px; color: #e2e8f0; line-height: 1.6;">
          ${recsHtml}
        </ul>
      </div>

      <div style="background: rgba(30, 41, 59, 0.7); border-left: 4px solid #f59e0b; border-radius: 6px; padding: 14px;">
        <h4 style="margin: 0 0 8px 0; color: #fcd34d; font-size: 1rem;">📝 關鍵日誌跡證</h4>
        ${evidenceHtml}
      </div>

      <div style="background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 12px;">
        <details>
          <summary style="cursor: pointer; color: #94a3b8; font-weight: 600; font-size: 0.85rem;">查看 5 分鐘運行日誌快照截錄 (點擊展開)</summary>
          <pre style="margin-top: 10px; padding: 10px; background: #020617; border-radius: 4px; font-size: 0.75rem; color: #cbd5e1; max-height: 250px; overflow-y: auto; white-space: pre-wrap;"><code>${escHtml(snapSnippet)}</code></pre>
        </details>
      </div>
    `;
  } catch (err) {
    content.innerHTML = sanitizeHtml(`<div style="color: #f87171;">載入異常: ${escHtml(err.message)}</div>`);
  }
}

function closeSelfMonModal() {
  const modal = document.getElementById('self-mon-detail-modal');
  if (modal) modal.style.display = 'none';
}

async function loadRecoveryStatus() {
  const badgeEl = document.getElementById('recovery-enabled-badge');
  const provEl = document.getElementById('recovery-providers-count');
  const actEl = document.getElementById('recovery-active-snapshots');
  const quarEl = document.getElementById('recovery-quarantined-snapshots');
  const savedEl = document.getElementById('recovery-last-saved');
  if (!badgeEl) return;

  try {
    const resp = await fetch('/api/recovery/status', {
      headers: getAuthHeaders(),
      credentials: 'same-origin',
    });
    if (resp.ok) {
      const data = await resp.json();
      const enabled = data.enabled !== false;
      badgeEl.textContent = enabled ? '🟢 運作中' : '⚪ 已停用';
      badgeEl.style.color = enabled ? '#4ade80' : '#94a3b8';
      badgeEl.style.background = enabled ? 'rgba(34, 197, 94, 0.15)' : 'rgba(148, 163, 184, 0.15)';
      badgeEl.style.borderColor = enabled ? 'rgba(34, 197, 94, 0.3)' : 'rgba(148, 163, 184, 0.3)';

      if (provEl) provEl.textContent = `${(data.registered_providers || []).length} 個`;
      const snap = data.snapshots || {};
      if (actEl) actEl.textContent = `${snap.active ?? 0} 份`;
      if (quarEl) quarEl.textContent = `${snap.quarantined ?? 0} 份`;
      if (savedEl) {
        if (snap.last_saved_at) {
          savedEl.textContent = formatToUtc8(snap.last_saved_at);
        } else {
          savedEl.textContent = '尚無紀錄';
        }
      }
    } else if (resp.status === 401) {
      badgeEl.textContent = '🔒 未認證';
      badgeEl.style.color = '#f87171';
    }
  } catch (e) {
    console.error('loadRecoveryStatus error:', e);
  }
}

async function loadTasks() {
  if (state.currentView !== 'tasks') return;
  if (state.inFlight.tasks) return;
  state.inFlight.tasks = true;

  const bContainer = document.getElementById('brain-queue-container');
  const aiContainer = document.getElementById('ai-tasks-container');

  const fetchBrain = async () => {
    if (!bContainer) return;
    try {
      const bResp = await fetch('/api/brain/queue', { headers: getAuthHeaders(), credentials: 'same-origin' });
      let bHtml = '';
      if (bResp.status === 401) {
        bHtml = '<div class="placeholder-msg" style="padding: 1.5rem 1rem;">請先登入管理員以檢視大腦佇列 🔒</div>';
      } else if (!bResp.ok) {
        bHtml = `<div class="placeholder-msg">載入大腦佇列失敗 (HTTP ${bResp.status})</div>`;
      } else {
        const bData = await bResp.json();
        const bQueue = bData.queue || [];
        if (bQueue.length === 0) {
          bHtml = '<div class="placeholder-msg" style="padding: 1.5rem 1rem;">目前大腦空閒中，無佇列等待中的推理任務 ✨</div>';
        } else {
          bQueue.forEach(item => {
            bHtml += `
              <div class="task-card" style="border-left: 4px solid #00e5ff;">
                <div class="task-left">
                  <div class="task-badge-row">
                    <span class="task-type-badge badge-once" style="background: rgba(0, 229, 255, 0.15); color: #00e5ff; border-color: rgba(0, 229, 255, 0.3);">Priority: ${item.priority} (Seq: #${item.sequence_id})</span>
                    <span class="task-id">Task ID: ${escHtml(item.task_id)}</span>
                    <span style="font-size: 0.8rem; background: rgba(255,255,255,0.06); padding: 2px 8px; border-radius: 4px; color: var(--text-muted);">🤖 ${escHtml(item.model)}</span>
                  </div>
                  <div class="task-prompt" style="margin-top: 6px; font-family: monospace; overflow-wrap: break-word; word-break: break-word;">${escHtml(item.prompt_preview || '(無文字預覽)')}</div>
                </div>
              </div>
            `;
          });
        }
      }
      bContainer.innerHTML = sanitizeHtml(bHtml);
    } catch (e) {
      bContainer.innerHTML = sanitizeHtml('<div class="placeholder-msg">載入大腦佇列時發生錯誤。</div>');
    }
  };

  const fetchAiTasks = async () => {
    if (!aiContainer) return;
    try {
      const aiResp = await fetch('/api/recovery/ai-tasks', { headers: getAuthHeaders(), credentials: 'same-origin' });
      let aiHtml = '';
      if (aiResp.status === 401) {
        aiHtml = '<div class="placeholder-msg" style="padding: 1.5rem 1rem;">請先登入管理員以檢視進行中 AI 任務 🔒</div>';
      } else if (!aiResp.ok) {
        aiHtml = `<div class="placeholder-msg">載入進行中 AI 任務失敗 (HTTP ${aiResp.status})</div>`;
      } else {
        const aiData = await aiResp.json();
        const aiTasks = aiData.tasks || [];
        if (aiTasks.length === 0) {
          aiHtml = '<div class="placeholder-msg" style="padding: 1.5rem 1rem;">目前無進行中的 AI 推演任務，系統狀態平穩 ✨</div>';
        } else {
          aiTasks.forEach(t => {
            aiHtml += `
              <div class="task-card" style="border-left: 4px solid #38bdf8;">
                <div class="task-left">
                  <div class="task-badge-row">
                    <span class="task-type-badge badge-once" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border-color: rgba(56, 189, 248, 0.3);">🤖 ${escHtml(t.task_type || 'chat')}</span>
                    <span class="task-id">ID: ${escHtml(t.task_id)}</span>
                    <span style="font-size: 0.8rem; background: rgba(234, 179, 8, 0.15); color: #facc15; padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(234, 179, 8, 0.3);">⏱️ 已推演 ${t.elapsed_seconds || 0}s</span>
                    <span style="font-size: 0.8rem; background: rgba(255,255,255,0.06); padding: 2px 8px; border-radius: 4px; color: var(--text-muted);">📍 頻道: ${escHtml(t.channel_id)}</span>
                    <span style="font-size: 0.8rem; background: rgba(34, 197, 94, 0.15); color: #4ade80; padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(34, 197, 94, 0.3);">🛡️ 快照保護中</span>
                  </div>
                  <div class="task-prompt" style="margin-top: 6px; font-family: monospace; overflow-wrap: break-word; word-break: break-word;">${escHtml(t.prompt || '(無文字預覽)')}</div>
                  <div class="task-meta" style="margin-top: 4px; font-size: 0.78rem; color: var(--text-muted);">
                    訊息 ID: ${escHtml(t.message_id || '--')}${t.user_id ? ' | 使用者: ' + escHtml(t.user_id) : ''}${t.origin_message_id ? ' | 原始訊息: ' + escHtml(t.origin_message_id) : ''}
                  </div>
                </div>
              </div>
            `;
          });
        }
      }
      aiContainer.innerHTML = sanitizeHtml(aiHtml);
    } catch (e) {
      aiContainer.innerHTML = sanitizeHtml('<div class="placeholder-msg">載入進行中 AI 任務時發生錯誤。</div>');
    }
  };

  try {
    await Promise.allSettled([
      loadRecoveryStatus(),
      fetchBrain(),
      fetchAiTasks(),
    ]);
  } finally {
    state.inFlight.tasks = false;
    if (state.currentView === 'tasks') {
      scheduleNextPoll('tasksTimer', loadTasks, 3000, 'tasks');
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
// R1：管理員憑證改由伺服器 HttpOnly Cookie 持有，前端不再接觸 Token；
// 變更型請求以 CSRF double-submit（sherry_csrf → X-CSRF-Token）防護。
function getCsrfToken() {
  const match = document.cookie.split('; ').find(c => c.startsWith('sherry_csrf='));
  return match ? match.slice('sherry_csrf='.length) : '';
}

function getAuthHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  const csrf = getCsrfToken();
  if (csrf) {
    headers['X-CSRF-Token'] = csrf;
  }
  return headers;
}

async function checkAdminAuth() {
  try {
    const resp = await fetch('/api/auth/check', {
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (resp.ok && data.is_admin) {
      updateAuthUI(true);
    } else {
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
    if (!state.isAdmin) return;

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

    if (resp.ok && data.status === 'success') {
      // R1：Session/CSRF 憑證已由伺服器寫入 HttpOnly/Secure Cookie，前端僅更新狀態
      state.lastActivityTime = Date.now();
      closeLoginModal();
      if (passwordInput) passwordInput.value = '';
      showToast(`🔐 管理員 ${escHtml(data.username || username)} 登入成功！已解鎖系統控制 (10分鐘無動作自動登出)`, 'success');
      updateAuthUI(true);
      await loadLogFiles();
      if (state.currentView === 'logs') loadLogs();
      if (state.currentView === 'settings') loadSettings();
      if (state.currentView === 'tasks') loadTasks();
      if (state.currentView === 'audit') refreshCurrentAuditTab();
      if (state.currentView === 'roast') loadRoastConfig();
      if (state.currentView === 'care' && window.CareApp) CareApp.init();
      if (state.currentView === 'spark' && window.SparkApp) SparkApp.init();
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
  try {
    fetch('/api/auth/logout', {
      method: 'POST',
      headers: getAuthHeaders(),
      credentials: 'same-origin',
      body: JSON.stringify({}),
    });
  } catch (e) {}

  state.settingsData = {};
  state.dirtyKeys.clear();
  state.dirtyValues = {};
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
  if (state.currentView === 'roast') loadRoastConfig();
  if (state.currentView === 'care' && window.CareApp) CareApp.init();
  if (state.currentView === 'spark' && window.SparkApp) SparkApp.init();
}

function updateAuthUI(isAdmin) {
  state.isAdmin = isAdmin;
  const loginBtn = document.getElementById('login-nav-btn');
  const adminPill = document.getElementById('admin-pill');
  const logoutBtn = document.getElementById('logout-nav-btn');
  const adminOnlyEls = document.querySelectorAll('.admin-only');
  const guestNotice = document.getElementById('chat-guest-notice');
  const compNotice = document.getElementById('component-metrics-auth-notice');
  const compContainer = document.getElementById('component-metrics-container');
  const compActions = document.getElementById('component-metrics-actions');
  const careGuestNotice = document.getElementById('care-guest-notice');

  if (isAdmin) {
    if (loginBtn) loginBtn.classList.add('hidden');
    if (adminPill) adminPill.classList.remove('hidden');
    if (logoutBtn) logoutBtn.classList.remove('hidden');
    adminOnlyEls.forEach(el => el.classList.remove('hidden'));
    if (guestNotice) guestNotice.style.display = 'none';
    if (compNotice) compNotice.classList.add('hidden');
    if (careGuestNotice) careGuestNotice.style.display = 'none';
    if (typeof loadComponentMetrics === 'function') {
      loadComponentMetrics();
    }
  } else {
    if (loginBtn) loginBtn.classList.remove('hidden');
    if (adminPill) adminPill.classList.add('hidden');
    if (logoutBtn) logoutBtn.classList.add('hidden');
    adminOnlyEls.forEach(el => el.classList.add('hidden'));
    if (guestNotice) guestNotice.style.display = 'block';
    if (compNotice) compNotice.classList.remove('hidden');
    if (careGuestNotice) careGuestNotice.style.display = 'flex';
    if (compContainer) compContainer.classList.add('hidden');
    if (compActions) compActions.classList.add('hidden');
  }
}

// =========================================================
// CHAT TEST ROOM (跟雪莉聊天 穩定性測試)
// =========================================================

async function sendTestChat() {
  if (!state.isAdmin) {
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
    const act = m[1].trim().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const actIn = (m[2] || '').trim().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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

  const tagHtml = subTag ? `<span class="chat-tag">${escHtml(subTag)}</span>` : '';
  const renderedContent = renderChatMessageContent(text);

  msgDiv.innerHTML = sanitizeHtml(`
    <div class="chat-avatar">${escHtml(avatar)}</div>
    <div class="chat-bubble">
      <div class="chat-sender">${escHtml(senderName)} ${tagHtml}</div>
      <div class="chat-text">${renderedContent}</div>
    </div>
  `);

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
  if (state.currentView !== 'services') return;
  if (state.inFlight.services) return;
  state.inFlight.services = true;

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
  } finally {
    state.inFlight.services = false;
    if (state.currentView === 'services') {
      scheduleNextPoll('serviceCheckTimer', loadServiceStatus, 5000, 'services');
    }
  }
}

// =========================================================
// VISITOR & USER CONNECTION TRACKER (訪客與連線追蹤)
// =========================================================
let currentAuditTab = 'admin';

function getVisitorCookie(name = 'visitor_id') {
  const match = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[2]) : '';
}

function sendUserHeartbeat() {
  const vid = getVisitorCookie('visitor_id');
  fetch('/api/user/heartbeat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ visitor_id: vid, path: window.location.pathname || '/' }),
    credentials: 'same-origin',
  }).catch(() => {});
}

function sendUserDisconnect() {
  const vid = getVisitorCookie('visitor_id');
  const payload = JSON.stringify({ visitor_id: vid });
  if (navigator.sendBeacon) {
    const blob = new Blob([payload], { type: 'application/json' });
    navigator.sendBeacon('/api/user/disconnect', blob);
  } else {
    fetch('/api/user/disconnect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
      credentials: 'same-origin',
    }).catch(() => {});
  }
}

function startUserHeartbeatTracker() {
  // 初次載入即時發送心跳
  sendUserHeartbeat();

  // 每 30 秒定期心跳
  setInterval(() => {
    if (state.isPageVisible) {
      sendUserHeartbeat();
    }
  }, 30000);

  // 視窗離開或關閉觸發離線 Beacon (支援 desktop beforeunload 與 mobile/safari pagehide)
  window.addEventListener('beforeunload', () => {
    sendUserDisconnect();
  });
  window.addEventListener('pagehide', () => {
    sendUserDisconnect();
  });
}

function switchAuditTab(tab) {
  currentAuditTab = tab;
  const btnAdmin = document.getElementById('tab-btn-admin-audit');
  const btnUser = document.getElementById('tab-btn-user-conn');
  const panelAdmin = document.getElementById('audit-tab-admin');
  const panelUser = document.getElementById('audit-tab-user');

  if (tab === 'admin') {
    if (btnAdmin) btnAdmin.classList.add('active');
    if (btnUser) btnUser.classList.remove('active');
    if (panelAdmin) panelAdmin.classList.remove('hidden');
    if (panelUser) panelUser.classList.add('hidden');
    loadAuditLogs();
  } else {
    if (btnUser) btnUser.classList.add('active');
    if (btnAdmin) btnAdmin.classList.remove('active');
    if (panelUser) panelUser.classList.remove('hidden');
    if (panelAdmin) panelAdmin.classList.add('hidden');
    loadUserConnections();
  }
}

function refreshCurrentAuditTab() {
  if (currentAuditTab === 'user') {
    loadUserConnections();
  } else {
    loadAuditLogs();
  }
}

function formatDurationSeconds(sec) {
  sec = Math.max(0, parseInt(sec, 10) || 0);
  if (sec < 60) return `${sec}秒`;
  const mins = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (mins < 60) return `${mins}分 ${remSec}秒`;
  const hrs = Math.floor(mins / 60);
  const remMin = mins % 60;
  return `${hrs}小時 ${remMin}分`;
}

async function loadUserConnections() {
  const activeContainer = document.getElementById('user-active-container');
  const historyContainer = document.getElementById('user-history-container');
  if (!activeContainer || !historyContainer) return;

  try {
    const resp = await fetch('/api/audit/user-connections', {
      headers: getAuthHeaders(),
      credentials: 'same-origin',
    });
    if (!resp.ok) {
      activeContainer.innerHTML = '<div class="placeholder-msg">🔒 訪客連線狀況僅限登入管理員查看</div>';
      historyContainer.innerHTML = '<div class="placeholder-msg">🔒 訪客連線狀況僅限登入管理員查看</div>';
      return;
    }

    const data = await resp.json();
    const summary = data.summary || {};
    const activeVisitors = data.active_visitors || [];
    const history = data.history || [];

    // 1. KPI Cards
    const statOnline = document.getElementById('user-stat-online');
    const statToday = document.getElementById('user-stat-today');
    const statAvgDur = document.getElementById('user-stat-avg-duration');
    const statTopLoc = document.getElementById('user-stat-top-location');
    const activeSummaryText = document.getElementById('user-active-summary-text');

    if (statOnline) statOnline.textContent = summary.online_count || 0;
    if (statToday) statToday.textContent = summary.today_total || 0;
    if (statAvgDur) statAvgDur.textContent = formatDurationSeconds(summary.avg_duration_seconds || 0);

    if (statTopLoc) {
      const topLocs = summary.top_locations || {};
      const entries = Object.entries(topLocs);
      if (entries.length > 0) {
        entries.sort((a, b) => b[1] - a[1]);
        statTopLoc.textContent = `${entries[0][0]} (${entries[0][1]}次)`;
      } else {
        statTopLoc.textContent = '本機端 (Localhost)';
      }
    }

    if (activeSummaryText) {
      activeSummaryText.textContent = `共 ${activeVisitors.length} 位活躍訪客 (在線: ${summary.online_count || 0}, 閒置: ${summary.idle_count || 0})`;
    }

    // 2. 即時在線連線池
    if (activeVisitors.length === 0) {
      activeContainer.innerHTML = '<div class="placeholder-msg">目前尚無即時在線的訪客連線</div>';
    } else {
      activeContainer.innerHTML = `
        <table class="audit-table">
          <thead>
            <tr>
              <th>訪客會話識別碼</th>
              <th>連線狀態</th>
              <th>來源 IP</th>
              <th>網路位置標籤</th>
              <th>裝置與瀏覽器</th>
              <th>當前停留路徑</th>
              <th>已在線時長</th>
              <th>首次連線時間</th>
              <th>最後活躍時間</th>
            </tr>
          </thead>
          <tbody>
            ${activeVisitors.map(v => {
              const statusBadge = v.status === 'online'
                ? `<span class="badge-status-online">● 在線</span>`
                : `<span class="badge-status-idle">○ 閒置</span>`;
              return `
                <tr>
                  <td><span class="visitor-badge">🆔 ${escHtml(v.visitor_id)}</span></td>
                  <td>${statusBadge}</td>
                  <td><code class="ip-code">${escHtml(v.ip || '-')}</code></td>
                  <td><span class="location-tag">${escHtml(v.location || '-')}</span></td>
                  <td><span class="device-text">${escHtml(v.device || '-')}</span></td>
                  <td><code class="path-code">${escHtml(v.last_path || '/')}</code></td>
                  <td><strong style="color: #38bdf8;">${formatDurationSeconds(v.duration_seconds)}</strong></td>
                  <td><span class="audit-time">${escHtml(formatToUtc8(v.first_seen_at))}</span></td>
                  <td><span class="audit-time">${escHtml(formatToUtc8(v.last_active_at))}</span></td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    }

    // 3. 歷史訪客紀錄
    if (history.length === 0) {
      historyContainer.innerHTML = '<div class="placeholder-msg">尚無訪客連線歷史紀錄</div>';
    } else {
      historyContainer.innerHTML = `
        <table class="audit-table">
          <thead>
            <tr>
              <th>訪客會話識別碼</th>
              <th>狀態</th>
              <th>來源 IP</th>
              <th>網路位置標籤</th>
              <th>裝置與瀏覽器</th>
              <th>停留路徑</th>
              <th>總連線時長</th>
              <th>請求次數</th>
              <th>首次連線時間</th>
              <th>最後活躍時間</th>
            </tr>
          </thead>
          <tbody>
            ${history.map(h => {
              const isOnline = h.status === 'online';
              const isIdle = h.status === 'idle';
              const statusBadge = isOnline
                ? `<span class="badge-status-online">● 在線</span>`
                : (isIdle ? `<span class="badge-status-idle">○ 閒置</span>` : `<span class="badge-status-offline">已離線</span>`);
              return `
                <tr>
                  <td><span class="visitor-badge">🆔 ${escHtml(h.visitor_id)}</span></td>
                  <td>${statusBadge}</td>
                  <td><code class="ip-code">${escHtml(h.ip || '-')}</code></td>
                  <td><span class="location-tag">${escHtml(h.location || '-')}</span></td>
                  <td><span class="device-text">${escHtml(h.device || '-')}</span></td>
                  <td><code class="path-code">${escHtml(h.last_path || '/')}</code></td>
                  <td><strong>${formatDurationSeconds(h.duration_seconds)}</strong></td>
                  <td><span style="font-weight: 600; color: #a78bfa;">${h.hit_count || 1} 次</span></td>
                  <td><span class="audit-time">${escHtml(formatToUtc8(h.first_seen_at))}</span></td>
                  <td><span class="audit-time">${escHtml(formatToUtc8(h.last_active_at))}</span></td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    }
  } catch (err) {
    console.error('Failed to load user connections:', err);
    activeContainer.innerHTML = '<div class="placeholder-msg">無法連線至使用者連線 API</div>';
    historyContainer.innerHTML = '<div class="placeholder-msg">無法連線至使用者連線 API</div>';
  }
}

window.switchAuditTab = switchAuditTab;
window.refreshCurrentAuditTab = refreshCurrentAuditTab;

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
              ? `<span class="badge-logout">已登出 (${escHtml(formatToUtc8(rec.logout_time))})</span>`
              : `<span class="badge-active">● 線上中 (Active)</span>`;
            const actionsCount = (rec.actions || []).length;
            const actionsJson = escAttr(JSON.stringify(rec.actions || []));

            return `
              <tr>
                <td><span class="user-badge">👤 ${escHtml(rec.username || 'admin')}</span></td>
                <td><span class="audit-time">${escHtml(formatToUtc8(rec.login_time))}</span></td>
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
              <div class="timeline-time">${escHtml(formatToUtc8(act.timestamp))}</div>
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
  if (state.currentView !== 'bot-status') return;
  if (state.inFlight.botStatus) return;
  state.inFlight.botStatus = true;

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

        const recoveryBadge = vc.recovery_ready
          ? `<span class="service-badge" style="background: rgba(168, 85, 247, 0.15); color: #c084fc; border-color: rgba(168, 85, 247, 0.3); font-size: 0.75rem;">🛡️ 斷點續播就緒</span>`
          : '';

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
          const elapsedSec = vc.elapsed_seconds || 0;
          const durSec = song.duration || 0;
          const fmtTime = (s) => {
            const m = Math.floor(s / 60);
            const sec = Math.floor(s % 60);
            return `${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
          };
          const timeStr = durSec > 0 ? `${fmtTime(elapsedSec)} / ${fmtTime(durSec)}` : (elapsedSec > 0 ? `${fmtTime(elapsedSec)} / LIVE` : '--:--');
          const pct = durSec > 0 ? Math.min(100, Math.max(0, (elapsedSec / durSec) * 100)) : (isPlaying ? 100 : 0);

          songHtml = `
            <div style="background: rgba(0, 229, 255, 0.05); border: 1px solid rgba(0, 229, 255, 0.2); border-radius: 8px; padding: 12px; margin-top: 12px;">
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.85rem; color: #00e5ff; font-weight: 600;">🎵 當前播放曲目：</span>
                <span style="font-size: 0.82rem; font-family: monospace; color: var(--sherry-blue); font-weight: 600;">${timeStr}</span>
              </div>
              <div style="font-weight: 700; font-size: 1rem; margin-top: 4px; overflow-wrap: break-word; word-break: break-word;">
                ${song.url ? `<a href="${escAttr(song.url)}" target="_blank" style="color: var(--text-main); text-decoration: underline;">${escHtml(song.title)}</a>` : escHtml(song.title)}
              </div>
              <div style="background: rgba(255,255,255,0.08); border-radius: 4px; height: 4px; width: 100%; margin-top: 8px; overflow: hidden;">
                <div style="background: linear-gradient(90deg, #38bdf8, #a855f7); height: 100%; width: ${pct}%; transition: width 0.3s ease;"></div>
              </div>
              <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 6px; display: flex; gap: 12px; flex-wrap: wrap;">
                <span>點歌者: ${escHtml(song.requester)}</span>
                ${song.duration ? `<span>總長: ${Math.floor(song.duration/60)}分${Math.floor(song.duration%60)}秒</span>` : ''}
                <span>循環: ${escHtml(vc.loop_mode || 'none')}</span>
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
              <div style="display: flex; gap: 8px; align-items: center;">
                ${statusBadge}
                ${recoveryBadge}
              </div>
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
  } finally {
    state.inFlight.botStatus = false;
    if (state.currentView === 'bot-status') {
      scheduleNextPoll('botStatusTimer', loadBotStatus, 3000, 'bot-status');
    }
  }
}

// ── Dashboard Overview & Git Version Live Monitor ────────
async function loadDashboard() {
  const gitContainer = document.getElementById('dashboard-git-container');
  const coreGrid = document.getElementById('dashboard-core-grid');
  if (!gitContainer || !coreGrid) return;
  if (state.currentView !== 'dashboard') return;
  if (state.inFlight.dashboard) return;
  state.inFlight.dashboard = true;

  try {
    const resp = await fetch('/api/system/dashboard');
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.status !== 'success') return;

    const versionTag = document.getElementById('dashboard-version-tag');
    if (versionTag) versionTag.textContent = data.version || 'v1.9.0-dev';

    const git = data.git || {};
    const running = git.running || {};
    const disk = git.disk || {};
    const needsRestart = git.needs_restart || false;

    const syncBadge = needsRestart
      ? `<span class="service-badge service-offline">⚠️ 有新版本待重啟</span>`
      : `<span class="service-badge service-online">● 執行中版本為最新</span>`;

    let gitAlert = '';
    if (needsRestart) {
      gitAlert = `
        <div class="git-alert">
          <span><strong>版本升級提醒：</strong> 本地磁碟已更新至 <code>${escHtml(disk.commit_hash)}</code>（${escHtml(disk.commit_msg)}），目前執行中的仍是 <code>${escHtml(running.commit_hash)}</code>。</span>
          <button class="btn-restart admin-only" onclick="requestRestart()">🚀 立即重啟雪莉</button>
        </div>
      `;
    }

    gitContainer.innerHTML = `
      <div class="git-card">
        <div class="git-card-head">
          <div class="git-identity">
            <div class="git-icon">🌿</div>
            <div>
              <div class="git-title">Git 代碼庫與實時運作版本</div>
              <div class="git-sub">分支 <strong>${escHtml(disk.branch || 'dev')}</strong> · 標籤 <strong>${escHtml(data.version || 'v1.9.0-dev')}</strong></div>
            </div>
          </div>
          ${syncBadge}
        </div>
        <div class="git-meta-grid">
          <div class="git-meta-item">
            <div class="git-meta-label">🤖 實時運作中 Commit · Bot Process</div>
            <div class="git-meta-value is-mono is-running">${escHtml(running.commit_hash || 'unknown')}</div>
            <div class="git-sub" title="${escAttr(running.commit_msg)}">${escHtml(running.commit_msg || '啟動時 Commit 快照')}</div>
          </div>
          <div class="git-meta-item">
            <div class="git-meta-label">📦 本地磁碟 Commit · Latest Code</div>
            <div class="git-meta-value is-mono is-disk">${escHtml(disk.commit_hash || 'unknown')}</div>
            <div class="git-sub" title="${escAttr(disk.commit_msg)}">${escHtml(disk.commit_msg || '無提交紀錄')}</div>
          </div>
          <div class="git-meta-item">
            <div class="git-meta-label">👤 最新提交作者與時間</div>
            <div class="git-meta-value">${escHtml(disk.commit_author || '未知')}</div>
            <div class="git-sub">${escHtml(formatToUtc8(disk.commit_date || '--'))}</div>
          </div>
        </div>
        ${gitAlert}
      </div>
    `;

    const bot = data.bot || {};
    const sys = data.system || {};
    const vram = sys.vram || {};
    const queues = data.queues || {};
    const users = data.users || {};

    coreGrid.innerHTML = `
      <div class="metric-card stat-card" data-tone="${bot.ready ? 'green' : 'rose'}">
        <div class="stat-head">
          <span class="stat-icon">🤖</span>
          <span class="stat-label">Bot 狀態</span>
        </div>
        <div class="stat-value ${bot.ready ? 'is-green' : 'is-rose'}">${bot.ready ? 'ONLINE' : 'OFFLINE'}</div>
        <div class="stat-meta">延遲 <strong>${bot.ping_ms} ms</strong> · 伺服器 <strong>${bot.guild_count}</strong> · 語音房 <strong>${bot.voice_count}</strong></div>
      </div>

      <div class="metric-card stat-card" data-tone="blue">
        <div class="stat-head">
          <span class="stat-icon">👥</span>
          <span class="stat-label">訪客連線</span>
        </div>
        <div class="stat-value is-blue">${(users.online_count !== undefined) ? users.online_count : 0}</div>
        <div class="stat-meta">目前線上 · 今日累計 <strong>${(users.today_total !== undefined) ? users.today_total : 0}</strong> 次會話</div>
      </div>

      <div class="metric-card stat-card" data-tone="violet">
        <div class="stat-head">
          <span class="stat-icon">💻</span>
          <span class="stat-label">CPU & RAM</span>
        </div>
        <div class="stat-value is-violet">CPU ${sys.cpu_percent}% · ${sys.mem_used_gb} GB</div>
        <div class="stat-meta">總記憶體 <strong>${sys.mem_total_gb} GB</strong> · 使用率 <strong>${sys.mem_percent}%</strong></div>
      </div>

      <div class="metric-card stat-card" data-tone="rose">
        <div class="stat-head">
          <span class="stat-icon">🎮</span>
          <span class="stat-label">GPU 溫度 & 顯存</span>
        </div>
        <div class="stat-value is-rose">${vram.gpu_temp ? vram.gpu_temp + '°C' : 'N/A'} · ${vram.used_gb} / ${vram.total_gb} GB</div>
        <div class="stat-meta">${escHtml(vram.gpu_name || 'GPU')} · 使用率 <strong>${vram.percent}%</strong></div>
      </div>

      <div class="metric-card stat-card" data-tone="amber">
        <div class="stat-head">
          <span class="stat-icon">🧠</span>
          <span class="stat-label">大腦與排程</span>
        </div>
        <div class="stat-value is-amber">${queues.brain_llm} 個排隊推理</div>
        <div class="stat-meta">背景監控排程提醒 <strong>${queues.schedule}</strong> 個</div>
      </div>
    `;

  } catch (e) {
    console.error('Failed to load dashboard data:', e);
    if (gitContainer) gitContainer.innerHTML = '<div class="placeholder-msg">載入 Dashboard 時發生錯誤。</div>';
  } finally {
    state.inFlight.dashboard = false;
    if (state.currentView === 'dashboard') {
      scheduleNextPoll('dashboardTimer', loadDashboard, 5000, 'dashboard');
    }
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
    grid.innerHTML = sanitizeHtml(`<div class="mod-empty">❌ 載入模組清單失敗: ${escHtml(e)}</div>`);
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
    card.id = `mod-card-${sanitizeId(key)}`;

    const badgeClass = enabled ? 'mod-badge mod-badge-enabled' : 'mod-badge mod-badge-disabled';
    const badgeText = enabled ? '🟢 啟用中' : '🔴 已停用';

    const gamesCount = (mod.info && Array.isArray(mod.info.games) && mod.info.games.length > 0)
      ? `<div class="mod-meta-badge">🎮 內包 ${mod.info.games.length} 款小遊戲</div>` : '';

    card.innerHTML = `
      <div>
        <div class="mod-card-header">
          <div class="mod-card-name">${escHtml(mod.display_name || key)}</div>
          <span class="${badgeClass}">${badgeText}</span>
        </div>
        <div class="mod-card-desc">${escHtml(mod.description || '未登記的自訂模組。')}</div>
        ${gamesCount ? `<div style="margin-bottom: 0.6rem;">${gamesCount}</div>` : ''}
      </div>
      <div>
        <div class="mod-card-meta">
          <span class="mod-category-tag">${escHtml(mod.category || '自訂')}</span>
          <span class="mod-key-tag"><code>${escHtml(key)}</code></span>
        </div>
        <div class="mod-card-actions">
          <button
            class="mod-toggle-btn ${enabled ? 'mod-toggle-disable' : 'mod-toggle-enable'}"
            onclick="toggleModuleStatus('${sanitizeId(key)}', ${!enabled})"
            id="toggle-btn-${sanitizeId(key)}"
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

  const btn = document.getElementById(`toggle-btn-${sanitizeId(modKey)}`);
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
  // R4：統一轉義工具——歷史命名相容層，委派 escHtml（唯一實作）
  return escHtml(str);
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


