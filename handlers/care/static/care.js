/**
 * handlers/care/static/care.js
 * 主動關懷 WebUI：回饋報表、灰度設定、測試頻道沙盒與雪莉自我記憶。
 */

const CareApp = {
    initialized: false,
    activeTab: 'surfer',
    selfMemoryTab: 'reading',
    surferPort: 8765,
    availableChannels: [],

    init() {
        if (!this.initialized) {
            this.bindEvents();
            this.initialized = true;
        }

        // Restore active sub-tab from localStorage if present (預設直接進入賽博群友大腦 surfer)
        try {
            const savedTab = localStorage.getItem('sherry_care_tab');
            if (savedTab && ['surfer', 'self', 'social'].includes(savedTab)) {
                this.switchTab(savedTab);
            } else {
                this.switchTab('surfer');
            }
        } catch (e) {
            this.switchTab('surfer');
        }

        this.loadOverview();
        this.loadActions();
        this.loadSurferConfig();
        this.loadCyberV2Config();
        this.loadCyberStatus();
        if (!this._cyberTimer) {
            this._cyberTimer = setInterval(() => {
                if (document.visibilityState !== 'hidden') this.loadCyberStatus();
            }, 5000);
        }
        this.loadSelfMemory();
    },

    getHeaders() {
        return window.SherryUI && typeof window.SherryUI.getAuthHeaders === 'function'
            ? window.SherryUI.getAuthHeaders()
            : { 'Content-Type': 'application/json' };
    },

    toast(message, type = 'info') {
        if (window.SherryUI && typeof window.SherryUI.showToast === 'function') {
            window.SherryUI.showToast(message, type);
        } else if (typeof showToast === 'function') {
            showToast(message, type);
        }
    },

    _esc(str) {
        return String(str ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    _rate(value) {
        const num = Number(value);
        return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : '0.0%';
    },

    _time(ts) {
        const num = Number(ts);
        if (!Number.isFinite(num) || num <= 0) return '—';
        return new Date(num * 1000).toLocaleString();
    },

    switchTab(tab) {
        this.activeTab = tab;
        try {
            localStorage.setItem('sherry_care_tab', tab);
        } catch (e) {}

        const tabs = ['overview', 'surfer', 'self', 'social'];
        tabs.forEach(t => {
            const btn = document.getElementById(`care-tab-btn-${t}`);
            const panel = document.getElementById(`care-panel-${t}`);

            if (btn) {
                if (t === tab) {
                    btn.classList.add('active');
                    btn.style.background = 'rgba(139, 92, 246, 0.25)';
                    btn.style.borderColor = '#8b5cf6';
                    btn.style.color = '#f8fafc';
                } else {
                    btn.classList.remove('active');
                    btn.style.background = 'rgba(30, 41, 59, 0.6)';
                    btn.style.borderColor = 'rgba(255, 255, 255, 0.08)';
                    btn.style.color = '#94a3b8';
                }
            }

            if (panel) {
                panel.style.display = (t === tab) ? 'block' : 'none';
            }
        });

        if (tab === 'social') {
            this.loadSocial();
        }
    },

    bindEvents() {
        // Sub-tabs
        const tabOverview = document.getElementById('care-tab-btn-overview');
        if (tabOverview) tabOverview.addEventListener('click', () => this.switchTab('overview'));

        const tabSurfer = document.getElementById('care-tab-btn-surfer');
        if (tabSurfer) tabSurfer.addEventListener('click', () => this.switchTab('surfer'));

        const tabSelf = document.getElementById('care-tab-btn-self');
        if (tabSelf) tabSelf.addEventListener('click', () => this.switchTab('self'));

        const tabSocial = document.getElementById('care-tab-btn-social');
        if (tabSocial) tabSocial.addEventListener('click', () => this.switchTab('social'));

        // General toolbar
        const refresh = document.getElementById('care-refresh');
        if (refresh) refresh.addEventListener('click', () => this.init());

        const settle = document.getElementById('care-settle');
        if (settle) settle.addEventListener('click', () => this.settleSilence());

        const form = document.getElementById('care-rollout-form');
        if (form) form.addEventListener('submit', event => this.saveRollout(event));

        const actions = document.getElementById('care-load-actions');
        if (actions) actions.addEventListener('click', () => this.loadActions());

        const openSurfer = document.getElementById('care-open-surfer');
        if (openSurfer) openSurfer.addEventListener('click', () => {
            const proto = window.location.protocol;
            const host = window.location.hostname;
            window.open(`${proto}//${host}:${this.surferPort}`, '_blank');
        });

        // Surfer & Channel Config
        const surferForm = document.getElementById('care-surfer-form');
        if (surferForm) surferForm.addEventListener('submit', event => this.saveSurferConfig(event));

        const surferSaveBtn = document.getElementById('care-surfer-save-btn');
        if (surferSaveBtn) surferSaveBtn.addEventListener('click', event => this.saveSurferConfig(event));

        const cyberV2SaveBtn = document.getElementById('care-cyber-v2-save-btn');
        if (cyberV2SaveBtn) cyberV2SaveBtn.addEventListener('click', () => this.saveCyberV2Config());

        const channelSelect = document.getElementById('care-surfer-channel-select');
        const channelInput = document.getElementById('care-surfer-channel');

        if (channelSelect && channelInput) {
            channelSelect.addEventListener('change', () => {
                const val = channelSelect.value.trim();
                if (val) {
                    channelInput.value = val;
                }
            });

            channelInput.addEventListener('input', () => {
                const val = channelInput.value.trim();
                if (channelSelect) {
                    channelSelect.value = val;
                }
            });
        }

        const dryBtn = document.getElementById('care-surfer-trigger-dry');
        if (dryBtn) dryBtn.addEventListener('click', () => this.triggerSurfer(true));

        const nowBtn = document.getElementById('care-surfer-trigger-now');
        if (nowBtn) nowBtn.addEventListener('click', () => this.triggerSurfer(false));

        // 5 Cyber-Friend Mode Triggers
        const modeAutoBtn = document.getElementById('care-trigger-auto');
        if (modeAutoBtn) modeAutoBtn.addEventListener('click', () => this.triggerSurferMode('auto'));

        const modeLurkBtn = document.getElementById('care-trigger-lurk');
        if (modeLurkBtn) modeLurkBtn.addEventListener('click', () => this.triggerSurferMode('lurk_chat'));

        const modeVibeBtn = document.getElementById('care-trigger-vibe');
        if (modeVibeBtn) modeVibeBtn.addEventListener('click', () => this.triggerSurferMode('daily_vibe'));

        const modePokeBtn = document.getElementById('care-trigger-poke');
        if (modePokeBtn) modePokeBtn.addEventListener('click', () => this.triggerSurferMode('care_poke'));

        const modeGossipBtn = document.getElementById('care-trigger-gossip');
        if (modeGossipBtn) modeGossipBtn.addEventListener('click', () => this.triggerSurferMode('surf_gossip'));

        const modeMindBtn = document.getElementById('care-trigger-mind');
        if (modeMindBtn) modeMindBtn.addEventListener('click', () => this.triggerSurferMode('mind_monologue'));

        const mindTickBtn = document.getElementById('care-mind-tick-btn');
        if (mindTickBtn) mindTickBtn.addEventListener('click', () => this.tickMindDecay(25));

        // Self-Memory Sub-tabs
        const tabReading = document.getElementById('care-self-tab-reading');
        if (tabReading) tabReading.addEventListener('click', () => {
            this.selfMemoryTab = 'reading';
            this.loadSelfMemory();
        });

        const tabEpisodes = document.getElementById('care-self-tab-episodes');
        if (tabEpisodes) tabEpisodes.addEventListener('click', () => {
            this.selfMemoryTab = 'episodes';
            this.loadSelfMemory();
        });

        const selfRefresh = document.getElementById('care-self-refresh');
        if (selfRefresh) selfRefresh.addEventListener('click', () => this.loadSelfMemory());

        // Social Core（沙盒群像 ＆ 決策帳本）
        const socialRefresh = document.getElementById('care-social-refresh');
        if (socialRefresh) socialRefresh.addEventListener('click', () => this.loadSocial());

        const socialEnable = document.getElementById('care-social-enable');
        if (socialEnable) socialEnable.addEventListener('click', () => this.saveChannelMode('SANDBOX_GROUP'));

        const socialDisable = document.getElementById('care-social-disable');
        if (socialDisable) socialDisable.addEventListener('click', () => this.saveChannelMode('SOLO'));

        const socialKill = document.getElementById('care-social-kill');
        if (socialKill) socialKill.addEventListener('click', () => this.killSandbox());

        const npcAdd = document.getElementById('care-npc-add');
        if (npcAdd) npcAdd.addEventListener('click', () => this.addNpc());

        const decisionsRefresh = document.getElementById('care-social-decisions-refresh');
        if (decisionsRefresh) decisionsRefresh.addEventListener('click', () => this.loadSocialDecisions());

        const decisionAction = document.getElementById('care-social-decision-action');
        if (decisionAction) decisionAction.addEventListener('change', () => this.loadSocialDecisions());
    },

    showStatus(message, isError = false) {
        const el = document.getElementById('care-status-message');
        if (!el) return;
        el.textContent = message;
        el.style.color = isError ? '#f87171' : '#4ade80';
        clearTimeout(this._statusTimer);
        this._statusTimer = setTimeout(() => {
            if (el.textContent === message) el.textContent = '';
        }, 5000);
    },

    async loadOverview() {
        try {
            const res = await fetch('/api/care/overview?days=30', { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || '載入關懷總覽失敗');
            }
            this.renderOverview(data.overview || {});
        } catch (err) {
            console.error('[CareApp] 載入總覽失敗:', err);
            this.showStatus(`載入總覽失敗：${err.message}`, true);
        }
    },

    renderOverview(data) {
        const stats = data.stats || {};
        const limits = data.limits || {};

        const optinEl = document.getElementById('care-stat-optin');
        if (optinEl) optinEl.textContent = stats.opted_in_users ?? 0;

        const respEl = document.getElementById('care-stat-response');
        if (respEl) respEl.textContent = this._rate(stats.response_rate);

        const silEl = document.getElementById('care-stat-silence');
        if (silEl) silEl.textContent = this._rate(stats.silence_rate);

        const pendEl = document.getElementById('care-stat-pending');
        if (pendEl) pendEl.textContent = stats.pending_actions ?? 0;

        const topEl = document.getElementById('care-stat-topics');
        if (topEl) topEl.textContent = stats.active_topics ?? 0;

        const limEl = document.getElementById('care-stat-limits');
        if (limEl) {
            limEl.textContent = `${limits.user_weekly_budget ?? '—'} / ${limits.global_daily_budget ?? '—'}`;
        }

        const adEl = document.getElementById('care-stat-adaptive');
        if (adEl) {
            adEl.textContent = limits.adaptive_applied ? '已啟動 (降級/暫停)' : '常態';
            adEl.style.color = limits.adaptive_applied ? '#fbbf24' : '#4ade80';
        }

        this.renderRollouts(data.rollouts || []);
        this.renderGuildFeedback(data.guild_feedback || []);
    },

    renderRollouts(rows) {
        const tbody = document.getElementById('care-rollout-list');
        if (!tbody) return;
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="4" style="padding: 16px; color: #94a3b8;">尚無設定灰度的伺服器（預設皆為 0% 不投放）。</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map(row => `
            <tr>
                <td style="padding: 10px; font-weight: 600;">${this._esc(row.guild_id)}</td>
                <td style="padding: 10px;">
                    <span style="display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; background: ${row.enabled ? 'rgba(74, 222, 128, 0.15)' : 'rgba(239, 68, 68, 0.15)'}; color: ${row.enabled ? '#4ade80' : '#f87171'};">
                        ${row.enabled ? '開啟' : '停用'}
                    </span>
                </td>
                <td style="padding: 10px;">${this._esc(row.rollout_percent)}%</td>
                <td style="padding: 10px; color: #94a3b8;">${this._esc(this._time(row.updated_at))}</td>
            </tr>
        `).join('');
    },

    renderGuildFeedback(rows) {
        const tbody = document.getElementById('care-guild-feedback');
        if (!tbody) return;
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="5" style="padding: 16px; color: #94a3b8;">尚無各伺服器結案樣本。</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map(row => `
            <tr>
                <td style="padding: 10px; font-weight: 600;">${this._esc(row.guild_id)}</td>
                <td style="padding: 10px;">${this._esc(row.approved)}</td>
                <td style="padding: 10px;">${this._esc(row.responded)}</td>
                <td style="padding: 10px;">${this._esc(row.silent)}</td>
                <td style="padding: 10px; font-weight: 600; color: ${Number(row.silence_rate) >= 0.5 ? '#f87171' : '#4ade80'};">${this._rate(row.silence_rate)}</td>
            </tr>
        `).join('');
    },

    async saveRollout(event) {
        event.preventDefault();
        const guildInput = document.getElementById('care-guild-id');
        const percentInput = document.getElementById('care-rollout-percent');
        const enabledInput = document.getElementById('care-rollout-enabled');
        const guildId = guildInput ? guildInput.value.trim() : '';
        const percent = percentInput ? Number(percentInput.value) : 100;
        if (!/^\d+$/.test(guildId) || Number(guildId) <= 0) {
            this.showStatus('請輸入有效的伺服器 ID（正整數）。', true);
            return;
        }
        if (!Number.isFinite(percent) || percent < 0 || percent > 100) {
            this.showStatus('灰度比例需介於 0–100。', true);
            return;
        }
        try {
            const res = await fetch('/api/care/guilds', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({
                    guild_id: guildId,
                    enabled: enabledInput ? enabledInput.checked : true,
                    rollout_percent: percent,
                }),
            });
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || '儲存灰度設定失敗');
            }
            this.showStatus(`伺服器 ${guildId} 灰度設定已更新。`);
            this.toast('設定已儲存！', 'success');
            await this.loadOverview();
        } catch (err) {
            console.error('[CareApp] 儲存灰度失敗:', err);
            this.showStatus(`儲存灰度失敗：${err.message}`, true);
            this.toast(`儲存失敗：${err.message}`, 'error');
        }
    },

    async settleSilence() {
        try {
            const res = await fetch('/api/care/settle', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({ hours: 48 }),
            });
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || '結算沉默失敗');
            }
            this.showStatus(`已將 ${data.settled ?? 0} 筆逾時未回應的關懷標記為沉默。`);
            this.toast(`已將 ${data.settled ?? 0} 筆標記為沉默`, 'success');
            await this.loadOverview();
            await this.loadActions();
        } catch (err) {
            console.error('[CareApp] 結算沉默失敗:', err);
            this.showStatus(`結算失敗：${err.message}`, true);
            this.toast(`結算失敗：${err.message}`, 'error');
        }
    },

    async loadActions() {
        try {
            const res = await fetch('/api/care/actions?limit=30', { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || '載入稽核列表失敗');
            }
            this.renderActions(data.actions || []);
        } catch (err) {
            console.error('[CareApp] 載入稽核列表失敗:', err);
        }
    },

    renderActions(rows) {
        const tbody = document.getElementById('care-actions-body');
        if (!tbody) return;
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="padding: 16px; color: #94a3b8;">尚無關懷行動紀錄。</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map(row => `
            <tr>
                <td style="padding: 10px;">${this._esc(this._time(row.created_at))}</td>
                <td style="padding: 10px;">${this._esc(row.user_id)}</td>
                <td style="padding: 10px;">${this._esc(row.source)}</td>
                <td style="padding: 10px;">${this._esc(row.status)}</td>
                <td style="padding: 10px;">${this._esc(row.topic)}</td>
                <td style="padding: 10px;">${this._esc(row.reason)}</td>
            </tr>
        `).join('');
    },

    async loadCyberV2Config() {
        try {
            const res = await fetch('/api/care/cyber/config', { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status !== 'success') return;
            const c = data.config || {};
            const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
            const setChk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
            setVal('cyber-v2-engine-mode', c.engine_mode || 'shadow');
            setVal('cyber-v2-mention-scope', c.mention_scope || 'sandbox');
            setVal('cyber-v2-window', c.window_seconds ?? 300);
            setVal('cyber-v2-followup-window', c.followup_window_seconds ?? 900);
            setVal('cyber-v2-debounce', c.debounce_seconds ?? 10);
            setVal('cyber-v2-mention-debounce', c.mention_debounce_seconds ?? 2);
            setChk('cyber-v2-scoring-enabled', c.reply_scoring_enabled !== false);
            setVal('cyber-v2-score-threshold', c.ambient_score_threshold ?? 6);
            setVal('cyber-v2-idle-proactive', c.idle_proactive_seconds ?? 1800);
            setChk('cyber-v2-autonomy-enabled', c.autonomy_enabled !== false);
            setChk('cyber-v2-autonomy-thought', c.autonomy_thought_enabled !== false);
            setVal('cyber-v2-autonomy-hourly', c.autonomy_hourly ?? 2);
            setVal('cyber-v2-autonomy-daily', c.autonomy_daily ?? 12);
            setVal('cyber-v2-autonomy-wait-max', c.autonomy_wait_max_minutes ?? 90);
            setVal('cyber-v2-reasoning-effort', c.reasoning_effort || 'low');
            const scoringToggle = document.getElementById('cyber-v2-scoring-enabled');
            const scoreThreshold = document.getElementById('cyber-v2-score-threshold');
            if (scoringToggle && scoreThreshold) {
                const syncScoringUi = () => { scoreThreshold.disabled = !scoringToggle.checked; };
                scoringToggle.onchange = syncScoringUi;
                syncScoringUi();
            }
            setVal('cyber-v2-tool-rounds', c.tool_max_rounds ?? 3);
            setVal('cyber-v2-max-bubbles', c.max_bubbles ?? 2);
            setVal('cyber-v2-hourly', c.contact_hourly ?? 8);
            setVal('cyber-v2-daily', c.contact_daily ?? 120);
            setVal('cyber-v2-cooldown', c.contact_cooldown ?? 120);
            setVal('cyber-v2-active-start', c.active_hour_start ?? 9);
            setVal('cyber-v2-active-end', c.active_hour_end ?? 4);
            setVal('cyber-v2-mind-pace', c.mind_pace || 'normal');
            setChk('cyber-v2-gateway', c.gateway_enabled);
            setChk('cyber-v2-owns-mentions', c.owns_mentions);
            setChk('cyber-v2-autonomous', c.autonomous_enabled !== false);
            setChk('cyber-v2-tools-enabled', c.tools_enabled !== false);
            setChk('cyber-v2-sanitize', c.sanitize_output !== false);
            setChk('cyber-v2-dynamic-context', c.dynamic_context_enabled !== false);
            setChk('cyber-v2-identity-tags', c.identity_tags_enabled !== false);
            setChk('cyber-v2-memory-context', c.memory_context_enabled !== false);
            setChk('cyber-v2-schedule-context', c.schedule_context_enabled !== false);
            const tools = c.allowed_tools || ['SEARCH', 'STOCK', 'SUMMARY'];
            setChk('cyber-v2-tool-search', tools.includes('SEARCH'));
            setChk('cyber-v2-tool-stock', tools.includes('STOCK'));
            setChk('cyber-v2-tool-summary', tools.includes('SUMMARY'));
        } catch (e) {
            // 舊伺服器可能尚未有 v2 端點
        }
    },

    async saveCyberV2Config() {
        const val = id => { const el = document.getElementById(id); return el ? el.value : ''; };
        const chk = id => { const el = document.getElementById(id); return el ? !!el.checked : false; };
        const allowed = [];
        if (chk('cyber-v2-tool-search')) allowed.push('SEARCH');
        if (chk('cyber-v2-tool-stock')) allowed.push('STOCK');
        if (chk('cyber-v2-tool-summary')) allowed.push('SUMMARY');
        const config = {
            engine_mode: val('cyber-v2-engine-mode') || 'shadow',
            gateway_enabled: chk('cyber-v2-gateway'),
            owns_mentions: chk('cyber-v2-owns-mentions'),
            mention_scope: val('cyber-v2-mention-scope') || 'sandbox',
            autonomous_enabled: chk('cyber-v2-autonomous'),
            tools_enabled: chk('cyber-v2-tools-enabled'),
            allowed_tools: allowed,
            tool_max_rounds: Number(val('cyber-v2-tool-rounds') || 3),
            dynamic_context_enabled: chk('cyber-v2-dynamic-context'),
            identity_tags_enabled: chk('cyber-v2-identity-tags'),
            memory_context_enabled: chk('cyber-v2-memory-context'),
            schedule_context_enabled: chk('cyber-v2-schedule-context'),
            max_bubbles: Number(val('cyber-v2-max-bubbles') || 2),
            sanitize_output: chk('cyber-v2-sanitize'),
            contact_hourly: Number(val('cyber-v2-hourly') || 0),
            contact_daily: Number(val('cyber-v2-daily') || 0),
            contact_cooldown: Number(val('cyber-v2-cooldown') || 0),
            window_seconds: Number(val('cyber-v2-window') || 300),
            followup_window_seconds: Number(val('cyber-v2-followup-window') || 900),
            debounce_seconds: Number(val('cyber-v2-debounce') || 10),
            mention_debounce_seconds: Number(val('cyber-v2-mention-debounce') || 2),
            reply_scoring_enabled: chk('cyber-v2-scoring-enabled'),
            ambient_score_threshold: Number(val('cyber-v2-score-threshold') || 6),
            idle_proactive_seconds: Number(val('cyber-v2-idle-proactive') || 1800),
            autonomy_enabled: chk('cyber-v2-autonomy-enabled'),
            autonomy_thought_enabled: chk('cyber-v2-autonomy-thought'),
            autonomy_hourly: Number(val('cyber-v2-autonomy-hourly') || 0),
            autonomy_daily: Number(val('cyber-v2-autonomy-daily') || 0),
            autonomy_wait_max_minutes: Number(val('cyber-v2-autonomy-wait-max') || 90),
            reasoning_effort: val('cyber-v2-reasoning-effort') || 'low',
            active_hour_start: Number(val('cyber-v2-active-start') || 0),
            active_hour_end: Number(val('cyber-v2-active-end') || 0),
            mind_pace: val('cyber-v2-mind-pace') || 'normal',
        };
        try {
            const res = await fetch('/api/care/cyber/config', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({ config }),
            });
            const data = await res.json();
            if (data.status === 'success') {
                const msg = document.getElementById('care-cyber-v2-msg');
                if (msg) msg.textContent = `已儲存 v2 設定（engine=${data.applied_mode || config.engine_mode}）`;
                this.toast('✅ v2 架構設定已儲存', 'success');
                // 立即回填伺服器實際套用的值（例如氣泡上限超出範圍會被收斂），
                // 不要等到重新整理才發現設定變了。
                await this.loadCyberV2Config();
                this.loadCyberStatus();
            } else {
                this.toast('❌ v2 設定儲存失敗', 'error');
            }
        } catch (e) {
            this.toast('❌ v2 設定儲存失敗：' + e.message, 'error');
        }
    },

    async loadCyberStatus() {
        try {
            const res = await fetch('/api/care/cyber/status', { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status !== 'success') return;
            const cyber = data.cyber_engine || {};
            const pm = data.primary_model || {};

            const modelPill = document.getElementById('care-cyber-model-pill');
            if (modelPill) {
                const ok = pm.healthy !== false && pm.value;
                modelPill.textContent = `🧠 ${ok ? pm.value : '主力模型未設定'}`;
                modelPill.style.cssText = ok
                    ? 'padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:600;background:rgba(56,189,248,.15);color:#38bdf8;border:1px solid rgba(56,189,248,.35);'
                    : 'padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:600;background:rgba(248,113,113,.18);color:#f87171;border:1px solid rgba(248,113,113,.4);';
            }

            const phasePill = document.getElementById('care-cyber-phase-pill');
            if (phasePill) {
                const phaseMap = {
                    idle: '💤 待機', listening: '👂 聽話中', thinking: '🤔 思考中',
                    speaking: '💬 發送中', cooldown: '⏳ 冷卻中'
                };
                const mode = cyber.mode || 'off';
                const followup = !!cyber.followup_open && !cyber.window_open;
                phasePill.textContent = `⚙️ ${phaseMap[cyber.phase] || cyber.phase || '—'} / ${mode}` + (followup ? '｜追話寬限中' : '');
                phasePill.style.cssText = (cyber.window_open || cyber.followup_open)
                    ? 'padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:600;background:rgba(34,197,94,.18);color:#4ade80;border:1px solid rgba(74,222,128,.4);'
                    : 'padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:600;background:rgba(148,163,184,.15);color:#94a3b8;border:1px solid rgba(148,163,184,.3);';
            }

            const queuePill = document.getElementById('care-cyber-queue-pill');
            if (queuePill) {
                const n = Number(cyber.pending_events || 0);
                queuePill.textContent = `📥 緩衝: ${n}`;
                queuePill.style.cssText = n > 0
                    ? 'padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:600;background:rgba(251,191,36,.18);color:#fbbf24;border:1px solid rgba(251,191,36,.4);'
                    : 'padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:600;background:rgba(148,163,184,.15);color:#94a3b8;border:1px solid rgba(148,163,184,.3);';
            }

            // 🧠 L2 自主：她想不想說話、為什麼、下次什麼時候
            const autonomy = cyber.autonomy || {};
            const actLabels = {
                speak: '🗣️ 想說話',
                silent: '🤫 想安靜',
                wait: '⏳ 晚點再說',
                blocked: '⛔ 沒問她',
            };
            const actStyles = {
                speak: ['rgba(34,197,94,.18)', '#4ade80', 'rgba(74,222,128,.4)'],
                silent: ['rgba(148,163,184,.18)', '#cbd5e1', 'rgba(148,163,184,.4)'],
                wait: ['rgba(251,191,36,.18)', '#fbbf24', 'rgba(251,191,36,.4)'],
                blocked: ['rgba(248,113,113,.18)', '#f87171', 'rgba(248,113,113,.4)'],
            };
            const actPill = document.getElementById('care-autonomy-act-pill');
            if (actPill) {
                const act = String(autonomy.act || '');
                if (actStyles[act]) {
                    const [bg, color, border] = actStyles[act];
                    actPill.textContent = actLabels[act];
                    actPill.style.cssText = `padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;background:${bg};color:${color};border:1px solid ${border};`;
                } else {
                    actPill.textContent = autonomy.enabled === false ? '⏸️ 自主判斷已關閉' : '💤 還沒有決定';
                    actPill.style.cssText = 'padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;background:rgba(148,163,184,.15);color:#94a3b8;border:1px solid rgba(148,163,184,.3);';
                }
            }

            const fmtClock = (ts) => {
                if (!ts) return '—';
                try {
                    return new Date(Number(ts) * 1000).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });
                } catch (e) {
                    return '—';
                }
            };
            const motivationEl = document.getElementById('care-autonomy-motivation');
            if (motivationEl) {
                motivationEl.textContent = autonomy.motivation
                    || (autonomy.reason === 'autonomy_budget' ? '主動額度已用完' : '—');
            }
            const nextEl = document.getElementById('care-autonomy-next');
            if (nextEl) {
                const nextAt = Number(autonomy.next_at || 0);
                nextEl.textContent = nextAt > Date.now() / 1000 ? fmtClock(nextAt) : '下一輪機會';
            }
            const countEl = document.getElementById('care-autonomy-count');
            if (countEl) {
                countEl.textContent = `${Number(autonomy.today_speaks || 0)} / ${Number(autonomy.daily_limit || 0)}`;
            }
            const lastEl = document.getElementById('care-autonomy-last');
            if (lastEl) {
                const lastAct = String(autonomy.act || '');
                const label = actLabels[lastAct] || '—';
                lastEl.textContent = autonomy.decided_at
                    ? `${label} · ${fmtClock(autonomy.decided_at)}`
                    : '—';
            }
            const noteEl = document.getElementById('care-autonomy-note');
            if (noteEl) {
                if (autonomy.act === 'blocked') {
                    noteEl.style.display = 'block';
                    noteEl.textContent = '⛔ 這一輪沒有問她：主動額度已用完。';
                } else if (autonomy.act === 'wait') {
                    noteEl.style.display = 'block';
                    noteEl.textContent = `⏳ 她說想晚點再聊，下次機會延到 ${fmtClock(autonomy.next_at)}。`;
                } else {
                    noteEl.style.display = 'none';
                    noteEl.textContent = '';
                }
            }
            const thoughtRow = document.getElementById('care-autonomy-thought-row');
            const thoughtEl = document.getElementById('care-autonomy-thought');
            if (thoughtRow && thoughtEl) {
                if (autonomy.thought) {
                    thoughtRow.style.display = 'block';
                    thoughtEl.textContent = autonomy.thought;
                } else {
                    thoughtRow.style.display = 'none';
                    thoughtEl.textContent = '—';
                }
            }

            try {
                const historyRes = await fetch('/api/care/cyber/autonomy?limit=20', { headers: this.getHeaders() });
                const historyData = await historyRes.json();
                const historyBody = document.getElementById('care-autonomy-history-body');
                if (historyBody && historyData.status === 'success') {
                    const items = historyData.items || [];
                    if (!items.length) {
                        historyBody.innerHTML = '<tr><td colspan="5" style="padding:8px;color:#64748b;">還沒有紀錄</td></tr>';
                    } else {
                        historyBody.innerHTML = items.map((item) => {
                            const act = actLabels[String(item.act || '')] || '—';
                            const next = item.next_at ? fmtClock(item.next_at) : '—';
                            return `<tr>
                                <td style="padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.05);white-space:nowrap;">${fmtClock(item.ts)}</td>
                                <td style="padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.05);white-space:nowrap;">${act}</td>
                                <td style="padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.05);">${this._esc(item.motivation || '—')}</td>
                                <td style="padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.05);color:#c4b5fd;">${this._esc(item.thought || '—')}</td>
                                <td style="padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.05);white-space:nowrap;">${next}</td>
                            </tr>`;
                        }).join('');
                    }
                }
            } catch (e) {
                // 歷史讀取失敗不影響即時狀態
            }
        } catch (e) {
            // 狀態列失敗不影響設定頁
        }
    },

    async loadSurferConfig() {
        try {
            const res = await fetch('/api/care/surfer/config', { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status !== 'success') {
                if (res.status === 401 || (data.message && data.message.includes('登入'))) {
                    this.toast('⚠️ 賽博群友需要管理員權限，請先由右上角登入', 'warning');
                    this.showStatus('需要管理員權限，請點擊右上角「管理員登入」', true);
                }
                return;
            }
            if (data.port) this.surferPort = data.port;
            this.availableChannels = data.available_channels || [];

            const cfg = data.config || {};
            const chInfo = data.channel_info || {};

            // 1. Populate Channel Selector Dropdown
            const channelSelect = document.getElementById('care-surfer-channel-select');
            if (channelSelect) {
                let html = '<option value="">-- 請選擇伺服器文字頻道 (一鍵帶入) --</option>';
                if (this.availableChannels.length > 0) {
                    for (const g of this.availableChannels) {
                        html += `<optgroup label="🏠 ${this._esc(g.guild_name)}">`;
                        for (const ch of g.channels) {
                            const icon = ch.can_send ? '💬' : '🔒';
                            const note = ch.can_send ? '' : ' [無發送權限]';
                            html += `<option value="${this._esc(ch.id)}">${icon} #${this._esc(ch.name)}${note} (${this._esc(ch.id)})</option>`;
                        }
                        html += `</optgroup>`;
                    }
                } else {
                    html += '<option value="" disabled>（目前無可用頻道，請確認 Bot 是否在線）</option>';
                }
                channelSelect.innerHTML = html;
                if (cfg.sandbox_channel_id && cfg.sandbox_channel_id !== '0') {
                    channelSelect.value = cfg.sandbox_channel_id;
                }
            }

            // 2. Set Channel Input & Form controls
            const chInput = document.getElementById('care-surfer-channel');
            if (chInput) chInput.value = cfg.sandbox_channel_id || '';

            const intInput = document.getElementById('care-surfer-interval');
            if (intInput) intInput.value = cfg.interval_minutes || 120;

            const modeSelect = document.getElementById('care-surfer-tag-mode');
            if (modeSelect) modeSelect.value = cfg.tag_mode || 'interest_match';

            const enInput = document.getElementById('care-surfer-enabled');
            if (enInput) enInput.checked = !!cfg.enabled;

            // 賽博群友擴充欄位
            const behModeSelect = document.getElementById('care-surfer-behavior-mode');
            if (behModeSelect) behModeSelect.value = cfg.behavior_mode || 'balanced';

            const chatReplyChk = document.getElementById('care-surfer-chat-reply');
            if (chatReplyChk) chatReplyChk.checked = cfg.chat_reply_enabled !== false;

            const interjectThInput = document.getElementById('care-surfer-interject-threshold');
            if (interjectThInput) interjectThInput.value = cfg.interject_threshold ?? 6;

            const interjectScoreInput = document.getElementById('care-surfer-interject-score');
            if (interjectScoreInput) interjectScoreInput.value = cfg.interject_score_threshold ?? 7;

            const activeWinInput = document.getElementById('care-surfer-active-window');
            if (activeWinInput) activeWinInput.value = cfg.active_chat_window_seconds ?? 180;

            const maxHourlyInput = document.getElementById('care-surfer-max-hourly');
            if (maxHourlyInput) maxHourlyInput.value = cfg.max_replies_per_hour ?? 8;

            const maxDailyInput = document.getElementById('care-surfer-max-daily');
            if (maxDailyInput) maxDailyInput.value = cfg.max_replies_per_day ?? 60;

            const proactiveCdInput = document.getElementById('care-surfer-proactive-cooldown');
            if (proactiveCdInput) proactiveCdInput.value = cfg.proactive_cooldown_seconds ?? 120;

            const multiMsgChk = document.getElementById('care-surfer-multi-msg');
            if (multiMsgChk) multiMsgChk.checked = cfg.multi_message_enabled !== false;

            const emojiChk = document.getElementById('care-surfer-emoji');
            if (emojiChk) emojiChk.checked = cfg.emoji_reaction_enabled !== false;

            const urlModeSelect = document.getElementById('care-surfer-url-mode');
            if (urlModeSelect) urlModeSelect.value = cfg.include_url_mode || 'never';

            const activeStartSelect = document.getElementById('care-surfer-active-start');
            if (activeStartSelect) activeStartSelect.value = String(cfg.active_hour_start ?? 9);

            const activeEndSelect = document.getElementById('care-surfer-active-end');
            if (activeEndSelect) activeEndSelect.value = String(cfg.active_hour_end ?? 23);

            const mindPaceSelect = document.getElementById('care-surfer-mind-pace');
            if (mindPaceSelect) mindPaceSelect.value = String(cfg.mind_pace || 'normal');

            const followupEnabledChk = document.getElementById('care-surfer-followup-enabled');
            if (followupEnabledChk) followupEnabledChk.checked = cfg.followup_scan_enabled !== false;

            const followupIntervalsInput = document.getElementById('care-surfer-followup-intervals');
            if (followupIntervalsInput) {
                const arr = Array.isArray(cfg.followup_scan_intervals) ? cfg.followup_scan_intervals : [1, 3, 5, 7, 10];
                followupIntervalsInput.value = arr.join(', ');
            }

            // 3. Render Hero Channel Status Card & Cyber Rice / Chat Window Pills
            this.renderChannelStatusCard(chInfo, data.env_channel_id, data.live_stats);
        } catch (err) {
            console.error('[CareApp] 載入衝浪設定失敗:', err);
        }
    },

    renderChannelStatusCard(chInfo, envChannelId, liveStats) {
        const pill = document.getElementById('care-channel-status-pill');
        const tabBadge = document.getElementById('care-tab-channel-badge');
        const nameVal = document.getElementById('care-channel-name-val');
        const guildVal = document.getElementById('care-guild-name-val');
        const idVal = document.getElementById('care-channel-id-val');
        const sourceVal = document.getElementById('care-channel-source-val');

        const status = chInfo.status || 'unset';
        const cid = chInfo.id || '0';

        let pillText = '⚪ 尚未設定測試頻道';
        let pillStyle = 'background: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3);';
        let badgeText = '⚪ 未綁定';
        let badgeStyle = 'background: rgba(148, 163, 184, 0.2); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3);';

        if (status === 'valid') {
            pillText = '🟢 連線正常 (具發言權限)';
            pillStyle = 'background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.4);';
            badgeText = `🟢 #${chInfo.name || '測試'}`;
            badgeStyle = 'background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.4);';
        } else if (status === 'missing_permissions') {
            pillText = '🟡 缺少發言權限 (請檢查 Bot 角色)';
            pillStyle = 'background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.4);';
            badgeText = '🟡 權限受限';
            badgeStyle = 'background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.4);';
        } else if (status === 'not_found') {
            pillText = '🔴 找不到頻道 (Bot 尚未加入該群或 ID 不存在)';
            pillStyle = 'background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(248, 113, 113, 0.4);';
            badgeText = '🔴 找不到頻道';
            badgeStyle = 'background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(248, 113, 113, 0.4);';
        }

        if (pill) {
            pill.textContent = pillText;
            pill.style.cssText = `padding: 4px 10px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; ${pillStyle}`;
        }
        if (tabBadge) {
            tabBadge.textContent = badgeText;
            tabBadge.style.cssText = `font-size: 0.72rem; padding: 2px 6px; border-radius: 10px; ${badgeStyle}`;
        }

        // 追話狀態與賽博白飯狀態 Pill
        const chatWinPill = document.getElementById('care-chat-window-pill');
        if (chatWinPill) {
            const stats = liveStats || {};
            if (stats.active_chat_window_open) {
                chatWinPill.textContent = '🟢 追話狀態 (視窗開啟中)';
                chatWinPill.style.cssText = 'padding: 4px 10px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.4);';
            } else {
                chatWinPill.textContent = '💤 潛水聆聽中';
                chatWinPill.style.cssText = 'padding: 4px 10px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; background: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3);';
            }
        }

        const ricePill = document.getElementById('care-cyber-rice-pill');
        if (ricePill) {
            const stats = liveStats || {};
            const bowls = stats.bowls_of_rice ?? 0;
            const tokens = stats.tokens_consumed_today ?? 0;
            ricePill.textContent = `🍚 今日白飯: ${bowls} 碗 (${Number(tokens).toLocaleString()} tokens)`;
            ricePill.style.cssText = 'padding: 4px 10px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3);';
        }

        if (nameVal) {
            if (chInfo.name) {
                nameVal.innerHTML = `<span style="color: #38bdf8;">#${this._esc(chInfo.name)}</span>`;
            } else if (cid === '0' || !cid) {
                nameVal.innerHTML = '<span style="color: #94a3b8; font-weight: normal;">(尚未指定測試頻道)</span>';
            } else {
                nameVal.innerHTML = '<span style="color: #f87171;">未知頻道 (ID 不存在)</span>';
            }
        }

        if (guildVal) {
            if (chInfo.guild_name) {
                guildVal.innerHTML = `<span style="color: #f8fafc;">${this._esc(chInfo.guild_name)}</span> <span style="font-size: 0.75rem; color: #64748b;">(Guild: ${this._esc(chInfo.guild_id)})</span>`;
            } else {
                guildVal.innerHTML = '<span style="color: #64748b;">—</span>';
            }
        }

        if (idVal) {
            idVal.textContent = (cid && cid !== '0') ? cid : '0 (無發文目標)';
        }

        if (sourceVal) {
            if (chInfo.is_env_default) {
                sourceVal.innerHTML = `<span style="color: #38bdf8;">🌱 來自環境變數預設 (CARE_SURFER_CHANNEL_ID)</span>`;
            } else if (cid && cid !== '0') {
                sourceVal.innerHTML = `<span style="color: #a78bfa;">💾 資料庫動態覆蓋 (預設 .env: ${this._esc(envChannelId || '0')})</span>`;
            } else {
                sourceVal.innerHTML = `<span style="color: #94a3b8;">⚪ 停用發文 (預設 .env: ${this._esc(envChannelId || '0')})</span>`;
            }
        }

        // 🧠 雪莉即時心智狀態池 (Mind Dynamics) 進度條與狀態徽章
        const mindState = liveStats?.mind_state || {};
        const boredomVal = Math.round(Number(mindState.boredom_level ?? (100 - (mindState.social_satiety ?? 72))));
        const curiosityVal = Math.round(Number(mindState.curiosity_level ?? (100 - (mindState.curiosity_satiety ?? 68))));
        const energyVal = Math.round(Number(mindState.energy_level ?? 90));

        const boredomValEl = document.getElementById('care-mind-boredom-val') || document.getElementById('care-mind-social-val');
        const boredomBarEl = document.getElementById('care-mind-boredom-bar') || document.getElementById('care-mind-social-bar');
        if (boredomValEl) boredomValEl.textContent = `${boredomVal}%`;
        if (boredomBarEl) {
            boredomBarEl.style.width = `${Math.min(100, Math.max(0, boredomVal))}%`;
            boredomBarEl.style.background = boredomVal >= 80 ? 'linear-gradient(90deg, #ef4444, #f87171)' : 'linear-gradient(90deg, #ec4899, #f43f5e)';
        }

        const curiosityValEl = document.getElementById('care-mind-curiosity-val');
        const curiosityBarEl = document.getElementById('care-mind-curiosity-bar');
        if (curiosityValEl) curiosityValEl.textContent = `${curiosityVal}%`;
        if (curiosityBarEl) {
            curiosityBarEl.style.width = `${Math.min(100, Math.max(0, curiosityVal))}%`;
            curiosityBarEl.style.background = curiosityVal >= 75 ? 'linear-gradient(90deg, #38bdf8, #818cf8)' : 'linear-gradient(90deg, #0284c7, #38bdf8)';
        }

        const energyValEl = document.getElementById('care-mind-energy-val');
        const energyBarEl = document.getElementById('care-mind-energy-bar');
        if (energyValEl) energyValEl.textContent = `${energyVal}%`;
        if (energyBarEl) {
            energyBarEl.style.width = `${Math.min(100, Math.max(0, energyVal))}%`;
        }

        const activeBadgeEl = document.getElementById('care-mind-active-badge');
        const isActiveHours = liveStats?.is_active_hours !== false;
        if (activeBadgeEl) {
            if (isActiveHours) {
                activeBadgeEl.textContent = '☀️ 活躍中可發言';
                activeBadgeEl.style.cssText = 'padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.4);';
            } else {
                activeBadgeEl.textContent = '🌙 深夜靜音休眠中';
                activeBadgeEl.style.cssText = 'padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; background: rgba(99, 102, 241, 0.2); color: #a5b4fc; border: 1px solid rgba(129, 140, 248, 0.4);';
            }
        }

        const hoursTipEl = document.getElementById('care-mind-hours-tip');
        if (hoursTipEl) {
            const hStart = liveStats?.active_hour_start ?? 9;
            const hEnd = liveStats?.active_hour_end ?? 23;
            hoursTipEl.textContent = `時段: ${String(hStart).padStart(2, '0')}:00 ~ ${String(hEnd).padStart(2, '0')}:00`;
        }

        const thoughtPreviewEl = document.getElementById('care-mind-thought-preview');
        if (thoughtPreviewEl && mindState.last_thought) {
            thoughtPreviewEl.textContent = mindState.last_thought;
        }
    },

    async saveSurferConfig(event) {
        if (event) event.preventDefault();
        const chInput = document.getElementById('care-surfer-channel');
        const intInput = document.getElementById('care-surfer-interval');
        const modeSelect = document.getElementById('care-surfer-tag-mode');
        const enInput = document.getElementById('care-surfer-enabled');

        const behModeSelect = document.getElementById('care-surfer-behavior-mode');
        const chatReplyChk = document.getElementById('care-surfer-chat-reply');
        const interjectThInput = document.getElementById('care-surfer-interject-threshold');
        const interjectScoreInput = document.getElementById('care-surfer-interject-score');
        const activeWinInput = document.getElementById('care-surfer-active-window');
        const maxHourlyInput = document.getElementById('care-surfer-max-hourly');
        const maxDailyInput = document.getElementById('care-surfer-max-daily');
        const proactiveCdInput = document.getElementById('care-surfer-proactive-cooldown');
        const multiMsgChk = document.getElementById('care-surfer-multi-msg');
        const emojiChk = document.getElementById('care-surfer-emoji');
        const urlModeSelect = document.getElementById('care-surfer-url-mode');
        const activeStartSelect = document.getElementById('care-surfer-active-start');
        const activeEndSelect = document.getElementById('care-surfer-active-end');
        const mindPaceSelect = document.getElementById('care-surfer-mind-pace');
        const followupEnabledChk = document.getElementById('care-surfer-followup-enabled');
        const followupIntervalsInput = document.getElementById('care-surfer-followup-intervals');

        const payload = {
            sandbox_channel_id: chInput ? chInput.value.trim() : '0',
            interval_minutes: intInput ? Number(intInput.value) : 120,
            tag_mode: modeSelect ? modeSelect.value : 'interest_match',
            enabled: enInput ? enInput.checked : false,
            behavior_mode: behModeSelect ? behModeSelect.value : 'balanced',
            chat_reply_enabled: chatReplyChk ? chatReplyChk.checked : true,
            interject_threshold: interjectThInput ? Number(interjectThInput.value) : 6,
            interject_score_threshold: interjectScoreInput ? Number(interjectScoreInput.value) : 7,
            active_chat_window_seconds: activeWinInput ? Number(activeWinInput.value) : 180,
            max_replies_per_hour: maxHourlyInput ? Number(maxHourlyInput.value) : 8,
            max_replies_per_day: maxDailyInput ? Number(maxDailyInput.value) : 60,
            proactive_cooldown_seconds: proactiveCdInput ? Number(proactiveCdInput.value) : 120,
            multi_message_enabled: multiMsgChk ? multiMsgChk.checked : true,
            emoji_reaction_enabled: emojiChk ? emojiChk.checked : true,
            include_url_mode: urlModeSelect ? urlModeSelect.value : 'never',
            active_hour_start: activeStartSelect ? Number(activeStartSelect.value) : 9,
            active_hour_end: activeEndSelect ? Number(activeEndSelect.value) : 23,
            mind_pace: mindPaceSelect ? mindPaceSelect.value : 'normal',
            followup_scan_enabled: followupEnabledChk ? followupEnabledChk.checked : true,
            followup_scan_intervals: followupIntervalsInput ? followupIntervalsInput.value.trim() : '1, 3, 5, 7, 10',
        };

        try {
            const res = await fetch('/api/care/surfer/config', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || '儲存衝浪設定失敗');
            }
            this.showStatus('網活仔衝浪與賽博群友設定已成功儲存！');
            this.toast('設定已儲存！', 'success');
            await this.loadSurferConfig();
        } catch (err) {
            console.error('[CareApp] 儲存衝浪設定失敗:', err);
            this.showStatus(`儲存失敗：${err.message}`, true);
            this.toast(`儲存失敗：${err.message}`, 'error');
        }
    },

    async triggerSurfer(dryRun) {
        return this.triggerSurferMode('auto', dryRun);
    },

    async triggerSurferMode(mode = 'auto', forcedDryRun = null) {
        const dryChk = document.getElementById('care-trigger-dryrun-chk');
        const dryRun = forcedDryRun !== null ? forcedDryRun : (dryChk ? dryChk.checked : true);

        // 鎖定所有觸發按鈕，防止重複點擊
        const triggerBtns = [
            document.getElementById('care-trigger-auto'),
            document.getElementById('care-trigger-mind'),
            document.getElementById('care-trigger-lurk'),
            document.getElementById('care-trigger-vibe'),
            document.getElementById('care-trigger-poke'),
            document.getElementById('care-trigger-gossip'),
            document.getElementById('care-mind-tick-btn'),
            document.getElementById('care-surfer-trigger-dry'),
            document.getElementById('care-surfer-trigger-now'),
        ].filter(Boolean);

        triggerBtns.forEach(btn => {
            btn.disabled = true;
            btn.style.opacity = '0.6';
            btn.style.cursor = 'not-allowed';
        });

        const resultBox = document.getElementById('care-trigger-result-box');
        if (resultBox) {
            resultBox.style.display = 'block';
            const thoughtEl = document.getElementById('care-result-thought');
            if (thoughtEl) thoughtEl.textContent = '⏳ 雪莉正在動腦筋思考中...';
            const bubblesEl = document.getElementById('care-result-bubbles');
            if (bubblesEl) bubblesEl.innerHTML = '<div style="color: #94a3b8; font-size: 0.85rem;">思考推論中，畫面同步推播至 Port 8765 實況台...</div>';
            const modeBadge = document.getElementById('care-result-mode-badge');
            if (modeBadge) modeBadge.textContent = `模式: ${mode}`;
            const dryBadge = document.getElementById('care-result-dryrun-badge');
            if (dryBadge) {
                dryBadge.textContent = dryRun ? '🧪 Dry-run 模擬 (未外發)' : '🚀 實體發送 (Discord)';
                dryBadge.style.color = dryRun ? '#fbbf24' : '#4ade80';
            }
            const statusMsg = document.getElementById('care-result-status-msg');
            if (statusMsg) statusMsg.textContent = '';
        }

        try {
            const res = await fetch('/api/care/surfer/trigger', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({ mode: mode, dry_run: dryRun }),
            });
            const data = await res.json();
            if (res.status === 409 || data.status === 'busy') {
                this.showStatus('⏳ 雪莉目前正在執行前一個思考任務，請稍候 3~5 秒再試！', true);
                if (resultBox) {
                    const statusMsg = document.getElementById('care-result-status-msg');
                    if (statusMsg) {
                        statusMsg.textContent = '⏳ 雪莉目前正在執行前一個任務，請稍候 3~5 秒再試！';
                        statusMsg.style.color = '#fbbf24';
                    }
                }
                return;
            }
            if (data.status !== 'success') {
                throw new Error(data.message || '觸發模式失敗');
            }

            this.showStatus(data.message || `已以【${mode}】模式完成冒泡（dry_run=${dryRun}）`);

            // 在現場回饋卡片渲染結果
            if (resultBox && data.result) {
                const r = data.result;
                const thoughtEl = document.getElementById('care-result-thought');
                if (thoughtEl) thoughtEl.textContent = r.thought || '（無特別內心 OS）';

                const bubblesEl = document.getElementById('care-result-bubbles');
                if (bubblesEl) {
                    bubblesEl.innerHTML = '';
                    const bubbles = r.bubbles || (r.post_text ? [r.post_text] : []);
                    if (bubbles.length === 0) {
                        bubblesEl.innerHTML = '<div style="color: #94a3b8; font-size: 0.85rem;">（未生成發言文字）</div>';
                    } else {
                        bubbles.forEach((b, idx) => {
                            const bDiv = document.createElement('div');
                            bDiv.style.cssText = 'background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 8px 12px; color: #f8fafc; font-size: 0.88rem;';
                            bDiv.innerHTML = `<span style="font-size: 0.75rem; color: #a78bfa; margin-right: 6px;">[氣泡 ${idx + 1}]</span>${this._esc(b)}`;
                            bubblesEl.appendChild(bDiv);
                        });
                    }
                }

                const statusMsg = document.getElementById('care-result-status-msg');
                if (statusMsg) {
                    if (dryRun) {
                        statusMsg.textContent = '✅ Dry-run 模擬預覽完成（未發送至 Discord）。若要發送，請取消勾選「Dry-run 預覽模式」或點擊「立即衝浪並發文」！';
                        statusMsg.style.color = '#fbbf24';
                        this.toast('Dry-run 模擬預覽完成！', 'info');
                    } else {
                        statusMsg.textContent = `🚀 已成功以 ${r.bubbles ? r.bubbles.length : 1} 則短句氣泡發送至 Discord 測試頻道！`;
                        statusMsg.style.color = '#4ade80';
                        this.toast('已成功發送至 Discord 測試頻道！', 'success');
                    }
                }
                const timeEl = document.getElementById('care-result-time');
                if (timeEl) timeEl.textContent = new Date().toLocaleTimeString();
            }

            setTimeout(() => {
                this.loadSelfMemory();
                this.loadActions();
                this.loadSurferConfig();
            }, 1000);
        } catch (err) {
            console.error('[CareApp] 觸發模式失敗:', err);
            this.showStatus(`觸發失敗：${err.message}`, true);
            if (resultBox) {
                const statusMsg = document.getElementById('care-result-status-msg');
                if (statusMsg) {
                    statusMsg.textContent = `❌ 錯誤：${err.message}`;
                    statusMsg.style.color = '#f87171';
                }
            }
        } finally {
            triggerBtns.forEach(btn => {
                btn.disabled = false;
                btn.style.opacity = '';
                btn.style.cursor = 'pointer';
            });
        }
    },

    async tickMindDecay(forceBoost = 25) {
        const mindTickBtn = document.getElementById('care-mind-tick-btn');
        if (mindTickBtn) {
            mindTickBtn.disabled = true;
            mindTickBtn.style.opacity = '0.6';
        }

        try {
            const res = await fetch('/api/care/surfer/mind-tick', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({ force_boost: forceBoost }),
            });
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || '推進心智需求失敗');
            }
            const b = data.mind_state?.boredom_level ?? (100 - (data.mind_state?.social_satiety ?? 50));
            const c = data.mind_state?.curiosity_level ?? (100 - (data.mind_state?.curiosity_satiety ?? 50));
            this.showStatus(`🔥 已推進心智累積 (+${forceBoost}%)，當前指標：無聊度 ${b}%、好奇欲 ${c}%`);
            this.toast('心智需求指標已更新！', 'success');
            await this.loadSurferConfig();
        } catch (err) {
            console.error('[CareApp] 推進心智需求失敗:', err);
            this.showStatus(`推進心智需求失敗：${err.message}`, true);
            this.toast(`推進心智需求失敗：${err.message}`, 'error');
        } finally {
            if (mindTickBtn) {
                mindTickBtn.disabled = false;
                mindTickBtn.style.opacity = '';
            }
        }
    },

    async loadSelfMemory() {
        const tabReading = document.getElementById('care-self-tab-reading');
        const tabEpisodes = document.getElementById('care-self-tab-episodes');
        if (tabReading && tabEpisodes) {
            if (this.selfMemoryTab === 'reading') {
                tabReading.style.background = '#8b5cf6';
                tabReading.style.color = '#ffffff';
                tabEpisodes.style.background = '';
                tabEpisodes.style.color = '';
            } else {
                tabEpisodes.style.background = '#8b5cf6';
                tabEpisodes.style.color = '#ffffff';
                tabReading.style.background = '';
                tabReading.style.color = '';
            }
        }

        try {
            const res = await fetch(`/api/care/self-memory?type=${this.selfMemoryTab}&limit=30`, {
                headers: this.getHeaders(),
            });
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || '載入自我記憶失敗');
            }
            this.renderSelfMemory(data.items || []);
        } catch (err) {
            console.error('[CareApp] 載入自我記憶失敗:', err);
        }
    },

    renderSelfMemory(items) {
        const tbody = document.getElementById('care-self-table-body');
        if (!tbody) return;
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="padding: 16px; color: #94a3b8;">尚無自我記憶記錄。</td></tr>';
            return;
        }

        if (this.selfMemoryTab === 'reading') {
            tbody.innerHTML = items.map(item => `
                <tr>
                    <td style="padding: 10px;">${this._esc(this._time(item.created_at))}</td>
                    <td style="padding: 10px; max-width: 200px;">
                        <a href="${this._esc(item.url)}" target="_blank" style="color: #38bdf8; text-decoration: none;">${this._esc(item.title)}</a>
                    </td>
                    <td style="padding: 10px;">${this._esc(item.source)}</td>
                    <td style="padding: 10px; max-width: 280px; color: #f1f5f9;">${this._esc(item.sherry_opinion || item.post_text || '—')}</td>
                    <td style="padding: 10px;">${this._esc(item.tagged_user_name || item.tagged_user_id || '—')}</td>
                    <td style="padding: 10px;">
                        <button onclick="CareApp.deleteSelfMemory('reading', '${this._esc(item.reading_id)}')" style="background: none; border: 1px solid rgba(239, 68, 68, 0.4); color: #ef4444; padding: 3px 8px; border-radius: 4px; cursor: pointer;">刪除</button>
                    </td>
                </tr>
            `).join('');
        } else {
            tbody.innerHTML = items.map(item => `
                <tr>
                    <td style="padding: 10px;">${this._esc(this._time(item.created_at))}</td>
                    <td style="padding: 10px; font-weight: 600; color: #a855f7;">${this._esc(item.category)}</td>
                    <td style="padding: 10px;">${(item.tags || []).map(t => `<span style="background: rgba(255,255,255,0.08); padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; margin-right: 4px;">${this._esc(t)}</span>`).join('')}</td>
                    <td style="padding: 10px; max-width: 320px; color: #f1f5f9;">${this._esc(item.content)}</td>
                    <td style="padding: 10px;">重要度 ${item.importance}/5</td>
                    <td style="padding: 10px;">
                        <button onclick="CareApp.deleteSelfMemory('episode', '${this._esc(item.episode_id)}')" style="background: none; border: 1px solid rgba(239, 68, 68, 0.4); color: #ef4444; padding: 3px 8px; border-radius: 4px; cursor: pointer;">刪除</button>
                    </td>
                </tr>
            `).join('');
        }
    },

    async deleteSelfMemory(type, id) {
        if (!confirm('確定要刪除這筆雪莉自我記憶嗎？')) return;
        try {
            const res = await fetch(`/api/care/self-memory?type=${type}&id=${encodeURIComponent(id)}`, {
                method: 'DELETE',
                headers: this.getHeaders(),
            });
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || '刪除失敗');
            }
            this.showStatus('自我記憶條目已刪除。');
            this.toast('自我記憶條目已刪除！', 'info');
            this.loadSelfMemory();
        } catch (err) {
            console.error('[CareApp] 刪除自我記憶失敗:', err);
            this.showStatus(`刪除失敗：${err.message}`, true);
            this.toast(`刪除失敗：${err.message}`, 'error');
        }
    },

    // ======================================================================
    // Social Core（沙盒群像 ＆ 決策帳本）
    // ======================================================================
    _socialChannelId() {
        const input = document.getElementById('care-social-channel-id');
        if (!input) return '';
        let value = (input.value || '').trim();
        if (!value) {
            const surferInput = document.getElementById('care-surfer-channel');
            value = surferInput ? (surferInput.value || '').trim() : '';
        }
        return value;
    },

    async loadSocial() {
        try {
            const [overviewRes, personasRes] = await Promise.all([
                fetch('/api/care/social/overview?limit=60&hours=24', { headers: this.getHeaders() }),
                fetch('/api/care/social/personas', { headers: this.getHeaders() }),
            ]);
            const overview = await overviewRes.json();
            const personas = await personasRes.json();
            if (overview.status !== 'success') {
                throw new Error(overview.message || '載入賽博群友總覽失敗');
            }
            this.renderSocialOverview(overview);
            if (personas.status === 'success') {
                this.renderSocialRoster(personas.personas || []);
                this.fillSoulOptions(personas.available_souls || []);
            }
            if (this._socialChannelId()) {
                await this.loadSocialScene();
            }
        } catch (err) {
            console.error('[CareApp] 載入沙盒群像失敗:', err);
            this.showStatus(`載入沙盒群像失敗：${err.message}`, true);
        }
    },

    renderSocialOverview(data) {
        const badge = document.getElementById('care-social-sandbox-badge');
        if (badge) {
            const enabled = !!data.sandbox_enabled;
            badge.textContent = enabled ? '沙盒總開關：已開啟' : '沙盒總開關：關閉（正式群安全）';
            badge.style.background = enabled ? 'rgba(16, 185, 129, 0.18)' : 'rgba(239, 68, 68, 0.18)';
            badge.style.color = enabled ? '#6ee7b7' : '#fca5a5';
            badge.style.borderColor = enabled ? 'rgba(16, 185, 129, 0.35)' : 'rgba(239, 68, 68, 0.35)';
        }

        const channelId = this._socialChannelId();
        const summary = document.getElementById('care-social-mode-summary');
        if (summary) {
            const modeRow = (data.modes || []).find(m => String(m.channel_id) === String(channelId));
            if (!channelId) {
                summary.innerHTML = '請先輸入沙盒頻道 ID，再載入 Scene 與模式。';
            } else if (!modeRow) {
                summary.innerHTML = `頻道 <code>${this._esc(channelId)}</code> 目前未設定，系統視為 <strong style="color:#6ee7b7;">SOLO（只有雪莉）</strong>。`;
            } else {
                const modeLabel = modeRow.mode === 'SANDBOX_GROUP' ? 'SANDBOX_GROUP（多角色沙盒）' : 'SOLO（只有雪莉）';
                summary.innerHTML = `頻道 <code>${this._esc(channelId)}</code>：<strong>${this._esc(modeLabel)}</strong>，
                    啟用中：${modeRow.enabled ? '是' : '否'}，允許 Webhook：${modeRow.allow_webhooks ? '是' : '否'}。
                    ${modeRow.note ? `備註：${this._esc(modeRow.note)}` : ''}`;
            }
        }

        const limits = data.limits || {};
        const statsEl = document.getElementById('care-social-stats');
        if (statsEl) {
            const chips = Object.entries(data.stats || {}).map(([action, info]) => {
                const color = action === 'SPEAK' ? '#4ade80' : (action === 'SILENT' ? '#fbbf24' : '#94a3b8');
                return `<span style="padding: 3px 9px; border-radius: 999px; font-size: 0.76rem; background: rgba(255,255,255,0.06); color: ${color};">${this._esc(action)} × ${info.count}（均分 ${info.avg_score}）</span>`;
            });
            chips.push(`<span style="padding: 3px 9px; border-radius: 999px; font-size: 0.76rem; background: rgba(139, 92, 246, 0.12); color: #c4b5fd;">NPC 上限 ${limits.max_npcs ?? 3}／連續上限 ${limits.max_consecutive ?? 3}</span>`);
            statsEl.innerHTML = chips.join('');
        }

        this.renderSocialDecisions(data.decisions || []);
    },

    renderSocialRoster(personas) {
        const tbody = document.getElementById('care-social-roster-body');
        if (!tbody) return;
        if (!personas.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="padding: 14px; color: #94a3b8;">目前沒有任何 persona；雪莉會在第一次發言時自動建立。</td></tr>';
            return;
        }
        tbody.innerHTML = personas.map(p => {
            const isPrimary = !!p.is_primary;
            const statusColor = p.enabled ? '#4ade80' : '#94a3b8';
            const action = isPrimary
                ? '<span style="color:#94a3b8; font-size:0.75rem;">正式群唯一身分</span>'
                : `<button onclick="CareApp.disableNpc('${this._esc(p.persona_key)}')" style="background:none;border:1px solid rgba(239,68,68,0.4);color:#ef4444;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:0.75rem;">停用</button>`;
            return `
                <tr>
                    <td style="padding: 7px; font-family: monospace;">${this._esc(p.persona_key)}</td>
                    <td style="padding: 7px;">${this._esc(p.display_name)}${isPrimary ? ' <span style="font-size:0.7rem;color:#c4b5fd;">(PRIMARY)</span>' : ''}</td>
                    <td style="padding: 7px; font-family: monospace; font-size: 0.75rem;">${this._esc(p.soul_name)}</td>
                    <td style="padding: 7px;">${this._esc(p.role)}</td>
                    <td style="padding: 7px; color: ${statusColor};">${p.enabled ? '啟用' : '停用'}</td>
                    <td style="padding: 7px;">${action}</td>
                </tr>
            `;
        }).join('');
    },

    fillSoulOptions(souls) {
        const select = document.getElementById('care-npc-soul');
        if (!select) return;
        if (!souls.length) {
            select.innerHTML = '<option value="">（file/soul/sandbox/ 尚無檔案）</option>';
            return;
        }
        select.innerHTML = souls.map(name => `<option value="${this._esc(name)}">${this._esc(name)}</option>`).join('');
    },

    async loadSocialScene() {
        const channelId = this._socialChannelId();
        if (!channelId) return;
        const summaryEl = document.getElementById('care-social-scene-summary');
        const turnsEl = document.getElementById('care-social-scene-turns');
        try {
            const res = await fetch(`/api/care/social/scene?channel_id=${encodeURIComponent(channelId)}`, { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || '載入 Scene 失敗');

            if (summaryEl) {
                const mode = data.mode || {};
                const scene = data.scene;
                const room = data.room || {};
                summaryEl.innerHTML = `
                    <div>模式：<strong>${this._esc(mode.mode || 'SOLO')}</strong>（${this._esc(mode.reason || '—')}）</div>
                    <div>Scene：${scene ? `#${scene.scene_id}／${this._esc(scene.status)}／${scene.turns_used}/${scene.max_turns}` : '目前沒有 open scene'}</div>
                    <div>上次人類發言：${this._time(room.last_human_at)}　冷卻至：${this._time(room.cooldown_until)}</div>
                `;
            }
            if (turnsEl) {
                const turns = data.turns || [];
                if (!turns.length) {
                    turnsEl.innerHTML = '<div style="color:#94a3b8;">目前沒有 scene turn。</div>';
                } else {
                    turnsEl.innerHTML = turns.map(t => {
                        const who = t.role === 'human' ? '群友' : (t.role === 'bot' ? '雪莉' : (t.persona_key || 'NPC'));
                        const color = t.role === 'human' ? '#94a3b8' : (t.role === 'bot' ? '#c4b5fd' : '#60a5fa');
                        return `<div style="padding: 6px 8px; border-radius: 6px; background: rgba(15,23,42,0.6);">
                            <span style="color:${color}; font-weight:600;">${this._esc(who)}</span>
                            <span style="color:#64748b; font-size:0.72rem; margin-left:6px;">${this._esc(this._time(t.created_at))}</span>
                            <div style="margin-top:3px; color:#e2e8f0;">${this._esc(t.content)}</div>
                        </div>`;
                    }).join('');
                }
            }
        } catch (err) {
            console.error('[CareApp] 載入 Scene 失敗:', err);
            if (summaryEl) summaryEl.textContent = `載入 Scene 失敗：${err.message}`;
        }
    },

    async loadSocialDecisions() {
        try {
            const action = (document.getElementById('care-social-decision-action') || {}).value || '';
            const params = new URLSearchParams({ limit: '100', hours: '24' });
            if (action) params.set('action', action);
            const res = await fetch(`/api/care/social/decisions?${params.toString()}`, { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || '載入決策帳本失敗');
            this.renderSocialDecisions(data.decisions || []);
        } catch (err) {
            console.error('[CareApp] 載入決策帳本失敗:', err);
            this.showStatus(`載入決策帳本失敗：${err.message}`, true);
        }
    },

    renderSocialDecisions(decisions) {
        const tbody = document.getElementById('care-social-decisions-body');
        if (!tbody) return;
        if (!decisions.length) {
            tbody.innerHTML = '<tr><td colspan="8" style="padding: 16px; color: #94a3b8;">尚無決策紀錄（SPEAK 與 SILENT 都會留痕）。</td></tr>';
            return;
        }
        tbody.innerHTML = decisions.map(d => {
            const actionColor = d.action === 'SPEAK' ? '#4ade80' : (d.action === 'SILENT' ? '#fbbf24' : '#94a3b8');
            const reasons = (d.reasons || []).join('、') || '—';
            return `
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.04);">
                    <td style="padding: 6px; white-space: nowrap;">${this._esc(this._time(d.created_at))}</td>
                    <td style="padding: 6px; font-family: monospace;">${this._esc(d.channel_id)}</td>
                    <td style="padding: 6px;">${this._esc(d.mode)}</td>
                    <td style="padding: 6px;">${this._esc(d.trigger_type)}</td>
                    <td style="padding: 6px;">${this._esc(d.persona_key || '—')}</td>
                    <td style="padding: 6px; color:${actionColor}; font-weight:600;">${this._esc(d.action)}</td>
                    <td style="padding: 6px;">${Number(d.score || 0).toFixed(2)}</td>
                    <td style="padding: 6px; color:#94a3b8; max-width: 320px;">${this._esc(reasons)}</td>
                </tr>
            `;
        }).join('');
    },

    async saveChannelMode(mode) {
        const channelId = this._socialChannelId();
        if (!/^\d+$/.test(channelId)) {
            this.showStatus('請先輸入有效的沙盒頻道 ID（純數字）。', true);
            return;
        }
        const isSandbox = mode === 'SANDBOX_GROUP';
        if (isSandbox && !confirm('確定要把此頻道設為 SANDBOX_GROUP？這會允許 Webhook NPC 在此頻道發言。')) return;
        try {
            const res = await fetch('/api/care/social/modes', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({
                    channel_id: channelId,
                    mode,
                    enabled: isSandbox,
                    allow_webhooks: isSandbox,
                    note: isSandbox ? 'WebUI 設為沙盒群像' : 'WebUI 降回 SOLO',
                }),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || '設定頻道模式失敗');
            this.showStatus(isSandbox ? '已設為沙盒群像（仍需至少一個啟用中的 NPC）。' : '已降回 SOLO，只允許雪莉發言。');
            this.toast('設定已儲存！', 'success');
            await this.loadSocial();
        } catch (err) {
            console.error('[CareApp] 設定頻道模式失敗:', err);
            this.showStatus(`設定頻道模式失敗：${err.message}`, true);
            this.toast(`設定頻道模式失敗：${err.message}`, 'error');
        }
    },

    async killSandbox() {
        const channelId = this._socialChannelId();
        if (!/^\d+$/.test(channelId)) {
            this.showStatus('請先輸入有效的沙盒頻道 ID（純數字）。', true);
            return;
        }
        if (!confirm('⚠️ Kill Switch 會立刻降回 SOLO、關閉 open scene、停用所有 NPC 並取消待送回合。確定執行？')) return;
        try {
            const res = await fetch('/api/care/social/kill', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({ channel_id: channelId }),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'Kill Switch 失敗');
            this.showStatus('Kill Switch 已執行：此頻道已降回 SOLO。');
            this.toast('Kill Switch 已執行！', 'warning');
            await this.loadSocial();
        } catch (err) {
            console.error('[CareApp] Kill Switch 失敗:', err);
            this.showStatus(`Kill Switch 失敗：${err.message}`, true);
            this.toast(`Kill Switch 失敗：${err.message}`, 'error');
        }
    },

    async addNpc() {
        const channelId = this._socialChannelId();
        const key = (document.getElementById('care-npc-key') || {}).value || '';
        const name = (document.getElementById('care-npc-name') || {}).value || '';
        const soul = (document.getElementById('care-npc-soul') || {}).value || '';
        const style = (document.getElementById('care-npc-style') || {}).value || '';
        if (!/^\d+$/.test(channelId)) {
            this.showStatus('請先輸入有效的沙盒頻道 ID（純數字）。', true);
            return;
        }
        if (!key.trim() || !name.trim() || !soul.trim()) {
            this.showStatus('persona_key、顯示名稱與 SOUL 都必須填寫。', true);
            return;
        }
        try {
            const res = await fetch('/api/care/social/personas', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({
                    channel_id: channelId,
                    persona_key: key.trim(),
                    display_name: name.trim(),
                    soul_name: soul.trim(),
                    speech_style: style.trim(),
                    enabled: true,
                }),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || '新增 NPC 失敗');
            this.showStatus(`NPC「${name.trim()}」已建立。`);
            this.toast('NPC 已成功加入！', 'success');
            ['care-npc-key', 'care-npc-name', 'care-npc-style'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = '';
            });
            await this.loadSocial();
        } catch (err) {
            console.error('[CareApp] 新增 NPC 失敗:', err);
            this.showStatus(`新增 NPC 失敗：${err.message}`, true);
            this.toast(`新增 NPC 失敗：${err.message}`, 'error');
        }
    },

    async disableNpc(personaKey) {
        if (!confirm(`確定要停用 NPC「${personaKey}」嗎？（不會刪除 Webhook 歷史）`)) return;
        try {
            const res = await fetch(`/api/care/social/personas/${encodeURIComponent(personaKey)}`, {
                method: 'DELETE',
                headers: this.getHeaders(),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || '停用 NPC 失敗');
            this.showStatus('NPC 已停用。');
            await this.loadSocial();
        } catch (err) {
            console.error('[CareApp] 停用 NPC 失敗:', err);
            this.showStatus(`停用 NPC 失敗：${err.message}`, true);
        }
    },
};

window.CareApp = CareApp;
