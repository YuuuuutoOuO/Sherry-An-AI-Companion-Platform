# 🖥️ 雪莉 Web 管理後台 (Sherry Console v2) — 技術清單

> **版本**：v1.4.1  
> **生成日期**：2026-08-05  
> **用途**：完整記錄橘雪莉 Web UI 後台使用的所有技術、架構、API 端點與實現細節

---

## 目錄

1. [總覽架構](#1-總覽架構)
2. [後端技術堆疊](#2-後端技術堆疊)
3. [前端技術堆疊](#3-前端技術堆疊)
4. [認證與安全系統](#4-認證與安全系統)
5. [完整 REST API 端點清單](#5-完整-rest-api-端點清單)
6. [系統監控 (Metrics)](#6-系統監控-metrics)
7. [地端 AI 服務診斷](#7-地端-ai-服務診斷)
8. [動態設定管理](#8-動態設定管理)
9. [模型選擇器 (Model Picker)](#9-模型選擇器-model-picker)
10. [日誌系統](#10-日誌系統)
11. [Git 版本控制儀表板](#11-git-版本控制儀表板)
12. [Bot 遠端操作](#12-bot-遠端操作)
13. [模組管理 (Mod Manager)](#13-模組管理-mod-manager)
14. [小遊戲遊樂場 (Game Center)](#14-小遊戲遊樂場-game-center)
15. [雪莉測試室 (Chat Test Room)](#15-雪莉測試室-chat-test-room)
16. [語音與音樂監控](#16-語音與音樂監控)
17. [管理員審計](#17-管理員審計)
18. [SSL / HTTPS](#18-ssl--https)
19. [UI 設計系統](#19-ui-設計系統)
20. [前端狀態管理與自動刷新](#20-前端狀態管理與自動刷新)

---

## 1. 總覽架構

```
                   ┌─────────────────────────┐
                   │      Discord Bot        │
                   │   (discord.py asyncio)   │
                   └──────────┬──────────────┘
                              │ bot.loop.create_task(start_server_async)
                              │
                   ┌──────────▼──────────────┐
                   │   Quart + uvicorn        │
                   │   (ASGI async server)    │
                   │   HTTPS port 8080        │
                   └──────────┬──────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
   ┌─────▼─────┐     ┌───────▼───────┐    ┌──────▼──────┐
   │ Ollama     │     │  LM Studio    │    │  SD WebUI   │
   │ :11434     │     │  :1234/v1     │    │  :7860      │
   └───────────┘     └───────────────┘    └─────────────┘
```

- **Web UI 程序與 Bot 進程共用同一個 OS process**
- 使用 `bot.loop.create_task(start_server_async(bot))` 將 Quart 整合進 discord.py 的 asyncio event loop
- Bot 實例以 `_bot_instance` 注入 Web UI，實現雙向互動

---

## 2. 後端技術堆疊

### 2.1 伺服器框架

| 技術 | 版本 | 用途 | 檔案 |
|------|------|------|------|
| **Quart** | (最新) | 全非同步 (asyncio) Web 框架，Flask API 相容。與 discord.py 共用同一個 event loop，不需另開 process/thread | `web_ui/server.py` |
| **uvicorn** | `>=0.20.0` | 高效 ASGI HTTP 伺服器，支援 SSL | `web_ui/server.py` → `start_server_async()` |
| **httpx** | `>=0.27.0` | 代理後端模型 API（Ollama / LM Studio / vLLM） | `web_ui/server.py` |

### 2.2 核心功能總覽

- **單一應用檔**：`web_ui/server.py` (1966 行) 承載所有邏輯（自包含伺服器）
- **模板引擎**：Quart 內建 Jinja2 → 僅渲染 `index.html`（單頁應用）
- **靜態檔案**：`/static/*` 路徑（`style.css` 2037 行 / `app.js` 2370 行）
- **無外部 DB 依賴**：所有狀態儲存於記憶體 dict + 本地 JSON 檔案

### 2.3 系統資源監控依賴

| 技術 | 用途 |
|------|------|
| **psutil** | CPU 使用率、邏輯核心數、時脈、RAM 總量/已用/百分比、網路 I/O 計數器（即時計算速率） |
| **nvidia-smi** (subprocess) | GPU 名稱、使用率、VRAM 用量、溫度（CSV 解析） |
| **subprocess (git)** | Git 分支、commit hash、提交訊息、作者、日期 |

### 2.4 SSL / 加密依賴

| 技術 | 用途 |
|------|------|
| **cryptography** (x509, hashes, serialization, rsa) | 自動生成自簽 TLS 憑證（RSA 2048 + SHA256） |
| **secrets** (標準庫) | `token_urlsafe(32)` 生成 session token |
| **hmac** (標準庫) | `hmac.compare_digest()` 恆定時間密碼比對（防時序攻擊） |

### 2.5 稽核整合

- `mod/audit_manager` → `record_login()` / `record_logout()` / `record_action()`
- 落盤位置：`data/admin_audit.jsonl`

---

## 3. 前端技術堆疊

### 3.1 核心選擇

| 選擇 | 說明 |
|------|------|
| **純 HTML/CSS/JS SPA** | 無前端框架（無 React / Vue / Angular 等），保持零建置依賴 |
| **Google Fonts** | **Inter**（UI 文字）+ **JetBrains Mono**（程式碼/日誌區塊） |
| **Jinja2** | Quart 內建模板引擎，僅用於渲染單一 `index.html`，無服務端動態模板 |
| **RESTful AJAX** | `fetch()` + JSON 進行所有 API 互動 |
| **純 CSS 自訂** | 2037 行 CSS（無 Tailwind / Bootstrap），手寫暗色玻璃擬態主題 |

### 3.2 CSS 設計系統

| 特性 | 實現 |
|------|------|
| **CSS Custom Properties** | 完整 Design Token 系統（`--bg-deep`、`--cyan`、`--purple`、`--text-primary`、`--radius-md` 等） |
| **暗色主題** | 深空藍色調 `#080e1a ~ #0d1729` |
| **玻璃擬態** | `backdrop-filter: blur(20px)` + `rgba(15, 25, 45, 0.7)` 半透明卡片 |
| **Neon Accent** | `--cyan: #64ffda` / `--purple: #bd93f9` 發光按鈕與狀態指示 |
| **自訂 Scrollbar** | `-webkit-scrollbar` 透明底 + 青色 thumb |
| **過渡動畫** | `cubic-bezier(0.4, 0, 0.2, 1)` 統一緩動、`fade-out` 離開動畫 |
| **響應式** | Grid 佈局 `repeat(auto-fit, minmax(...))`、側邊欄固定寬度、主內容 flex-grow |

### 3.3 JavaScript 架構

| 特性 | 說明 |
|------|------|
| **單一全域狀態物件** | `state` dict 管理：`currentView`、`settingsData`、`dirtyKeys`、`adminToken`、`lastActivityTime` 等 |
| **View Switching** | `switchView(view)` 動態顯示/隱藏 11 個功能頁面，管理各自的計時器與工具列 |
| **自動輪詢** | Dashboard (5s) / Metrics (2s) / Services (5s) / BotStatus (3s) / Logs (5s) |
| **預設無快取** | Server 端 `add_no_cache_headers` 注入 `no-cache, no-store, must-revalidate`，加上 query `?v=7` cache busting |
| **XSS 防護** | `escHtml()` / `escAttr()` 自訂 HTML 轉義函式 |
| **Token 儲存** | `localStorage` + `HttpOnly` Cookie 雙通道 |

---

## 4. 認證與安全系統

### 4.1 認證架構

```
┌──────────────┐     POST /api/auth/login     ┌──────────────┐
│   前端 (JS)   │ ──────────────────────────▶	│   Quart API   │
│ localStorage │                             │_admin_sessions│
│ HttpOnly     │ ◀── set-cookie  HttpOnly ─── │   dict{}      │
│ Cookie       │     json { token }          │               │
└──────────────┘                              └──────────────┘
```

### 4.2 Token 發行與驗證

| 步驟 | 說明 |
|------|------|
| **密碼比對** | `hmac.compare_digest(password.encode(), target_pw.encode())` — 恆定時間比對，防時序攻擊 |
| **Token 生成** | `secrets.token_urlsafe(32)` — 加密安全隨機 32 bytes |
| **Token 儲存** | 記憶體 `_admin_sessions: dict[str, float]` — token → last_active timestamp |
| **Token 回傳** | (1) JSON `{token}` + (2) `set_cookie("admin_session", token, httponly=True, samesite="Strict")` |
| **Token 驗證** | 前端發送時可選擇 `Authorization: Bearer <token>` header 或 `admin_session` cookie 或 `X-Admin-Token` header |
| **Session 過期** | `_SESSION_TIMEOUT_SEC = 600`（10 分鐘無動作） |
| **Session 清理** | `_clean_expired_sessions()` 於每次請求前被呼叫 |

### 4.3 IP 暴力破解防護

| 參數 | 預設值 | 位置 |
|------|--------|------|
| `WEB_UI_MAX_LOGIN_ATTEMPTS` | 5 次 | `settings.py` |
| `WEB_UI_LOCKOUT_MINUTES` | 15 分鐘 | `settings.py` |

- **失敗記錄**：`_login_attempts: dict[str, list[float]]` （IP → 失敗時間戳記列表，60 秒滑動窗口）
- **封鎖機制**：`_locked_ips: dict[str, float]` （IP → 解鎖時間戳）
- **狀態碼**：觸發時回傳 `429 Too Many Requests`，附 `retry_after_sec`

### 4.4 權限分層

| 層級 | GET 路由 | POST/PUT/DELETE 路由 | 日誌可見 |
|------|---------|---------------------|---------|
| **訪客** | 全部開放（唯讀） | 登入與遊戲 API 開放；其他回 401 | 僅 `bot.log` + `runtime_log.jsonl` |
| **管理員** | 全部開放 | 全部開放 | 所有日誌 + 審計紀錄 |

- **實現**：`@app.before_request` → `require_auth()` — GET 全部放行、POST 依路徑區分

### 4.5 前端無動作偵測

- `startInactivityTracker()` 監聽 `mousemove/click/keydown/touchstart/scroll` 事件
- 每秒檢查剩餘時間 → 更新 UI 倒數計時器 → 滿 10 分鐘自動 `logoutAdmin(true)`
- 顯示「⏳ 超過 10 分鐘無動作，已自動切回訪客唯讀模式」

---

## 5. 完整 REST API 端點清單

### 5.1 認證 (Auth)

| 方法 | 路徑 | 說明 | Auth |
|------|------|------|------|
| POST | `/api/auth/login` | 使用密碼登入，回傳 session token + 設定 HttpOnly Cookie | 公開 |
| POST | `/api/auth/logout` | 撤銷 token、清除 Cookie、記錄審計 | 公開 |
| GET | `/api/auth/check` | 檢查當前 session 是否有效 | Token/Cookie |

### 5.2 日誌 (Logs)

| 方法 | 路徑 | 參數 | 說明 |
|------|------|------|------|
| GET | `/api/logs/files` | — | 列出 data/ 下可讀取的日誌檔案（`.jsonl/.json/.log`，排除 `config_override`） |
| GET | `/api/logs` | `?file= &page= &size=` | 讀取日誌內容（分頁、最新優先、訪客僅能看 bot.log + runtime_log.jsonl） |

### 5.3 模型 (Models)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/models/ollama` | 代理 `OLLAMA_BASE_URL/api/tags` → 回傳可用 Ollama 模型列表 |
| GET | `/api/models/lms` | 代理 `LMS_BASE_URL/models` → 回傳已加載的 LM Studio 模型列表 |
| GET | `/api/models/vllm` | 代理 `VLLM_BASE_URL/models` → 回傳 vLLM 模型列表 |

### 5.4 系統設定 (Settings)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/settings` | 回傳所有可編輯設定（含 meta：label/type/category/min/max/step）；過濾 TOKEN/KEY/PASSWORD/SECRET |
| POST | `/api/settings` | 批次更新設定（JSON body `{KEY: value}`）；防注入過濾、型別消毒、`config_override.json` 落盤 |

### 5.5 系統監控 (System)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/system/metrics` | CPU/RAM/網路速率/GPU(VRAM/溫度) 即時數值 |
| GET | `/api/system/dashboard` | 總覽儀表板聚合資料（Git 狀態 + Bot 狀態 + 系統摘要 + 佇列統計） |
| GET | `/api/system/tasks` | 回傳所有排程任務（單次 + Cron） |
| DELETE | `/api/system/tasks/<task_id>` | 取消指定排程任務（需 Admin） |

### 5.6 AI 服務診斷 (Services)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/services/status` | 即時 ping Ollama / LM Studio / SD WebUI → 線上狀態 + 延遲 ms + 模型清單 |

### 5.7 Bot 狀態 (Bot Status)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/bot/status` | Bot 連線狀態、Discord 伺服器數、WebSocket 延遲、語音頻道清單、當前播放歌曲、歌單 |

### 5.8 大腦推理 (Brain)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/brain/queue` | BotBrain PriorityQueue 中的等待中推理任務列表 |
| POST | `/api/chat/test` | 強制觸發 CHAT 推理（繞過分類器）→ 回覆 + latency_ms + CoT 思維鏈（需 Admin） |

### 5.9 Bot 操作 (Bot Control) — 均需 Admin

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/restart` | 重啟雪莉（依平台叫用 restart_sherry.bat/.sh，`CREATE_NEW_CONSOLE`） |
| POST | `/api/stop` | 關閉雪莉（調用 `os._exit(0)`） |
| POST | `/api/update` | `git pull origin dev` + 重啟（叫用 update_and_restart.bat/.sh） |

### 5.10 審計 (Audit) — 均需 Admin

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/audit/logs` | 回傳最近 50 筆管理員登入/操作紀錄（含 IP、位置、裝置、操作履歷） |

### 5.11 模組管理 (Modules) — 切換需 Admin

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/modules` | 掃描 `./mod/` 目錄，回傳所有可用模組及其啟用狀態、metadata |
| POST | `/api/modules/toggle` | `{mod_key, enabled}` → 啟用/停用模組（寫入 `data/mod_config.json`） |

### 5.12 小遊戲 (Games) — 公開 POST

| 方法 | 路徑 | 遊戲 |
|------|------|------|
| POST | `/api/games/rps/play` | 猜拳（石頭/剪刀/布 + LLM 生成對話） |
| POST | `/api/games/blackjack/start` | 21 點開局 |
| POST | `/api/games/blackjack/hit` | 21 點叫牌 |
| POST | `/api/games/blackjack/stand` | 21 點停牌 |
| POST | `/api/games/minesweeper/start` | 踩地雷開局 (8×8, 10 地雷) |
| POST | `/api/games/minesweeper/reveal` | 踩地雷翻開格子 |
| POST | `/api/games/minesweeper/flag` | 踩地雷插旗標記 |
| POST | `/api/games/minesweeper/hint` | 踩地雷求助雪莉 LLM 推理提示 |
| POST | `/api/games/turtle_soup/start` | 海龜湯開局（隨機抽題） |
| POST | `/api/games/turtle_soup/ask` | 海龜湯提出是/否問題 |
| POST | `/api/games/akinator/start` | 讀心術 (20Q) 開局 |
| POST | `/api/games/akinator/answer` | 回答是/否/不確定（雪莉 LLM 推導下一題） |
| POST | `/api/games/escape_room/start` | 密室逃脫開局 |
| POST | `/api/games/escape_room/action` | 密室調查行動（書桌/畫框/大門） |
| POST | `/api/games/ttt/start` | 圈圈叉叉開局 |
| POST | `/api/games/ttt/move` | 圈圈叉叉下子 |

**總計：32 個 API 端點（含同路徑不同方法）**

---

## 6. 系統監控 (Metrics)

### 6.1 資料來源

| 指標 | 來源 | 函式 |
|------|------|------|
| CPU 使用率 | `psutil.cpu_percent(interval=None)` | `_get_system_metrics()` |
| CPU 邏輯核心數 | `psutil.cpu_count(logical=True)` | ↑ |
| CPU 時脈 | `psutil.cpu_freq()` | ↑ |
| RAM 用量 | `psutil.virtual_memory()` | ↑ |
| 網路即時速率 | `psutil.net_io_counters()` 差值 / dt | ↑ (差分計算，`_last_net_bytes` 全域快取) |
| GPU 資訊 | `nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits` | ↑ |

### 6.2 前端渲染

- `GET /api/system/metrics` → JSON 資料 → `loadMetrics()` 更新 DOM
- **2 秒自動刷新**（Metrics 頁面）
- 進度條樣式：`progress-bar-fill bar-cpu`（青色）、`bar-ram`（紫色）、`bar-gpu`（綠色）
- GPU 卡片：未偵測到時顯示「無獨顯 / 未偵測到」；偵測到時顯示名稱、溫度、使用率、VRAM 用量
- 網路速率：KB/s 動態顯示；超過 1024 KB/s 自動切換為 MB/s

---

## 7. 地端 AI 服務診斷

### 7.1 檢測項目

| 服務 | 檢測端點 | 逾時 |
|------|---------|------|
| **Ollama LLM** | `GET {OLLAMA_BASE_URL}/api/tags` | `WEB_UI_HTTP_TIMEOUT` (5s) |
| **LM Studio** | `GET {LMS_BASE_URL}/models` | 5s |
| **Stable Diffusion WebUI** | `GET http://127.0.0.1:7860/sdapi/v1/options` | 5s |

### 7.2 回傳內容

```json
{
  "id": "ollama",
  "name": "Ollama LLM 服務",
  "icon": "🦙",
  "url": "http://localhost:11434",
  "status": "online" | "offline",
  "ping_ms": 12,
  "details": "服務線上，共偵測到 5 個可用模型",
  "models": ["model1", "model2", ...]  // 最多 6 個
}
```

- **前端 5 秒自動刷新**（Services 頁面）
- 服務卡片樣式：`service-online`（青色邊框）/ `service-offline`（暗色）
- 連線延遲色標：`<60ms`（快速）/ `<200ms`（中等）/ `>200ms`（緩慢）

---

## 8. 動態設定管理

### 8.1 後端架構

```
settings.py
├── SETTINGS_META (dict)  ← 每個可編輯設定的 metadata
│   ├── label             "主力模型 (Primary Model)"
│   ├── description       說明文字
│   ├── type              "model" | "bool" | "float" | "int" | "str" | "url"
│   ├── category          "模型設定" | "LLM 參數" | ...
│   ├── min / max / step  (for float/int 型別)
│   └── ... 共 60+ 項設定
│
├── get_editable_settings()  ← 收集所有在 SETTINGS_META 中的設定
├── update_setting(key, value)  ← 單項安全寫入（型別檢查 + config_override.json）
└── config_override.json    ← 持久化覆寫設定（環境變數設定的 URL 不會被覆蓋）
```

### 8.2 防護機制

| 機制 | 實現 |
|------|------|
| **敏感欄位過濾** | 任何包含 `TOKEN`、`KEY`、`PASSWORD`、`SECRET` 的設定在 GET 回應中完全移除 |
| **型別強制** | POST 時檢查 `globals()[key]` 的原始型別，自動轉型（str/int/float/bool） |
| **安全字串處理** | 非基本型別（dict/list 等）強制轉為純文字 string |
| **路徑/注入過濾** | Key 名必須全大寫 + `isidentifier()`，否則拒絕寫入 |
| **環境變數保護** | `config_override.json` 載入時：若 `.env` 有設定 OLLAMA/LMS/VLLM/SD URL → 不允許 override 覆蓋 |

### 8.3 前端渲染

- `GET /api/settings` → `state.settingsData` → `renderSettings()` 自動依型別生成控件：

| type | 控件 |
|------|------|
| `"model"` | 前綴下拉（ollama/lms/openai/deepseek/gemini/claude）+ 模型名稱下拉列表 + 文字輸入框 + 載入按鈕 |
| `"bool"` | CSS toggle switch（ON/OFF 文字標籤） |
| `"float"` | Slider（range input）+ 數值輸入框（step 0.05/0.1） |
| `"int"` | Slider + 數值輸入框 |
| `"str"` | 文字輸入框（>50 字自動切換為 textarea） |
| `"url"` | URL 輸入框（`type="url"`） |

### 8.4 分類頁籤

| 頁籤 ID | 類別 | 包含設定項 |
|--------|------|-----------|
| `tab-ai` | AI 模型與大腦 | 模型設定、LLM 參數、Chain-of-Thought |
| `tab-persona` | 雪莉靈魂與人設 | 雪莉人設與心跳、插話引擎、打字模擬、記憶與上下文 |
| `tab-voice` | 語音通話 (TTS/STT) | 語音辨識與合成 |
| `tab-art` | 繪圖與二次元圖庫 | 圖片生成、二次元圖庫 API |
| `tab-search` | 搜尋與時事爬蟲 | Web 爬蟲、外部資訊檢索、YouTube 爬蟲 |
| `tab-advanced` | 系統與進階 | 所有其他類別（包含設置優先級、音樂、系統、深夜整合、健康、氛圍、速率、後端連線） |

### 8.5 髒值追蹤 (Dirty Tracking)

- `markDirty(key, newValue)` — 比對原始值與當前值 → `Set dirtyKeys`
- 視覺提示：變更的輸入框加上 `.dirty` 樣式（黃色邊框）
- 頂部提示：`● 有未儲存的變更` 動態顯示/隱藏
- `saveSettings()` → `POST /api/settings` → 成功 toast `"請重啟雪莉套用變更"`

---

## 9. 模型選擇器 (Model Picker)

### 9.1 組件結構

```
┌─────────────────────────────────────────────────────┐
│ [前綴下拉: ollama ▼] [模型名稱下拉 ▼] [手動輸入___] 🔄│
└─────────────────────────────────────────────────────┘
```

### 9.2 互動流程

1. 使用者點擊 `⟳` 按鈕或切換前綴
2. 前端判斷是否為本地後端（`ollama` / `lms`）→ 調用對應 API
3. 成功 → 填充下拉列表，sync 當前值
4. 失敗或被選為雲端 → 隱藏下拉，僅顯示文字輸入框
5. 下拉選項最末附 `✏️ 手動輸入...` 選項

---

## 10. 日誌系統

### 10.1 支援格式

| 副檔名 | 解析方式 |
|--------|---------|
| `.jsonl` | 逐行 `json.loads()` + 雙空行區塊支援 |
| `.json` | `json.loads()` → 偵測 list/dict |
| `.log` | 逐行包裝為 `{event: "raw", message: ln}` |

### 10.2 安全限制

- **路徑遍歷防護**：`safe_name = Path(filename).name`（僅取出檔名部分）
- **黑名單**：`config_override` 字串自動排除
- **訪客限制**：僅允許查看 `bot.log` 與 `runtime_log.jsonl`

### 10.3 前端功能

| 功能 | 操作 |
|------|------|
| **分頁** | `page` + `size` query params，`changePage(±1)` 導航 |
| **事件過濾** | 全部 / CHAT / 插話 / ERROR 按鈕，`setEventFilter()` |
| **詳情 Modal** | 點擊日誌行 → `showLogDetail()` → JSON 格式化預覽（pre element） |
| **自動刷新** | 5 秒輪詢，可透過 toggle checkbox 開關（`auto-refresh-chk`） |

### 10.4 事件分類渲染

| event 值 | 渲染內容 | 樣式 |
|---------|---------|------|
| `chat_reply` | user 輸入 + intent + latency_ms + reply_preview + cot_thought | `badge-chat` (青色) |
| `interject` | score + reasoning + reply | `badge-interject` (紫色) |
| `error` | message / error 內容 | `badge-error` (紅色) |

---

## 11. Git 版本控制儀表板

### 11.1 後端實作

- **`_get_git_info()`**：`git rev-parse --abbrev-ref HEAD` + `git log -1 --format=%h|%H|%s|%cd|%an`
- **啟動快照**：`_STARTUP_GIT_INFO` — 記錄 Bot 啟動時的 commit hash（寫入記憶體）
- **磁碟即時查詢**：每次 Dashboard 請求時重新 query 本地磁碟 git 狀態
- **需重啟偵測**：`running_hash != disk_hash` → 觸發前端警告（`needs_restart: true`）

### 11.2 前端顯示

- **執行中版本 vs 磁碟版本** 並排比較
- **版本同步狀態**：🟢 最新 / ⚠️ 檢測到新版本已 Pull，請重啟套用
- **警告橫幅**：提示當前執行版與磁碟版 commit 差異 + 一鍵重啟按鈕
- **Git pull 歷史**：讀取 `data/gitpull_result.txt` 顯示最後 pull log 與時間

---

## 12. Bot 遠端操作

### 12.1 重啟 (Restart)

1. `POST /api/restart` → 驗證 Admin → 記錄審計
2. `asyncio.call_later(0.8, _do_restart)` → 延遲 0.8 秒確保 HTTP 回應先送出
3. 依平台叫用對應啟動腳本：
   - **Windows**：`subprocess.Popen(["cmd.exe", "/C", "restart_sherry.bat"], creationflags=0x00000010)`（`CREATE_NEW_CONSOLE`，脫離 VS Code 終端）
   - **Linux**：`subprocess.Popen(["/bin/bash", "restart_sherry.sh"], start_new_session=True)`
4. `os._exit(0)` 立即釋放 OS 資源（避免 Python GC SSL Transport 錯誤）
5. **前端輪詢重連**：`_pollForRestart()` 每秒 ping `/api/logs/files`，最多 35 次 → 成功自動 `window.location.reload()`

### 12.2 更新並重啟 (Update = git pull + restart)

1. `POST /api/update` → 驗證 Admin
2. 叫用 `update_and_restart.bat` / `.sh`（先 `git pull origin dev` 再重啟）
3. 前端輪詢 50 次（預期 git pull 耗時較長）

### 12.3 關閉 (Stop)

1. `POST /api/stop` → 驗證 Admin → 記錄審計
2. `os._exit(0)` 直接終止 Python 程序 → Web UI 連線中斷
3. 前端顯示停止確認 overlay

---

## 13. 模組管理 (Mod Manager)

### 13.1 後端

- **`_MOD_REGISTRY`**：手動登記的 Mod metadata（名稱、描述、分類、導航按鈕 ID、Python 套件路徑）
- **`_load_mod_config()`** / **`_save_mod_config()`**：讀寫 `data/mod_config.json`
- **`/api/modules`**：掃描 `./mod/` 目錄 + 合併 registry metadata + config 狀態
- **`/api/modules/toggle`**：Admin-only，啟用/停用 + 記錄審計

### 13.2 前端

- **模組卡片**：名稱、描述、類別標籤、`🟢 啟用中` / `🔴 已停用` 徽章
- **切換按鈕**：`toggleModuleStatus(key, enable)` → 成功後顯示通知橫幅「請重啟雪莉生效」
- **動態側邊欄**：game_center 啟用/停用時即時注入/移除「🎮 小遊戲遊樂場」按鈕（不須重啟）

---

## 14. 小遊戲遊樂場 (Game Center)

### 14.1 遊戲清單與 API 對應

| 遊戲 | 引擎位置 | API 端點 |
|------|---------|---------|
| **猜拳** | `mod.game_center.rock_paper_scissors` | `/api/games/rps/play` |
| **21 點** | `mod.game_center.blackjack` | start / hit / stand |
| **踩地雷** | `mod.game_center.minesweeper` | start / reveal / flag / hint |
| **海龜湯** | `mod.game_center.turtle_soup` | start / ask |
| **讀心術 (20Q)** | `mod.game_center.akinator_20q` | start / answer |
| **密室逃脫** | `mod.game_center.escape_room` | start / action |
| **圈圈叉叉** | `mod.game_center.tic_tac_toe` | start / move |

### 14.2 通用技術

- **LLM 對話生成**：所有遊戲均透過 `SherryGameAIService`（`mod.game_center.sherry_ai`）生成雪莉的遊戲內對話
- **前端狀態機**：每種遊戲維護獨立 state 物件
- **遊戲面板切換**：`switchGameTab(gameId)` 顯示/隱藏對應面板

---

## 15. 雪莉測試室 (Chat Test Room)

### 15.1 功能

- **Admin-only**：為避免濫用，僅管理員可觸發
- **強制 CHAT**：繞過分類器，直接以 `InferenceTask(priority=PRIORITY_TAG_RESPONSE)` 送入 BotBrain
- **即時 Web 搜尋整合**：`_should_trigger_search()` + `fetch_web_and_entity_context()` 動態注入搜尋結果

### 15.2 技術細節

- **BotBrain 橋接**：透過 `_bot_instance.get_cog("BotBrain")` 獲取 Brain Cog → `brain.enqueue_task(task)`
- **非同步等待**：`asyncio.wait_for(fut, timeout=60.0)` 等待 LLM 回覆
- **CoT 抽離**：`_process_micro_cot_extraction()` 將 `<thought>` 寫入 runtime_log
- **逾時處理**：60 秒逾時 → HTTP 504

### 15.3 前端

- 聊天介面：`appendChatMessage()` 動態新增訊息泡泡
- 打字中動畫：先插入 `typing-bubble` 佔位 → 收到回覆後移除
- 耗時顯示：`⚡ 耗時 {latency_ms} ms`
- 清空按鈕：`clearChatHistory()` → 重置為初始系統訊息

---

## 16. 語音與音樂監控

### 16.1 資料來源

- Bot 的 `voice_clients` 清單 → 取得語音頻道資訊
- `MusicHandler` Cog → 取得當前歌曲 + 歌單隊列 + 播放狀態

### 16.2 顯示內容

| 資訊 | 欄位 |
|------|------|
| **Bot 狀態** | 名稱、已連線伺服器數、Discord API 延遲 (ms) |
| **語音頻道** | 公會名稱 + 圖示、頻道名稱、在線成員名單（含頭像）、成員數 |
| **播放狀態** | ▶️ 播放中 / ⏸️ 暫停中 / ⏹️ 空閒中 |
| **當前曲目** | 標題（超連結）、點歌者、總長 |
| **歌單隊列** | 最多顯示前 5 首，附總剩餘數量 |

- **3 秒自動刷新**（Bot Status 頁面）

---

## 17. 管理員審計

### 17.1 後端

- `mod/audit_manager` → `record_login()` / `record_logout()` / `record_action()`
- 落盤：`data/admin_audit.jsonl`（JSONL 格式）
- `GET /api/audit/logs` 回傳最近 50 筆

### 17.2 前端

- **表格檢視**：帳號、登入時間、狀態/登出時間、IP、位置、裝置、操作履歷
- **操作履歷詳情 Modal**：時間軸樣式（timeline）顯示操作順序

---

## 18. SSL / HTTPS

### 18.1 憑證生成

- **自動自簽**：`_ensure_self_signed_cert()` — 首次啟用時自動生成
- **依賴**：`cryptography` 套件
- **演算法**：RSA 2048 + SHA256
- **有效期限**：3650 天（10 年）
- **SAN（主體別名）**：
  - `DNSName("localhost")`
  - `IPAddress("127.0.0.1")`
  - `IPAddress("0.0.0.0")`
  - `IPAddress(WEB_UI_RADMIN_IP)`（Radmin VPN 虛擬 IP，用於遠端管理）或 fallback `sherry.local`

### 18.2 憑證路徑

- **Cert**：`data/ssl/cert.pem`
- **Key**：`data/ssl/key.pem`
- **可覆寫**：透過 `.env` 設定 `WEB_UI_SSL_CERT` / `WEB_UI_SSL_KEY`

### 18.3 啟動

- `start_server_async()` (async coroutine) → `uvicorn.Config(ssl_certfile=, ssl_keyfile=)` + `server.serve()`
- 預設 HTTPS enabled (`WEB_UI_HTTPS = True`)
- 綁定失敗 fallback：嘗試 `0.0.0.0`（若非管理員權限導致無法綁定特定 IP）

---

## 19. UI 設計系統

### 19.1 色彩方案

| Token | 值 | 用途 |
|-------|-----|------|
| `--bg-deep` | `#080e1a` | 頁面底色 |
| `--bg-mid` | `#0d1729` | 卡片/區塊底色 |
| `--bg-card` | `rgba(15, 25, 45, 0.7)` | 半透明卡片 |
| `--cyan` | `#64ffda` | 主色調（highlight、按鈕、連結） |
| `--purple` | `#bd93f9` | 次要色調 |
| `--orange` | `#ffb86c` | 警示/暖色強調 |
| `--green` | `#50fa7b` | 成功/上線狀態 |
| `--red` | `#ff5555` | 錯誤/離線狀態 |
| `--yellow` | `#f1fa8c` | 警告 |
| `--text-primary` | `#e2e8f8` | 主要文字 |
| `--text-muted` | `#7b8db0` | 次要/說明文字 |
| `--text-dim` | `#4a5570` | 最淡文字 |

### 19.2 佈局

| Token | 值 | 用途 |
|-------|-----|------|
| `--sidebar-w` | `220px` | 側邊欄固定寬度 |
| `--topbar-h` | `64px` | 頂部工具列高度 |
| `--radius-sm/md/lg` | `6px / 12px / 18px` | 圓角層級 |

### 19.3 UI 元件

| 元件 | CSS 類別 |
|------|---------|
| Toast 通知 | `.toast-container` + `.toast-{success/info/warning/error}`（3 秒自動消失，fade-out 動畫） |
| Modal | `.modal-backdrop` + `.modal`（日誌詳情、登入、審計詳情、重啟確認、停止確認） |
| Toggle Switch | `.toggle-switch` + `.toggle-track`（CSS only，無 JS lib） |
| Progress Bar | `.progress-bar-bg` + `.progress-bar-fill`（CPU/RAM/GPU 條） |
| 服務卡片 | `.service-card.service-online` / `.service-offline`（青色/暗色邊框） |
| 分頁 | `.pagination` + `.page-btn`（上一頁/下一頁） |
| 過濾按鈕 | `.filter-btn`（全部/CHAT/插話/ERROR） |
| 側邊導航 | `.nav-btn.active`（啟動中頁面反白） |
| Admin-only | `.admin-only` 自動顯示/隱藏（依 `updateAuthUI()`） |

---

## 20. 前端狀態管理與自動刷新

### 20.1 全域狀態物件

```javascript
const state = {
  currentView: 'dashboard',       // 當前頁面
  currentLogFile: 'runtime_log.jsonl',
  currentPage: 1,
  totalPages: 1,
  totalEntries: 0,
  eventFilter: 'all',             // 日誌過濾
  autoRefreshEnabled: true,       // 日誌自動刷新開關
  currentSettingsTab: 'tab-ai',   // 設定頁籤
  settingsData: {},               // 設定項快取
  dirtyKeys: new Set(),           // 未儲存變更
  ollamaModels: [],
  lmsModels: {},
  adminToken: localStorage.getItem('sherry_admin_token') || null,
  isAdmin: false,
  lastActivityTime: Date.now(),
  // 計時器
  autoRefreshTimer: null,         // 日誌 5s
  metricsTimer: null,             // 系統資源 2s
  serviceCheckTimer: null,        // 服務診斷 5s
  botStatusTimer: null,           // Bot 狀態 3s
  dashboardTimer: null,           // 儀表板 5s
  inactivityCheckTimer: null,     // 無動作 1s
};
```

### 20.2 自動刷新排程

| 頁面 | 間隔 | 計時器 |
|------|------|--------|
| 總覽主控台 | 5 秒 | `dashboardTimer` |
| 系統資源 | 2 秒 | `metricsTimer` |
| 地端 AI 服務 | 5 秒 | `serviceCheckTimer` |
| 語音音樂監控 | 3 秒 | `botStatusTimer` |
| 即時日誌 | 5 秒 | `autoRefreshTimer`（可手動開關） |

- **頁面切換時管理**：`switchView()` 會清除離開頁面的計時器，並啟動新頁面的計時器

### 20.3 頁面總覽

| 導航項目 | 頁面 ID | 權限 |
|---------|--------|------|
| 📊 總覽主控台 | `view-dashboard` | 公開 |
| 📋 即時日誌 | `view-logs` | 訪客僅能看 bot.log + runtime_log |
| 💻 系統資源 | `view-metrics` | 公開 |
| ⏰ 任務排程 | `view-tasks` | 公開（刪除需 Admin） |
| 💬 雪莉測試室 | `view-chat` | Admin-only |
| 📡 地端 AI 服務 | `view-services` | 公開 |
| 🎵 語音與音樂監控 | `view-bot-status` | 公開 |
| 🛡️ 管理員審計 | `view-audit` | Admin-only |
| 🧩 模組管理 | `view-mod-manager` | 切換需 Admin |
| 🎮 小遊戲遊樂場 | `view-games` | 動態顯示（game_center 啟用時） |
| ⚙️ 系統設定 | `view-settings` | 公開檢視（儲存需 Admin） |

---

> 📝 **備註**：此文件為 Web UI 的**獨立完整技術清單**。全系統技術摘要請參閱 `技術摘要_全系統.md`。