/**
 * mod/game_center/static/games.js
 * ===============================
 * Frontend logic for Game Center Mod.
 * Supports Rock-Paper-Scissors, Blackjack, Minesweeper, Turtle Soup,
 * Akinator 20Q, Escape Room, and Tic-Tac-Toe.
 */

(function () {
  let currentBlackjackId = null;
  let currentMinesweeperId = null;
  let currentTurtleSoupId = null;
  let currentAkinatorId = null;
  let currentEscapeRoomId = null;
  let currentTTTId = null;

  function switchGameTab(gameId) {
    document.querySelectorAll('.game-tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.game-panel').forEach(p => p.classList.add('hidden'));

    const activeTab = document.getElementById(`tab-game-${gameId}`);
    if (activeTab) activeTab.classList.add('active');

    const activePanel = document.getElementById(`panel-game-${gameId}`);
    if (activePanel) activePanel.classList.remove('hidden');
  }

  function _escHtml(str) {
    if (!str) return '';
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

  // R3：/api/games/* 收斂為管理員專用後，Cookie 鑑權之 POST 需附 CSRF 標頭
  // （double-submit：sherry_csrf → X-CSRF-Token，由 SherryUI.getAuthHeaders 提供）
  function _gameHeaders() {
    return (window.SherryUI && typeof window.SherryUI.getAuthHeaders === 'function')
      ? window.SherryUI.getAuthHeaders()
      : { 'Content-Type': 'application/json' };
  }

  // ── Rock Paper Scissors ──────────────────────────────────
  async function playRPS(choice) {
    const dialogueEl = document.getElementById('sherry-game-dialogue');
    if (dialogueEl) dialogueEl.textContent = '「雪莉正在出拳並思考對話中……✨🍊」';

    try {
      const res = await fetch('/api/games/rps/play', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ choice }),
      });
      const data = await res.json();
      if (data.error) {
        _showToast(`猜拳錯誤: ${data.error}`, 'error');
        return;
      }

      document.getElementById('rps-result-box').classList.remove('hidden');
      document.getElementById('rps-player-choice').textContent = data.player_choice;
      document.getElementById('rps-sherry-choice').textContent = data.sherry_choice;

      const titleEl = document.getElementById('rps-result-title');
      if (data.outcome === 'win') {
        titleEl.textContent = '🎉 你贏了！';
        titleEl.style.color = '#00ffaa';
      } else if (data.outcome === 'lose') {
        titleEl.textContent = '😈 雪莉贏了！';
        titleEl.style.color = '#ff007f';
      } else {
        titleEl.textContent = '🤝 平手！';
        titleEl.style.color = '#ffaa00';
      }

      if (dialogueEl && data.sherry_dialogue) {
        dialogueEl.textContent = `「${data.sherry_dialogue}」`;
      }
    } catch (e) {
      _showToast(`猜拳連線失敗: ${e}`, 'error');
    }
  }

  // ── Blackjack ─────────────────────────────────────────────
  async function startBlackjack() {
    const dialogueEl = document.getElementById('sherry-game-dialogue');
    if (dialogueEl) dialogueEl.textContent = '「洗牌中……名偵探要發牌囉！🃏✨」';

    try {
      const res = await fetch('/api/games/blackjack/start', {
        method: 'POST',
        headers: _gameHeaders(),
      });
      const data = await res.json();
      if (data.error) {
        _showToast(`21點開局失敗: ${data.error}`, 'error');
        return;
      }

      currentBlackjackId = data.game_id;
      renderBlackjackState(data);
    } catch (e) {
      _showToast(`21點連線失敗: ${e}`, 'error');
    }
  }

  async function hitBlackjack() {
    if (!currentBlackjackId) return;
    try {
      const res = await fetch('/api/games/blackjack/hit', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentBlackjackId }),
      });
      const data = await res.json();
      renderBlackjackState(data);
    } catch (e) {
      _showToast(`叫牌失敗: ${e}`, 'error');
    }
  }

  async function standBlackjack() {
    if (!currentBlackjackId) return;
    try {
      const res = await fetch('/api/games/blackjack/stand', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentBlackjackId }),
      });
      const data = await res.json();
      renderBlackjackState(data);
    } catch (e) {
      _showToast(`停牌失敗: ${e}`, 'error');
    }
  }

  function renderBlackjackState(data) {
    const dCardsEl = document.getElementById('bj-dealer-cards');
    const pCardsEl = document.getElementById('bj-player-cards');
    const dScoreEl = document.getElementById('bj-dealer-score');
    const pScoreEl = document.getElementById('bj-player-score');
    const dialogueEl = document.getElementById('sherry-game-dialogue');

    if (pScoreEl) pScoreEl.textContent = `點數: ${data.player_score}`;
    if (dScoreEl) {
      dScoreEl.textContent = data.is_player_turn ? `點數: ?` : `點數: ${data.dealer_score}`;
    }

    if (pCardsEl) {
      pCardsEl.innerHTML = data.player_cards.map(c => `
        <div style="background: #2a2d3d; border: 1px solid var(--accent-color); border-radius: 6px; padding: 10px 14px; text-align: center; font-weight: 700; font-size: 1.1rem; color: ${c.suit === '♥' || c.suit === '♦' ? '#ff4444' : '#ffffff'};">
          ${c.suit} ${c.rank}
        </div>
      `).join('');
    }

    if (dCardsEl) {
      if (data.is_player_turn) {
        const visible = data.dealer_cards[0];
        dCardsEl.innerHTML = `
          <div style="background: #2a2d3d; border: 1px solid var(--accent-color); border-radius: 6px; padding: 10px 14px; text-align: center; font-weight: 700; font-size: 1.1rem; color: ${visible.suit === '♥' || visible.suit === '♦' ? '#ff4444' : '#ffffff'};">
            ${visible.suit} ${visible.rank}
          </div>
          <div style="background: rgba(255,255,255,0.1); border: 1px dashed var(--border-color); border-radius: 6px; padding: 10px 14px; text-align: center; font-weight: 700; font-size: 1.1rem; color: var(--text-muted);">
            🎴 隱藏
          </div>
        `;
      } else {
        dCardsEl.innerHTML = data.dealer_cards.map(c => `
          <div style="background: #2a2d3d; border: 1px solid var(--accent-color); border-radius: 6px; padding: 10px 14px; text-align: center; font-weight: 700; font-size: 1.1rem; color: ${c.suit === '♥' || c.suit === '♦' ? '#ff4444' : '#ffffff'};">
            ${c.suit} ${c.rank}
          </div>
        `).join('');
      }
    }

    const hitBtn = document.getElementById('btn-bj-hit');
    const standBtn = document.getElementById('btn-bj-stand');
    if (hitBtn) hitBtn.disabled = !data.is_player_turn;
    if (standBtn) standBtn.disabled = !data.is_player_turn;

    if (dialogueEl && data.sherry_dialogue) {
      dialogueEl.textContent = `「${data.sherry_dialogue}」`;
    }
  }

  // ── Minesweeper ───────────────────────────────────────────
  async function startMinesweeper() {
    try {
      const res = await fetch('/api/games/minesweeper/start', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ rows: 8, cols: 8, mines: 10 }),
      });
      const data = await res.json();
      currentMinesweeperId = data.game_id;
      renderMinesweeperBoard(data);
    } catch (e) {
      _showToast(`踩地雷開局失敗: ${e}`, 'error');
    }
  }

  async function revealMinesweeperCell(r, c) {
    if (!currentMinesweeperId) return;
    try {
      const res = await fetch('/api/games/minesweeper/reveal', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentMinesweeperId, row: r, col: c }),
      });
      const data = await res.json();
      renderMinesweeperBoard(data);
    } catch (e) {
      _showToast(`踩地雷翻開失敗: ${e}`, 'error');
    }
  }

  async function flagMinesweeperCell(e, r, c) {
    e.preventDefault();
    if (!currentMinesweeperId) return;
    try {
      const res = await fetch('/api/games/minesweeper/flag', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentMinesweeperId, row: r, col: c }),
      });
      const data = await res.json();
      renderMinesweeperBoard(data);
    } catch (err) {
      _showToast(`踩地雷標記失敗: ${err}`, 'error');
    }
  }

  async function hintMinesweeper() {
    if (!currentMinesweeperId) {
      _showToast('請先開局踩地雷！', 'info');
      return;
    }
    const dialogueEl = document.getElementById('sherry-game-dialogue');
    if (dialogueEl) dialogueEl.textContent = '「名偵探放大鏡觀察中……分析周圍線索中！🔍✨」';

    try {
      const res = await fetch('/api/games/minesweeper/hint', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentMinesweeperId }),
      });
      const data = await res.json();
      if (dialogueEl && data.sherry_hint) {
        dialogueEl.textContent = `「${data.sherry_hint}」`;
      }
    } catch (e) {
      _showToast(`名偵探推理提示失敗: ${e}`, 'error');
    }
  }

  function renderMinesweeperBoard(data) {
    const boardEl = document.getElementById('minesweeper-board');
    if (!boardEl) return;

    boardEl.style.gridTemplateColumns = `repeat(${data.cols}, 42px)`;
    let html = '';

    for (let r = 0; r < data.rows; r++) {
      for (let c = 0; c < data.cols; c++) {
        const cell = data.board[r][c];
        let content = '';
        let style = 'width:42px; height:42px; display:flex; align-items:center; justify-content:center; border-radius:6px; font-weight:700; cursor:pointer; user-select:none; transition:all 0.15s; font-size:1.1rem;';

        if (cell.is_revealed) {
          style += ' background: rgba(255,255,255,0.08); border: 1px solid var(--border-color);';
          if (cell.is_mine) {
            content = '💣';
            style += ' background: rgba(255,0,0,0.3);';
          } else if (cell.neighbor_mines > 0) {
            content = cell.neighbor_mines;
            const colors = ['', '#00e5ff', '#00ffaa', '#ffaa00', '#ff007f', '#7c4dff'];
            style += ` color: ${colors[cell.neighbor_mines] || '#ffffff'};`;
          }
        } else {
          style += ' background: rgba(0,229,255,0.15); border: 1px solid rgba(0,229,255,0.3);';
          if (cell.is_flagged) {
            content = '🚩';
          }
        }

        html += `<div style="${style}" onclick="revealMinesweeperCell(${r}, ${c})" oncontextmenu="flagMinesweeperCell(event, ${r}, ${c})">${content}</div>`;
      }
    }
    boardEl.innerHTML = html;

    const dialogueEl = document.getElementById('sherry-game-dialogue');
    if (dialogueEl) {
      if (data.status === 'win') {
        dialogueEl.textContent = '「太厲害啦！地雷全部成功掃除了！名偵探為你鼓掌！🎉🍊✨」';
      } else if (data.status === 'lose') {
        dialogueEl.textContent = '「踩到地雷了？！沒關係，失敗乃成功之母，名偵探陪你重新再來一把！💥🔍」';
      }
    }
  }

  // ── Turtle Soup ───────────────────────────────────────────
  async function startTurtleSoup() {
    try {
      const res = await fetch('/api/games/turtle_soup/start', { method: 'POST', headers: _gameHeaders() });
      const data = await res.json();
      currentTurtleSoupId = data.game_id;

      document.getElementById('ts-title').textContent = `湯面：${data.title}`;
      document.getElementById('ts-surface').textContent = data.surface_story;
      document.getElementById('ts-dialogue-history').innerHTML = '';

      const dialogueEl = document.getElementById('sherry-game-dialogue');
      if (dialogueEl && data.sherry_reply) dialogueEl.textContent = `「${data.sherry_reply}」`;
    } catch (e) {
      _showToast(`開啟海龜湯失敗: ${e}`, 'error');
    }
  }

  async function askTurtleSoup() {
    const inputEl = document.getElementById('ts-input');
    if (!inputEl || !inputEl.value.trim() || !currentTurtleSoupId) return;
    const question = inputEl.value.trim();
    inputEl.value = '';

    const dialogueEl = document.getElementById('sherry-game-dialogue');
    if (dialogueEl) dialogueEl.textContent = '「名偵探湯主推理審查中……🔍✨」';

    try {
      const res = await fetch('/api/games/turtle_soup/ask', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentTurtleSoupId, question }),
      });
      const data = await res.json();

      const historyEl = document.getElementById('ts-dialogue-history');
      if (historyEl) {
        historyEl.innerHTML += `
          <div style="background: rgba(255,255,255,0.05); padding: 8px 12px; border-radius: 6px; font-size: 0.9rem;">
            <div style="color: var(--text-main); font-weight: 600;">❓ 問：${_escHtml(question)}</div>
            <div style="color: #00e5ff; margin-top: 4px;">🍊 雪莉：${_escHtml(data.sherry_reply)}</div>
          </div>
        `;
        historyEl.scrollTop = historyEl.scrollHeight;
      }

      if (dialogueEl && data.sherry_reply) dialogueEl.textContent = `「${data.sherry_reply}」`;
    } catch (e) {
      _showToast(`提問失敗: ${e}`, 'error');
    }
  }

  // ── Akinator 20 Questions ──────────────────────────────────
  async function startAkinator() {
    try {
      const res = await fetch('/api/games/akinator/start', { method: 'POST', headers: _gameHeaders() });
      const data = await res.json();
      currentAkinatorId = data.game_id;

      document.getElementById('aki-q-num').textContent = `第 ${data.question_count} / ${data.max_questions} 題`;
      document.getElementById('aki-question').textContent = data.current_question;
    } catch (e) {
      _showToast(`開始讀心術失敗: ${e}`, 'error');
    }
  }

  async function answerAkinator(ans) {
    if (!currentAkinatorId) return;
    const dialogueEl = document.getElementById('sherry-game-dialogue');
    if (dialogueEl) dialogueEl.textContent = '「搜集情報中……名偵探鎖定範圍中！🧠✨」';

    try {
      const res = await fetch('/api/games/akinator/answer', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentAkinatorId, answer: ans }),
      });
      const data = await res.json();

      document.getElementById('aki-q-num').textContent = `第 ${data.question_count} / ${data.max_questions} 題`;
      document.getElementById('aki-question').textContent = data.current_question;

      if (dialogueEl && data.current_question) {
        dialogueEl.textContent = `「名偵探推導出下一題：${data.current_question}」`;
      }
    } catch (e) {
      _showToast(`回答失敗: ${e}`, 'error');
    }
  }

  // ── Escape Room ───────────────────────────────────────────
  async function startEscapeRoom() {
    try {
      const res = await fetch('/api/games/escape_room/start', { method: 'POST', headers: _gameHeaders() });
      const data = await res.json();
      currentEscapeRoomId = data.game_id;

      document.getElementById('er-narration').textContent = data.sherry_narration;
      const dialogueEl = document.getElementById('sherry-game-dialogue');
      if (dialogueEl && data.sherry_narration) dialogueEl.textContent = `「${data.sherry_narration}」`;
    } catch (e) {
      _showToast(`開啟密室失敗: ${e}`, 'error');
    }
  }

  async function actionEscapeRoom(target) {
    if (!currentEscapeRoomId) return;
    const dialogueEl = document.getElementById('sherry-game-dialogue');
    if (dialogueEl) dialogueEl.textContent = `「名偵探與你一同搜索『${target}』中……🔍✨」`;

    try {
      const res = await fetch('/api/games/escape_room/action', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentEscapeRoomId, target }),
      });
      const data = await res.json();

      document.getElementById('er-narration').textContent = data.sherry_narration;
      if (dialogueEl && data.sherry_narration) dialogueEl.textContent = `「${data.sherry_narration}」`;
    } catch (e) {
      _showToast(`調查失敗: ${e}`, 'error');
    }
  }

  // ── Tic Tac Toe ────────────────────────────────────────────
  async function startTTT() {
    try {
      const res = await fetch('/api/games/ttt/start', { method: 'POST', headers: _gameHeaders() });
      const data = await res.json();
      currentTTTId = data.game_id;
      renderTTTBoard(data);
    } catch (e) {
      _showToast(`圈圈叉叉開局失敗: ${e}`, 'error');
    }
  }

  async function moveTTT(index) {
    if (!currentTTTId) return;
    try {
      const res = await fetch('/api/games/ttt/move', {
        method: 'POST',
        headers: _gameHeaders(),
        body: JSON.stringify({ game_id: currentTTTId, index }),
      });
      const data = await res.json();
      renderTTTBoard(data);
    } catch (e) {
      _showToast(`下棋失敗: ${e}`, 'error');
    }
  }

  function renderTTTBoard(data) {
    const boardEl = document.getElementById('ttt-board');
    if (!boardEl) return;

    boardEl.innerHTML = data.board.map((val, idx) => `
      <button onclick="moveTTT(${idx})" style="width:70px; height:70px; font-size:1.8rem; font-weight:800; border-radius:10px; border:1px solid var(--accent-color); background:#2a2d3d; color:${val === 'X' ? '#ff007f' : '#00e5ff'}; cursor:pointer;" ${val !== '' || data.status !== 'ongoing' ? 'disabled' : ''}>
        ${val}
      </button>
    `).join('');

    const dialogueEl = document.getElementById('sherry-game-dialogue');
    if (dialogueEl && data.sherry_dialogue) {
      dialogueEl.textContent = `「${data.sherry_dialogue}」`;
    }
  }

  // Expose to window for inline onclick handlers
  window.switchGameTab = switchGameTab;
  window.playRPS = playRPS;
  window.startBlackjack = startBlackjack;
  window.hitBlackjack = hitBlackjack;
  window.standBlackjack = standBlackjack;
  window.startMinesweeper = startMinesweeper;
  window.revealMinesweeperCell = revealMinesweeperCell;
  window.flagMinesweeperCell = flagMinesweeperCell;
  window.hintMinesweeper = hintMinesweeper;
  window.startTurtleSoup = startTurtleSoup;
  window.askTurtleSoup = askTurtleSoup;
  window.startAkinator = startAkinator;
  window.answerAkinator = answerAkinator;
  window.startEscapeRoom = startEscapeRoom;
  window.actionEscapeRoom = actionEscapeRoom;
  window.startTTT = startTTT;
  window.moveTTT = moveTTT;

  // Register with SherryUI plugin ecosystem if present
  if (window.SherryUI && typeof window.SherryUI.registerPlugin === 'function') {
    window.SherryUI.registerPlugin('game_center', {
      viewId: 'games',
      onActivate: () => {
        // Nothing special needed on activate
      },
      onDeactivate: () => {
        // Nothing special needed on deactivate
      },
    });
  }
})();
