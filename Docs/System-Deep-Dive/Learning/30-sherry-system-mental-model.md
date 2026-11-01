# 30 — 雪莉系統工程核心心智模型 (Mental Model)

> **難度標記**：`Difficulty: Intermediate`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
為架構師、新進工程師與技術管理者建立理解雪莉 (Sherry) 伴侶平台的**頂層心智模型 (Mental Model)**。將數萬行程式碼提煉為五大核心工程支柱，化繁為簡，讓讀者在腦海中形成清晰的系統運作圖像。

---

## 🧭 Prerequisites (先備知識)
- 軟體架構心智模型 (Conceptual Mental Models)。
- 模組化單體架構 (Modular Monolith)。

---

## 💡 Concept (系統心智模型理論)
心智模型是工程師用來推理複雜系統運作、預測故障與設計新功能的認知框架。
若將雪莉比喻為一個活生生的人類大腦：
- **感官神經 (Sensory)**：Discord Gateway 與 Web UI，即時接收外部刺激。
- **指揮微核心 (Micro-kernel)**：`SherryCore`，決定注意力的分配與工具的啟用。
- **記憶體系 (Hippocampus & Cortex)**：三層記憶資料庫，維繫好感度與跨群經驗。
- **神經元推論與算力守護 (Frontal Lobe & Homeostasis)**：`BotBrain`，排程大腦思維，保護本地顯存不致過載。
- **表達中樞 (Motor & Speech)**：智慧聚合派發器與 Discord UI 元件。

---

## 🛠️ Sherry Implementation (雪莉五大核心支柱)

```text
+-----------------------------------------------------------------------------------+
|                        雪莉系統工程五大支柱 (The 5 Pillars)                       |
+-----------------------------------------------------------------------------------+

 1. 單一非同步事件核心 (Single Asyncio Event Core)
    - Discord Gateway 與 Quart Web UI 共存於同一個 Event Loop。
    - 絕不容忍同步阻塞 I/O，所有耗時計算必須下沉執行緒池。

 2. 雙軌推論與顯存守護 (Dual-Engine Inference & VRAM Arbiter)
    - 本地 GPU 嚴格以 asyncio.Lock 串行執行，保證零 CUDA OOM。
    - 地端生圖搶佔顯存時，對話任務自動切換雲端模型並行繞過，達成零等待。

 3. 三層認知記憶閉環 (Three-Tier Closed-Loop Memory)
    - 短期對話 (chat_messages) -> 個人長程事實 (user_facts) -> 世界宏觀 (world_events)。
    - 對話主路徑非同步落盤，後台 Worker 異步提煉事實，回填演化。

 4. 多策略 Agent 調度矩陣 (Multi-Strategy Agent Matrix)
    - Integrated (單階段內嵌標籤，極速對話)
    - Parallel (雙階段規劃-並行執行-合成，複合工具)
    - ReAct (多輪推理行動，深度排錯)

 5. 雙重外掛插槽與分層邊界 (Dual-Slot Modular Monolith)
    - 15 個內建核心 Cog (Fail-Fast，保證基本法)。
    - 動態探索可選 Mod 插件 (Fault-Tolerant，故障半徑隔離)。
```

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **微核心指揮官**：[`app/orchestrator.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/app/orchestrator.py)。
2. `[CONFIRMED]` **推論顯存協調**：[`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py)。
3. `[CONFIRMED]` **分層邊界紀律**：[`.importlinter`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/.importlinter)。

---

## 📊 Flow Diagram (五大支柱立體交互圖)

```text
                        [外部事件: Discord / Web]
                                   │
                                   ▼
                 ┌───────────────────────────────────┐
                 │ 支柱 1: 單一非同步事件核心 (Loop)  │
                 └─────────────────┬─────────────────┘
                                   │
                                   ▼
                 ┌───────────────────────────────────┐
                 │ 支柱 5: 模組插槽與調度指揮官      │
                 └───────┬───────────────────┬───────┘
                         │                   │
                         ▼                   ▼
     ┌────────────────────────┐         ┌────────────────────────┐
     │ 支柱 3: 三層認知記憶   │         │ 支柱 4: 策略調度矩陣   │
     │ (短期/長期/世界記憶)   │         │ (Integrated/Parallel)  │
     └────────────────────────┘         └───────────┬────────────┘
                                                    │
                                                    ▼
                                        ┌────────────────────────┐
                                        │ 支柱 2: 雙軌推論顯存鎖 │
                                        │ (地端串行 / 雲端繞過)  │
                                        └────────────────────────┘
```

---

## ⚠️ Failure Cases & Edge Cases (心智模型偏差防範)

- **常見認知偏差 1**：「以為雪莉有獨立的後端 Web 容器（如 Docker Gunicorn）。」
  - **真相修正**：Web UI 是被包裹在協程任務中，與 Bot 跑在同一個行程內！
- **常見認知偏差 2**：「以為本地 LLM 可以像雲端 API 一樣任意高並發直接發送。」
  - **真相修正**：本地 GPU 顯存非常脆弱，必須排隊等鎖，並在生圖時主動切換至雲端！

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

- 本架構以「單機極限資源利用」與「個人私有化部署」為出發點，在不依賴分散式 K8s 叢集的前提下，達成了極高的工程完整度與體驗流暢度。

---

## 💼 Interview Relevance (面試考點)

- **Q: 如何用一句話向非技術主管或資深架構師介紹雪莉的技術定位？**
  - **Ans**: 雪莉是一個採用模組化微核心架構、整合了三層式認知記憶與雙軌 GPU 資源調度的自主型 AI 伴侶平台。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Learning/31-system-interview-guide.md](./31-system-interview-guide.md) 研讀資深架構師技術面試模擬問答。
