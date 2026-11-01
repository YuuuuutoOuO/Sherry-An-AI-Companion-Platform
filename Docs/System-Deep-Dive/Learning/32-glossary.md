# 32 — 全系統專用名詞定義庫 (Domain Glossary)

> **難度標記**：`Difficulty: Foundational`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
統整雪莉 (Sherry) 伴侶平台專案內部所使用的專用名詞、架構術語、設計模式與縮寫對照表，消除跨團隊溝通歧異，建立統一的領域語言 (Ubiquitous Language)。

---

## 📖 領域名詞辭典 (Alphabetical & Thematic)

### A ~ E
- **Affinity (好感度)**：衡量使用者與雪莉關係親密程度的數值 (0.0 ~ 100.0)，儲存於 `sherry_memory.db`，直接影響回覆語氣與稱謂。
- **BotBrain (機器人大腦)**：`handlers/bot_brain/cog.py` 的核心 Cog，管理 LLM 推論任務優先佇列、VRAM 顯存排它鎖與多模型重試。
- **Cloud Bypass (雲端繞過/分流)**：當本地 Stable Diffusion 生圖佔用 VRAM 時，文字對話任務自動切換至雲端 API 模型，完全繞過本地 GPU 鎖以避免等待的架構機制。
- **Context Engine (上下文引擎)**：負責從 SQLite 記憶庫、頻道歷史與靈魂檔案中撈取資料，並拼裝為完整 Prompt 的核心組件。
- **Early Termination (早期對話抑制)**：在雙階段 Agent 調度中，若工具已直接在 Discord 渲染 UI 且使用者未要求聊天，主動跳過第二階段文字合成以節省 Token 與延遲。
- **ECA Engine (事件-條件-行動引擎)**：SPARK 推播系統採用的規則觸發模式 (Event-Condition-Action)。

---

### F ~ P
- **Fail-Fast (快速失敗)**：針對 15 個核心 Cog 採用的載入原則；任一失敗立即終止進程 (`sys.exit(1)`)，避免系統處於不可預測的半殘狀態。
- **Head Persona (頭部靜態人設)**：置於 Prompt 最前端的靈魂設定與互動鐵則，維持 100% 字元恆定以最大化 Prefix KV Cache 命中率。
- **Integrated Strategy (單階段整合調度)**：雪莉預設的 Agent 模式，在一次推論中同時輸出對話文本與內嵌 `<action>` 標籤。
- **Lorebook (世界書)**：根據使用者當前對話關鍵字動態觸發的補充人設與背景規則，置於 `tail_lore`。
- **Parallel Strategy (雙階段並行調度)**：由 Planner 模型輸出 JSON Plan，並行執行多個外部工具，再由 Synthesizer 模型合成陪伴回覆的模式。
- **Prefix KV Caching (前綴鍵值快取)**：推論引擎在輸入 Prompt 前綴相同時，直接重用先前計算好的 Attention KV 張量以降低首字延遲 (TTFT) 的技術。

---

### R ~ Z
- **ReAct Strategy (推理行動循環)**：交替執行 Thought -> Action -> Observation 的多輪 Agent 探索架構。
- **SherryCore (雪莉中央微核心)**：位於 `app/orchestrator.py` 的調度指揮官，管理全局能力註冊表 (`CapabilityRegistry`)、安全守衛與心智狀態快照。
- **SOUL File (靈魂檔案)**：存放於 `file/soul/` 下的 Markdown 文件，定義雪莉的性格特質、說話口吻、人稱習慣與禁忌條款。
- **Tail Lore (尾部動態世界書)**：置於對話歷史最尾端（緊貼生成點）的區塊，包含當前時間戳記與觸發的 Lorebook。
- **ToolResponseMode / ToolCategory (工具回應模式)**：定義工具執行後的類別：`DIRECT_UI`（自行發送 Discord 視圖/多媒體）、`DATA_PROVIDER`（回傳結構化摘要供 Agent 轉述）。
- **Trace ID (分散式追蹤碼)**：透過 `contextvars.ContextVar` 在非同步協程中隱式傳遞的唯一標識符（對話請求為 `chat-xxxxxxxx`，斜線指令為 `cmd-xxxxxxxx`），串聯全鏈路日誌與審計事件。
- **WAL Mode (預寫式日誌)**：SQLite 的 `PRAGMA journal_mode = WAL;`，允許讀寫並行並大幅提升並發吞吐量。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Learning/33-architecture-atlas.md](./33-architecture-atlas.md) 研讀架構地圖集與多維度視角對照。
