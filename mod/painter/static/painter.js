/**
 * mod/painter/static/painter.js
 * =============================
 * Client-side script for Painter & Art Gallery Mod.
 * Adheres to SherryUI lifecycle plugin protocol.
 */

(function () {
    let currentWebBrush = 'pencil';
    let currentWebColor = '#E63946';
    let currentBrushSize = 6;
    let isDrawingWeb = false;
    let webCanvasCtx = null;
    let lastWebX = 0, lastWebY = 0;

    function _getAuthHeaders() {
        if (window.SherryUI && typeof window.SherryUI.getAuthHeaders === 'function') {
            return window.SherryUI.getAuthHeaders();
        }
        return { 'Content-Type': 'application/json' };
    }

    function _showToast(msg, type = 'info') {
        if (window.SherryUI && typeof window.SherryUI.showToast === 'function') {
            window.SherryUI.showToast(msg, type);
        } else if (typeof showToast === 'function') {
            showToast(msg, type);
        } else {
            console.log(`[${type.toUpperCase()}] ${msg}`);
        }
    }

    function switchPainterTab(tabName) {
        document.querySelectorAll('.painter-tab-content').forEach(el => el.style.display = 'none');
        document.querySelectorAll('.painter-tabs .btn').forEach(btn => {
            btn.classList.remove('btn-primary');
            btn.classList.add('btn-secondary');
        });

        const activeContent = document.getElementById(`painter-tab-${tabName}`);
        const activeBtn = document.getElementById(`tab-btn-${tabName}`);
        if (activeContent) activeContent.style.display = 'block';
        if (activeBtn) {
            activeBtn.classList.remove('btn-secondary');
            activeBtn.classList.add('btn-primary');
        }

        if (tabName === 'gallery') {
            refreshPaintingGallery();
        } else if (tabName === 'interactive') {
            initInteractiveCanvas();
        }
    }

    async function refreshPaintingGallery() {
        const grid = document.getElementById('gallery-grid');
        if (!grid) return;
        grid.innerHTML = '<div class="glass-card" style="text-align: center; padding: 40px; color: var(--text-muted); grid-column: 1 / -1;">正在讀取畫廊...</div>';

        try {
            const resp = await fetch('/api/painter/paintings', { headers: _getAuthHeaders() });
            const data = await resp.json();
            const paintings = data.paintings || [];
            const countEl = document.getElementById('gallery-count');
            if (countEl) countEl.textContent = data.total || paintings.length;

            if (paintings.length === 0) {
                grid.innerHTML = '<div class="glass-card" style="text-align: center; padding: 40px; color: var(--text-muted); grid-column: 1 / -1;">目前尚無已保存的畫作，快使用 <code>/draw</code> 或上方的委託面板讓雪莉畫一張吧！</div>';
                return;
            }

            grid.innerHTML = '';
            paintings.forEach(p => {
                const card = document.createElement('div');
                card.className = 'glass-card';
                card.style.cssText = 'padding: 12px; border-radius: 10px; display: flex; flex-direction: column; gap: 10px; transition: transform 0.2s; background: rgba(255,255,255,0.03); border: 1px solid var(--border-color);';
                card.onmouseenter = () => card.style.transform = 'translateY(-3px)';
                card.onmouseleave = () => card.style.transform = 'translateY(0)';

                const imgWrap = document.createElement('div');
                imgWrap.style.cssText = 'width: 100%; aspect-ratio: 1/1; border-radius: 6px; overflow: hidden; background: #000; cursor: pointer;';
                imgWrap.onclick = () => window.open(p.url, '_blank');

                const img = document.createElement('img');
                img.src = p.url;
                img.loading = 'lazy';
                img.style.cssText = 'width: 100%; height: 100%; object-fit: cover; transition: transform 0.3s;';
                imgWrap.appendChild(img);

                const info = document.createElement('div');
                info.innerHTML = `
                    <div style="font-weight: 600; font-size: 0.95rem; color: #FFF; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${p.prompt}">${p.prompt}</div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">
                        <span class="badge" style="background: rgba(255,105,180,0.2); color: var(--accent-pink); padding: 2px 6px; border-radius: 4px;">${p.style}</span>
                        <span>${p.duration_sec}s · ${p.command_count} 筆觸</span>
                    </div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 6px;">
                        ${new Date(p.created_at).toLocaleString()}
                    </div>
                `;

                const btnBar = document.createElement('div');
                btnBar.style.cssText = 'display: flex; gap: 6px; margin-top: auto;';
                btnBar.innerHTML = `
                    <a href="${p.url}" download="${p.filename}" class="btn btn-secondary btn-sm" style="flex: 1; text-align: center; text-decoration: none; padding: 4px 8px;">📥 下載</a>
                    <button class="btn btn-secondary btn-sm" onclick="window.open('${p.url}', '_blank')">🔍 查看</button>
                `;

                card.appendChild(imgWrap);
                card.appendChild(info);
                card.appendChild(btnBar);
                grid.appendChild(card);
            });
        } catch (err) {
            grid.innerHTML = `<div class="glass-card" style="color: #ff5555; padding: 20px; grid-column: 1 / -1;">讀取畫廊失敗：${err}</div>`;
        }
    }

    async function triggerAiDrawing() {
        const prompt = document.getElementById('ai-draw-prompt').value.trim();
        const style = document.getElementById('ai-draw-style').value;
        const refine = document.getElementById('ai-draw-refine') ? document.getElementById('ai-draw-refine').checked : false;
        const btn = document.getElementById('btn-trigger-ai-draw');
        const statusText = document.getElementById('ai-status-text');
        const progressWrap = document.getElementById('ai-progress-wrap');
        const progressBar = document.getElementById('ai-progress-bar');
        const previewWrap = document.getElementById('ai-result-preview');
        const previewImg = document.getElementById('ai-result-img');

        if (!prompt) {
            _showToast('請填寫想畫的內容提示詞！', 'warning');
            return;
        }

        btn.disabled = true;
        btn.innerHTML = '<span>🎨 雪莉揮灑畫筆中...</span>';
        statusText.textContent = refine
            ? `雪莉正在構思【${style}】風格草稿，並將進行 Gemma 4 視覺自我審視精修...`
            : `雪莉正在構思【${style}】風格的「${prompt}」...`;
        progressWrap.style.display = 'block';
        progressBar.style.width = '30%';
        previewWrap.style.display = 'none';

        try {
            let imageB64 = null;
            const fileInput = document.getElementById('ai-draw-image-file');
            if (fileInput && fileInput.files && fileInput.files[0]) {
                const file = fileInput.files[0];
                imageB64 = await new Promise((resolve) => {
                    const reader = new FileReader();
                    reader.onload = () => {
                        const res = reader.result;
                        resolve(res.includes(',') ? res.split(',')[1] : res);
                    };
                    reader.readAsDataURL(file);
                });
            }

            const resp = await fetch('/api/painter/draw', {
                method: 'POST',
                headers: _getAuthHeaders(),
                body: JSON.stringify({ prompt, style, image_b64: imageB64, refine: refine })
            });
            const res = await resp.json();
            if (res.success) {
                progressBar.style.width = '100%';
                statusText.innerHTML = `✨ <b>雪莉說：</b>「${res.commentary}」<br><small>耗時 ${res.duration_sec}s · ${res.command_count} 步筆觸</small>`;
                previewImg.src = res.url;
                previewWrap.style.display = 'block';
                _showToast('畫作繪製完成並已存檔！', 'success');
            } else {
                statusText.textContent = `❌ 繪圖失敗：${res.error || '未知原因'}`;
                _showToast(`繪圖失敗: ${res.error || '未知原因'}`, 'error');
            }
        } catch (err) {
            statusText.textContent = `❌ 發生錯誤：${err}`;
            _showToast(`發生錯誤: ${err}`, 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<span>🚀 讓雪莉開始作畫！</span>';
        }
    }

    function initInteractiveCanvas() {
        const canvas = document.getElementById('interactive-drawing-canvas');
        if (!canvas) return;
        webCanvasCtx = canvas.getContext('2d');

        canvas.onmousedown = (e) => {
            isDrawingWeb = true;
            const rect = canvas.getBoundingClientRect();
            lastWebX = e.clientX - rect.left;
            lastWebY = e.clientY - rect.top;
        };
        canvas.onmousemove = (e) => {
            if (!isDrawingWeb || !webCanvasCtx) return;
            const rect = canvas.getBoundingClientRect();
            const curX = e.clientX - rect.left;
            const curY = e.clientY - rect.top;

            webCanvasCtx.beginPath();
            webCanvasCtx.moveTo(lastWebX, lastWebY);
            webCanvasCtx.lineTo(curX, curY);

            if (currentWebBrush === 'eraser') {
                webCanvasCtx.strokeStyle = '#FFFFFF';
                webCanvasCtx.lineWidth = currentBrushSize * 2;
            } else {
                webCanvasCtx.strokeStyle = currentWebColor;
                webCanvasCtx.lineWidth = currentBrushSize;
            }
            webCanvasCtx.lineCap = 'round';
            webCanvasCtx.lineJoin = 'round';
            webCanvasCtx.stroke();

            lastWebX = curX;
            lastWebY = curY;
        };
        window.onmouseup = () => isDrawingWeb = false;
    }

    function selectWebBrush(type) {
        currentWebBrush = type;
        document.querySelectorAll('[id^="brush-"]').forEach(b => b.classList.remove('active'));
        const target = document.getElementById(`brush-${type === 'ink_wash' ? 'ink' : type}`);
        if (target) target.classList.add('active');
    }

    function updateBrushSize(val) {
        currentBrushSize = parseFloat(val);
        const sizeVal = document.getElementById('brush-size-val');
        if (sizeVal) sizeVal.textContent = `${val}px`;
    }

    function updateWebColor(hex) {
        currentWebColor = hex;
        const picker = document.getElementById('web-color-picker');
        const hexInput = document.getElementById('web-color-hex');
        if (picker) picker.value = hex;
        if (hexInput) hexInput.value = hex;
    }

    function clearInteractiveCanvas() {
        const canvas = document.getElementById('interactive-drawing-canvas');
        if (!canvas || !webCanvasCtx) return;
        webCanvasCtx.clearRect(0, 0, canvas.width, canvas.height);
    }

    function downloadInteractiveCanvas() {
        const canvas = document.getElementById('interactive-drawing-canvas');
        if (!canvas) return;
        const a = document.createElement('a');
        a.href = canvas.toDataURL('image/jpeg', 0.95);
        a.download = `my_painting_${Date.now()}.jpg`;
        a.click();
    }

    // Expose functions globally for inline HTML onclick attributes
    window.switchPainterTab = switchPainterTab;
    window.refreshPaintingGallery = refreshPaintingGallery;
    window.triggerAiDrawing = triggerAiDrawing;
    window.selectWebBrush = selectWebBrush;
    window.updateBrushSize = updateBrushSize;
    window.updateWebColor = updateWebColor;
    window.clearInteractiveCanvas = clearInteractiveCanvas;
    window.downloadInteractiveCanvas = downloadInteractiveCanvas;

    // Register with SherryUI plugin ecosystem
    if (window.SherryUI && typeof window.SherryUI.registerPlugin === 'function') {
        window.SherryUI.registerPlugin('painter', {
            viewId: 'painter',
            title: '🎨 雪莉藝術畫布工作室',
            onActivate: () => {
                refreshPaintingGallery();
            },
            onDeactivate: () => {
                // Cleanup if needed
            },
        });
    } else {
        setTimeout(refreshPaintingGallery, 300);
    }
})();
