# 10 — Quart Web 控制台與 Uvicorn 雙軌架構 (Web UI Architecture)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度解析雪莉系統內建的 Quart Web 管理後台架構。探討其如何透過 Uvicorn 在 Discord 同一個非同步事件循環中無縫掛載、Token 驗證與 10 分鐘閒置過期安全機制、自簽憑證 HTTPS 自動生成、外掛模組動態 Web 視圖掛載以及專屬日誌通道分流 (`data/web_ui.log`)。

---

## 🧭 Prerequisites (先備知識)
- Quart 非同步 Web 框架（與 Flask API 語法相容但全非同步）。
- Uvicorn ASGI Server 配置與 programmatic running (`Server.serve()`)。
- HTTP Cookie、Session 與 Bearer Token 身份驗證安全機制。

---

## 💡 Concept (嵌入式運維儀表板設計理論)
傳統 Discord 機器人往往缺乏可視化後台，維運時必須頻繁查看終端或直接修改設定檔。
將輕量級 Web 伺服器嵌入於 Bot 進程內，具備以下優勢：
1. **零外部依賴**：不需部署 Nginx 或獨立後端，開箱即用。
2. **同行程資料共享**：可以直接讀取 Bot 實例記憶體中的即時狀態（如連線中的 Guild 清單、推論佇列長度、記憶體用量）。
3. **安全性挑戰**：必須防範未經授權的外部公網存取、暴力破解攻擊與敏感環境變數洩漏。

---

## 🛠️ Sherry Implementation (雪莉 Web UI 實作細節)

### 一、伺服器啟動與網路綁定
- `[CONFIRMED]` [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2726-L2798)：
  - 進入點為 `async def start_server_async(bot: Any = None)`。
  - 預設綁定 `settings.WEB_UI_HOST` (預設 127.0.0.1 或 0.0.0.0) 與 `settings.WEB_UI_PORT` (預設 8000)。
  - 若自訂主機綁定失敗，具備自動降級機制（回退至 `0.0.0.0` 重新綁定）。

---

### 二、資安驗證與安全防禦
1. **一次性安全密碼防禦**：
   - `[CONFIRMED]` [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2734-L2740)：若環境變數未設置 `WEB_UI_PASSWORD`，系統在啟動時自動透過 `secrets` 模組生成隨機一次性管理員密碼，僅在本地控制台印出，不寫入任何日誌。
2. **Token 驗證與 10 分鐘閒置過期**：
   - `[CONFIRMED]` [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L53-L58)：
     ```python
     _admin_sessions: dict[str, float] = {}  # token -> last_active_timestamp
     _SESSION_TIMEOUT_SEC: float = 600.0     # 10 分鐘無動作自動過期
     _login_attempts: dict[str, list[float]] = {}
     _locked_ips: dict[str, float] = {}
     ```
   - 內建暴力密碼破解防禦：同一 IP 登入失敗超標自動鎖定冷卻。
3. **自簽 SSL 憑證生成**：
   - `[CONFIRMED]` 若啟用 `WEB_UI_HTTPS=true` 且未提供憑證路徑，`_ensure_self_signed_cert()` 會透過 `cryptography` 套件即時生成本地自簽憑證，保證傳輸通道 TLS 加密。

---

### 三、日誌分流隔離機制
為了避免高頻的 Web 存取日誌（如輪詢、靜態資源載入）污染 Discord 終端主視窗：
- `[CONFIRMED]` [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2752-L2771)：
  - 建立專屬的 `data/web_ui.log` FileHandler。
  - 將 `uvicorn.access` 的 propagate 設為 `False`，使其存取日誌僅寫入 `web_ui.log`，徹底維持主終端潔淨。

---

### 四、動態 Mod Web Plugin 自動掛載
- `[CONFIRMED]` [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2731)：
  - 呼叫 `_discover_and_mount_mod_plugins(app, bot)`，自動搜尋各 `mod/<name>/web_routes.py` 或 Blueprint，將可選外掛的管理面板動態掛載至 Web 路由。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **Uvicorn 日誌分離代碼**：
   - 見 [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2759-L2763)。
2. `[CONFIRMED]` **自動 HTTPS 憑證處理**：
   - 見 [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2745-L2748)。
3. `[CONFIRMED]` **敏感金鑰遮蔽輸出**：
   - 見 [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L16)，在回傳 `/api/settings` 時，包含 `TOKEN`, `KEY`, `SECRET`, `PASSWORD` 等欄位一律遮蔽為星號或排除。

---

## 📊 Flow Diagram (Web UI 與 Bot 雙軌架構)

```text
[Browser / Admin]
        │
        │ HTTPS (Port 8000)
        ▼
[Uvicorn ASGI Server]
        │
        │ HTTP Request Pipeline
        ▼
[Quart App (web_ui/server.py)]
        │
        +---> (1) Session 驗證 (_admin_sessions & IP Lock)
        |
        +---> (2) 日誌過濾器 ──► [data/web_ui.log] (Propagate=False)
        |
        +---> (3) 核心 API 處理:
                    - /api/logs         (讀取 bot.log / 歷史日誌)
                    - /api/settings     (動態讀寫 settings.py)
                    - /api/models/*     (代理轉發 Ollama/LMStudio 模型清單)
                    - /api/tasks/queue  (即時查詢 BotBrain 推論佇列)
                    │
                    ▼
          [Direct Memory Access to `_bot_instance`]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **熱重載設定引發未同步狀態**：
   - 當管理員透過 Web UI 修改特定模型名稱時，若該常數已被某個 Cog 在模組初始化時靜態賦值至局部變數，動態覆寫 `settings.py` 可能無法即時影響已載入之 Cog，需搭配 `AdminCog` 的熱重載指令。
2. **大檔案下載 OOM 邊界**：
   - 若管理員透過 Web UI 下載超大型日誌或 SQLite 備份檔，若採 `read()` 整檔讀入記憶體再一次性回傳，將導致瞬間記憶體暴增。應採用 Quart 的串流回應 (`quart.Response(stream_generator())`)。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **純記憶體 Session 字典 (`_admin_sessions`)** | 極簡無依賴，不需 Redis 或 DB 儲存，重啟自動登出更安全。 | 每次重開機所有已登入管理員 Session 立即失效，需重新輸入密碼。 | 採用基於 JWT (JSON Web Token) 的無狀態簽名或 SQLite Session 表。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在一個將 Web 控制台掛在後台守護進程的系統中，如何防範跨站請求偽造 (CSRF) 與敏感配置洩漏？**
  - **Ans**:
    1. **CSRF 防禦**：管理 API 全面採用 JSON POST + 自訂 Header (如 `X-Admin-Token` 或 `Authorization: Bearer <token>`)，瀏覽器跨域表單無法任意夾帶自訂 Header，天然免疫標準 CSRF。
    2. **資訊屏障**：在序列化輸出全域配置時，建立黑名單正則 (`re.search(r"(TOKEN|KEY|SECRET|PASSWORD)", key)`)，凡命中者一律抹除真實內容，防止攻擊者藉由讀取設定竊取 Discord Bot Token 或 LLM API Key。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Systems/11-database-storage.md](./11-database-storage.md) 研讀系統內 SQLite 多庫儲存架構、WAL 模式落實現狀與連線防護。
