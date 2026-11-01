/**
 * mod/stock_trader/static/stock_trader.js
 * =======================================
 * Frontend logic for Sherry Stock Trader Mod.
 * Handles dashboard data loading, Chart.js line chart rendering,
 * real-time indicators, and evaluation triggers.
 */

(function () {
  let stReturnChart = null;

  function _getAuthHeaders() {
    if (window.SherryUI && typeof window.SherryUI.getAuthHeaders === 'function') {
      return window.SherryUI.getAuthHeaders();
    } else if (typeof getAuthHeaders === 'function') {
      return getAuthHeaders();
    }
    return { 'Content-Type': 'application/json' };
  }

  // R5：統一轉義工具——伺服器資料入 innerHTML 前一律先轉義（報告 §5.4.1 政策）
  function _escHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
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

  function _isAdmin() {
    if (window.SherryUI && window.SherryUI.state) {
      return !!window.SherryUI.state.isAdmin;
    }
    if (typeof state !== 'undefined' && state) {
      return !!state.isAdmin;
    }
    return false;
  }

  function _openLoginModal() {
    if (typeof openLoginModal === 'function') {
      openLoginModal();
    }
  }

  async function loadStockTraderData() {
    try {
      // 1. Overview
      const resOver = await fetch('/api/stock_trader/overview', { headers: _getAuthHeaders() });
      if (resOver.ok) {
        const json = await resOver.json();
        if (json.status === 'success') {
          const d = json.data;
          const totalAssetEl = document.getElementById('st-total-asset');
          if (totalAssetEl) {
            totalAssetEl.textContent = `$${d.total_asset_value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
          }

          const pnlColor = d.total_pnl >= 0 ? '#ff4444' : '#00ff88';
          const pnlSign = d.total_pnl >= 0 ? '+' : '';
          const totalPnlSub = document.getElementById('st-total-pnl-sub');
          if (totalPnlSub) {
            totalPnlSub.innerHTML = `<span style="color:${pnlColor}; font-weight:700;">${pnlSign}$${d.total_pnl.toFixed(2)} (${pnlSign}${d.total_pnl_pct.toFixed(2)}%)</span>`;
          }

          const cashEl = document.getElementById('st-cash');
          if (cashEl) {
            cashEl.textContent = `$${d.cash.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
          }

          const posCountSub = document.getElementById('st-positions-count-sub');
          if (posCountSub) {
            posCountSub.textContent = `${d.positions_count} 筆持倉標的`;
          }

          const pnlPctEl = document.getElementById('st-pnl-pct');
          if (pnlPctEl) {
            pnlPctEl.textContent = `${pnlSign}${d.total_pnl_pct.toFixed(2)}%`;
            pnlPctEl.style.color = pnlColor;
          }

          const dayPnlColor = d.day_pnl >= 0 ? '#ff4444' : '#00ff88';
          const daySign = d.day_pnl >= 0 ? '+' : '';
          const dayPnlSub = document.getElementById('st-day-pnl-sub');
          if (dayPnlSub) {
            dayPnlSub.innerHTML = `當日損益: <span style="color:${dayPnlColor}; font-weight:600;">${daySign}$${d.day_pnl.toFixed(2)}</span>`;
          }

          const winRateEl = document.getElementById('st-win-rate');
          if (winRateEl) {
            winRateEl.textContent = `${d.win_rate_pct.toFixed(1)}%`;
          }

          const tradesCountSub = document.getElementById('st-trades-count-sub');
          if (tradesCountSub) {
            tradesCountSub.textContent = `${d.winning_trades_count} / ${d.total_trades_count} 筆交易勝場`;
          }
        }
      }

      // 2. 1-Month Return Rate Line Chart
      const resChart = await fetch('/api/stock_trader/chart', { headers: _getAuthHeaders() });
      if (resChart.ok) {
        const json = await resChart.json();
        if (json.status === 'success') {
          renderStockReturnChart(json.data);
        }
      }

      // 3. Holdings
      const resHold = await fetch('/api/stock_trader/holdings', { headers: _getAuthHeaders() });
      if (resHold.ok) {
        const json = await resHold.json();
        const tbody = document.getElementById('st-holdings-tbody');
        if (tbody) {
          if (json.status === 'success' && json.data.length > 0) {
            let html = '';
            json.data.forEach(h => {
              const color = h.unrealized_pnl >= 0 ? '#ff4444' : '#00ff88';
              const sign = h.unrealized_pnl >= 0 ? '+' : '';
              html += `
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                  <td style="padding: 10px; font-weight:700; color:#00e5ff;">${_escHtml(h.ticker)}</td>
                  <td style="padding: 10px;">${h.shares.toFixed(4)}</td>
                  <td style="padding: 10px;">$${h.avg_cost.toFixed(2)}</td>
                  <td style="padding: 10px;">$${h.current_price.toFixed(2)}</td>
                  <td style="padding: 10px; color:${color}; font-weight:600;">${sign}$${h.unrealized_pnl.toFixed(2)} (${sign}${h.unrealized_pnl_pct.toFixed(2)}%)</td>
                </tr>
              `;
            });
            tbody.innerHTML = html;
          } else {
            tbody.innerHTML = '<tr><td colspan="5" style="padding: 16px; text-align: center; color: var(--text-muted);">無持倉標的 (全額現金)</td></tr>';
          }
        }
      }

      // 4. Trade History
      const resTrades = await fetch('/api/stock_trader/trades', { headers: _getAuthHeaders() });
      if (resTrades.ok) {
        const json = await resTrades.json();
        const tbody = document.getElementById('st-trades-tbody');
        if (tbody) {
          if (json.status === 'success' && json.data.length > 0) {
            let html = '';
            json.data.forEach(t => {
              const actColor = t.action === 'BUY' ? '#00e5ff' : '#ffab00';
              const pnlColor = t.realized_pnl > 0 ? '#ff4444' : (t.realized_pnl < 0 ? '#00ff88' : 'var(--text-muted)');
              const pnlStr = t.action === 'SELL' ? `${t.realized_pnl >= 0 ? '+' : ''}$${t.realized_pnl.toFixed(2)}` : '-';
              html += `
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                  <td style="padding: 8px; color:var(--text-muted); font-size:0.8rem;">${_escHtml(t.timestamp)}</td>
                  <td style="padding: 8px; font-weight:700;">${_escHtml(t.ticker)}</td>
                  <td style="padding: 8px;"><span style="background:rgba(255,255,255,0.08); color:${actColor}; padding:2px 8px; border-radius:4px; font-weight:700; font-size:0.78rem;">${_escHtml(t.action)}</span></td>
                  <td style="padding: 8px;">${t.shares.toFixed(4)}</td>
                  <td style="padding: 8px;">$${t.execution_price.toFixed(2)}</td>
                  <td style="padding: 8px; color:var(--text-muted);">$${t.fee.toFixed(2)}</td>
                  <td style="padding: 8px;">$${t.total_amount.toFixed(2)}</td>
                  <td style="padding: 8px; color:${pnlColor}; font-weight:600;">${pnlStr}</td>
                  <td style="padding: 8px; color:var(--text-muted); max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${_escHtml(t.reason)}">${_escHtml(t.reason)}</td>
                </tr>
              `;
            });
            tbody.innerHTML = html;
          } else {
            tbody.innerHTML = '<tr><td colspan="9" style="padding: 20px; text-align: center; color: var(--text-muted);">尚無交易紀錄</td></tr>';
          }
        }
      }

      // 5. Sherry CoT Thoughts
      const resThoughts = await fetch('/api/stock_trader/thoughts', { headers: _getAuthHeaders() });
      if (resThoughts.ok) {
        const json = await resThoughts.json();
        const container = document.getElementById('st-thought-container');
        if (container) {
          if (json.status === 'success' && json.data.length > 0) {
            let html = '';
            json.data.forEach(th => {
              html += `
                <div style="margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px dashed rgba(255,255,255,0.1);">
                  <div style="font-weight: 700; color: #00e5ff; margin-bottom: 4px; display: flex; justify-content: space-between;">
                    <span>⏱️ [${_escHtml(th.timestamp)}] (${_escHtml(th.trigger_type)})</span>
                  </div>
                  <div style="color: #bbb; margin-bottom: 6px; white-space: pre-wrap;">${_escHtml(th.thought_process)}</div>
                  <div style="color: #00e5ff; font-weight: 600;">🎯 決策結論: ${_escHtml(th.decision_summary)}</div>
                </div>
              `;
            });
            container.innerHTML = html;
          } else {
            container.innerHTML = '<div style="color: var(--text-muted);">尚無思考日誌。美股盤中時間 (23:00~04:00) 即可看到雪莉即時 CoT 推理！</div>';
          }
        }
      }

      // 6. Target Stock Indicators
      const resTargets = await fetch('/api/stock_trader/targets', { headers: _getAuthHeaders() });
      if (resTargets.ok) {
        const json = await resTargets.json();
        const tbody = document.getElementById('st-targets-tbody');
        if (tbody) {
          if (json.status === 'success' && json.data.length > 0) {
            let html = '';
            json.data.forEach(t => {
              const dayColor = t.day_pct >= 0 ? '#ff4444' : '#00ff88';
              const monColor = t.mon_pct >= 0 ? '#ff4444' : '#00ff88';
              const daySign = t.day_pct >= 0 ? '+' : '';
              const monSign = t.mon_pct >= 0 ? '+' : '';
              const macdColor = (t.macd_hist || 0) >= 0 ? '#ff4444' : '#00ff88';
              const macdSign = (t.macd_hist || 0) >= 0 ? '+' : '';
              html += `
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                  <td style="padding: 8px; font-weight:700; color:#00e5ff;">${_escHtml(t.ticker)}</td>
                  <td style="padding: 8px; font-weight:600;">$${t.close.toFixed(2)}</td>
                  <td style="padding: 8px; color:${dayColor}; font-weight:600;">${daySign}${t.day_pct.toFixed(2)}%</td>
                  <td style="padding: 8px; color:${monColor}; font-weight:600;">${monSign}${t.mon_pct.toFixed(2)}%</td>
                  <td style="padding: 8px; font-weight:600;">${t.rsi}</td>
                  <td style="padding: 8px;">$${t.ma20 || 'N/A'}</td>
                  <td style="padding: 8px;">$${t.ma60 || 'N/A'}</td>
                  <td style="padding: 8px;">$${t.ema20 || 'N/A'}</td>
                  <td style="padding: 8px; font-size:0.8rem; color:var(--text-muted);">$${t.bb_upper || 'N/A'} / $${t.bb_lower || 'N/A'}</td>
                  <td style="padding: 8px; color:${macdColor}; font-weight:600;">${macdSign}${t.macd_hist || 0.0}</td>
                  <td style="padding: 8px; font-weight:600;">${t.volume_ratio || 1.0}x</td>
                </tr>
              `;
            });
            tbody.innerHTML = html;
          } else {
            tbody.innerHTML = '<tr><td colspan="11" style="padding: 20px; text-align: center; color: var(--text-muted);">指標抓取中或暫無數據</td></tr>';
          }
        }
      }

    } catch (e) {
      console.error('Failed to load stock trader data:', e);
    }
  }

  function renderStockReturnChart(chartData) {
    const canvas = document.getElementById('st-return-chart');
    if (!canvas) return;

    if (stReturnChart) {
      stReturnChart.destroy();
      stReturnChart = null;
    }

    if (!chartData || chartData.length === 0) {
      chartData = [
        { date: '起初', return_rate_pct: 0.0, total_asset_value: 10000.0, day_pnl: 0.0 }
      ];
    }

    const labels = chartData.map(d => d.date);
    const values = chartData.map(d => d.return_rate_pct);

    const ctx = canvas.getContext('2d');
    const lastVal = values[values.length - 1] || 0;
    const mainColor = lastVal >= 0 ? '#ff4444' : '#00ff88';

    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, lastVal >= 0 ? 'rgba(255, 68, 68, 0.35)' : 'rgba(0, 255, 136, 0.35)');
    gradient.addColorStop(1, 'rgba(0, 0, 0, 0.0)');

    stReturnChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: '近 1 個月盈餘率 (%)',
          data: values,
          borderColor: mainColor,
          borderWidth: 3,
          pointBackgroundColor: mainColor,
          pointBorderColor: '#000',
          pointRadius: 4,
          pointHoverRadius: 7,
          fill: true,
          backgroundColor: gradient,
          tension: 0.3
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: true,
            labels: { color: '#e0e0e0', font: { family: 'Inter', size: 12 } }
          },
          tooltip: {
            backgroundColor: 'rgba(15, 20, 30, 0.9)',
            titleColor: '#00e5ff',
            bodyColor: '#ffffff',
            borderColor: 'rgba(0, 229, 255, 0.3)',
            borderWidth: 1,
            padding: 12,
            callbacks: {
              label: function(context) {
                const index = context.dataIndex;
                const item = chartData[index];
                return [
                  `盈餘率: ${item.return_rate_pct >= 0 ? '+' : ''}${item.return_rate_pct.toFixed(2)}%`,
                  `總資產: $${item.total_asset_value.toLocaleString('en-US', {minimumFractionDigits: 2})}`,
                  `當日損益: ${item.day_pnl >= 0 ? '+' : ''}$${item.day_pnl.toFixed(2)}`
                ];
              }
            }
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: { color: '#888888', font: { family: 'Inter', size: 11 } }
          },
          y: {
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: {
              color: '#888888',
              font: { family: 'Inter', size: 11 },
              callback: function(val) { return val + '%'; }
            }
          }
        }
      }
    });
  }

  async function triggerStockTraderEval() {
    if (!_isAdmin()) {
      _showToast('手動觸發看盤僅限管理員使用，請先登入！', 'warning');
      _openLoginModal();
      return;
    }

    _showToast('🤖 已對雪莉發送手動看盤與量化評估請求...', 'info');

    try {
      const res = await fetch('/api/stock_trader/trigger', {
        method: 'POST',
        headers: _getAuthHeaders()
      });

      if (res.ok) {
        const data = await res.json();
        if (data.status === 'success') {
          _showToast('✅ 雪莉完成看盤評估與演算法交易！', 'success');
          loadStockTraderData();
        } else {
          _showToast(`⚠️ 評估失敗: ${data.message || '未知錯誤'}`, 'error');
        }
      } else {
        _showToast('❌ 呼叫伺服器失敗', 'error');
      }
    } catch (e) {
      _showToast(`❌ 發生異常: ${e.message}`, 'error');
    }
  }

  async function refreshStockTraderPrices() {
    if (!_isAdmin()) {
      _showToast('手動刷新行情僅限管理員使用，請先登入！', 'warning');
      _openLoginModal();
      return;
    }

    _showToast('🔄 正在連線 yfinance 刷新美股最新行情與持倉損益...', 'info');

    try {
      const res = await fetch('/api/stock_trader/refresh_prices', {
        method: 'POST',
        headers: _getAuthHeaders()
      });

      if (res.ok) {
        const data = await res.json();
        if (data.status === 'success') {
          _showToast(data.message || '✅ 最新美股行情與持倉損益刷新成功！', 'success');
          loadStockTraderData();
        } else {
          _showToast(`❌ 刷新失敗: ${data.message}`, 'error');
        }
      } else {
        _showToast('❌ 呼叫伺服器失敗', 'error');
      }
    } catch (e) {
      console.error('Failed to refresh stock trader prices:', e);
      _showToast('❌ 網路請求發生錯誤', 'error');
    }
  }

  // Expose to window
  window.loadStockTraderData = loadStockTraderData;
  window.renderStockReturnChart = renderStockReturnChart;
  window.triggerStockTraderEval = triggerStockTraderEval;
  window.refreshStockTraderPrices = refreshStockTraderPrices;

  // Register with SherryUI plugin ecosystem
  if (window.SherryUI && typeof window.SherryUI.registerPlugin === 'function') {
    window.SherryUI.registerPlugin('stock_trader', {
      viewId: 'stock-trader',
      onActivate: () => {
        loadStockTraderData();
      },
      onDeactivate: () => {
        // Nothing special needed
      },
    });
  }
})();
