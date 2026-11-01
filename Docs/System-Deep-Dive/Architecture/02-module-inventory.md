# 02 — 模組庫存清單與職責邊界矩陣 (Module Inventory)

> **難度標記**：`Difficulty: Intermediate`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面盤點雪莉系統內 15 個內建 Cog 擴充套件、7 大核心服務模組與 6 大主題外掛 (Mod Plugins)，詳述各模組的職責邊界、所屬架構層、主要對外公開類別與其關鍵方法，建立精確的系統模組庫存清單。

---

## 🧭 Prerequisites (先備知識)
- Discord.py Cog 生命週期 (`cog_load`, `cog_unload`)。
- 《科技公司軟體架構與開發規範 v2.2》分層職責矩陣。
- 模組化設計原則 (Single Responsibility Principle, High Cohesion)。

---

## 💡 Concept (模組庫存與職責分離理論)
大型單體 (Modular Monolith) 系統的健康度取決於「職責邊界」是否清晰：
- 避免出現過度肥大的「上帝模組 (God Module)」。
- 清楚劃分 Controller (處理 Discord 事件)、Service (核心業務計算) 與 Repository (資料庫/API 存取)。
- 建立對照矩陣可確保模組依賴關係單向流動，防止循環引用 (Circular Imports)。

---

## 🛠️ Sherry Implementation (雪莉模組清單詳解)

### 一、核心服務層 (`core/` & `app/`)
| 模組路徑 | 職責定義 | 關鍵類別 / 進入點 | 關鍵方法 |
|---|---|---|---|
| `app/orchestrator.py` | [CONFIRMED] 中央調度微核心指揮官 | `SherryCore` | `dispatch()`, `_register_default_capabilities()` |
| `core/contracts.py` | [CONFIRMED] 全域 Protocol 與資料契約定義 | `CloudInferenceProtocol`, `SherryRequest`, `SherryResponse` | 抽象介面定義 |
| `core/db.py` | [CONFIRMED] SQLite WAL 連線池與交易管理器 | `get_db_connection()`, `db_transaction()` | `checkpoint_all_dbs()` |
| `core/db_migration.py`| [CONFIRMED] 資料庫結構安全遷移與自動備份 | `DatabaseMigrator` | `migrate()`, `backup_database()` |
| `core/guard.py` | [CONFIRMED] 安全守衛 (SSRF/限流/死循環防禦) | `CoreGuard`, `is_safe_public_url()` | `check_rate_limit()`, `check_loop_breaker()` |
| `core/dispatcher.py` | [CONFIRMED] 智慧聚合訊息派發器 | `SmartMessageDispatcher` | `send_aggregated_message()` (2000字切分) |
| `core/sys_inspector/`| [CONFIRMED] 系統資源採樣與審計資料服務 | `SystemSampler`, `ComponentMetrics` | `sample()`, `record_event()` |

---

### 二、展示與調度 Cog 層 (`handlers/`)
15 個內建 Cog 模組列表（依 `main.py` 載入順序）：

| 順序 | 模組名稱 | 檔案位置 | 核心職責 | 關鍵類別 |
|---|---|---|---|---|
| 1 | `handlers.bot_brain` | `handlers/bot_brain/cog.py` | [CONFIRMED] LLM 推論單步優先佇列與 GPU 顯存鎖 | `BotBrain` |
| 2 | `handlers.social` | `handlers/social/__init__.py` | [CONFIRMED] 社交對話中樞、動態人設與多策略 Agent 調度 | `SocialCog` |
| 3 | `handlers.vibe_manager` | `handlers/vibe_manager/` | [CONFIRMED] 頻道歷史氛圍感測、情境推導與情緒注入 | `VibeManager` |
| 4 | `handlers.music` | `handlers/music/` | [CONFIRMED] Wavelink 音樂播放器、留聲機佇列控制 | `MusicCog` |
| 5 | `handlers.memory` | `handlers/memory/` | [CONFIRMED] 記憶存取 Cog、對話持久化與歷史檢索 | `MemoryCog` |
| 6 | `handlers.admin` | `handlers/admin/cog.py` | [CONFIRMED] 系統熱重載、快取清理與權限運維命令 | `AdminCog` |
| 7 | `handlers.voice` | `handlers/voice/cog.py` | [CONFIRMED] 語音頻道 STT 聽覺與 TTS 發聲合成 | `VoiceCog` |
| 8 | `handlers.spark` | `handlers/spark/` | [CONFIRMED] SPARK 條件推播引擎 (事件觸發/鬧鐘/定時) | `SparkCog` |
| 9 | `handlers.executor` | `handlers/executor/` | [CONFIRMED] 隔離執行緒/處理程序命令執行工具 | `ExecutorCog` |
| 10 | `handlers.help_cog` | `handlers/help_cog.py` | [CONFIRMED] 互動式斜線指令說明與動態分頁面板 | `HelpCog` |
| 11 | `handlers.welcome_cog` | `handlers/welcome_cog.py` | [CONFIRMED] 新成員進群歡迎卡片與人設迎賓訊息 | `WelcomeCog` |
| 12 | `handlers.heartbeat` | `handlers/heartbeat/` | [CONFIRMED] 網關健康檢查心跳探針與死鎖自檢 | `HeartbeatCog` |
| 13 | `handlers.sys_inspector`| `handlers/sys_inspector/`| [CONFIRMED] 系統硬體監控與 CPU/RAM/GPU 狀態展示 | `SysInspectorCog` |
| 14 | `handlers.roast` | `handlers/roast/` | [CONFIRMED] 群友吐槽規則引擎與關鍵字自訂反應 | `RoastCog` |
| 15 | `handlers.context_menu`| `handlers/context_menu/` | [CONFIRMED] Discord 右鍵上下文選單 (訊息翻譯/重點摘要) | `ContextMenuCog` |

---

### 三、外掛模組層 (`mod/`)
凡包含 `cog.py` 且在 `data/mod_config.json` 中啟用的外掛，由進入點自動掃描載入：

| 外掛識別碼 | 模組路徑 | 核心職責 | 獨立資料庫 |
|---|---|---|---|
| `market_plugin` | `mod/market_plugin/` | [CONFIRMED] 美股、加密貨幣、PTCG 卡牌行情爬蟲與 AI 預測 | `market_stock_bot.db` |
| `deep_search` | `mod/deep_search/` | [CONFIRMED] 長篇深度主題研究、多輪檢索與文獻生成 | `deep_search.db` |
| `dnd` | `mod/dnd/` | [CONFIRMED] 龍與地下城 TRPG 角色卡、骰點與冒險劇本 | `dnd/dnd.db` |
| `feed_sentinel` | `mod/feed_sentinel/` | [CONFIRMED] RSS/Twitter 情報自動巡邏與訂閱推播哨兵 | `feed_sentinel/feed_sentinel.db` |
| `painter` | `mod/painter/` | [CONFIRMED] 多圖層向量渲染與高級畫布繪圖代理 | 無 (本機檔案系統儲存靜態畫布與圖檔，無 SQLite 資料庫) |
| `livestream` | `mod/livestream/` | [CONFIRMED] 直播彈幕監聽與實況連動陪伴 | (記憶體連線) |

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **EXTENSIONS 順序保證**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L85-L101)。
2. `[CONFIRMED]` **Capability 雙層註冊**：
   - 見 [`app/orchestrator.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/app/orchestrator.py#L70-L100)，`builtin_tools`（日常 AI 工具）設為 `is_ai_callable=True`，而 `mod_plugins`（專題外掛）設為 `is_ai_callable=False`，防止外部外掛提示詞污染日常 LLM 上下文。
3. `[CONFIRMED]` **外掛開關設定**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L115-L129)，從 `data/mod_config.json` 動態載入啟用布林值。

---

## 📊 Flow Diagram (模組呼叫關係圖)

```text
       +-------------------------------------------------------+
       |                  Discord Gateway Event                |
       +---------------------------+---------------------------+
                                   |
                                   v
       +-------------------------------------------------------+
       |               handlers.social.SocialCog               |
       |      (協同 handlers.vibe_manager 與 handlers.memory)   |
       +---------------------------+---------------------------+
                                   |
                     +-------------+-------------+
                     |                           |
                     v                           v
         +-----------------------+   +-----------------------+
         |  handlers.bot_brain   |   |   handlers.spark      |
         |  (佇列/顯存鎖/推論)    |   |  (條件推播/定時排程)  |
         +-----------+-----------+   +-----------+-----------+
                     |                           |
                     v                           v
         +-----------------------+   +-----------------------+
         |   mod.clients.*       |   |       core.db         |
         | (Ollama / Gemini API) |   | (SQLite WAL 交易持久化)|
         +-----------------------+   +-----------------------+
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **循環引用陷阱 (Circular Import)**：
   - 歷史債務 `TD-001` 曾因 `settings.py` 被所有模組引用，而 `settings.py` 又反向引用業務常數，造成啟動崩潰。現已重構為 `core/config/`，`settings.py` 僅作相容層。
2. **Cog 依賴時序失敗**：
   - 若 `handlers.social` 早於 `handlers.bot_brain` 載入，當社交模組在 `cog_load` 嘗試透過 `bot.get_cog("BotBrain")` 綁定推論佇列時，將取得 `None`。因此 `main.py` 的 `EXTENSIONS` 陣列有嚴格的先後順序約束 (`bot_brain` 必須是第一個)。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **Prompt 能力雙層隔離 (`is_ai_callable`)** | 避免注入 20+ 個外掛的龐大工具說明，省下 60% System Prompt Token 開銷。 | 專案外掛無法在一般閒聊中被自動呼叫，使用者需透過專屬指令或明確指定模式切換。 | 動態向量工具檢索 (Tool Retrieval / RAG for Tools)。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在一個擁有十數個插件的外掛系統中，如何避免插件間互相踩踏 (Contention) 與記憶體/Prompt 污染？**
  - **Ans**: 雪莉透過雙機制達成：（1）**註冊隔離**：利用 `CapabilityDescriptor` 的 `is_ai_callable` 旗標，將專題外掛與日常小工具在 Prompt 層完全隔離；（2）**推論資源隔離**：所有需要 GPU 算力的外掛必須經由 `BotBrain` 優先佇列排隊或直接導向雲端客戶端，禁止外掛自行啟動未受控的本地模型推理。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Architecture/03-entrypoint-map.md](./03-entrypoint-map.md) 研讀系統如何由 CLI、腳本與 main.py 正式引導啟動。
