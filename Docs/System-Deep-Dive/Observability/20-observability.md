# 20 — 結構化日誌、分散式 Trace ID 與可觀測性體系 (Observability)

> **難度標記**：`Difficulty: Intermediate`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉系統的可觀測性 (Observability) 體系。解構基於 Python `contextvars.ContextVar` 實作的協程安全 Trace ID 追蹤鏈路、標準日誌過濾器 `TraceIdFilter`、三大結構化指標記錄函式 (`log_command`, `log_error`, `log_inference`)、多通道日誌分流與 SQLite 即時審計持久化機制。

---

## 🧭 Prerequisites (先備知識)
- Python `logging` 模組底層機制 (LogRecord, Filter, Handler, Formatter)。
- 非同步協程上下文變數 (`contextvars.ContextVar`) 與執行緒本地儲存 (Thread-Local Storage) 的區別。
- 分散式追蹤 (Distributed Tracing, OpenTelemetry) 核心觀念 (Trace ID, Span ID)。

---

## 💡 Concept (非同步系統的鏈路追蹤挑戰)
在傳統同步多執行緒程式中，使用 `threading.local()` 即可將請求 ID 綁定到當前執行緒。
但在 `asyncio` 事件循環中：
- 數百個並發請求交替在「同一個作業系統執行緒」中執行。
- 若使用 Thread-Local，所有請求的 Trace ID 會互相覆蓋，產生嚴重的日誌串線 (Log Contamination)。
- **解決方案**：必須使用 Python 3.7+ 原生的 `contextvars.ContextVar`，其隨協程切換自動維護獨立的上下文副本，保證鏈路追蹤絕對隔離。

---

## 🛠️ Sherry Implementation (雪莉可觀測性實作)

### 一、ContextVar 與 Trace ID 生成 (`handlers/social/observability.py`)
- `[CONFIRMED]` [`handlers/social/observability.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/observability.py)：
  ```python
  _trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")

  def new_trace_id(prefix: str = "req") -> str:
      """產生以時間戳記與隨機字串組合的唯一 Trace ID。"""
      return f"{prefix}-{uuid.uuid4().hex[:8]}"

  def set_trace_id(tid: str) -> None:
      _trace_id_ctx.set(tid)

  def get_trace_id() -> str:
      return _trace_id_ctx.get()
  ```

---

### 二、TraceIdFilter 與日誌格式整合
- `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L49-L78)：
  ```python
  class TraceIdFilter(logging.Filter):
      def filter(self, record: logging.LogRecord) -> bool:
          record.trace_id = get_trace_id()
          return True

  logging.basicConfig(
      level=logging.INFO,
      format="%(asctime)s [%(levelname)s] [%(trace_id)s] %(name)s: %(message)s",
      handlers=[_stream_handler, _file_handler],
  )
  ```
  所有進入系統的日誌，無論來自哪個模組，均自動帶有當前請求的 `[cmd-xxxx]` 或 `[chat-xxxx]` 標籤。

---

### 三、關鍵事件結構化持久化
除文本日誌外，雪莉在關鍵節點主動寫入 `chat_history.db`：
1. **`log_command`**：記錄指令名稱、執行者、耗時 (`latency_ms`)、執行狀態。
2. **`log_inference`**：記錄任務 ID、使用模型、佇列等待時間 (`queue_latency_ms`)、推論耗時 (`gen_latency_ms`)、Token 統計。
3. **`log_error`**：記錄異常類型、呼叫堆疊快照、上下文中繼資料。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **TraceIdFilter 裝配代碼**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L69-L72)。
2. `[CONFIRMED]` **互動指令進入點 Trace ID 綁定**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L318-L319)：
     ```python
     tid = new_trace_id("cmd")
     set_trace_id(tid)
     ```
3. `[CONFIRMED]` **runtime_events 資料庫記錄結構**：
   - 見 [`tools/db_manager.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/tools/db_manager.py#L110-L126)。

---

## 📊 Flow Diagram (可觀測性鏈路追蹤圖)

```text
[Discord Interaction Event]
           │
           │ 1. new_trace_id("cmd-abc12345")
           ▼
     [set_trace_id] ──► (ContextVar 存入當前協程空間)
           │
           +---> logger.info("開始處理指令")
           |        │
           |        ▼
           |    [TraceIdFilter] ──► 注入 record.trace_id
           |        │
           |        ▼
           |    2026-10-07 10:45:00 [INFO] [cmd-abc12345] handlers.social: 開始處理指令
           |
           +---> 呼叫 LLM 推論
           |        │
           |        ▼
           |    [log_inference(tid, latency=1250ms, model="ollama")]
           |        │
           |        ▼
           |    寫入 chat_history.db -> runtime_events
           |
           ▼
[Discord 派發完成]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **跨執行緒傳遞 ContextVar 遺失 (Context Loss across Thread Boundary)**：
   - 當使用 `loop.run_in_executor(None, sync_fn)` 委派任務給執行緒池時，預設情況下執行緒池不會繼承當前協程的 `contextvars`。若在該同步函式內呼叫日誌，`trace_id` 會回退至預設值 `"-"`。
   - 解決方案：在送入 executor 前使用 `contextvars.copy_context().run(sync_fn)`。
2. **背景定時任務 Trace ID 污染**：
   - 在 SPARK 週期迴圈或 `presence_loop` 中，若未在每次迴圈開頭主動 `set_trace_id(new_trace_id("spark"))`，可能會沿用前一次殘留的 ID。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **自製輕量 ContextVar + SQLite 儲存** | 零外部基礎設施依賴，不需架設 Jaeger / Prometheus / ElasticSearch，本地開箱即用。 | 缺少分散式視覺化 Flamegraph（火焰圖）與跨服務 HTTP 傳播 (W3C TraceContext) 支援。 | 完整導入 OpenTelemetry Python SDK 與 OTLP Exporter。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 為什麼在 Python 非同步微服務中，不能使用 `threading.local` 實作分散式請求追蹤？**
  - **Ans**: 因為在 `asyncio` 模型中，單一執行緒負責處理成千上萬個並行協程。當協程 A 在 `await` 讓出控制權時，執行緒立即切換至協程 B。如果 Trace ID 存放在 `threading.local`，協程 B 會直接覆寫協程 A 的 Trace ID，導致日誌中的請求鏈路完全錯亂。只有 `contextvars.ContextVar` 具備協程感知 (Coroutine-aware) 的複製與回溯能力。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Security/21-security-architecture.md](../Security/21-security-architecture.md) 研讀 SSRF 防禦、Prompt 注入阻隔與憑證安全體系。
