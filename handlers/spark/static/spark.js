/**
 * mod/spark/static/spark.js
 * Spark 推播系統前端邏輯與外掛註冊。
 * 遵循 mod規範 §5。
 */

const SparkApp = {
    currentFilter: '',
    pollTimer: null,

    init() {
        this.onTypeChange();
        this.loadStats();
        this.loadTriggers();
    },

    getHeaders() {
        return window.SherryUI && typeof window.SherryUI.getAuthHeaders === 'function'
            ? window.SherryUI.getAuthHeaders()
            : { 'Content-Type': 'application/json' };
    },

    async loadStats() {
        try {
            const res = await fetch('/api/spark/stats', { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status === 'success' && data.stats) {
                const stats = data.stats;
                const totalEl = document.getElementById('spark-stat-total');
                const activeEl = document.getElementById('spark-stat-active');
                const pausedEl = document.getElementById('spark-stat-paused');
                if (totalEl) totalEl.textContent = stats.total || 0;
                if (activeEl) activeEl.textContent = stats.active || 0;
                if (pausedEl) pausedEl.textContent = stats.paused || 0;
            }
        } catch (err) {
            console.error('[SparkApp] 載入統計失敗:', err);
        }
    },

    async loadTriggers() {
        try {
            let url = '/api/spark/triggers';
            if (this.currentFilter) {
                url += `?type=${encodeURIComponent(this.currentFilter)}`;
            }
            const res = await fetch(url, { headers: this.getHeaders() });
            const data = await res.json();
            if (data.status === 'success') {
                this.renderTriggers(data.triggers || []);
            } else {
                const container = document.getElementById('spark-triggers-container');
                if (container) {
                    container.innerHTML = `
                        <div style="text-align: center; padding: 40px; background: rgba(239, 68, 68, 0.1); border-radius: 8px; color: #f87171;">
                            ❌ 載入觸發器失敗: ${data.message || '未知錯誤'}
                        </div>
                    `;
                }
                this.showError(data.message || '載入觸發器失敗');
            }
        } catch (err) {
            console.error('[SparkApp] 載入觸發器失敗:', err);
            const container = document.getElementById('spark-triggers-container');
            if (container) {
                container.innerHTML = `
                    <div style="text-align: center; padding: 40px; background: rgba(239, 68, 68, 0.1); border-radius: 8px; color: #f87171;">
                        ❌ 載入觸發器發生異常: ${err.message}
                    </div>
                `;
            }
            this.showError(err.message);
        }
    },

    renderTriggers(triggers) {
        const container = document.getElementById('spark-triggers-container');
        if (!container) return;

        if (!triggers || triggers.length === 0) {
            container.innerHTML = `
                <div style="text-align: center; padding: 40px; background: rgba(30, 41, 59, 0.4); border-radius: 8px; color: #94a3b8;">
                    📡 尚無任何推播觸發器。點擊上方「➕ 新增推播」建立第一個任務！
                </div>
            `;
            return;
        }

        const typeEmojis = {
            price: '💰 價格',
            schedule: '⏰ 定時',
            rss: '📰 RSS',
            weather: '🌤️ 天氣'
        };

        const html = triggers.map(t => {
            const typeLabel = typeEmojis[t.trigger_type] || t.trigger_type;
            const statusBadge = t.is_active
                ? '<span style="background: rgba(74, 222, 128, 0.2); color: #4ade80; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">🟢 啟用中</span>'
                : '<span style="background: rgba(251, 191, 36, 0.2); color: #fbbf24; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">⏸️ 已暫停</span>';
            
            const lastRun = t.last_triggered_at ? t.last_triggered_at.replace('T', ' ').slice(0, 19) : '尚未觸發';
            const condDetails = Object.entries(t.condition || {}).map(([k, v]) => `<span style="background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px; margin-right: 4px;">${k}: <b>${v}</b></span>`).join(' ');

            return `
                <div style="background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; padding: 16px; display: flex; justify-content: space-between; align-items: center; gap: 15px;">
                    <div style="flex: 1;">
                        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 6px;">
                            <span style="font-weight: 700; color: #c4b5fd;">[${typeLabel}]</span>
                            <code style="background: rgba(0,0,0,0.4); padding: 2px 6px; border-radius: 4px; font-size: 0.8rem; color: #94a3b8;">${t.trigger_id.slice(0, 8)}</code>
                            ${statusBadge}
                            <span style="font-size: 0.85rem; color: #64748b;">頻道: ${t.channel_id}</span>
                        </div>
                        <div style="font-size: 0.9rem; color: #cbd5e1; margin-bottom: 6px;">
                            條件: ${condDetails}
                        </div>
                        ${t.message ? `<div style="font-size: 0.85rem; color: #a78bfa; margin-bottom: 4px;">💬 訊息: "${t.message}"</div>` : ''}
                        <div style="font-size: 0.8rem; color: #64748b;">
                            上次觸發: ${lastRun} | 冷卻: ${t.cooldown_seconds}s
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <button onclick="SparkApp.testTrigger('${t.trigger_id}')" style="padding: 6px 12px; background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 6px; cursor: pointer; font-size: 0.85rem;">
                            🔍 測試
                        </button>
                        <button onclick="SparkApp.toggleTrigger('${t.trigger_id}')" style="padding: 6px 12px; background: rgba(251, 191, 36, 0.15); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.3); border-radius: 6px; cursor: pointer; font-size: 0.85rem;">
                            ${t.is_active ? '⏸️ 暫停' : '▶️ 啟用'}
                        </button>
                        <button onclick="SparkApp.deleteTrigger('${t.trigger_id}')" style="padding: 6px 12px; background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 6px; cursor: pointer; font-size: 0.85rem;">
                            🗑️ 刪除
                        </button>
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = html;
    },

    setFilter(type) {
        this.currentFilter = type;
        const tabs = document.querySelectorAll('.spark-tab');
        tabs.forEach(tab => {
            tab.style.background = 'rgba(30, 41, 59, 0.7)';
            tab.style.color = '#94a3b8';
            tab.style.border = '1px solid rgba(255,255,255,0.08)';
        });
        if (event && event.target) {
            event.target.style.background = 'rgba(139, 92, 246, 0.2)';
            event.target.style.color = '#c4b5fd';
            event.target.style.border = '1px solid #8b5cf6';
        }
        this.loadTriggers();
    },

    openAddModal() {
        const modal = document.getElementById('spark-add-modal');
        if (modal) modal.style.display = 'flex';
        this.onTypeChange();
    },

    closeAddModal() {
        const modal = document.getElementById('spark-add-modal');
        if (modal) modal.style.display = 'none';
    },

    onTypeChange() {
        const typeEl = document.getElementById('spark-form-type');
        const container = document.getElementById('spark-form-dynamic-fields');
        if (!typeEl || !container) return;

        const type = typeEl.value;
        if (type === 'price') {
            container.innerHTML = `
                <div style="margin-bottom: 8px;">
                    <label style="font-size: 0.8rem; color: #94a3b8;">標的代號 (Ticker)</label>
                    <input id="spark-field-ticker" type="text" placeholder="例如 BTC, 2330, AAPL" required style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;" />
                </div>
                <div style="display: flex; gap: 8px;">
                    <div style="flex: 1;">
                        <label style="font-size: 0.8rem; color: #94a3b8;">方向</label>
                        <select id="spark-field-direction" style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;">
                            <option value="above">高於目標價 (above)</option>
                            <option value="below">低於目標價 (below)</option>
                            <option value="cross_above">向上突破 (cross_above)</option>
                            <option value="cross_below">跌破 (cross_below)</option>
                        </select>
                    </div>
                    <div style="flex: 1;">
                        <label style="font-size: 0.8rem; color: #94a3b8;">目標價格</label>
                        <input id="spark-field-target-price" type="number" step="any" required placeholder="例如 65000" style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;" />
                    </div>
                </div>
            `;
        } else if (type === 'schedule') {
            container.innerHTML = `
                <div style="margin-bottom: 8px;">
                    <label style="font-size: 0.8rem; color: #94a3b8;">排程模式</label>
                    <select id="spark-field-sched-mode" style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;">
                        <option value="interval">固定間隔 (每 N 分鐘)</option>
                        <option value="daily_time">每日固定時間 (HH:MM)</option>
                    </select>
                </div>
                <div>
                    <label style="font-size: 0.8rem; color: #94a3b8;">間隔分鐘數 或 時間 (如 60 或 08:30)</label>
                    <input id="spark-field-sched-val" type="text" placeholder="60 或 08:30" required style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;" />
                </div>
            `;
        } else if (type === 'rss') {
            container.innerHTML = `
                <div style="margin-bottom: 8px;">
                    <label style="font-size: 0.8rem; color: #94a3b8;">RSS / YouTube Feed 網址</label>
                    <input id="spark-field-feed-url" type="url" placeholder="https://..." required style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;" />
                </div>
                <div>
                    <label style="font-size: 0.8rem; color: #94a3b8;">標題關鍵字過濾 (選填)</label>
                    <input id="spark-field-keyword" type="text" placeholder="例如 AI, release" style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;" />
                </div>
            `;
        } else if (type === 'weather') {
            container.innerHTML = `
                <div style="margin-bottom: 8px;">
                    <label style="font-size: 0.8rem; color: #94a3b8;">城市名稱</label>
                    <input id="spark-field-city" type="text" placeholder="例如 台北, Tokyo, London" required style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;" />
                </div>
                <div style="display: flex; gap: 8px;">
                    <div style="flex: 1;">
                        <label style="font-size: 0.8rem; color: #94a3b8;">預警類型</label>
                        <select id="spark-field-alert-type" style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;">
                            <option value="rain">降雨警報 (rain)</option>
                            <option value="temp_above">高溫特報 (temp_above)</option>
                            <option value="temp_below">低溫特報 (temp_below)</option>
                            <option value="daily_summary">每日概況 (daily_summary)</option>
                        </select>
                    </div>
                    <div style="flex: 1;">
                        <label style="font-size: 0.8rem; color: #94a3b8;">溫度/機率門檻 (選填)</label>
                        <input id="spark-field-threshold" type="number" placeholder="如 35 或 10" style="width: 100%; padding: 6px; border-radius: 4px; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); color: white;" />
                    </div>
                </div>
            `;
        }
    },

    async submitAddTrigger(e) {
        e.preventDefault();
        const type = document.getElementById('spark-form-type').value;
        const channelId = document.getElementById('spark-form-channel').value;
        const message = document.getElementById('spark-form-message').value;
        const cooldown = parseInt(document.getElementById('spark-form-cooldown').value || '3600', 10);

        let condition = {};
        if (type === 'price') {
            condition = {
                ticker: document.getElementById('spark-field-ticker').value,
                direction: document.getElementById('spark-field-direction').value,
                target_price: parseFloat(document.getElementById('spark-field-target-price').value)
            };
        } else if (type === 'schedule') {
            const mode = document.getElementById('spark-field-sched-mode').value;
            const val = document.getElementById('spark-field-sched-val').value;
            condition = { mode: mode };
            if (mode === 'interval') {
                condition.interval_minutes = parseInt(val, 10) || 60;
            } else {
                condition.time = val;
            }
        } else if (type === 'rss') {
            condition = {
                feed_url: document.getElementById('spark-field-feed-url').value,
                keyword: document.getElementById('spark-field-keyword').value || ''
            };
        } else if (type === 'weather') {
            condition = {
                city: document.getElementById('spark-field-city').value,
                alert_type: document.getElementById('spark-field-alert-type').value
            };
            const thresh = document.getElementById('spark-field-threshold').value;
            if (thresh) condition.threshold = parseFloat(thresh);
        }

        try {
            const res = await fetch('/api/spark/triggers', {
                method: 'POST',
                headers: this.getHeaders(),
                body: JSON.stringify({
                    channel_id: parseInt(channelId, 10),
                    trigger_type: type,
                    condition: condition,
                    message: message,
                    cooldown_seconds: cooldown
                })
            });
            const data = await res.json();
            if (data.status === 'success') {
                this.showToast('✅ 觸發器建立成功！', 'success');
                this.closeAddModal();
                this.loadStats();
                this.loadTriggers();
            } else {
                this.showToast(data.message || '建立失敗', 'error');
            }
        } catch (err) {
            this.showToast(`建立失敗: ${err.message}`, 'error');
        }
    },

    async toggleTrigger(id) {
        try {
            const res = await fetch(`/api/spark/triggers/${id}/toggle`, {
                method: 'POST',
                headers: this.getHeaders()
            });
            const data = await res.json();
            if (data.status === 'success') {
                this.showToast(`觸發器狀態已更新`, 'info');
                this.loadStats();
                this.loadTriggers();
            }
        } catch (err) {
            this.showToast(`切換失敗: ${err.message}`, 'error');
        }
    },

    async deleteTrigger(id) {
        if (!confirm('確定要刪除此推播觸發器嗎？')) return;
        try {
            const res = await fetch(`/api/spark/triggers/${id}`, {
                method: 'DELETE',
                headers: this.getHeaders()
            });
            const data = await res.json();
            if (data.status === 'success') {
                this.showToast('🗑️ 觸發器已刪除', 'success');
                this.loadStats();
                this.loadTriggers();
            }
        } catch (err) {
            this.showToast(`刪除失敗: ${err.message}`, 'error');
        }
    },

    async testTrigger(id) {
        this.showToast('🔍 正在測試觸發器條件...', 'info');
        try {
            const res = await fetch(`/api/spark/triggers/${id}/test`, {
                method: 'POST',
                headers: this.getHeaders()
            });
            const data = await res.json();
            if (data.status === 'success') {
                const icon = data.should_fire ? '✅' : '❌';
                alert(`${icon} 測試結果 (應觸發: ${data.should_fire ? '是' : '否'})\n\n摘要:\n${data.current_value}`);
            } else {
                this.showToast(data.message || '測試失敗', 'error');
            }
        } catch (err) {
            this.showToast(`測試失敗: ${err.message}`, 'error');
        }
    },

    showToast(msg, type) {
        if (window.SherryUI && typeof window.SherryUI.showToast === 'function') {
            window.SherryUI.showToast(msg, type);
        } else {
            console.log(`[Toast ${type}] ${msg}`);
        }
    },

    showError(msg) {
        this.showToast(msg, 'error');
    }
};

// 掛載至全域視窗物件
window.SparkApp = SparkApp;

// 註冊 SherryUI 外掛生命週期與自適應初始化
function _setupSparkIntegration() {
    if (window.SherryUI && typeof window.SherryUI.registerPlugin === 'function') {
        window.SherryUI.registerPlugin('spark', {
            viewId: 'spark',
            title: '📡 Spark 條件推播主控台',
            onActivate: function() {
                SparkApp.init();
                if (!SparkApp.pollTimer) {
                    SparkApp.pollTimer = setInterval(() => {
                        SparkApp.loadStats();
                        SparkApp.loadTriggers();
                    }, 10000);
                }
            },
            onDeactivate: function() {
                if (SparkApp.pollTimer) {
                    clearInterval(SparkApp.pollTimer);
                    SparkApp.pollTimer = null;
                }
            }
        });
    }

    // 若載入時當前視圖已為 spark，直接發動初始化
    const viewSpark = document.getElementById('view-spark');
    if (viewSpark && !viewSpark.classList.contains('hidden')) {
        SparkApp.init();
    }
}

_setupSparkIntegration();
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setupSparkIntegration);
}
