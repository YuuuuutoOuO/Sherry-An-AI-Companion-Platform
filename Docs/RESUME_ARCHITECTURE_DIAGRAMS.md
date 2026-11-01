# 橘雪莉 (Sherry Platform) 履歷用架構與流程圖手冊 (黑白極簡專業版)

本手冊專為**正式履歷 (Resume)、PDF 列印、技術作品集 (Portfolio) 與技術面試簡報**設計。
風格採用**純白底、黑線條、黑文字（無彩色、無圖標 Emoji）**，具備最高印刷對比度與工程專業度。

---

## 目錄
1. **[架構圖 1] 系統總體分層解耦架構圖 (Clean Architecture System Topology)**
2. **[時序圖 2] Dual-Pass 雙階段推理與 KV Cache 前綴對齊管線 (Prefix-Caching-Aligned Dual-Pass Pipeline)**
3. **[流程圖 3] 多模態視覺感知與長期記憶隔離檢索系統 (Multimodal Vision & Memory Architecture)**
4. **[流程圖 4] SPARK 事件驅動自主情報推播閉環 (SPARK Event-Driven Autonomous Loop)**

---

## 1. 系統總體分層解耦架構圖 (Clean Architecture System Topology)

> **設計重點**：Clean Architecture 分層解耦、依賴倒置 (IoC)、Composition Root、模組熱插拔與 GPU 顯存併發防禦。

```mermaid
graph TD
    %% 全域黑白簡約樣式
    classDef plainBox fill:#FFFFFF,stroke:#000000,stroke-width:1.5px,color:#000000;
    classDef subBox fill:#FAFAFA,stroke:#666666,stroke-width:1.5px,stroke-dasharray: 4 4,color:#000000;

    subgraph ClientLayer ["1. 展示與輸入介面層 (Presentation & Interface Layer)"]
        DiscordGW["Discord Gateway (discord.py 2.x / Slash / Views)"]:::plainBox
        WebConsole["Web UI 2.0 (FastHTML / Starlette / APM Dashboard)"]:::plainBox
        OpsTools["CLI 獨立終端監控 (Monitoring Tools / APM)"]:::plainBox
    end

    subgraph AppLayer ["2. 應用編排層 (Application Orchestration Layer)"]
        Orchestrator["SherryCore Orchestrator (app/orchestrator.py)<br/>[生命週期管理 / 優先權佇列 / 上下文組裝]"]:::plainBox
        DispatchAgent["DispatchAgent (Parallel / ReAct 模式切換)"]:::plainBox
    end

    subgraph CoreLayer ["3. 核心基礎與合約層 (Core Contracts & Infrastructure)"]
        Contracts["Contracts & Protocols (抽象合約介面)"]:::plainBox
        Registry["Service Registry & Composition Root (依賴注入)"]:::plainBox
        CoreGuard["CoreGuard (GPU VRAM 顯存防禦與併發協調)"]:::plainBox
        DB["Persistent Storage (SQLite WAL / JSONL)"]:::plainBox
        Dispatcher["SmartMessageDispatcher (Discord 429 退避 / 2000字切分)"]:::plainBox
    end

    subgraph HandlersLayer ["4. 核心領域服務層 (Handlers / Domain Services)"]
        BotBrain["BotBrain (動態思維流 / 人格生成)"]:::plainBox
        SparkCore["SPARK Core (排程 / 股價 / RSS / 天氣情報)"]:::plainBox
        MemSystem["Memory Subsystem (Top-K 加權檢索 / 好感度狀態)"]:::plainBox
        VibeMgr["VibeManager (讀空氣 / 頻道活躍度判定)"]:::plainBox
        SysInspector["SysInspector (全景微觀 APM 遙測)"]:::plainBox
        VoiceSubsys["Voice Subsystem (faster-whisper STT + XTTS-v2)"]:::plainBox
    end

    subgraph ModLayer ["5. 鬆耦合外掛模組 (Mod Plugin Ecosystem - 熱插拔)"]
        ModSysMon["sys_monitor (效能監控)"]:::plainBox
        ModSearch["deep_search (深度研究)"]:::plainBox
        ModPaint["painter (數位畫布)"]:::plainBox
        ModLive["livestream (實況劇院)"]:::plainBox
        ModTrade["stock_trader (量化交易)"]:::plainBox
        ModDND["dnd (TRPG 跑團引擎)"]:::plainBox
        ModGame["game_center (遊戲中心)"]:::plainBox
    end

    subgraph ExternalLayer ["6. 推理矩陣與外部服務 (External Inference & APIs)"]
        LocalLLM["地端推論後端 (Ollama / vLLM / LM Studio)"]:::plainBox
        CloudLLM["雲端模型 API (OpenAI / Claude / Gemini / DeepSeek / OpenRouter)"]:::plainBox
        ExternalAPI["外部資訊 API (Finnhub / OpenWeather / SafeBooru)"]:::plainBox
    end

    DiscordGW & WebConsole & OpsTools --> Orchestrator
    Orchestrator --> DispatchAgent
    DispatchAgent --> CoreGuard
    CoreGuard --> HandlersLayer
    HandlersLayer -.依賴倒置 (IoC)介面.-> Contracts
    Registry -.依賴注入 (DI).-> HandlersLayer
    HandlersLayer --> DB
    HandlersLayer --> Dispatcher
    Dispatcher --> DiscordGW & WebConsole

    Orchestrator -.動態載入 / 故障隔離.-> ModLayer
    ModLayer -.調用合約.-> Contracts

    BotBrain --> LocalLLM & CloudLLM
    SparkCore --> ExternalAPI
    VoiceSubsys --> CoreGuard
```

---

## 2. Dual-Pass 雙階段推理與 KV Cache 前綴對齊管線 (Prefix-Caching-Aligned Dual-Pass Pipeline)

> **設計重點**：Prompt Caching (KV Cache)、Zone 1/2 分離、Zero Secondary Prefill、TTFT < 200ms、asyncio.gather 並行調度。

```mermaid
sequenceDiagram
    autonumber
    actor User as 使用者 (Discord Client)
    participant Orch as Orchestrator
    participant Context as Context Assembly
    participant Pass1 as Pass-1 (Planner Model)
    participant Tools as Parallel Tools Executor
    participant Pass2 as Pass-2 (Primary Model)
    participant Disp as Dispatcher & LiveThinking

    User->>Orch: 發送請求 (文字訊息 / 圖片附件 / 引用上下文)
    
    Note over Orch,Context: 上下文前綴快取對齊 (Prefix Alignment)
    Orch->>Context: 組裝 Prompt (分離靜態前綴與動態情境)
    Context->>Context: Zone 1: 靜態角色 Soul + 精簡工具索引 (100% 位元組一致)
    Context->>Context: 動態檢索: Top-K 記憶加權 (Score = base_imp + rel * 5.0)
    Context->>Context: Zone 2: 動態情境 (歷史對話 + 視覺線索 + 頻道氛圍)
    Context-->>Orch: 生成對齊之 Pass-1 Prompt

    Orch->>Pass1: 意圖解析與規劃 (Inference Request)
    Note over Pass1: 命中 KV Cache (5,500+ Tokens Prefill 快取)<br/>快速推演意圖與工具調用參數
    Pass1-->>Orch: 回傳規劃結果 (Tool Calls 或 Direct Response)

    alt 無需調用工具 (Action Early Termination)
        Orch->>Disp: 進入直出通道 (立即響應)
    else 需調用外部工具 (Parallel Execution)
        Orch->>Tools: asyncio.gather 並行執行工具鏈 (股市 / 天氣 / 搜尋)
        Tools->>Tools: 讀取 Tier-1 記憶體 / TTL 高速快取
        Tools-->>Orch: 彙整工具執行結果 (JSON Context)

        Note over Orch,Pass2: Pass-2 複用 Pass-1 相同前綴 (Zero Secondary Prefill)
        Orch->>Pass2: 將工具結果附加於 User Prompt 尾部傳送
        Note over Pass2: 100% 命中 Pass-1 前綴快取<br/>首字延遲 (TTFT) 降低至 < 200ms
        Pass2-->>Disp: 生成具備人設風格之最終回覆
    end

    Disp->>User: LiveThinkingSession 原地動態展開思考流 (message.edit 防抖)
    Disp->>User: 智慧聚合長文分段發送 (長度 < 2000 字元 / 429 自適應退避)
```

---

## 3. 多模態視覺感知與長期記憶隔離檢索系統 (Multimodal Vision & Memory Architecture)

> **設計重點**：視覺神經與對話隔離防護（防止 OCR 文字誤判為群友對話）、Lorebook 自我形象辨識、動態加權記憶檢索演算法與 UID 記憶安全消毒。

```mermaid
flowchart TD
    classDef plainBox fill:#FFFFFF,stroke:#000000,stroke-width:1.5px,color:#000000;
    classDef decisionBox fill:#FFFFFF,stroke:#000000,stroke-width:1.5px,color:#000000;

    InputMsg["使用者訊息 (文字 + 圖片附件 + 引用上下文 Quote)"]:::plainBox --> HasImg{"是否包含圖片？"}:::decisionBox

    %% 視覺神經管線
    subgraph VisionPipeline ["多模態視覺管線 (Multimodal Vision Pipeline)"]
        HasImg -->|是| VisionModel["多模態視覺模型 (MiniCPM-V / Qwen2-VL)"]:::plainBox
        VisionModel --> FeatureExtract["畫面特徵抽取與 OCR 文字識別"]:::plainBox
        FeatureExtract --> LorebookMatch{"畫面特徵是否吻合角色形象？"}:::decisionBox
        LorebookMatch -->|是| TriggerLorebook["觸發 Lorebook 自我形象辨識 (維持角色認知)"]:::plainBox
        LorebookMatch -->|否| NormalScene["產生場景與物件客觀描述 (Scene Context)"]:::plainBox
        TriggerLorebook & NormalScene --> VisionIsolation["視覺神經與發言隔離層<br/>(嚴格防止 OCR 文字被誤判為使用者發言)"]:::plainBox
    end

    %% 記憶檢索管線
    subgraph MemoryPipeline ["雙層長短期記憶架構 (Memory Architecture)"]
        InputMsg --> QueryIntent["提取查詢關鍵字與上下文意圖"]:::plainBox
        QueryIntent --> VectorScore["計算語意關聯度: rel = calculate_query_relevance()"]:::plainBox
        VectorScore --> DynamicRank["記憶排序評分: Score = base_imp + (rel * 5.0)"]:::plainBox
        DynamicRank --> TopKSelect["檢索 Top-K 高權重關聯記憶"]:::plainBox
        TopKSelect --> Sanitize["記憶安全消毒 (sanitize_memory 移除惡意注入)"]:::plainBox
        Sanitize --> UserProfile["載入發話者個人事實庫 (UID Memory) 與好感度狀態"]:::plainBox
    end

    HasImg -->|否| QueryIntent

    %% 上下文組裝
    subgraph ContextSynthesizer ["上下文安全合成器 (Context Synthesizer)"]
        VisionIsolation --> SystemContext["System Context: [視覺線索] (標註非對話資訊)"]:::plainBox
        UserProfile --> SystemContext
        SystemContext --> ModelInput["注入 LLM 推理矩陣 (乾淨且具備記憶的上下文)"]:::plainBox
    end
```

---

## 4. SPARK 事件驅動自主情報推播閉環 (SPARK Event-Driven Autonomous Loop)

> **設計重點**：事件驅動架構 (EDA)、4 大動態觸發源、讀空氣 (Vibe Check) 頻率抑制、NLP 精煉與全景微觀 APM 遙測自癒。

```mermaid
flowchart LR
    classDef plainBox fill:#FFFFFF,stroke:#000000,stroke-width:1.5px,color:#000000;
    classDef decisionBox fill:#FFFFFF,stroke:#000000,stroke-width:1.5px,color:#000000;

    %% 觸發來源
    subgraph Triggers ["4 大動態事件觸發源 (SPARK Core Triggers)"]
        direction TB
        T1["Price Trigger<br/>美股與加密貨幣門檻監控"]:::plainBox
        T2["Schedule Trigger<br/>Cron 與自然語言排程"]:::plainBox
        T3["RSS Sentry<br/>15 分鐘社群情報巡邏"]:::plainBox
        T4["Weather Alert<br/>極端氣候與劇變預警"]:::plainBox
    end

    %% 門禁與環境感知
    subgraph Gatekeeper ["智慧門禁與環境感知 (Gatekeeper & Vibe Check)"]
        direction TB
        Cooldown{"智慧冷卻判定<br/>(防刷屏風控)"}:::decisionBox
        VibeCheck{"VibeManager<br/>(讀空氣: 評估頻道熱度與發話時機)"}:::decisionBox
    end

    %% 大腦決策
    subgraph BrainDecision ["智慧精煉與人設注入 (BotBrain Decision)"]
        direction TB
        NLP["NLP 意圖分析與資訊精煉 (提煉繁中 3 大重點)"]:::plainBox
        Persona["人設風格融合 (名偵探毒舌銳評 / 傲嬌人設轉換)"]:::plainBox
    end

    %% 執行端
    subgraph Execution ["多端主動交付 (Omnichannel Delivery)"]
        direction TB
        DiscordPush["Discord 頻道 (Rich Embed 與互動式 View 元件)"]:::plainBox
        WebPush["Web UI 看板 (即時 WebSocket 事件廣播)"]:::plainBox
    end

    %% 遙測與自癒
    subgraph APM ["全景微觀遙測與閉環自癒 (APM & Self-Healing)"]
        direction TB
        Telemetry["微觀指標採樣: ttft_ms / latency / cache_hit_ratio"]:::plainBox
        AlertCheck{"異常判定?"}:::decisionBox
        AdminDM["Discord 私訊自動警報管理員"]:::plainBox
    end

    T1 & T2 & T3 & T4 --> Cooldown
    Cooldown -->|通過| VibeCheck
    Cooldown -->|冷卻中| Drop["拋棄事件 / 延遲重試"]:::plainBox
    VibeCheck -->|氣氛合適| NLP
    NLP --> Persona
    Persona --> DiscordPush & WebPush

    DiscordPush & WebPush -.寫入遙測數據.-> Telemetry
    Telemetry --> AlertCheck
    AlertCheck -->|發現致命錯誤| AdminDM
```

---

## 履歷專案描述話術 (可直接複製進履歷)

- **系統架構設計 (Clean Architecture & IoC)**：
  - 「主導以 **Clean Architecture** 與 **IoC 依賴注入** 重構 AI 代理人核心，將排程調度、狀態管理與外部插件（10 大擴充模組）徹底解耦，透過單向契約防護將循環依賴降為 0，實現模組熱插拔與故障隔離。」
- **LLM 推理極限優化 (KV Cache Alignment & TTFT)**：
  - 「設計 **Zone 1 / Zone 2 前綴與動態內容分離架構**，確保 Pass-1 意圖規劃與 Pass-2 人格合成維持 100% 靜態前綴位元組一致；達成 **Zero Secondary Prefill**，使 Pass-2 首字延遲 (TTFT) 降低至 **< 200ms**，每輪對話節省 900+ Tokens。」
- **多模態安全與發言隔離 (Multimodal Isolation & Memory)**：
  - 「研發**視覺神經直出與發言隔離技術**，將多模態圖片 OCR 結果嚴格限定於 System Context，徹底消除大模型將圖片文字誤判為使用者發言的幻覺問題；結合 **Selective Top-K 加權演算法** 與 UID 記憶消毒機制，精準提取長期事實。」
- **事件驅動自主情報系統 (SPARK Event-Driven Engine)**：
  - 「構建 **SPARK 核心事件驅動推播系統**，支援價格異動、定期排程、RSS 資訊源與天氣預警等 4 大觸發源，配合自適應冷卻風控與讀空氣 (VibeManager) 機制，使 AI 具備主動發話與破冰能力。」
