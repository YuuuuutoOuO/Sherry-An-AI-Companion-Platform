#!/bin/bash
# ============================================================
# Sherry Bot Restart Launcher (Linux / Docker)
# 由 Web UI「重啟雪莉」按鈕或手動呼叫
# 1. 強制清空所有舊的 main.py 進程與 8763 Port 佔用
# 2. 啟動新的 main.py
# ============================================================
echo "=== 正在重啟雪莉機器人 (Linux/Docker) ==="

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BOT_DIR" || exit 1

# 多重確保殺掉舊的 main.py 進程與 8763 Port 佔用
pkill -9 -f "main.py" 2>/dev/null || true
if command -v fuser >/dev/null 2>&1; then
    fuser -k -9 8763/tcp 2>/dev/null || true
fi
if command -v ss >/dev/null 2>&1; then
    ss -lptn 'sport = :8763' 2>/dev/null | grep -oP 'pid=\K\d+' | xargs -r kill -9 2>/dev/null || true
fi

# 等待 2 秒讓 OS 完全釋放 Socket
sleep 2

# 啟動 main.py
if command -v python3 >/dev/null 2>&1; then
    python3 main.py
else
    python main.py
fi
