# 06 — 系統全域資料流與狀態轉移拓撲 (Data Flow)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面繪製雪莉平台中跨系統元件的五大主要資料管線：對話資料流、記憶提煉流、推播排程流、Web 管理資料流與觀測審計流。詳細定義各管線中的資料傳輸結構、序列化格式、持久化節點與資料生命週期。

---

## 🧭 Prerequisites (先備知識)
- 資料管線 (Data Pipeline) 與事件驅動架構 (EDA)。
- 記憶體狀態快照 vs. 磁碟持久化儲存。
- SQLite JSON1 擴展與 JSONL 日誌儲存。

---

## 💡 Concept (AI 伴侶系統資料流模型)
在自主陪伴型 AI 系統中，資料不僅是單向的「輸入-輸出」，更形成一個閉環回饋迴圈 (Closed-Loop Feedback)：
1. **輸入感知**：文本、圖片與情境中繼資料流入。
2. **上下文富化**：靜態人格 + 動態短期記憶 + 長期事實檢索。
3. **推論決策**：產生回應文本與工具呼叫指令。
4. **回饋提煉**：對話結束後，後台 Worker 異步分析對話，提煉出使用者的新喜好、新事實，寫回長期記憶資料庫。
5. **記憶重聚**：下一次對話時，提煉的事實再次被檢索注入，形成記憶演進。

---

## 🛠️ Sherry Implementation (五大核心資料流拓撲)

### 資料流一：互動對話與推論流 (Conversation & Inference Pipeline)
- **來源**：Discord Gateway WebSocket (`discord.Message`)。
- **處理流程**：
  1. `message.content` 被提取並清洗。
  2. `memory_builder` 從 `chat_history.db` 撈取近 10 則歷史對話，格式化為對話上下文。
  3. `persona.py` 載入靈魂檔 (`file/soul/*.md`) 組裝 `head_persona` 與 `tail_lore`。
  4. 封裝為 `InferenceTask` 物件投入 `BotBrain._queue`。
  5. 推論回傳後，原始對話與 AI 回覆由 `memory/storage.py` 同步寫入 `chat_history.db` 的 `chat_messages` 資料表。

### 資料流二：認知記憶提煉流 (Memory Extraction Pipeline)
- **觸發**：對話累積一定輪數或每日定時觸發。
- **處理流程**：
  1. 後台 Worker 讀取未標記處理之對話記錄 (`processed = 0`)。
  2. 呼叫輕量 LLM (`LIGHT_MODEL`) 進行事實抽取（例如：「使用者提到他養了一隻叫布丁的貓」）。
  3. 產生的事實結構寫入 `sherry_memory.db` 的 `user_facts` 表。
  4. 使用者好感度計算器根據互動深度更新 `user_profiles` 與 `user_relations` 表中的 `affinity` 數值。

### 資料流三：SPARK 條件推播流 (Event & Spark Flow)
- **來源**：時間 Tick、氣象變化、股票預警或自訂觸發條件。
- **儲存節點**：`spark/spark.db` 的 `triggers` 表。
- **處理流程**：
  1. `SparkCog` 輪詢條件評估器。
  2. 條件滿足時，生成自主推播任務 (Spontaneous Proactive Message)。
  3. 經由 `bot.get_channel(channel_id).send()` 發送至指定頻道。

### 資料流四：Web 控制台雙向管理流 (Web Management Flow)
- **來源**：管理員瀏覽器 HTTP REST / API 請求。
- **處理流程**：
  1. `web_ui/server.py` 接收 GET/POST 請求。
  2. 讀取或更新 `settings.py` (底層對接 `core/config/` 與 `.env`)。
  3. 查詢 `chat_history.db` 與 `data/*.log` 供管理員線上審閱與即時分析。

### 資料流五：可觀測性與審計日誌流 (Observability Pipeline)
- **來源**：全系統 `log_command()`, `log_error()`, `log_inference()`。
- **處理流程**：
  1. 帶有 `trace_id` 的結構化事件寫入 `data/chat_history.db` 的 `runtime_events` 與 `audit_logs` 表。
  2. 同步寫入 `data/bot.log` 與 `data/web_ui.log` (Rolling File)。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **對話訊息入庫**：
   - 見 [`tools/db_manager.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/tools/db_manager.py#L88-L100)，`chat_messages` 包含 `user_id`, `display_name`, `content`, `server_id`, `processed`。
2. `[CONFIRMED]` **事實庫綱要**：
   - 見 [`tools/db_manager.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/tools/db_manager.py#L186-L200)，`user_facts` 包含 `user_id`, `fact`, `category`, `importance`。
3. `[CONFIRMED]` **全表格軟刪除審計四本柱**：
   - 見 [`tools/db_manager.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/tools/db_manager.py#L74-L80)：全系統表格均包含 `is_deleted`, `deleted_at`, `created_at`, `updated_at`。

---

## 📊 Flow Diagram (全系統資料拓撲圖)

```text
[Discord User]
     │
     │ 1. 訊息發送
     ▼
[handlers.social] ─────────── 2. 檢索短期與長期記憶 ───────────► [sherry_memory.db]
     │                                                                 ▲
     │ 3. 封裝 Prompt                                                  │ 7. 事實提煉回填
     ▼                                                                 │
[handlers.bot_brain] ──► [Ollama / Gemini]                             │
     │                        │                                        │
     │ 4. 推論回傳            │                                        │
     ▼                        ▼                                        │
[handlers.social] ──► [Discord 回覆]                                   │
     │                                                                 │
     │ 5. 對話紀錄入庫                                                 │
     ▼                                                                 │
[chat_history.db] ◄── 6. 讀取未處理對話 ── [Memory Extractor Worker] ──┘
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **記憶提煉雪崩 (Extraction Backlog)**：
   - 若系統長時間高頻對話，而事實提煉 Worker 處理過慢或 LLM 限流，`processed = 0` 的未提煉對話會急劇累積，導致新記憶延遲數小時甚至數天才能生效。
2. **多頻道併發寫入鎖定 (SQLite Database is Locked)**：
   - 若多個 Discord 伺服器同時有大量對話湧入，同時嘗試向 `chat_history.db` 寫入，若未啟用 WAL 模式，單一交易卡住會拋出 `sqlite3.OperationalError: database is locked`。雪莉藉由 `core/db.py` 強制啟用 WAL 與 `busy_timeout = 5000` 來緩解此競爭。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **SQLite 集中儲存而非向量資料庫 (Vector DB)** | 單機零運維成本、無外部依賴、純 Python 標準函式庫相容、備份極為簡易。 | 缺乏百萬維度的語義稠密檢索能力 (Dense Vector Search)，目前依賴結構化分類與關鍵字檢索。 | 引入 ChromaDB / Qdrant / sqlite-vec 擴展。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在對話伴侶系統中，為什麼記憶存儲不宜在對話的主路徑 (Critical Path) 上進行同步提煉？**
  - **Ans**: LLM 提煉事實（實體識別、情感評估、重要性打分）是一次完整的推論計算，通常需要耗費 1~3 秒。若放在主路徑上，使用者收到回覆的等待時間 (TTFT) 會直接翻倍。標準架構必定是主路徑「先將原始訊息落盤」，由背景 Worker 進行非同步批次提煉。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Architecture/25-dependency-graph.md](./25-dependency-graph.md) 研讀全系統模組依賴有向圖與分層約束。
