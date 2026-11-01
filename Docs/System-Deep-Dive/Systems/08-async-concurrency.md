# 08 — 非同步並發模型與共用 Event Loop (Async Concurrency)

> **難度標記**：`Difficulty: Expert`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉系統中最關鍵的並發核心架構：Discord.py 網關客戶端與 Quart Web UI 伺服器如何共用「同一個 Python Asyncio Event Loop」。評估協程調度模型、協程飢餓 (Starvation) 風險、同步阻塞 I/O 毒藥效應以及並發任務強引用保護 (Strong Reference Pattern) 機制。

---

## 🧭 Prerequisites (先備知識)
- Python `asyncio` 事件循環運作機制（Selector vs. Proactor、Ready Queue、Timer Wheel）。
- Python 協程協同式多工 (Cooperative Multitasking) 原理。
- ASGI 規範與 Uvicorn 伺服器驅動原理。

---

## 💡 Concept (協同式多工與單一事件循環核心)
在 Python 的 `asyncio` 模型中：
- 只有**單一作業系統執行緒**在運行事件循環。
- 所有的 `async def` 協程都是藉由 `await` 主動讓出 CPU 控制權（Yield），將執行機會交回給循環中的其他任務。
- **關鍵危險點**：一旦有任何協程中執行了**同步阻塞操作**（如 `time.sleep()`, 原生 `requests.get()`, 密集 CPU 迴圈, 或無超時的同步檔案/DB 讀取），整個事件循環會被完全凍結！
  在此凍結期間，**所有的網路心跳、WebSocket 封包處理、Web 請求與其他協程全數停止**。

---

## 🛠️ Sherry Implementation (雪莉共用 Event Loop 實作)

### 一、單一事件循環綁定架構
雪莉系統並未採用「主執行緒跑 Bot、子執行緒跑 Web」的多執行緒架構，而是採用了極致輕量但也極具挑戰性的**共用 Event Loop 架構**：

1. `main.py` 透過 `asyncio.run(main())` 啟動主事件循環。
2. `commands.Bot` 在此循環中運作。
3. `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L298-L301)：
   ```python
   from web_ui.server import start_server_async
   bot.loop.create_task(start_server_async(bot))
   ```
4. `[CONFIRMED]` [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2786-L2788)：
   ```python
   config = uvicorn.Config(app=app, host=..., port=..., loop="asyncio", ...)
   server = uvicorn.Server(config)
   await server.serve()
   ```

**架構真相確認**：
`[CONFIRMED]` Quart (Uvicorn ASGI Server) 直接以 Coroutine 形式透過 `await server.serve()` 嵌入在 Discord Bot 的同一個事件循環中！

---

### 二、背景任務強引用模式 (Strong Reference Pattern)
Python 3.10+ 中，若使用 `asyncio.create_task()` 啟動任務但未保留其參照，垃圾回收器 (Garbage Collector) 可能在任務尚未執行完畢前將其非預期回收，造成任務中斷且不報錯。

- `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L94,L201-L203)：
  ```python
  self._background_tasks: set[asyncio.Task[Any]] = set()
  ...
  # 雲端模型背景並行放行（保持強引用防 GC）
  t = asyncio.create_task(self._process_task(task, MAX_RETRIES, RETRY_DELAY))
  self._background_tasks.add(t)
  t.add_done_callback(self._background_tasks.discard)
  ```
  雪莉嚴格實作了標準的「集合強引用 + done_callback 自我剔除」設計模式。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **共用 Loop 啟動代碼**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L300)。
2. `[CONFIRMED]` **Uvicorn 協程化啟動**：
   - 見 [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2788)。
3. `[CONFIRMED]` **背景任務 GC 防禦**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L201-L203)。

---

## 📊 Flow Diagram (共用 Event Loop 調度流)

```text
+-----------------------------------------------------------------------------------+
|                        Single Threaded Asyncio Event Loop                         |
|                                                                                   |
|   +--------------------+     +--------------------+     +--------------------+    |
|   |   Task: Discord    |     |    Task: Quart     |     |   Task: BotBrain   |    |
|   | Gateway Connection |     |   Uvicorn Server   |     |  Inference Worker  |    |
|   +---------+----------+     +---------+----------+     +---------+----------+    |
|             |                          |                          |               |
|             | (await WS read)          | (await HTTP read)        | (await queue) |
|             v                          v                          v               |
|   +---------------------------------------------------------------------------+   |
|   |                             Event Loop Scheduler                          |   |
|   +---------------------------------------------------------------------------+   |
|                                                                                   |
+-----------------------------------------------------------------------------------+
                                         │
                   ⚠️ 任何一個協程呼叫同步 blocking I/O
                                         ▼
            💥 整個 Event Loop 凍結！Gateway 心跳中斷！Web UI 無響應！
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **心跳逾時掉線 (Heartbeat Missed -> Gateway Disconnect)**：
   - Discord 要求客戶端必須準時回覆心跳。若某個 Web UI 路由或工具中執行了耗時 5 秒的同步計算或同步磁碟遍歷，Event Loop 被阻滯 5 秒。Discord Gateway 判定機器人已無響應 (Heartbeat Timeout)，伺服器端強制切斷 WebSocket 連線，引發重新連線與短暫失聯。
2. **Web API 慢查詢牽連對話**：
   - 當管理員在 Web UI 查詢幾十萬筆的歷史日誌檔案時，若未放入 ThreadPoolExecutor (`run_in_executor`)，檔案解壓縮與正則表達式搜尋將凍結 Event Loop，導致正在 Discord 語音通話或即時文字對話的群友體驗到嚴重卡頓。
3. **SSRF 安全防禦之同步 DNS 解析阻塞 (`core/guard.py:socket.getaddrinfo`)**：
   - `core/guard.py:is_safe_public_url()` 為了防範 DNS 重新綁定攻擊，直接呼叫了同步標準函式 `socket.getaddrinfo()`。若被檢驗的網域名稱所屬 DNS 伺服器延遲高或丟包，該同步呼叫將在協程主執行緒中直接阻塞數秒，形成短暫凍結事件循環的隱性風險；在高並發場景下應透過 `loop.run_in_executor()` 移至執行緒池非同步執行。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **共用單一 Event Loop** | 極致簡潔，Web API 路由處理常式可直接透過 `_bot_instance` 存取記憶體中所有 Bot 狀態與物件，無需跨進程通訊。 | 脆弱性極高，架構隔離性弱，任一 Web 端代碼缺陷均可直接癱瘓 Discord 機器人。 | 將 Web UI 跑在獨立作業系統進程，兩者透過 IPC (Redis Pub/Sub 或 Unix Socket) 交換狀態。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在一個將 Web 伺服器與 Bot 整合在同一個 Asyncio Event Loop 的系統中，身為架構師你會制定哪些防禦紀律？**
  - **Ans**:
    1. **程式碼層級禁令**：引入 `ruff` 規則集中的 `ASYNC` 規則，靜態掃描禁止在 `async def` 中出現 `requests.*`, `time.sleep`, 原生 `open()`, 原生 `sqlite3.connect` 等阻塞呼叫。
    2. **同步操作下沉**：所有無法避免的阻塞操作（如 CPU 密集型的影像處理、大檔案讀寫、第三方同步 SDK）必須強制封裝進 `loop.run_in_executor(None, fn)` 委託執行緒池處理。
    3. **逾時硬門檻**：所有網路 I/O 一律附加 `asyncio.wait_for(..., timeout=N)`。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Systems/09-thread-process-executor.md](./09-thread-process-executor.md) 研讀非同步系統中如何使用執行緒與進程池安全下沉阻塞操作。
