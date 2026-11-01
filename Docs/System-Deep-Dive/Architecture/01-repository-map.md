# 01 — 倉庫全景結構與目錄映射地圖 (Repository Map)

> **難度標記**：`Difficulty: Foundational`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面解構雪莉 (Sherry) 專案儲存庫的實體目錄拓撲與工程分層映射關係。釐清根目錄腳本、核心服務層 (`core/`, `app/`)、展示與調度層 (`handlers/`)、外掛模組層 (`mod/`)、Web 控制台 (`web_ui/`) 與持久化資料層 (`data/`) 的定位，建立系統全域視野。

---

## 🧭 Prerequisites (先備知識)
- Python 專案目錄組織慣例與 Package/Module 概念。
- Discord.py 的 Cog 插件化架構。
- 《科技公司軟體架構與開發規範 v2.2》第 0 節「分層對應原則」。

---

## 💡 Concept (架構分層理論)
在大型現代應用中，目錄結構直接反映系統的架構分層 (Architectural Layering)：
1. **Presentation / Controller 層**：負責處理外部輸入協議（如 Discord 互動、HTTP REST API、WebSocket 事件），不承載核心領域商業邏輯。
2. **Domain / Service 層**：封裝系統核心領域邏輯（心智狀態、排程觸發、認知記憶更新），不可直接相依具體外部 I/O 通訊協定庫。
3. **Repository / Infrastructure 層**：處理外部 I/O（DB、第三方 API、LLM 客戶端、檔案系統），向 Service 層提供抽象契約。
4. **Plugin / Extension 層**：具備高內聚、低耦合的可選外掛能力，不破壞核心穩定性。

---

## 🛠️ Sherry Implementation (雪莉實作拓撲)

雪莉儲存庫採用「微核心 + 雙重外掛槽 (Dual Extension Slots)」的工程架構：

```text
Sherry-An-AI-Companion-Platform/
├── main.py                     # [CONFIRMED] 應用程式單一引導進入點 (Bootstrap Entrypoint)
├── settings.py                 # [CONFIRMED] 全域組態中心 (向後相容 re-export core/config/)
├── app/                        # [CONFIRMED] 雪莉中央微核心 (Micro-kernel Orchestrator)
│   ├── orchestrator.py         #   - SherryCore 指揮官類別
│   └── ...
├── core/                       # [CONFIRMED] 系統共用核心服務與合約 (Contracts, DB, Guard, Inspector)
│   ├── contracts.py            #   - 跨模組 Protocol 與 Dataclass 定義
│   ├── db.py                   #   - SQLite WAL 共用連線池與交易管理器
│   ├── db_migration.py         #   - 自動 Schema 備份與 PRAGMA 遷移工具
│   ├── guard.py                #   - SSRF 防禦、頻率限制、死循環熔斷
│   ├── dispatcher.py           #   - 智慧聚合訊息派發器 (2000 字元分段)
│   ├── config/                 #   - 設定拆分模組 (paths, env, models, soul, manager)
│   └── sys_inspector/          #   - 系統觀測指標、硬體採樣與審計服務
├── handlers/                   # [CONFIRMED] Presentation 層：核心 Cog 擴充套件目錄 (15 個內建 Cog)
│   ├── bot_brain/              #   - LLM 推論佇列、GPU 顯存鎖、OllamaClient
│   ├── social/                 #   - 社交大腦、動態人格拼裝、多策略調度 (Integrated/Parallel/ReAct)
│   ├── spark/                  #   - SPARK 條件推播引擎 (全面接管傳統 scheduler)
│   ├── memory/                 #   - 記憶資料庫存取與生命週期管理
│   ├── vibe_manager/           #   - 頻道氛圍感測與動態情緒注入
│   ├── music/                  #   - Wavelink 音樂留聲機播放控制器
│   ├── voice/                  #   - 語音通話 (Piper TTS, Whisper STT)
│   ├── admin/                  #   - 管理員運維指令集
│   ├── executor/               #   - 外部命令與自定義腳本安全執行器
│   ├── sys_inspector/          #   - 系統巡檢 Discord 命令
│   ├── roast/                  #   - 吐槽規則引擎
│   ├── context_menu/           #   - 右鍵快捷應用指令 (翻譯、摘要)
│   ├── help_cog.py             #   - 說明面板
│   ├── heartbeat/              #   - 網關心跳與存活探針
│   └── welcome_cog.py          #   - 入群迎賓事件
├── mod/                        # [CONFIRMED] 可選主題外掛目錄 (Auto-discovered Mod Plugins)
│   ├── clients/                #   - 唯一允許 import 外部 I/O 的 Client 層 (符合規範)
│   ├── market_plugin/          #   - 美股/加密貨幣/PTCG 行情與預測
│   ├── deep_search/            #   - 多階段長文獻深度研究引擎
│   ├── dnd/                    #   - 龍與地下城 TRPG 跑團引擎
│   ├── feed_sentinel/          #   - RSS 與情報巡邏哨兵
│   ├── painter/                #   - 專業繪圖引擎
│   └── livestream/             #   - 直播間互動整合
├── web_ui/                     # [CONFIRMED] 獨立管理控制台 (Quart + Uvicorn ASGI Server)
│   ├── server.py               #   - Web API、身份驗證、日誌分流與動態 Plugin 掛載
│   ├── templates/              #   - Jinja2 HTML 儀表板視圖
│   └── static/                 #   - CSS / JS 靜態資源
├── data/                       # [CONFIRMED] 持久化資料庫與日誌輸出目錄
│   ├── *.db                    #   - 12 個核心業務 SQLite 資料庫 (磁碟含別名共 13 檔)
│   ├── *.log                   #   - bot.log, web_ui.log 滾動日誌
│   └── backups/                #   - 自動結構遷移快照與備份
├── Docs/                       # [CONFIRMED] 系統架構手冊、ADR、CR 與深度技術教材
├── tests/                      # [CONFIRMED] 單元測試 (unit)、整合測試 (integration)、評測 (evaluation)
└── tools/                      # [CONFIRMED] 離線維運工具 (db_manager.py, migrate_json_to_sqlite.py)
```

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **主要進入點與 Cog 列表**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L85-L101)，`EXTENSIONS` 明確列出 15 個核心 Cog 模組。
2. `[CONFIRMED]` **Mod 動態掃描機制**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L145-L163)，`_discover_optional_extensions()` 遍歷 `./mod/` 下具備 `cog.py` 的子資料夾，並透過 `data/mod_config.json` 判定開關。
3. `[CONFIRMED]` **微核心初始化**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L206-L207)，`bot.core = SherryCore(bot)` 在 Cog 載入前綁定。
4. `[CONFIRMED]` **資料目錄自動建立**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L27-L31)，在啟動初期即主動呼叫 `DATA_DIR.mkdir(...)`。

---

## 📊 Flow Diagram (結構映射關係圖)

```text
+-----------------------------------------------------------------------------------+
|                              Sherry Root (main.py)                                 |
+----------------------------------------+------------------------------------------+
                                         |
               +-------------------------+-------------------------+
               |                                                   |
               v                                                   v
   +-----------------------+                           +-----------------------+
   |   app.SherryCore      |                           |     commands.Bot      |
   | (微核心調度與能力註冊)  |                           | (Discord 連線與事件迴圈)|
   +-----------+-----------+                           +-----------+-----------+
               |                                                   |
      +--------+--------+                         +----------------+---------------+
      |                 |                         |                                |
      v                 v                         v                                v
+-----------+     +-----------+            +--------------+                 +--------------+
|   core/   |     |   mod/    |            |  handlers/   |                 |   web_ui/    |
| (基礎契約/|     | (可選外掛/|            | (15個內建 Cog|                 | (Quart Web   |
|  DB/安全) |     |  外部客戶)|            |  對話/大腦)  |                 |  管理控制台) |
+-----------+     +-----------+            +--------------+                 +--------------+
      |                 |                         |                                |
      +--------+--------+-------------------------+--------------------------------+
               |
               v
        +--------------+
        |    data/     |
        | (SQLite 儲存 |
        |  日誌與備份) |
        +--------------+
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **Mod 插件載入崩潰不影響主程序**：
   - `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L228-L232) 以 `try...except` 包裹外掛載入，當外掛拋出 `ImportError` 或初始化失敗時，僅記錄 `WARNING`，保證雪莉主核心不受牽連。
2. **核心 Cog 載入失敗實施 Fail-Fast**：
   - `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L216-L218)，若 15 個核心 `EXTENSIONS` 中有任何一個載入失敗，會直接執行 `sys.exit(1)` 終止進程，避免進入不完整的「半身不遂」運行狀態。
3. **目錄不存在時的 FileHandler 崩潰邊界**：
   - 若未在全域 Logging 設定前建立 `data/` 目錄，`RotatingFileHandler` 將拋出 `FileNotFoundError`。雪莉在 `main.py` 第 27 行強制優先呼叫 `mkdir(parents=True, exist_ok=True)` 解決此問題。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **雙層載入架構 (Core Cog + Mod Plugins)** | 核心功能具強保證 (Fail-Fast)，社群與實驗性功能隔離崩潰半徑。 | 外掛與核心通訊需仰賴 `bot.get_cog()` 或微核心協議，增加契約維護成本。 | 單一全量 Cog 平鋪或微服務 RPC 分離。 |
| **Monorepo 單儲存庫架構** | 跨層重構可原子化提交，型別檢查 (mypy) 與測試全域可達。 | 程式碼規模大時，跨模組非法引用 (如 mod 引用 presentation) 需依賴嚴格 linter 攔截。 | 拆分為 bot-core 與 mod-* 多套件倉庫。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 為什麼現代大型系統要區分「內建核心服務 (Fail-Fast)」與「可選外掛 (Fault-Tolerant)」的載入策略？**
  - **Ans**: 核心服務（如推論佇列 `bot_brain`、狀態記憶 `memory`）是維持系統基本運作合約的基石，任一缺失會導致邏輯不可預測，必須 Fail-Fast 讓容器或行程管理器 (Supervisor/K8s) 重啟或告警；而外掛（如跑團、看盤）屬於業務加值，應具備隔離性，容忍局部故障以維護系統高可用性 (High Availability)。

---

## 🔗 What to Study Next (後續章節銜接)
- 進入 [Architecture/02-module-inventory.md](./02-module-inventory.md) 深入了解 15 個核心 Cog 與外掛模組的職責邊界矩陣。
