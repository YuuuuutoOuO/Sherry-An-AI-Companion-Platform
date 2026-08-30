/**
 * mod/sys_monitor/static/sys_monitor.js
 * 系統資源監控 Web UI 前端外掛與即時更新邏輯。
 */

(function () {
    let sysTimer = null;
    let sysCurrentRange = 30; // 秒
    let sysChartCtx = null;
    let sysChartData = [];

    // ──────────────────────────────────────────────────────────────────────────
    // 工具函式
    // ──────────────────────────────────────────────────────────────────────────

    function fmtBytes(value) {
        if (value === null || value === undefined) return '-';
        const n = Number(value);
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0;
        let v = n;
        while (Math.abs(v) >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
        return i === 0 ? `${Math.round(v)} ${units[i]}` : `${v.toFixed(2)} ${units[i]}`;
    }

    function fmtGBMb(value) {
        // 輸入為 MB
        if (value === null || value === undefined) return '-';
        const n = Number(value);
        if (n >= 1024) return `${(n / 1024).toFixed(1)} GB`;
        return `${Math.round(n)} MB`;
    }

    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    function pct(value, suffix = '%') {
        return value === null || value === undefined ? '-' : `${value}${suffix}`;
    }

    // ──────────────────────────────────────────────────────────────────────────
    // 資料獲取與渲染
    // ──────────────────────────────────────────────────────────────────────────

    async function loadSysStatus() {
        try {
            const res = await fetch('/api/sys_monitor/status', {
                headers: window.SherryUI ? window.SherryUI.getAuthHeaders() : {}
            });
            if (!res.ok) return;
            const data = await res.json();
            if (data.status !== 'success') return;

            const mon = data.monitor || {};
            const summary = data.summary || {};

            setText('sysmon-ctx-interval', mon.interval_seconds ?? '-');
            setText('sysmon-ctx-flush', mon.flush_seconds ?? '-');
            setText('sysmon-history-path', mon.history_path || 'data/sys_monitor/series.jsonl');
            setText('sysmon-sample-count', `共 ${mon.sample_count ?? 0} 筆採樣 · 已 flush ${mon.flush_count ?? 0} 次`);

            setText('sysmon-cpu', pct(summary.cpu_percent));
            setText('sysmon-mem', pct(summary.memory_percent));
            setText('sysmon-mem-detail', `已用 ${fmtBytes(summary.memory_used)}`);

            const storagePct = (summary.storage_used !== null && summary.storage_used !== undefined
                && summary.storage_total > 0)
                ? `${((summary.storage_used / summary.storage_total) * 100).toFixed(1)}%`
                : '-';
            setText('sysmon-storage', storagePct);
            setText('sysmon-storage-detail', `${fmtBytes(summary.storage_used)} / ${fmtBytes(summary.storage_total)}`);

            setText('sysmon-proc-cpu', pct(summary.process_cpu_percent));
            setText('sysmon-proc-mem', fmtBytes(summary.process_memory_rss));

            // GPU
            const gpuEl = document.getElementById('sysmon-gpu-status');
            const gpu = summary.gpu || {};
            const attached = data.attached;
            if (!attached || (summary.gpu && Object.keys(summary.gpu).length === 0)) {
                if (gpuEl) { gpuEl.textContent = '未偵測到'; gpuEl.className = 'sysmon-tag sysmon-tag--off'; }
                setText('sysmon-gpu-detail', '本機無 NVIDIA GPU 或 nvidia-smi 不可用，僅監測 CPU / DRAM / Storage。');
                setText('sysmon-gpu-util', '-');
                setText('sysmon-gpu-vram', '-');
                setText('sysmon-gpu-temp', '-');
            } else {
                if (gpuEl) {
                    const busy = gpu.avg_utilization_percent !== null && gpu.avg_utilization_percent !== undefined && gpu.avg_utilization_percent > 0;
                    gpuEl.textContent = busy ? '🏃 運作中' : '😴 閒置';
                    gpuEl.className = 'sysmon-tag sysmon-tag--ok';
                }
                setText('sysmon-gpu-detail', `共 ${gpu.gpu_count ?? '-'} 顆 GPU · VRAM ${fmtGBMb(gpu.memory_used_mb)} / ${fmtGBMb(gpu.memory_total_mb)}`);
                setText('sysmon-gpu-util', pct(gpu.avg_utilization_percent));
                setText('sysmon-gpu-vram', fmtGBMb(gpu.memory_used_mb));
                setText('sysmon-gpu-temp', gpu.avg_temperature_c !== null && gpu.avg_temperature_c !== undefined ? `${gpu.avg_temperature_c}°C` : '-');
            }
        } catch (e) {
            console.error('[SysMonitor] 載入狀態失敗:', e);
        }
    }

    async function loadSysHistory() {
        try {
            const limit = Math.max(30, Math.ceil(sysCurrentRange / 5));
            const res = await fetch(`/api/sys_monitor/history?limit=${limit}`, {
                headers: window.SherryUI ? window.SherryUI.getAuthHeaders() : {}
            });
            if (!res.ok) return;
            const data = await res.json();
            if (data.status !== 'success') return;
            sysChartData = data.rows || [];
            renderSysChart();
        } catch (e) {
            console.error('[SysMonitor] 載入歷史失敗:', e);
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    // 趨勢圖 (Canvas 自繪，無外部依賴)
    // ──────────────────────────────────────────────────────────────────────────

    function renderSysChart() {
        const chartEl = document.getElementById('sysmon-chart');
        if (!chartEl) return;
        if (!sysChartData || sysChartData.length < 2) {
            chartEl.innerHTML = '<div class="sysmon-empty-state"><div class="sysmon-empty-icon">📈</div><h3>尚未有足夠趨勢資料</h3><p>持續累積採樣中，將自動繪製 CPU / DRAM / GPU 趨勢線。</p></div>';
            sysChartCtx = null;
            return;
        }

        // 建立 canvas
        if (!sysChartCtx) {
            chartEl.innerHTML = '<canvas id="sysmon-canvas"></canvas>';
        }
        const canvas = document.getElementById('sysmon-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        sysChartCtx = ctx;

        const rect = chartEl.getBoundingClientRect();
        const width = Math.max(320, rect.width || chartEl.clientWidth || 600);
        const height = 260;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = width * dpr;
        canvas.height = height * dpr;
        canvas.style.width = width + 'px';
        canvas.style.height = height + 'px';
        ctx.scale(dpr, dpr);

        ctx.clearRect(0, 0, width, height);

        const padL = 42, padR = 14, padT = 16, padB = 28;
        const plotW = width - padL - padR;
        const plotH = height - padT - padB;

        // 收集三條系列 (0-100%)
        const series = ['cpu', 'memory', 'gpu'].map(key => {
            if (key === 'gpu') {
                return sysChartData.map(r => {
                    const g = r && r.gpu && r.gpu.summary;
                    if (g && g.avg_utilization_percent !== null && g.avg_utilization_percent !== undefined) return Number(g.avg_utilization_percent);
                    return null;
                });
            }
            const node = r => r && r[key];
            return sysChartData.map(r => {
                const n = node(r);
                const v = n && n.percent;
                return v === null || v === undefined ? null : Number(v);
            });
        });

        // 只有 CPU / DRAM 一定有資料
        const dataLen = sysChartData.length;

        // 背景網格
        ctx.strokeStyle = 'rgba(148,163,184,0.15)';
        ctx.fillStyle = '#94a3b8';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right';
        for (let g = 0; g <= 4; g++) {
            const y = padT + plotH - (plotH * g / 4);
            ctx.beginPath();
            ctx.moveTo(padL, y);
            ctx.lineTo(width - padR, y);
            ctx.stroke();
            ctx.fillText(`${g * 25}%`, padL - 6, y + 3);
        }

        // 時間軸標籤
        const colors = { cpu: '#38bdf8', memory: '#4ade80', gpu: '#a78bfa' };
        const labels = { cpu: 'CPU', memory: 'DRAM', gpu: 'GPU' };
        series.forEach((vals, idx) => {
            drawLine(ctx, vals, padL + plotW, padT, padL, padT + plotH, colors[idx], dataLen);
        });

        // 圖例
        let legendX = padL;
        ctx.textAlign = 'left';
        series.forEach((vals, idx) => {
            const name = labels[idx];
            const hasData = vals.some(v => v !== null);
            const w = ctx.measureText(name).width;
            ctx.fillStyle = colors[idx];
            ctx.fillRect(legendX, padT - 8, 10, 10);
            ctx.fillStyle = hasData ? colors[idx] : '#64748b';
            ctx.textAlign = 'left';
            ctx.fillText(name, legendX + 14, padT);
            legendX += w + 30;
        });

        // 時間標籤 (最右為 now，向左回推)
        ctx.textAlign = 'center';
        ctx.fillStyle = '#94a3b8';
        const intervalSec = 5;
        const totalSec = dataLen * intervalSec;
        ctx.fillText(`-${totalSec}s`, padL, height - 8);
        ctx.fillText('now', width - padR - 20, height - 8);
    }

    function drawLine(ctx, vals, xRight, top, left, bottom, color, dataLen) {
        const plotW = xRight - left;
        const plotH = bottom - top;
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        let started = false;
        for (let i = 0; i < dataLen; i++) {
            const v = vals[i];
            if (v === null || v === undefined) continue;
            const x = left + (plotW * i / (dataLen - 1));
            const y = top + plotH - (plotH * Math.min(100, Math.max(0, v)) / 100);
            if (!started) { ctx.moveTo(x, y); started = true; }
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
    }

    // ──────────────────────────────────────────────────────────────────────────
    // 全域操作函式
    // ──────────────────────────────────────────────────────────────────────────

    window.setSysRange = function (seconds) {
        sysCurrentRange = seconds;
        document.querySelectorAll('.sysmon-range-btn').forEach(btn => {
            btn.classList.toggle('active', Number(btn.getAttribute('data-range')) === seconds);
        });
        loadSysHistory();
    };

    window.loadSysData = async function (isManual = false) {
        await Promise.all([loadSysStatus(), loadSysHistory()]);
        if (isManual && window.SherryUI) {
            window.SherryUI.showToast('✅ 監控資料已更新！', 'success');
        }
    };

    window.addEventListener('resize', () => {
        if (sysChartCtx) renderSysChart();
    });

    // ──────────────────────────────────────────────────────────────────────────
    // SherryUI 外掛生命週期註冊 (MUST)
    // ──────────────────────────────────────────────────────────────────────────

    if (window.SherryUI && typeof window.SherryUI.registerPlugin === 'function') {
        window.SherryUI.registerPlugin('sys_monitor', {
            viewId: 'sys-monitor',
            title: '🖥️ 系統資源監控主控台',

            onActivate: function () {
                // 重新獲取，確保 canvas 尺寸正確
                sysChartCtx = null;
                window.loadSysData(false);
                if (!sysTimer) {
                    sysTimer = setInterval(() => {
                        window.loadSysData(false);
                    }, 5000);
                }
            },

            onDeactivate: function () {
                if (sysTimer) {
                    clearInterval(sysTimer);
                    sysTimer = null;
                }
                sysChartCtx = null;
            }
        });
    }
})();
