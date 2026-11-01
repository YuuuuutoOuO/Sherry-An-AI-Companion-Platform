/**
 * mod/deep_search/static/deep_search.js
 * =====================================
 * Frontend logic for Sherry Deep Search Mod.
 * Handles research session history, live thought streaming,
 * simple markdown report rendering, and task triggers.
 */

(function () {
  let selectedSessionId = null;
  let currentTab = 'thoughts';
  let refreshTimer = null;

  function _getAuthHeaders() {
    if (window.SherryUI && typeof window.SherryUI.getAuthHeaders === 'function') {
      return window.SherryUI.getAuthHeaders();
    } else if (typeof getAuthHeaders === 'function') {
      return getAuthHeaders();
    }
    return { 'Content-Type': 'application/json' };
  }

  function _showToast(msg, type) {
    if (window.SherryUI && typeof window.SherryUI.showToast === 'function') {
      window.SherryUI.showToast(msg, type);
    } else if (typeof showToast === 'function') {
      showToast(msg, type);
    } else {
      console.log(`[Toast ${type}]`, msg);
    }
  }

  function _escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function openDeepSearchModal() {
    const modal = document.getElementById('deep-search-modal-backdrop');
    if (modal) {
      modal.classList.remove('hidden');
      const topicEl = document.getElementById('ds-modal-topic');
      if (topicEl) {
        topicEl.value = '';
        topicEl.focus();
      }
    }
  }

  function closeDeepSearchModal() {
    const modal = document.getElementById('deep-search-modal-backdrop');
    if (modal) modal.classList.add('hidden');
  }

  async function submitDeepSearchFromModal() {
    const topicEl = document.getElementById('ds-modal-topic');
    const modelEl = document.getElementById('ds-modal-model');
    const roundsEl = document.getElementById('ds-modal-rounds');

    const topic = (topicEl ? topicEl.value : '').trim();
    if (!topic) {
      _showToast('⚠️ 請輸入研究主題！', 'warning');
      if (topicEl) topicEl.focus();
      return;
    }

    const model = modelEl ? modelEl.value.trim() : '';
    const rounds = roundsEl ? parseInt(roundsEl.value, 10) : 4;

    closeDeepSearchModal();
    _showToast('🚀 正在啟動深度研究專案...', 'info');

    try {
      const res = await fetch('/api/deep_search/trigger', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ..._getAuthHeaders(),
        },
        body: JSON.stringify({ topic, model, rounds }),
      });

      if (res.ok) {
        const data = await res.json();
        _showToast('✅ ' + (data.message || '深度研究專案已啟動！'), 'success');
        selectedSessionId = data.session_id;
        loadDeepSearchData();
      } else {
        const err = await res.json();
        _showToast('❌ 啟動失敗: ' + (err.message || res.statusText), 'error');
      }
    } catch (e) {
      _showToast('❌ 發起請求失敗: ' + e.message, 'error');
    }
  }

  async function loadDeepSearchData() {
    try {
      const res = await fetch('/api/deep_search/sessions');
      if (!res.ok) return;

      const data = await res.json();
      const sessions = data.sessions || [];

      // 計算統計數值
      let activeCount = 0;
      let completedCount = 0;
      let totalSources = 0;

      sessions.forEach(s => {
        if (s.status === 'RUNNING') activeCount++;
        if (s.status === 'COMPLETED') completedCount++;
        if (s.sources && Array.isArray(s.sources)) totalSources += s.sources.length;
      });

      const activeEl = document.getElementById('ds-stat-active');
      if (activeEl) activeEl.textContent = activeCount;
      const completedEl = document.getElementById('ds-stat-completed');
      if (completedEl) completedEl.textContent = completedCount;
      const sourcesEl = document.getElementById('ds-stat-sources');
      if (sourcesEl) sourcesEl.textContent = totalSources + ' 篇';

      // 渲染左側列表
      renderDeepSearchSessionsList(sessions);

      // 如果尚未選取 Session，預設選取最新一筆或正在運行的 Session
      if (!selectedSessionId && sessions.length > 0) {
        const runningOne = sessions.find(s => s.status === 'RUNNING');
        selectedSessionId = runningOne ? runningOne.session_id : sessions[0].session_id;
      }

      // 載入當前選取的 Session 詳情
      if (selectedSessionId) {
        loadDeepSearchSessionDetail(selectedSessionId);
      } else {
        const emptyEl = document.getElementById('ds-detail-empty');
        if (emptyEl) emptyEl.classList.remove('hidden');
        const headerEl = document.getElementById('ds-detail-header');
        if (headerEl) headerEl.classList.add('hidden');
        const contentEl = document.getElementById('ds-detail-content');
        if (contentEl) contentEl.classList.add('hidden');
      }
    } catch (e) {
      console.debug('loadDeepSearchData error:', e);
    }
  }

  function renderDeepSearchSessionsList(sessions) {
    const container = document.getElementById('ds-sessions-list');
    if (!container) return;

    if (sessions.length === 0) {
      container.innerHTML = '<div class="text-muted ds-empty-hint">尚未有任何研究專案。點擊上方發起第一個研究吧！</div>';
      return;
    }

    let html = '';
    sessions.forEach(s => {
      const isSelected = s.session_id === selectedSessionId;
      const activeClass = isSelected ? 'active' : '';

      let statusBadge = '';
      if (s.status === 'RUNNING') {
        statusBadge = '<span class="ds-badge ds-badge--running">⏳ 進行中</span>';
      } else if (s.status === 'COMPLETED') {
        statusBadge = '<span class="ds-badge ds-badge--completed">✅ 完成</span>';
      } else if (s.status === 'CANCELLED') {
        statusBadge = '<span class="ds-badge ds-badge--cancelled">⏹️ 已取消</span>';
      } else {
        statusBadge = '<span class="ds-badge ds-badge--failed">❌ 失敗</span>';
      }

      const thoughtCount = (s.thought_logs || []).length;
      const sourceCount = (s.sources || []).length;

      html += `
        <div class="ds-session-card ${activeClass}" onclick="selectDeepSearchSession('${s.session_id}')">
          <div class="ds-card-header">
            <span class="ds-card-topic">${_escapeHtml(s.topic || '未命名主題')}</span>
            ${statusBadge}
          </div>
          <div class="ds-card-meta">
            <span>🤖 ${_escapeHtml(s.model || '預設')}</span>
            <span>⏱️ ${s.started_at ? s.started_at.split(' ')[1] || s.started_at : '-'}</span>
          </div>
          <div class="ds-card-stats">
            <span>🧠 ${thoughtCount} 步思考</span>
            <span>🌐 ${sourceCount} 篇文獻</span>
            <span>📊 ${s.progress_pct || 0}%</span>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  function selectDeepSearchSession(sessionId) {
    selectedSessionId = sessionId;
    document.querySelectorAll('.ds-session-card').forEach(card => card.classList.remove('active'));
    loadDeepSearchSessionDetail(sessionId);
  }

  async function loadDeepSearchSessionDetail(sessionId) {
    try {
      const res = await fetch(`/api/deep_search/session/${encodeURIComponent(sessionId)}`);
      if (!res.ok) return;

      const data = await res.json();
      const session = data.session;
      if (!session) return;

      renderDeepSearchSessionDetail(session);
    } catch (e) {
      console.debug('loadDeepSearchSessionDetail error:', e);
    }
  }

  function renderDeepSearchSessionDetail(session) {
    const emptyEl = document.getElementById('ds-detail-empty');
    if (emptyEl) emptyEl.classList.add('hidden');
    const headerEl = document.getElementById('ds-detail-header');
    if (headerEl) headerEl.classList.remove('hidden');
    const contentEl = document.getElementById('ds-detail-content');
    if (contentEl) contentEl.classList.remove('hidden');

    // Header
    const topicEl = document.getElementById('ds-detail-topic');
    if (topicEl) topicEl.textContent = session.topic || '未命名主題';

    const badgeEl = document.getElementById('ds-detail-status-badge');
    if (badgeEl) {
      badgeEl.className = 'ds-status-badge ds-status-badge--' + (session.status || 'PENDING').toLowerCase();
      badgeEl.textContent = session.status === 'RUNNING' ? '⏳ 深度推演中' : (session.status === 'COMPLETED' ? '✅ 研究完成' : session.status);
    }

    const modelEl = document.getElementById('ds-detail-model');
    if (modelEl) modelEl.textContent = session.model || '-';

    const startedEl = document.getElementById('ds-detail-started');
    if (startedEl) startedEl.textContent = session.started_at || '-';

    const idEl = document.getElementById('ds-detail-id');
    if (idEl) idEl.textContent = session.session_id || '-';

    const stepEl = document.getElementById('ds-detail-step');
    if (stepEl) stepEl.textContent = session.current_step || '-';

    const pctEl = document.getElementById('ds-detail-pct');
    if (pctEl) pctEl.textContent = (session.progress_pct || 0) + '%';

    const progressFill = document.getElementById('ds-detail-progress-fill');
    if (progressFill) progressFill.style.width = (session.progress_pct || 0) + '%';

    const btnCancel = document.getElementById('ds-btn-cancel');
    if (btnCancel) {
      if (session.status === 'RUNNING') btnCancel.classList.remove('hidden');
      else btnCancel.classList.add('hidden');
    }

    // Counts on Tabs
    const thoughts = session.thought_logs || [];
    const sources = session.sources || [];
    const tabThoughtsCount = document.getElementById('ds-tab-thoughts-count');
    if (tabThoughtsCount) tabThoughtsCount.textContent = thoughts.length;
    const tabSourcesCount = document.getElementById('ds-tab-sources-count');
    if (tabSourcesCount) tabSourcesCount.textContent = sources.length;

    // 渲染當前 Tab
    renderThoughtTimeline(thoughts);
    renderSourcesList(sources);
    renderReportMarkdown(session.final_report, session);
  }

  function renderThoughtTimeline(thoughts) {
    const container = document.getElementById('ds-thought-timeline');
    if (!container) return;

    if (thoughts.length === 0) {
      container.innerHTML = '<div class="text-muted" style="padding: 20px;">尚無思考歷程記錄，推演將在啟動後即時串流...</div>';
      return;
    }

    let html = '';
    thoughts.forEach((t) => {
      let phaseClass = 'phase-general';
      let phaseIcon = '💡';
      if (t.phase === 'PLANNING') { phaseClass = 'phase-planning'; phaseIcon = '📐'; }
      else if (t.phase === 'SEARCHING') { phaseClass = 'phase-searching'; phaseIcon = '🔍'; }
      else if (t.phase === 'READING') { phaseClass = 'phase-reading'; phaseIcon = '📖'; }
      else if (t.phase === 'REFLECTING') { phaseClass = 'phase-reflecting'; phaseIcon = '🧠'; }
      else if (t.phase === 'SYNTHESIZING') { phaseClass = 'phase-synthesizing'; phaseIcon = '📑'; }

      html += `
        <div class="ds-timeline-item ${phaseClass}">
          <div class="ds-timeline-marker">
            <span class="ds-timeline-icon">${phaseIcon}</span>
          </div>
          <div class="ds-timeline-content">
            <div class="ds-timeline-header">
              <span class="ds-timeline-title">${_escapeHtml(t.title || '思考步驟')}</span>
              <span class="ds-timeline-time">${_escapeHtml(t.timestamp || '')}</span>
            </div>
            <div class="ds-timeline-body">${renderSimpleMarkdown(t.content || '')}</div>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  function renderSourcesList(sources) {
    const container = document.getElementById('ds-sources-container');
    if (!container) return;

    if (sources.length === 0) {
      container.innerHTML = '<div class="text-muted" style="padding: 20px;">尚未檢索到文獻來源。</div>';
      return;
    }

    let html = '';
    sources.forEach((s, idx) => {
      html += `
        <div class="ds-source-card">
          <div class="ds-source-header">
            <span class="ds-source-idx">#${idx + 1}</span>
            <a href="${_escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer" class="ds-source-title">
              ${_escapeHtml(s.title || s.url)} 🔗
            </a>
          </div>
          <div class="ds-source-url">${_escapeHtml(s.url)}</div>
          <div class="ds-source-snippet">${_escapeHtml(s.snippet || (s.content ? s.content.slice(0, 160) + '...' : ''))}</div>
          <div class="ds-source-meta">
            <span>字數: ${s.char_count || 0} 字</span>
            <span>擷取時間: ${s.scraped_at ? s.scraped_at.split('T')[0] : '-'}</span>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  function renderReportMarkdown(report, session) {
    const bodyEl = document.getElementById('ds-report-body');
    const metaEl = document.getElementById('ds-report-meta');
    if (!bodyEl) return;

    if (!report || !report.full_content) {
      if (session && session.status === 'RUNNING') {
        bodyEl.innerHTML = '<div class="text-muted" style="padding: 30px; text-align: center;">⏳ 研究推演進行中（目前進度 ' + (session.progress_pct || 0) + '%），報告將在多輪精讀與反思完成後由地端模型進行完整大篇幅撰寫...</div>';
      } else {
        bodyEl.innerHTML = '<div class="text-muted" style="padding: 30px; text-align: center;">此專案尚未產出完整報告。</div>';
      }
      if (metaEl) metaEl.textContent = '未完成報告';
      return;
    }

    if (metaEl) {
      metaEl.textContent = `生成模型: ${report.model_used || '-'} | 總耗時: ${(report.total_time_seconds || 0).toFixed(1)} 秒 | 引用文獻: ${(report.sources || []).length} 篇`;
    }

    bodyEl.innerHTML = renderSimpleMarkdown(report.full_content);
  }

  function switchDsTab(tabName) {
    currentTab = tabName;
    document.querySelectorAll('.ds-tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.ds-tab-pane').forEach(pane => pane.classList.add('hidden'));

    const btn = document.getElementById('ds-tab-btn-' + tabName);
    if (btn) btn.classList.add('active');
    const pane = document.getElementById('ds-tab-' + tabName);
    if (pane) pane.classList.remove('hidden');
  }

  async function cancelCurrentDeepSearch() {
    if (!selectedSessionId) return;
    if (!confirm('確定要終止目前正在運行的深度研究任務嗎？')) return;

    try {
      const res = await fetch(`/api/deep_search/cancel/${encodeURIComponent(selectedSessionId)}`, {
        method: 'POST',
        headers: _getAuthHeaders(),
      });
      if (res.ok) {
        _showToast('⏹️ 已取消該研究任務', 'info');
        loadDeepSearchData();
      } else {
        _showToast('❌ 取消失敗', 'error');
      }
    } catch (e) {
      _showToast('❌ 請求錯誤: ' + e.message, 'error');
    }
  }

  async function deleteCurrentDeepSearch() {
    if (!selectedSessionId) return;
    if (!confirm('確定要刪除這筆深度研究專案記錄嗎？')) return;

    try {
      const res = await fetch(`/api/deep_search/session/${encodeURIComponent(selectedSessionId)}`, {
        method: 'DELETE',
        headers: _getAuthHeaders(),
      });
      if (res.ok) {
        _showToast('🗑️ 已刪除研究專案', 'success');
        selectedSessionId = null;
        loadDeepSearchData();
      } else {
        _showToast('❌ 刪除失敗', 'error');
      }
    } catch (e) {
      _showToast('❌ 請求錯誤: ' + e.message, 'error');
    }
  }

  async function copyDeepSearchReportMarkdown() {
    if (!selectedSessionId) return;
    try {
      const res = await fetch(`/api/deep_search/session/${encodeURIComponent(selectedSessionId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const report = data.session ? data.session.final_report : null;
      if (report && report.full_content) {
        await navigator.clipboard.writeText(report.full_content);
        _showToast('📋 已複製完整 Markdown 報告至剪貼簿！', 'success');
      } else {
        _showToast('⚠️ 目前尚無可複製的報告內容', 'warning');
      }
    } catch (e) {
      _showToast('❌ 複製失敗: ' + e.message, 'error');
    }
  }

  function renderSimpleMarkdown(md) {
    if (!md) return '';
    let html = _escapeHtml(md);

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

    // Links
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="md-link">$1 🔗</a>');

    // Bullet points
    html = html.replace(/^\s*[-*•]\s+(.*$)/gim, '<li class="md-li">$1</li>');

    // Paragraph breaks
    html = html.replace(/\n\n+/g, '<br><br>');
    html = html.replace(/\n/g, '<br>');

    return html;
  }

  // Expose to window
  window.openDeepSearchModal = openDeepSearchModal;
  window.closeDeepSearchModal = closeDeepSearchModal;
  window.submitDeepSearchFromModal = submitDeepSearchFromModal;
  window.loadDeepSearchData = loadDeepSearchData;
  window.selectDeepSearchSession = selectDeepSearchSession;
  window.switchDsTab = switchDsTab;
  window.cancelCurrentDeepSearch = cancelCurrentDeepSearch;
  window.deleteCurrentDeepSearch = deleteCurrentDeepSearch;
  window.copyDeepSearchReportMarkdown = copyDeepSearchReportMarkdown;

  // Register with SherryUI plugin ecosystem
  if (window.SherryUI && typeof window.SherryUI.registerPlugin === 'function') {
    window.SherryUI.registerPlugin('deep_search', {
      viewId: 'deep-search',
      onActivate: () => {
        loadDeepSearchData();
        if (!refreshTimer) {
          refreshTimer = setInterval(loadDeepSearchData, 3000);
        }
      },
      onDeactivate: () => {
        if (refreshTimer) {
          clearInterval(refreshTimer);
          refreshTimer = null;
        }
      },
    });
  }
})();
