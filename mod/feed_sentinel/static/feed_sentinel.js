/**
 * mod/feed_sentinel/static/feed_sentinel.js
 * AI 社群情報哨兵 Web UI 前端外掛與即時更新邏輯。
 */

(function () {
    let fsTimer = null;
    let fsCurrentSource = 'all';
    let fsSearchQuery = '';
    let fsCurrentPage = 1;
    const fsPageLimit = 15;
    let fsSearchTimeout = null;

    // ──────────────────────────────────────────────────────────────────────────
    // 資料獲取與渲染
    // ──────────────────────────────────────────────────────────────────────────

    async function loadFsStats() {
        try {
            const res = await fetch('/api/feed_sentinel/stats', {
                headers: window.SherryUI ? window.SherryUI.getAuthHeaders() : {}
            });
            if (!res.ok) return;
            const data = await res.json();
            if (data.status !== 'success') return;

            const stats = data.stats || {};
            const sentinel = data.sentinel || {};

            // 1. 總數
            const elTotal = document.getElementById('fs-stat-total');
            if (elTotal) elTotal.textContent = (stats.total_posts || 0).toLocaleString();

            // 2. 各來源分佈
            const elSources = document.getElementById('fs-stat-sources');
            if (elSources && stats.source_counts) {
                const sc = stats.source_counts;
                elSources.textContent = `HF: ${sc.huggingface || 0} | arXiv: ${sc.arxiv || 0} | GH: ${sc.github || 0}`;
            }

            // 3. 最近推播時間
            const elLast = document.getElementById('fs-stat-last-posted');
            const elLastSub = document.getElementById('fs-stat-last-sub');
            if (elLast) {
                if (stats.last_posted_at) {
                    elLast.textContent = stats.last_posted_at.split(' ')[1] || stats.last_posted_at;
                    if (elLastSub) elLastSub.textContent = `推播日期: ${stats.last_posted_at.split(' ')[0]}`;
                } else {
                    elLast.textContent = '尚無紀錄';
                    if (elLastSub) elLastSub.textContent = '等待首次巡邏推播';
                }
            }

            // 4. 哨兵巡邏狀態
            const elStatus = document.getElementById('fs-stat-status');
            const elStatusSub = document.getElementById('fs-stat-status-sub');
            if (elStatus) {
                if (sentinel.is_processing) {
                    elStatus.textContent = '⚡ 深度推理中';
                    elStatus.style.color = '#f59e0b';
                    if (elStatusSub) elStatusSub.textContent = '正在萃取 3 大重點與銳評';
                } else if (!sentinel.in_schedule_window) {
                    elStatus.textContent = '🌙 休息時段';
                    elStatus.style.color = '#94a3b8';
                    if (elStatusSub) elStatusSub.textContent = `時段外 (${sentinel.start_hour || 8}:00~${sentinel.end_hour || 23}:00)`;
                } else if (sentinel.cooldown_remaining_seconds > 0) {
                    const minRem = Math.ceil(sentinel.cooldown_remaining_seconds / 60);
                    elStatus.textContent = `⏳ 冷卻中 (${minRem}m)`;
                    elStatus.style.color = '#38bdf8';
                    if (elStatusSub) elStatusSub.textContent = '維持每小時推播節奏';
                } else {
                    elStatus.textContent = '🟢 巡邏守備中';
                    elStatus.style.color = '#4ade80';
                    if (elStatusSub) elStatusSub.textContent = '隨時捕捉最新 AI 突破';
                }
            }
        } catch (e) {
            console.error('[FeedSentinel] 載入統計失敗:', e);
        }
    }

    async function loadFsPosts(showLoading = false) {
        const loadingEl = document.getElementById('fs-loading-indicator');
        const container = document.getElementById('fs-posts-container');
        if (showLoading && loadingEl) loadingEl.classList.remove('hidden');

        try {
            const url = `/api/feed_sentinel/posts?page=${fsCurrentPage}&limit=${fsPageLimit}&source=${encodeURIComponent(fsCurrentSource)}&q=${encodeURIComponent(fsSearchQuery)}`;
            const res = await fetch(url, {
                headers: window.SherryUI ? window.SherryUI.getAuthHeaders() : {}
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || '載入失敗');

            renderFsPosts(data.posts || [], data.total || 0, data.page || 1, data.total_pages || 1);
        } catch (e) {
            console.error('[FeedSentinel] 載入文章清單失敗:', e);
            if (showLoading && window.SherryUI) {
                window.SherryUI.showToast(`載入情報失敗: ${e.message}`, 'error');
            }
        } finally {
            if (loadingEl) loadingEl.classList.add('hidden');
        }
    }

    function renderFsPosts(posts, total, page, totalPages) {
        const container = document.getElementById('fs-posts-container');
        const countBadge = document.getElementById('fs-feed-count-badge');
        const pagination = document.getElementById('fs-pagination');
        const pageInfo = document.getElementById('fs-page-info');
        const btnPrev = document.getElementById('fs-page-prev');
        const btnNext = document.getElementById('fs-page-next');

        if (countBadge) countBadge.textContent = `共 ${total} 篇`;

        if (!posts || posts.length === 0) {
            if (container) {
                container.innerHTML = `
                    <div class="fs-empty-state">
                        <div class="fs-empty-icon">📡</div>
                        <h3>尚未有推播文章或查無符合結果</h3>
                        <p>哨兵會在每日 08:00~23:00 巡邏並自動推播最新 AI 突破至 Discord 頻道。</p>
                    </div>
                `;
            }
            if (pagination) pagination.classList.add('hidden');
            return;
        }

        let html = '';
        posts.forEach(post => {
            const sourceBadgeClass = getSourceBadgeClass(post.source);
            const formattedDate = post.posted_at || post.published_at || '剛剛';
            const escTitle = escapeHtml(post.title);
            const escUrl = escapeHtml(post.url);
            const escAuthor = escapeHtml(post.author_or_repo || 'Community');
            const escSummary = escapeHtml(post.original_summary || '無摘要提供。');
            const escAnalysis = formatSherryAnalysis(post.sherry_analysis);

            const tagsHtml = (post.tags || []).map(t => `<span class="fs-tag-pill">🏷️ ${escapeHtml(t)}</span>`).join('');

            html += `
                <div class="fs-card" id="fs-card-${escapeHtml(post.post_id)}">
                    <div class="fs-card-top">
                        <div class="fs-card-meta">
                            <span class="fs-badge ${sourceBadgeClass}">${escapeHtml(post.source)}</span>
                            <span class="fs-card-time">⏱️ ${escapeHtml(formattedDate)}</span>
                            <span>👤 ${escAuthor}</span>
                        </div>
                        <div class="fs-card-actions">
                            <button class="fs-btn-copy" onclick="copyFsBriefing('${escapeHtml(post.post_id)}')">📋 複製重點</button>
                            <button class="fs-btn-delete" onclick="deleteFsPost('${escapeHtml(post.post_id)}')">🗑️</button>
                        </div>
                    </div>

                    <h3 class="fs-card-title">
                        <a href="${escUrl}" target="_blank" rel="noopener noreferrer">${escTitle} ↗</a>
                    </h3>

                    ${tagsHtml ? `<div class="fs-card-tags">${tagsHtml}</div>` : ''}

                    <div class="fs-briefing-box" id="fs-analysis-${escapeHtml(post.post_id)}">
                        ${escAnalysis}
                    </div>

                    <details class="fs-raw-summary-details">
                        <summary>📄 查看原始英文/專案摘要</summary>
                        <div class="fs-raw-summary-content">${escSummary}</div>
                    </details>
                </div>
            `;
        });

        if (container) container.innerHTML = html;

        // 分頁控制
        if (pagination && totalPages > 1) {
            pagination.classList.remove('hidden');
            if (pageInfo) pageInfo.textContent = `第 ${page} / ${totalPages} 頁 (共 ${total} 篇)`;
            if (btnPrev) btnPrev.disabled = (page <= 1);
            if (btnNext) btnNext.disabled = (page >= totalPages);
        } else if (pagination) {
            pagination.classList.add('hidden');
        }
    }

    function getSourceBadgeClass(source) {
        const s = (source || '').toLowerCase();
        if (s.includes('huggingface')) return 'fs-badge--hf';
        if (s.includes('arxiv')) return 'fs-badge--arxiv';
        if (s.includes('github')) return 'fs-badge--github';
        return 'fs-badge--info';
    }

    function formatSherryAnalysis(rawText) {
        if (!rawText) {
            return '<div class="text-muted">（尚未包含雪莉分析，點擊複製或外部連結查看詳情）</div>';
        }
        let formatted = escapeHtml(rawText);
        formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        formatted = formatted.replace(/\n/g, '<br/>');
        return formatted;
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    // ──────────────────────────────────────────────────────────────────────────
    // 全域操作函式 (掛載至 window 供 HTML inline 調用)
    // ──────────────────────────────────────────────────────────────────────────

    window.loadFsData = async function (isManual = false) {
        await Promise.all([loadFsStats(), loadFsPosts(isManual)]);
        if (isManual && window.SherryUI) {
            window.SherryUI.showToast('✅ 情報列表已更新！', 'success');
        }
    };

    window.setFsSourceFilter = function (source) {
        fsCurrentSource = source;
        fsCurrentPage = 1;
        document.querySelectorAll('.fs-source-tabs .fs-tab-btn').forEach(btn => {
            if (btn.getAttribute('data-source') === source) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
        loadFsPosts(true);
    };

    window.handleFsSearch = function (query) {
        fsSearchQuery = query;
        fsCurrentPage = 1;
        const clearBtn = document.getElementById('fs-search-clear');
        if (clearBtn) {
            if (query) clearBtn.classList.remove('hidden');
            else clearBtn.classList.add('hidden');
        }

        if (fsSearchTimeout) clearTimeout(fsSearchTimeout);
        fsSearchTimeout = setTimeout(() => {
            loadFsPosts(false);
        }, 300);
    };

    window.clearFsSearch = function () {
        const input = document.getElementById('fs-search-input');
        if (input) input.value = '';
        window.handleFsSearch('');
    };

    window.changeFsPage = function (delta) {
        fsCurrentPage = Math.max(1, fsCurrentPage + delta);
        loadFsPosts(true);
    };

    window.triggerFsPatrol = async function () {
        const btn = document.getElementById('fs-btn-trigger');
        if (btn) btn.disabled = true;

        if (window.SherryUI) {
            window.SherryUI.showToast('🚀 正在啟動情報哨兵背景巡邏與 AI 精讀...', 'info');
        }

        try {
            const res = await fetch('/api/feed_sentinel/trigger', {
                method: 'POST',
                headers: window.SherryUI ? window.SherryUI.getAuthHeaders() : {}
            });
            const data = await res.json();
            if (res.ok && data.status === 'success') {
                if (window.SherryUI) window.SherryUI.showToast(data.message, 'success');
                setTimeout(() => window.loadFsData(false), 2000);
            } else {
                const toastType = data.status === 'warning' ? 'warning' : 'info';
                if (window.SherryUI) window.SherryUI.showToast(data.message || '巡邏已完成', toastType);
            }
        } catch (e) {
            if (window.SherryUI) window.SherryUI.showToast(`觸發巡邏失敗: ${e.message}`, 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    };

    window.deleteFsPost = async function (postId) {
        if (!confirm(`確定要從封存歷史中刪除此篇情報嗎？`)) return;

        try {
            const res = await fetch(`/api/feed_sentinel/posts/${encodeURIComponent(postId)}`, {
                method: 'DELETE',
                headers: window.SherryUI ? window.SherryUI.getAuthHeaders() : {}
            });
            const data = await res.json();
            if (res.ok && data.status === 'success') {
                if (window.SherryUI) window.SherryUI.showToast('🗑️ 已刪除該情報紀錄', 'info');
                const card = document.getElementById(`fs-card-${postId}`);
                if (card) card.remove();
                loadFsStats();
            } else {
                if (window.SherryUI) window.SherryUI.showToast(data.message || '刪除失敗', 'error');
            }
        } catch (e) {
            if (window.SherryUI) window.SherryUI.showToast(`刪除失敗: ${e.message}`, 'error');
        }
    };

    window.copyFsBriefing = function (postId) {
        const box = document.getElementById(`fs-analysis-${postId}`);
        if (!box) return;
        const text = box.innerText || box.textContent;
        navigator.clipboard.writeText(text).then(() => {
            if (window.SherryUI) window.SherryUI.showToast('📋 已複製雪莉 3 大重點與銳評至剪貼簿！', 'success');
        }).catch(() => {
            if (window.SherryUI) window.SherryUI.showToast('複製失敗，請手動選取複製', 'warning');
        });
    };

    // ──────────────────────────────────────────────────────────────────────────
    // SherryUI 外掛生命週期註冊 (MUST)
    // ──────────────────────────────────────────────────────────────────────────

    if (window.SherryUI && typeof window.SherryUI.registerPlugin === 'function') {
        window.SherryUI.registerPlugin('feed_sentinel', {
            viewId: 'feed-sentinel',
            title: '📡 AI 社群情報哨兵主控台',

            onActivate: function () {
                window.loadFsData(false);
                if (!fsTimer) {
                    fsTimer = setInterval(() => {
                        window.loadFsData(false);
                    }, 15000);
                }
            },

            onDeactivate: function () {
                if (fsTimer) {
                    clearInterval(fsTimer);
                    fsTimer = null;
                }
            }
        });
    }
})();
