# 19 — 背景任務系統、SPARK 條件推播與週期巡檢 (Background Tasks)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面剖析雪莉系統內建的背景排程與主動推播架構。解構 SPARK 條件觸發引擎（取代傳統 cron 排程）、定時提醒巡檢器 (`schedules.db`)、記憶生命週期自動清理 (`retention_worker.py`) 以及硬體效能取樣守護程序的週期運作機制。

---

## 🧭 Prerequisites (先備知識)
- 非同步定時任務實作模式（`asyncio.sleep` 迴圈 vs. APScheduler vs. Celery）。
- Cron 表達式解析與基於事件條件 (Event-Condition-Action, ECA) 的自主主動推播。
- 記憶體快取清理與資料過期淘汰 (TTL Retention)。

---

## 💡 Concept (從被動響應到自主主動觸發)
傳統聊天機器人屬於「被動式 (Reactive)」系統：只有當使用者發送訊息時才會給予回覆。
真正具備伴侶感的 AI 系統必須具備「主動性 (Proactivity)」：
1. **Time-driven (時間驅動)**：早安問候、睡前提醒、鬧鐘。
2. **Event-driven (事件驅動)**：股市劇烈波動、天氣突變（降雨預警）、RSS 訂閱新聞更新。
3. **State-driven (狀態驅動)**：當群組冷場超過特定時間，或發現某個使用者好感度達成里程碑時自主發話。

---

## 🛠️ Sherry Implementation (雪莉 SPARK 與背景任務架構)

### 一、SPARK 條件觸發推播架構 (`handlers/spark/`)
- `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L93) 明確註記：
  `"handlers.spark", # 核心 SPARK 條件排程與推播系統（全面接管 scheduler）`
- **運作模式**：
  1. 規則儲存於 `data/spark/spark.db` 的 `triggers` 表。
  2. 每條規則定義 `condition_type`（如時段、氣溫、股價閥值）與 `condition_config`。
  3. `SparkCog` 背景協程每分或每秒比對觸發條件，符合條件時發動 `action_type`（調用 LLM 生成專屬問候語並推播至 Discord 指定頻道）。

---

### 二、定時鬧鐘與提醒巡檢 (`data/schedules.db`)
- 專門處理使用者自然語言委託的「10分鐘後叫我」、「明天早上8點提醒我出門開會」。
- 巡檢協程持續比對 `run_at <= datetime.now()`，發送後標記或刪除一次性任務，若為 `is_recurring=1` 則計算下一次 Cron 執行時間。

---

### 三、記憶生命週期與自動清理 (`retention_worker.py`)
- 歷史技術債 `TD-005` 解決方案：
  - `[CONFIRMED]` [`Docs/02_Technical_Debt_Register.md`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/Docs/02_Technical_Debt_Register.md#L11)：
    實作 `handlers/memory/retention_worker.py` 定期清理超過保留期限的暫存日誌與低價值對話快取，避免磁碟與 SQLite 空間無界膨脹。

---

### 四、硬體採樣與自檢循環 (`core/sys_inspector/sampler.py`)
- 每隔 5~10 秒採樣一次 CPU 使用率、RAM 記憶體、GPU 負載與溫度。
- 將時間序列資料寫入內部滑動視窗 (Sliding Window)，供 Web UI 與 `/sys_inspect` 指令呈現即時監控圖表。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **SPARK 取代傳統 Scheduler**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L93)。
2. `[CONFIRMED]` **SPARK 與 Schedules 資料庫定義**：
   - 見 [`tools/db_manager.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/tools/db_manager.py#L280-L319)。
3. `[CONFIRMED]` **動態狀態輪播循環**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L273-L294)，`presence_loop` 每 300 秒在背景隨機切換一次 Discord Game 狀態。

---

## 📊 Flow Diagram (背景任務調度拓撲圖)

```text
+-------------------------------------------------------------------------------+
|                       Sherry Background Workers Registry                      |
+-------------------------------------------------------------------------------+
        │                               │                               │
        ▼ (每 60 秒)                    ▼ (每 10 秒)                    ▼ (每 300 秒)
+-----------------------+       +-----------------------+       +-----------------------+
|     Spark Worker      |       | System Sampler Worker |       |     Presence Loop     |
| (比對 spark.db 與      |       | (採樣 CPU/RAM/GPU     |       | (輪播雪莉 Discord     |
|   schedules.db)       |       |  更新監控時間序列)     |       |  即時動態狀態文字)    |
+-----------+-----------+       +-----------+-----------+       +-----------+-----------+
            │                               │                               │
            ▼ (條件滿足)                     ▼ (寫入記憶體/DB)                ▼ (API 呼叫)
  [產生主動推播訊息]                  [metrics.py 聚合]            bot.change_presence()
            │
            ▼
  [Discord Channel]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **背景異常靜默吞噬 (Silent Exception)**：
   - 若某個背景 while 迴圈內的協程在執行推播時拋出例外且未被 `try...except` 包裹，該 Task 會直接終止！若無外部監控機制，使用者會發現「排程突然再也不發送了」。
   - 雪莉防禦：所有背景迴圈（如 `presence_loop`、`inference_worker`）均在頂層包裹 `try...except Exception as e: logger.error(...)` 並在異常後進行非同步退避睡眠，確保主迴圈永不退出。
2. **夏令時間與時區飄移**：
   - 跨國伺服器或系統時區設定為 UTC 時，排程容易發生 8 小時偏差。雪莉全系統統一採用明確的台灣時間 (`timezone(timedelta(hours=8))`)。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **自製輕量 SPARK 迴圈** | 零外部套件依賴，與 Bot 生命週期緊密結合，記憶體開銷小於 1MB。 | 缺少分散式排程的高階特性（如多實例搶佔鎖、DAG 工作流依賴）。 | 引入 Celery + Redis 或 Temporal 分散式工作流引擎。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在 Asyncio 常駐背景輪詢任務中，如何避免「漂移延遲 (Drift)」？**
  - **Ans**:
    - 若單純寫 `await asyncio.sleep(60)`，每次迴圈中計算與 I/O 所耗費的時間（例如 2 秒）會累加，導致實際每 62 秒才觸發一次，長期運行會產生嚴重時間漂移。
    - 解決方案：記錄基準時間 `next_run = time.monotonic() + interval`，在每次迴圈結尾計算剩餘時間 `await asyncio.sleep(max(0, next_run - time.monotonic()))`，動態補償執行時間，消除漂移。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Systems/23-shutdown-and-recovery.md](./23-shutdown-and-recovery.md) 研讀關機信號捕捉、WAL 日誌落盤與崩潰復原機制。
