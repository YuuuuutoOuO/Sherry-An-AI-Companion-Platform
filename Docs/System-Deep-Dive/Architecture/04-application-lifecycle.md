# 04 — 應用程式全生命週期狀態機 (Application Lifecycle)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉系統從冷啟動 (Cold Boot)、擴充套件掛載 (Extension Loading)、網路網關就緒 (Gateway Readiness)、穩態運行 (Steady State) 到終止關機 (Shutdown) 的全生命週期狀態機。評估狀態轉移條件、非同步勾點 (Lifecycle Hooks) 與各階段資源依賴關係。

---

## 🧭 Prerequisites (先備知識)
- 有限狀態機 (Finite State Machine, FSM) 觀念。
- Asyncio 事件循環生命週期 (`run_until_complete`, `create_task`, `gather`)。
- Discord.py 的連線狀態遷移 (`on_connect`, `on_ready`, `on_resumed`, `on_disconnect`)。

---

## 💡 Concept (分散式代理人生命週期理論)
一個高可用度的常駐代理人應用程式，其生命週期包含五個關鍵相態：
1. **Bootstrap Phase (引導期)**：初始化環境變數、配置日誌、準備目錄結構。
2. **Mount Phase (掛載期)**：載入核心組件、建立微核心指揮官、建立連線池與背景 Worker。
3. **Synchronization Phase (同步期)**：連線至遠端網關、註冊斜線指令樹、校驗資料庫 Schema。
4. **Active Runtime Phase (穩態運行期)**：處理事件流、定時排程巡檢、心跳健康維護。
5. **Teardown Phase (拆卸期)**：取消背景任務、落盤 WAL 日誌、關閉網路 Session 與連線池。

---

## 🛠️ Sherry Implementation (雪莉生命週期狀態機)

### 狀態轉移詳細規格
```text
  [BOOTSTRAP] ──(資源目錄與日誌就緒)──> [INITIALIZING]
                                            │
                                    (建構 Bot & 載入 Cogs)
                                            │
                                            v
                                       [CONNECTING] ──(Gateway 鑑權成功)──> [CONNECTED]
                                                                                │
                                                                           (on_ready 觸發)
                                                                                │
                                                                                v
  [TERMINATED] <──(進程中斷/例外)── [SHUTDOWN] <──(關機信號)── [STEADY_STATE (RUNNING)]
```

### 生命週期階段詳述

1. **BOOTSTRAP (同步前置)**：
   - 原始碼：[`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L27-L78)。
   - 行為：建立實體目錄、綁定 PID、設置日誌 Stream 與 FileHandler、掛載 TraceIdFilter。
2. **INITIALIZING (組件裝配)**：
   - 原始碼：[`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L205-L232)。
   - 行為：
     - `bot = build_bot()`
     - `bot.core = SherryCore(bot)`
     - 依序 `await bot.load_extension(ext)`：每個 Cog 執行其 `cog_load()`（例如 `BotBrain` 啟動 `_inference_worker` 協程）。
     - 自動掃描 `./mod/`，動態掛載已啟用之外掛。
3. **CONNECTING / CONNECTED (網關握手)**：
   - 原始碼：[`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L509)。
   - 行為：`await bot.start(settings.DISCORD_BOT_TOKEN)` 發起 WebSocket TLS 握手，提交 IDENTIFY 封包與 Intents。
4. **STEADY_STATE (就緒與穩態運作)**：
   - 原始碼：[`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L234-L304)。
   - 觸發：Discord 觸發 `on_ready` 事件。
   - 行為：
     - 記錄啟動時間 `setattr(bot, "_start_time", time.time())`。
     - 同步 Slash Commands：`synced = await bot.tree.sync()`。
     - 印出啟動系統摘要橫幅 (Startup Summary Banner)。
     - 啟動常駐動態狀態迴圈：`bot.loop.create_task(presence_loop())`。
     - 啟動 Quart Web 控制台：`bot.loop.create_task(start_server_async(bot))`。
5. **SHUTDOWN / TEARDOWN (終止與資源釋放)**：
   - 原始碼：[`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L101-L114) 與各 Cog `cog_unload()`。
   - 現狀問題：`[CONFIRMED]` 當前 `main.py` 在 `asyncio.run(main())` 頂層缺少 `SIGINT` / `SIGTERM` 信號處理器（見下方異常分析）。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **Worker 啟動於 cog_load**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L114-L119)：
     ```python
     async def cog_load(self) -> None:
         self._worker_task = asyncio.create_task(
             self._inference_worker(), name="inference-worker"
         )
     ```
2. `[CONFIRMED]` **Web UI 啟動於 on_ready**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L298-L301)：
     ```python
     from web_ui.server import start_server_async
     bot.loop.create_task(start_server_async(bot))
     ```
3. `[CONFIRMED]` **缺少 SIGINT/SIGTERM 優雅關機捕捉**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L509-L513)：`main()` 僅直接 `await bot.start(...)`，沒有 `signal.signal(signal.SIGINT, ...)` 或 `try...finally: checkpoint_all_dbs()`。

---

## 📊 Flow Diagram (生命週期時序圖)

```text
Time  Phase          Event / Action                                   Status Flag
 |
 |   BOOTSTRAP      main.py 入口執行                                   _bootstrapped = False
 |                  建立目錄、設置 UTF-8、寫入 PID
 |
 v   INITIALIZE     SherryCore 初始化                                  state: INIT
 |                  載入 15 個核心 Cog (各 Cog 啟動內部背景 Task)
 |                  動態探索並載入 mod 外掛
 |
 v   CONNECT        bot.start() 建立 WebSocket TLS 連線               is_ready() = False
 |                  Discord Gateway IDENTIFY / RESUME
 |
 v   READY          on_ready 觸發                                     is_ready() = True
 |                  - bot.tree.sync()
 |                  - presence_loop() 啟動 (每 300 秒換狀態)
 |                  - start_server_async() 啟動 Web UI
 |
 v   STEADY STATE   全系統穩態並行運作 (Gateway / Quart / SPARK)        is_closed() = False
 |
 v   TERMINATION    使用者 Ctrl+C 或 taskkill                         is_closed() = True
                    [現狀：直接被 OS 殺死，依賴 DB busy_timeout]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **斷線重連狀態漂移 (Gateway Reconnect / Invalidation)**：
   - 若 Discord 伺服器發送 `Opcode 9 (Invalid Session)`，Discord.py 會重新 IDENTIFY。此時 `on_ready` 可能再次觸發！
   - `[CONFIRMED]` 若未做好冪等防護，重連再次觸發 `on_ready` 時會重複呼叫 `start_server_async(bot)`，嘗試再次綁定 8000 埠號導致端口碰撞。
2. **缺少 SIGINT 導致 WAL 未 TRUNCATE**：
   - 在直接按 `Ctrl+C` 終止時，SQLite WAL 日誌可能尚未執行 `PRAGMA wal_checkpoint(TRUNCATE);`。雖然 SQLite 具備崩潰恢復能力（重啟時自動讀取 WAL），但若使用者立即拷貝 `.db` 檔而遺漏 `-wal` 檔，將造成資料損壞。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **在 `on_ready` 啟動 Web UI 而非主程序開頭** | 保證 Web UI 啟動時 `bot.user` 與 Guilds 資料已由 Discord 填妥，API 不會回傳空物件。 | 若 Discord 網路連線延遲或 Token 鑑權卡住，本地 Web 管理後台也無法及時上線。 | 在 `main()` 開頭先啟動 Web UI，以非同步狀態旗標動態標註 Bot 連線狀態。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在 Asyncio 系統中，如何實作健全的優雅關機 (Graceful Shutdown)？雪莉目前缺少了什麼？**
  - **Ans**:
    - 標準做法：監聽 `SIGINT`/`SIGTERM`，收到信號時：（1）停止接受新請求（關閉 Gateway 與 HTTP 監聽）；（2）遍歷未完成的 `asyncio.all_tasks(loop)` 並發送 `task.cancel()`；（3）使用 `asyncio.gather(*tasks, return_exceptions=True)` 等待其取消或清空佇列；（4）執行資源落盤勾點（如 `checkpoint_all_dbs()`）與關閉連線池。
    - 雪莉現狀：直接依賴 Python 行程終止時的 OS 資源清理，缺少中央信號捕捉與優雅落盤排空。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Architecture/05-request-lifecycle.md](./05-request-lifecycle.md) 研讀單一互動訊息進入系統後的完整請求處理生命週期。
