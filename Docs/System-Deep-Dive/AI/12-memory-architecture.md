# 12 — 三層式認知記憶系統 (Memory Architecture)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉伴侶系統的認知記憶核心架構。解構三層式記憶層級：短期對話視窗 (Short-term Working Memory)、使用者長期事實與好感度記憶 (Long-term Fact Memory) 以及世界跨群共享記憶 (World Memory)。評估記憶的檢索機制、非同步萃取管線、軟刪除審計與好感度等級演進。

---

## 🧭 Prerequisites (先備知識)
- 認知心理學中的工作記憶 (Working Memory) 與長程語義記憶 (Episodic/Semantic Memory)。
- 對話系統的滑動視窗 (Sliding Window Context) 截斷策略。
- 關係好感度模型 (Affinity & Relationship Tiers)。

---

## 💡 Concept (AI 伴侶的三層記憶模型理論)
沒有記憶的 AI 只是單純的無狀態查詢引擎。一個擬真的 AI 伴侶必須具備多層級記憶：
1. **第一層：短期工作記憶 (Working Memory)**：當前頻道最近 N 則對話，維持語義連貫性與即時話題上下文。
2. **第二層：個人長程事實庫 (Personal Semantic Memory)**：記錄每個使用者的個別事實（生日、愛好、寵物、職業、專屬稱呼）與好感度數值。
3. **第三層：世界/社群宏觀記憶 (Collective World Memory)**：跨頻道/跨伺服器發生的重大事件（如某群打贏了重要活動、伺服器重大公告、全域重要回憶）。

---

## 🛠️ Sherry Implementation (雪莉記憶架構實作)

### 一、資料模型與分層儲存
`[CONFIRMED]` 雪莉將三層記憶嚴格劃分於專用資料庫與資料表：

```text
[三層記憶架構拓撲]

├── 1. 短期工作記憶 ──► chat_history.db
│                        └── chat_messages (近期 10~20 條訊息)
│
├── 2. 個人長程記憶 ──► sherry_memory.db
│                        ├── user_profiles (好感度 affinity, 互動次數)
│                        ├── user_facts    (萃取的事實 fact, category, importance)
│                        ├── user_aliases  (稱呼別名 alias)
│                        └── user_relations (好感等級 tier: stranger/friend/beloved)
│
└── 3. 世界集體記憶 ──► world_memory.db
                         ├── world_events  (伺服器重大事件紀錄)
                         └── world_summary (每日跨群摘要)
```

---

### 二、記憶組裝管線 (`memory_builder.py`)
當使用者發起對話時，`handlers/social/memory_builder.py` 負責將記憶富化為 Prompt 片段：
- `[CONFIRMED]` [`handlers/social/memory_builder.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/memory_builder.py)：
  1. 依據 `user_id` 查詢 `user_profiles` 取得好感度（如 `Affinity: 65 (熟絡的朋友)`）。
  2. 依據 `importance >= 3` 查詢此使用者的前 5~10 條重要事實，組合成 `<user_memory>` 區塊。
  3. 依據 `server_id` 查詢世界記憶中相關的重要事件摘要。
  4. 整合並過濾已軟刪除 (`is_deleted = 0`) 的資料。

---

### 三、好感度等級遷移 (Affinity State Machine)
- 好感度以數值 (0.0 ~ 100.0) 衡量，動態映射到不同人設親密度等級 (`tier`)：
  - `stranger` (陌生人, 0~19)：客氣、保有距離。
  - `acquaintance` (點頭之交, 20~39)：友善、日常互動。
  - `friend` (朋友, 40~69)：親暱、主動吐露想法。
  - `close_friend` (摯友, 70~89)：極高信任感、專屬玩笑。
  - `beloved` (深厚羈絆, 90~100)：極致溫柔、無保留陪伴。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **記憶資料庫 Schema 定義**：
   - 見 [`tools/db_manager.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/tools/db_manager.py#L168-L275)。
2. `[CONFIRMED]` **記憶建構函式**：
   - 見 [`handlers/social/dispatch/integrated.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/integrated.py#L28)：
     `from handlers.social.memory_builder import build_user_memory_block`
3. `[CONFIRMED]` **記憶過期保留 Worker 登錄**：
   - 見 [`Docs/02_Technical_Debt_Register.md`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/Docs/02_Technical_Debt_Register.md#L11)，`TD-005` 標註已實作 `retention_worker.py`。

---

## 📊 Flow Diagram (記憶讀寫閉環流程圖)

```text
[User Input] ──► [handlers.social]
                       │
                       │ 1. 查詢讀取 (SELECT where user_id=?)
                       ▼
          +-------------------------+
          |    sherry_memory.db     |
          | (user_profiles, facts)  |
          +------------+------------+
                       │
                       │ 2. 注入 User Turn Prompt
                       ▼
          +-------------------------+
          |      InferenceTask      | ──► [LLM 推論生成回覆]
          +-------------------------+
                       │
                       │ 3. 原始對話入庫 (processed = 0)
                       ▼
          +-------------------------+
          |    chat_history.db      |
          |    (chat_messages)      |
          +------------+------------+
                       ▲
                       │ 4. 背景 Worker 批次抽取事實
                       ▼
          +-------------------------+
          | Memory Extractor Worker | ──► 5. 新事實寫入 sherry_memory.db
          +-------------------------+
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **記憶衝突與幻覺覆寫**：
   - 若使用者在多天對話中改變喜好（例如早期說「我喜歡喝綠茶」，後來改說「我現在只喝黑咖啡」），若事實抽取系統未做語意覆寫邏輯，`user_facts` 中會同時並存兩筆相反事實，導致 LLM 在回覆時產生認知混亂。
2. **跨群隱私外洩風險 (Privacy Leak across Guilds)**：
   - 若使用者在私人群組透露的敏感事實被錯誤標記為世界記憶 (`world_events`)，可能在另一個公開 Discord 伺服器被調用提及。雪莉嚴格區分 `user_facts`（綁定私密個人）與 `world_events`（需明確具備伺服器公開重要性與 `server_id` 隔離）。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **關聯式 SQL 表格儲存事實條目** | 結構精確、支援交易 ACID、可直接依類別/重要度精確過濾與軟刪除。 | 缺乏語義模糊檢索能力，非同義字或關鍵字未命中時無法回憶。 | 引入向量嵌入資料庫 (如 Milvus/ChromaDB) 進行 RAG 檢索。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在設計個人化 AI 系統的長期記憶架構時，如何平衡記憶的新鮮度 (Recency)、頻率 (Frequency) 與重要度 (Importance)？**
  - **Ans**:
    - 參考 Generative Agents 經典記憶模型：記憶評分 $Score = \alpha \cdot Recency + \beta \cdot Importance + \gamma \cdot Relevance$。
    - 雪莉在 `user_facts` 中標配 `importance` (重要性權重 1~10) 與 `created_at`，並在檢索時優先撈取高重要度記憶，次級事實則依更新時間進行衰減截斷。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [AI/13-context-engine.md](./13-context-engine.md) 研讀動態 Prompt 組裝、頭尾注入與 Prefix KV 快取優化。
