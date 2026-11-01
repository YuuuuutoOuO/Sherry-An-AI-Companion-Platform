# 07 — 網路協定棧 (Discord Gateway, HTTP, aiohttp)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面解構雪莉平台的網路通訊架構，包括 Discord Gateway WebSocket 長連線維持機制、Heartbeat 心跳協商、Discord REST API 限流桶 (Rate Limit Buckets)、本地 aiohttp ClientSession 連線池重用策略與外部 HTTP 請求超時防禦。

---

## 🧭 Prerequisites (先備知識)
- WebSocket 協定 (RFC 6455) 與 TLS/SSL 加密交握。
- HTTP/1.1 連線 Keep-Alive 與 TCP 連線池原理。
- Discord Gateway 狀態機 (Opcode 1 Heartbeat, Opcode 2 Identify, Opcode 6 Resume)。

---

## 💡 Concept (網路通訊與連線池治理理論)
在長連線即時應用中，網路層有兩大生命線：
1. **網關長連線保活 (Gateway Keep-Alive)**：定時發送 Heartbeat，若在指定時間 (Heartbeat ACK) 內未收到回應，必須及時觸發重連並透過 Session ID 執行 Resume，防止事件遺失。
2. **HTTP 客戶端連線池 (Connection Pool Pooling)**：高頻呼叫外部 API 時，為每個請求建立新的 TCP+TLS 連線（Handshake 需 3 次 RTT）會帶來巨大延遲與端口耗盡 (Socket Exhaustion) 風險。應使用長效的 `aiohttp.ClientSession` 重用連線。

---

## 🛠️ Sherry Implementation (雪莉網路架構實作)

### 一、Discord Gateway 連線機制
- **協定版本**：Discord Gateway v10 (透過 discord.py 封裝)。
- **心跳間隔**：由 Gateway 在 HELLO 封包中下發 `heartbeat_interval`（通常約 41.25 秒），由底層背景任務以高精準度定時發送 Opcode 1。
- **Intents 最小化權限**：
  `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L171-L173)：
  ```python
  intents = discord.Intents.default()
  intents.message_content = True
  intents.members = True
  ```
  僅開啟接收訊息內容與群組成員變更所需之必要權限，落實資安最小權限原則。

---

### 二、aiohttp ClientSession 連線池與單一例項管理
在各工具模組中（如 `DrawTool`, `SearchTool` / `URLSummaryTool`, `OllamaClient`）：
- `[CONFIRMED]` [`handlers/social/tools/draw_tool.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/tools/draw_tool.py#L80-L84)：
  ```python
  async def _get_session(self) -> aiohttp.ClientSession:
      if self._session is None or self._session.closed:
          self._session = aiohttp.ClientSession(trust_env=True)
      return self._session
  ```
  採用惰性單例模式管理 session，避免重複建立 TCP 連線。

---

### 三、全域逾時常數與防禦
依規範第 5 節規定，所有外部呼叫必須具備 Timeout：
- `[CONFIRMED]` 全域設定中定義 `DEFAULT_TIMEOUT_SEC = 8`（或特定工具專屬超時）。
- `OllamaClient` 與 `CloudInferenceClient` 均設定了 `ClientTimeout(total=...)`，避免遠端模型或本地端卡死時造成非同步協程永久掛起。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **Gateway 心跳自檢 Cog**：
   - 見 [`handlers/heartbeat/`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/heartbeat/)，透過發送模擬封包或檢測 `bot.latency` 判斷網關連線品質。
2. `[CONFIRMED]` **Ollama HTTP Client 連線配置**：
   - 見 [`handlers/bot_brain/client.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/client.py)，透過 `aiohttp.ClientTimeout(total=settings.OLLAMA_TIMEOUT)` 封裝所有 REST 推論呼叫。

---

## 📊 Flow Diagram (網路連線與 Session 拓撲圖)

```text
[Discord Gateway WebSocket] ◄======== Heartbeat (Opcode 1) ========► [Discord.py Client]
                                  ACK (Opcode 11)                           │
                                                                            │
      +---------------------------------------------------------------------+
      │
      v (長效 ClientSession 連線池)
+-----------------------------------------------------------------------------------+
|                        Sherry Shared Network Session Pool                         |
+-------------------+---------------------------+-----------------------------------+
                    |                           |
                    v (HTTP REST Keep-Alive)     v (HTTP REST Keep-Alive)
         +-----------------------+   +-----------------------+
         |     Local Ollama      |   |  External Cloud APIs  |
         |  http://127.0.0.1:    |   | (Gemini / OpenAI /    |
         |         11434         |   |      OpenRouter)      |
         +-----------------------+   +-----------------------+
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **Windows TCP 10054 連線重置異常**：
   - 在 Windows 上，當遠端伺服器非預期中斷 TCP 連線時，底層 Proactor 會向 Event Loop 拋出 `OSError [WinError 10054]`。
   - `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L190-L198) 實作 `_asyncio_loop_exception_handler` 專門靜默該無害連線重置，防止控制台日誌被非關鍵中斷洗版。
2. **Discord 429 速率限制與 X-RateLimit-Reset**：
   - 當機器人短時間內在同一頻道密集發送訊息時，Discord 會回傳 HTTP 429。Discord.py 內部會自動依照 `retry_after` 標頭非同步睡眠等待，但在併發大量訊息派發時，可能造成該協程等待延遲堆疊。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **每個 Cog / Tool 各自惰性維護 ClientSession** | 模組獨立解耦，不需在微核心中強制傳遞集中式 session。 | 程式碼內存在多個 ClientSession 實例，未達到全應用級別極限 TCP 連線複用。 | 在微核心頂層建立全域唯一的 `SharedSessionManager` 並統一生命週期注入。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在高併發非同步系統中，未妥善關閉 `aiohttp.ClientSession` 會引發什麼嚴重的系統問題？**
  - **Ans**:
    1. **Unclosed client session 警告**：Python GC 回收未關閉的 session 時會觸發 ResourceWarning。
    2. **底層 SSL Transport 洩漏**：TCP 連線未發送 FIN/RST 封包即被銷毀，可能導致作業系統核心中維持大量 `TIME_WAIT` 或 `CLOSE_WAIT` 狀態的通訊埠，最終耗盡系統 Socket 檔案描述符 (File Descriptors)，導致 `Too many open files` 致命崩潰。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Systems/08-async-concurrency.md](./08-async-concurrency.md) 研讀非同步事件循環核心與協程調度真相。
