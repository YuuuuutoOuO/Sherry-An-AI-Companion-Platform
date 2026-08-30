# 🤖 橘雪莉 — Discord AI Companion & Agent Platform (v1.8.0 - Cognitive Symphony)

一個擁有獨立靈魂、會讀空氣、懂得自我反思、精通深度探研與數位藝術創作的 Discord 虛擬實況群友與全能 AI 代理人。  
基於 **discord.py 2.x** + **SherryCore 微核心調度架構** + **8 大 LLM 推理引擎 (Ollama / LM Studio / vLLM / OpenRouter / OpenAI / Gemini / DeepSeek / Claude)** + **asyncio 優先權佇列**，支援 Linux / Docker 容器化與 Windows 跨平台部署，具備即時動態思維鏈展開、條件觸發情報推播 (SPARK)、系統效能診斷採樣 (SysMonitor)、地端深度研究、數位畫布創作、實況劇院、個性化毒舌吐槽、多模態視覺自我認知、11 大擴充模組系統 (Mod System) 與全功能 Web 視覺化主控台。

---

## ✨ 主要能力矩陣 (Core Capabilities)

| 類別 | 功能亮點 |
|---|---|
| 🎼 **微核心架構 (SherryCore)** | **SherryCore 樂團指揮家調度** · **雙層能力註冊隔離 (CapabilityRegistry)** · **安全守衛與顯存協調 (CoreGuard)** · **智慧單一聚合輸出與 2000 字自適應分段 (SmartMessageDispatcher)** · **即時心智快照 (SherryMindState)** |
| 🧠 **智慧 Agent 架構 & 動態思維流** | **DispatchAgent 統合調度** · **LiveThinkingSession 即時動態編輯思考推演 (`message.edit` 零延遲防抖展開)** · **Parallel Agent** 單次規劃並行調度 · **ReAct Agent** 多輪推理工具鏈 · **Action Early Termination** 動作自適應秒結 · 複合指令自動拆解 · 自然語言 @Tag 回應 · 主動插話 (Interject) · 讀空氣 (VibeManager) |
| ⚡ **條件觸發與情報推播 (SPARK Engine)** | **全新 SPARK 模組 (`mod/spark`)** · **4 大動態觸發器** (價格監控 `price` / 定期排程 `schedule` / RSS 資訊源 `rss` / 天氣劇變 `weather`) · **NLP 自然語言意圖解析** · **防過度推播與冷卻風控** · **Web UI 專屬推播管理看板 (`spark.html`)** |
| 📊 **系統效能診斷與採樣 (SysMonitor)** | **全新效能監測模組 (`mod/sys_monitor`)** · **低干擾記憶體緩衝採樣 (CPU/DRAM/GPU/Storage/進程 RSS/IO)** · **每 30 秒批次落盤 JSONL** · **Discord `/sys_status` 炫彩狀態儀表** · **Web UI 即時動態效能趨勢圖 (`sys_monitor.html`)** |
| 🎛️ **現代化交互式 UI (Discord Views)** | **全面升級 Discord View/Button/Select 元件** · **音樂播放器互動控制面板 (`handlers/music/ui.py`)** · **D&D 跑團冒險操作面板 (`mod/dnd/views.py`)** · **實況劇院控制 UI** · **股市卡牌行情面板** · **`/execute` Command Hub 下拉式模組預覽中樞** |
| 🔬 **地端深度研究 (Deep Research)** | **10~30 分鐘級多輪自主深思推演** · **全網實時檢索與交叉文獻比對** · **批判性反思與論點自我驗證** · **論文級繁中學術報告生成** · **Web UI 專屬深度研究推演主控台** |
| 🎨 **藝術與創作工作室 (Creative Studio)** | **雪莉數位畫布繪圖引擎 (`mod/painter`)** (多筆刷系統 / 調色盤 / Gemma 多模態視覺臨摹 / 歷史畫廊) · Stable Diffusion 圖片生成 (`/draw`) · **實況劇院與大螢幕影音播放 (`mod/livestream`)** · YouTube 音樂串流 (`/play`) · 影片指定秒數轉 GIF (`/video`) |
| 💬 **毒舌銳評與吐槽 (Roast Engine)** | **關鍵字自動監聽與機率擲骰判定** · **雪莉名偵探毒舌銳評 / 傲嬌吐槽動態人設** · 頻道冷卻風控機制 · **Web UI 吐槽專屬管理看板 (`roast.html`)** (支援規則配置、風格切換與即時發送) |
| 🌐 **8 大 LLM 推理矩陣** | **全新支援 OpenRouter (`openrouter:`)** · **支援 FreeToken 雲端端點** · 本地主力 (**Ollama / LM Studio / vLLM**) · 雲端主力 (**OpenAI / Gemini / DeepSeek / Claude**) · ClientSession 連線池複用與動態前綴路由 |
| 👁️ **多模態視覺與自我認知** | **多模態圖片深度解析 (`VISION_MODEL`)** · **跨模組自我形象辨識 (`⚠️自我辨識` Lorebook 觸發)** · **引用回覆上下文感知 (Quote Context)** |
| 🛠️ **全能工具與技能庫** | 台美股即時 K 線技術分析 (`/stock`) · 即時天氣預報 (`/weather`) · 網頁深度 RAG 搜尋 (`/search`) · Safebooru/Pixiv 搜圖 · 自然語言與 cron 定時排程提醒 (`/schedule`) · `/execute` 全能工具獨立測試與預覽 |
| 🔌 **11 大擴充模組系統 (Mod System)** | **11 大外掛模組熱插拔**：條件推播 (`spark`) · 系統監控 (`sys_monitor`) · 深度研究 (`deep_search`) · 畫布工作室 (`painter`) · 實況劇院 (`livestream`) · AI 社群哨兵 (`feed_sentinel`) · 美股虛擬量化交易 (`stock_trader`) · D&D 跑團 (`dnd`) · 小遊戲遊樂場 (`game_center`) · 股市卡價追蹤 (`market_plugin`) · 影片 GIF (`video_clipper`) |
| 🎤 **語音系統 (Voice)** | Discord 語音通話 · faster-whisper STT 語音辨識 · XTTS-v2 聲音克隆 TTS · Silero VAD 語音活動檢測 · **語音房與音樂動態實時監控** |
| 🧠 **動態記憶與社交體驗** | UID 長期記憶萃取 · **記憶物理消毒 (`sanitize_memory`) 防錯亂與杜絕幻覺** · 好感度等級追蹤 (`RelationshipManager`) · 每日聊天精華摘要 (`NightlyDigest`) · **長對話話題連續性增強** · **新人加入動態歡迎 (`WelcomeCog`)** |
| 🖥️ **Web 控制台 2.0** | **13 大模組看板**：總覽主控台 (Dashboard) · 即時日誌 · 系統資源 · 任務排程與大腦推理佇列 · 雪莉測試室 · 地端 AI 服務即時監測 · **SPARK 條件推播管理** · **SysMonitor 效能圖表** · 深度研究主控台 · 畫畫工作室 · 雪莉吐槽管理 · 美股炒股看板 · 管理員審計 |
| 🛠️ **獨立終端監控工具** | **`tools/` 專用運維套件**：大腦佇列監控 (`monitor_brain.py`) · 情報哨兵即時追蹤 (`monitor_sentry.py`) · Web UI 狀態監控 (`monitor_web.py`) |
| 🛡️ **資安與審計** | **管理員登入與操作歷史審計 (`./data/admin_audit.jsonl`)** · IP 暴力破解防禦鎖定 · 時序攻擊防禦 · Guest 唯讀模式 · 敏感 Token 掩碼防洩漏 · XSS 防護 |
| 🐧 **容器與跨平台** | **Linux / Docker 容器化完整支援** · **`0.0.0.0` 綁定自動降級** · **`.bat` / `.sh` 跨平台快速啟動與重啟腳本 (`start_sherry_tabs.bat` / `open_web_ui.bat`)** |

---

## 🧠 核心 Agent 架構與微核心調度機制

雪莉採用 **SherryCore 中央調度微核心** 統籌所有生命週期，並結合 **DispatchAgent** 與 **LiveThinkingSession** 達成毫秒級響應與流暢思考視覺反饋：

```mermaid
flowchart TD
    UserMsg["群友訊息 / 圖片 @雪莉 / Discord 指令"] --> SherryCore["🎼 SherryCore 中央調度指揮官"]
    
    subgraph CoreGuardLayer [CoreGuard 安全守衛與狀態]
        SherryCore --> RateLimit{"Rate Limit 檢查"}
        RateLimit -->|超標| QuickReject["友好冷卻提示秒回"]
        RateLimit -->|通過| CapabilityCheck["CapabilityRegistry 雙層能力過濾"]
    end
    
    CapabilityCheck --> VisionCheck{"是否夾帶圖片？"}
    VisionCheck -->|是| VisionModel["VISION_MODEL 視覺解析與特徵辨識"]
    VisionCheck -->|否| QuoteContext["擷取引用回覆上下文"]
    VisionModel --> QuoteContext
    
    QuoteContext --> DispatchAgent["🧠 DispatchAgent 統合調度器"]
    
    subgraph ExecutionModes [調度推演模式]
        DispatchAgent -.->|PARALLEL 模式| ParallelPlan["單次規劃 + asyncio.gather 並行執行"]
        DispatchAgent -.->|REACT 模式| ReActLoop["思考 ➔ 行動 ➔ 觀察 迭代推理"]
        ParallelPlan -.-> ActionEarlyTerm{"所有工具皆 DIRECT_UI 且無閒聊？"}
        ActionEarlyTerm -->|是| DirectFinish["⚡ 行為秒結 (Action Early Termination)"]
        ActionEarlyTerm -->|否| PersonaSynth["結合三層 Prompt + Lorebook 合成人格回覆"]
        ReActLoop --> PersonaSynth
    end
    
    DispatchAgent --> LiveThinking["⚡ LiveThinkingSession 即時思維鏈動態展開 (message.edit)"]
    PersonaSynth --> Dispatcher["📮 SmartMessageDispatcher 智慧聚合發送與 2000 字分段"]
    DirectFinish --> Dispatcher
    Dispatcher --> Output["發送至 Discord 頻道 / Web UI"]
```

### 核心調度亮點

1. **SherryCore 中央調度微核心 (`core/orchestrator.py`)**：
   - **全生命週期管控**：統一輸入 `SherryRequest` 與輸出 `SherryResponse`，維護即時心智快照 (`SherryMindState`)。
   - **雙層能力註冊隔離 (`CapabilityRegistry`)**：將日常 AI 工具（`SEARCH`, `STOCK`, `DRAW`, `MUSIC`, `SCHEDULE`, `WEATHER`, `PIXIV`）與專題 Mod 外掛（`DND`, `MARKET`, `SPARK`, `DEEP_SEARCH` 等）進行隔離，避免 Prompt 污染並節省 15% 以上 Token 開銷。
   - **智慧單一聚合分發 (`SmartMessageDispatcher`)**：自動處理 Discord 2000 字限制與 Embed/附件聚合，杜絕分片碎片化洗版。
2. **LiveThinking 即時動態思維鏈展開 (`handlers/social/streaming_thinking.py`)**：
   - 使用 Discord `message.edit` 原地動態更新技術，即時呈現意圖解析、工具發動與推演步驟。
   - 採用 **非同步節流防抖 (Debounce)** 設計，確保推論速度 0 阻塞、0 延遲，大幅提升使用者視覺反饋與互動感。
3. **DispatchAgent 統合調度 (`handlers/social/dispatch_agent.py`)**：
   - 整合並行規劃 (Parallel) 與多步推理 (ReAct) 雙重引擎，支援行為自適應終止 (Action Early Termination)，操作秒結無多餘廢話。

---

## 🚀 快速開始

### 環境需求

- **作業系統**：Windows 10/11 或 Linux (Ubuntu 20.04+ / Debian 11+ / Docker)
- **Python**：3.10 ~ 3.12
- **推論後端**：[Ollama](https://ollama.com) / LM Studio / vLLM 本地運行中（或外部 OpenAI / Gemini / DeepSeek / Claude / OpenRouter API Key）
- **多媒體支援**：FFmpeg（音樂串流與 GIF 轉檔需用）
- **GPU 算力**（建議）：NVIDIA CUDA GPU（供語音 STT/TTS、本地大模型與 Stable Diffusion 圖片生成）

### 安裝步驟

```bash
# 1. 複製專案
git clone https://github.com/YuuuuutoOuO/Sherry-An-AI-Companion-Platform.git
cd Sherry-An-AI-Companion-Platform

# 2. 建立並啟用虛擬環境
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / Docker

# 3. 安裝相依套件
pip install -r requirements.txt
```

### 設定 `.env`

於專案根目錄建立 `.env` 檔案，範例如下：

```env
# ── Discord 必要設定 ──────────────────────────────────────
DISCORD_BOT_TOKEN=你的_DISCORD_BOT_TOKEN
ADMIN_USER_IDS=[425311733503229972]

# ── Agent 架構與調度模式 ─────────────────────────────────
AGENT_DISPATCH_MODE=PARALLEL      # PARALLEL (預設並行) | REACT (多步推理) | CLASSIC (傳統)
ACTION_EARLY_TERMINATION_ENABLED=True
LIVE_THINKING_EDIT=True           # 是否啟用 Discord 原地動態思維鏈展開

# ── 本地 LLM 後端 Base URLs ──────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434
LMS_BASE_URL=http://localhost:1234/v1
VLLM_BASE_URL=http://localhost:8000/v1

# ── 雲端 API Keys (選填，填入即解鎖對應雲端模型) ─────────
OPENAI_API_KEY=
GEMINI_API_KEY=
DEEPSEEK_API_KEY=
CLAUDE_API_KEY=
OPENROUTER_API_KEY=

# ── 工具 API Keys (選填) ──────────────────────────────────
OPENWEATHERMAP_API_KEY=          # 天氣查詢
FINNHUB_API_KEY=                 # 美股即時報價與技術指標

# ── Web 控制台安全性設定 ─────────────────────────────────
WEB_UI_PASSWORD=sherry123        # Web UI 管理員登入密碼 (帳號預設為 admin)
WEB_UI_PORT=8763
WEB_UI_HTTPS=True
```

### 拉取常用模型

```bash
# 對話與推理模型 (Ollama)
ollama pull krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS
ollama pull qwen2.5:3b

# 視覺辨識模型 (多模態圖片理解)
ollama pull minicpm-v
# 或 ollama pull qwen2-vl-7b-instruct
```

### 啟動雪莉

* **Windows**:
  ```bash
  python main.py
  # 或直接雙擊 start_sherry_tabs.bat / restart_sherry.bat
  ```
* **Linux / Docker**:
  ```bash
  chmod +x restart_sherry.sh update_and_restart.sh
  ./restart_sherry.sh
  ```

啟動完成後，可開啟瀏覽器造訪 Web 後台控制台（或雙擊 `open_web_ui.bat` / `open_browser.bat`）：  
🔗 **[https://localhost:8763](https://localhost:8763)**  
（預設帳號：`admin`，密碼：同 `.env` 中之 `WEB_UI_PASSWORD`）

---

## 🔌 11 大擴充模組系統 (Mod System)

雪莉採用鬆散耦合的 Plugin 架構，系統於啟動時自動掃描 `./mod/` 目錄下所有包含 `cog.py` 的子資料夾並嘗試載入。**任一插件出錯絕不影響雪莉核心運作！**

```text
mod/
├── spark/           # ⚡ SPARK 條件觸發與主動情報探勘推播引擎 (全新 v1.8.0)
├── sys_monitor/     # 📊 系統效能診斷與低干擾記憶體採樣監控 (全新 v1.8.0)
├── deep_search/     # 🔬 地端 LLM / SLM 多輪自主深度研究主控台
├── painter/         # 🎨 雪莉數位畫布繪圖引擎與多筆刷調色盤
├── livestream/      # 📺 實況劇院與大螢幕影音播放系統
├── feed_sentinel/   # 📰 AI 社群情報哨兵與主動話題探勘
├── stock_trader/    # 📈 雪莉美股虛擬量化交易系統
├── video_clipper/   # 🎬 YouTube 影片轉 GIF 動圖製作工具
├── dnd/             # 🎲 D&D TRPG AI 地下城主跑團遊戲引擎
├── game_center/     # 🎮 小遊戲遊樂場 (21點/猜拳/踩地雷/海龜湯/AI對弈)
└── market_plugin/   # 📊 股市技術晨報、AI晚報、PTCG卡價與ETF溢折價
```

### 模組詳細介紹與指令清單

#### 1. ⚡ `spark` — 條件觸發與主動情報探勘推播引擎 (NEW in v1.8.0)
- **功能**：具備 4 大動態檢查器（股票/加密貨幣價格 `price`、定期提醒與 Cron `schedule`、RSS 即時新訊 `rss`、氣溫與降雨劇變 `weather`）。支援 NLP 意圖分析建立推播、自動去重防洗版機制與 Web UI 視覺化規則管理。
- **指令與 Web 支援**：
  - `/spark_add` / `/spark_list` / `/spark_delete` / `/spark_toggle`：推播觸發器管理。
  - **Web UI Spark 看板 (`spark.html`)**：即時查看所有觸發器狀態、手動立即檢查與一鍵新增規則。

#### 2. 📊 `sys_monitor` — 系統效能診斷與採樣監控 (NEW in v1.8.0)
- **功能**：高頻記憶體緩衝採樣（CPU、RAM、NVIDIA GPU 負載/溫度/VRAM、硬碟、雪莉進程 CPU 與記憶體 RSS、磁碟寫入），每 30 秒批次落盤單行 JSONL，達到零頻繁開關檔的極致低干擾。
- **指令與 Web 支援**：
  - `/sys_status`：在 Discord 即時顯示伺服器硬體與主程式資源狀態 Embed。
  - **Web UI 效能監控面板 (`sys_monitor.html`)**：提供動態時間序列折線圖與健康狀態診斷。

#### 3. 🔬 `deep_search` — 深度研究主控台
- **功能**：支援 10~30 分鐘級地端 LLM / SLM 多輪自主思考推演、全網搜尋、文獻精讀、批判性反思與論文級研究報告產出，並可在 Web UI 即時觀看思考進度與思維樹！
- **指令**：
  - `/deep_search query:<研究主題> depth:<深/淺>`：發起深度研究任務。

#### 4. 🎨 `painter` — 畫畫工作室 (數位畫布繪圖引擎)
- **功能**：雪莉專屬數位畫布繪圖引擎 (`VirtualCanvas`)、多筆刷系統 (`BrushEngine`)、調色盤管理 (`PaletteManager`)、指令直譯器 (`CommandInterpreter`)，支援 Gemma 多模態視覺臨摹、歷史畫廊與自定義手繪生成。

#### 5. 📺 `livestream` — 實況劇院與影音播放
- **功能**：支援 Discord 點播 YouTube 與本地影片至實況大螢幕（獨立伺服器 PORT 8764），具備選集連播、現代化 View 控制介面與播放器伺服器。

#### 6. 📰 `feed_sentinel` — AI 社群情報哨兵 & 主動話題探勘
- **功能**：每日 08:00~23:00 每 15 分鐘自動巡邏 RSS/情報來源，自動去重並由 BotBrain 提煉 3 點繁中重點與雪莉犀利銳評，主動推播至 Discord 頻道，解決群組冷場！
- **指令**：
  - `/sentinel_fetch`：手動立即觸發情報哨兵抓取與推播。

#### 7. 📈 `stock_trader` — 雪莉美股虛擬量化交易系統
- **功能**：自帶 $10,000 美金虛擬本金，美股交易時段 (台灣時間 23:00~04:00) 自動巡邏，03:50 臨關盤強制看盤。結合 AI 策略分析師 (`TradeStrategist`) 與演算法交易員 (`AlgoTrader`) 執行自動交易與風控，成交自動推播至頻道。
- **指令與 Web 支援**：
  - `!stock_status`：查看當前美股持倉、資產淨值與收益率。
  - **Web UI 炒股專屬看板**：提供視覺化未實現損益、AI 交易決策思路日誌、一鍵「刷新行情損益」與「手動評估看盤」。

#### 8. 🎬 `video_clipper` — YouTube 影片轉 GIF 動態截取
- **功能**：快速將 YouTube 指定時間片段轉換為高畫質 GIF 動圖發送至頻道。
- **指令**：
  - `/video url:<網址> start_time:<開始時間> end_time:<結束時間>`  
    *範例：`/video url:https://youtu.be/xxx start_time:1:30 end_time:1:45`*

#### 9. 🎲 `dnd` — D&D TRPG AI 地下城主跑團遊戲引擎
- **功能**：完整整合地下城主 AI (DM)、角色卡、屬性骰判定 (優勢/劣勢)、互動式 Discord View 按鈕面板與動態世界模擬。
- **指令**：
  - `/dnd_join`：創建專屬 D&D 冒險者角色卡。
  - `/dnd_session`：開啟/關閉本頻道的 D&D 自動遊戲模式。
  - `/dnd_action`：向 DM 提出冒險動作描述。
  - `/dnd_roll`：進行 D&D 擲骰判定 (支援 d20, d6, 優勢/劣勢)。
  - `/dnd_status`：查看角色卡狀態與生命值。
  - `/dnd_inventory`：查看背包裝備與物品。
  - `/dnd_world`：查看世界時間與陣營勢力狀態。

#### 10. 🎮 `game_center` — 小遊戲遊樂場
- **功能**：支援 21 點撲克 (`blackjack`)、猜拳大對決 (`rps`)、踩地雷 (`minesweeper`)、海龜湯 (`turtle_soup`)、井字棋 (`tic_tac_toe`)、密室逃脫 (`escape_room`)，並結合地端 LLM 進行雪莉名偵探個性對弈與調侃！

#### 11. 📊 `market_plugin` — 股市與卡價追蹤
- **功能**：技術指標晨報 (08:00)、Gemini AI 晚間分析 (21:30)、PTCG 卡片即時報價 (10:00)、ETF 溢折價監控 (盤中每 10 分鐘)。具備互動式 View 操作面板。
- **指令**：
  - `/market_raw` / `!raw`：手動產出技術指標報告。
  - `/market_add` / `/market_remove` / `/market_list`：股票追蹤清單管理。
  - `/card_add` / `/card_remove` / `/card_list` / `/card_update`：PTCG 卡片追蹤與爬蟲更新。

---

## 💬 常用核心互動與指令指南

雪莉全面支援 **自然語言對話** 與 **Discord 斜線指令 (`/`)** 雙軌並行：

```text
@雪莉 幫我播 Yoasobi 的 Idol，順便找一張雪莉的可愛圖片！   # 複合指令自動拆解並行處理
@雪莉 查一下 2330 走勢與最新新聞                          # 即時網頁 RAG + 台股 K 線
@雪莉 10分鐘後提醒我出門                                  # 自然語言排程
@雪莉 (夾帶圖片) 妳覺得這張圖好看嗎？                      # 多模態圖片視覺理解 + 自我認知
```

| 類別 | 指令 / 操作 | 說明 |
|---|---|---|
| 📖 **全能幫助** | `/help` | 叫出 Discord Select Menu 分類式全功能指南 |
| 💬 **指揮中心** | `/execute intent:<TOOL> query:<內容>` | Command Hub 互動選單預覽各模組面板或強制執行特定工具 |
| 📊 **系統監控** | `/sys_status` | 即時顯示 CPU / RAM / GPU / 雪莉主程式資源佔用 Embed |
| ⚡ **靈動推播** | `/spark_add` · `/spark_list` · `/spark_toggle` | 管理價格、RSS、天氣與排程等條件觸發推播規則 |
| 🎵 **留聲機音樂** | `/play` | 播放音樂並呼叫全功能互動式 View 控制器（暫停/切歌/音量/清單） |
| 🎤 **語音通話** | `/join_voice` · `/leave_voice` | 讓雪莉加入/離開目前語音頻道進行即時語音交談 |
| 🧠 **記憶管理** | `/memory_show` · `/memory_clear` | 查看或清除雪莉對自己的長期記憶檔案 (僅自己可見) |
| 👥 **氛圍感知** | `/vibe_show` · `/vibe_clear` | 查看或重設頻道當前熱門話題與氛圍狀態 |
| ⚙️ **系統維運** | `/restart` · `/reload` · `/stats` · `/config` | 熱重啟指定模組、查看系統統計儀表板與伺服器端配置 |

---

## 🖥️ Web UI 視覺化控制台 2.0

雪莉內建基於 **Quart** 的高質感現代化 Web 管理後台 (`https://localhost:8763`)，包含以下 13 大核心分頁：

1. **📊 總覽主控台 (Dashboard)**：實時監控 CPU / GPU / RAM / 網路資源，精確比對「🤖 運作中的雪莉 Commit」與「📦 本地最新 Git 代碼」，支援一鍵 Git Pull + 重啟更新。
2. **📋 即時日誌 (Live Logs)**：支援依事件類型 (CHAT, 插話, ERROR) 與分頁過濾，具備 Guest 日誌白名單防護。
3. **💻 系統資源 (System Metrics)**：硬體資源即時動態圖表 (Chart.js)。
4. **⏰ 任務排程 (Tasks & LLM Queue)**：即時掌握排程提醒任務與 **「🧠 大腦即時推理佇列 (LLM Task Queue)」** 優先級與深度。
5. **💬 雪莉測試室 (Chat Playground)**：免開 Discord 直接在瀏覽器與雪莉對話，**支援視覺化展開 ReAct / Parallel 思考過程標籤與 Tool Invocation**。
6. **📡 地端 AI 服務 (Local AI Monitor)**：每 5 秒自動診斷 Ollama (`11434`)、LM Studio (`1234`)、vLLM (`8000`)、Stable Diffusion (`7860`) 之連線狀態、Ping 延遲與掛載模型。
7. **⚡ SPARK 條件推播看板 (Spark Manager)**：視覺化觸發器清單、即時手動檢查觸發、支援價格/RSS/天氣/排程條件設定。
8. **📈 系統效能診斷 (Sys Monitor)**：獨立高頻時間序列採樣歷史圖表，掌握雪莉進程資源佔用與 GPU 溫度變化。
9. **🔬 深度研究控制台 (Deep Research)**：即時查看多輪深度主題研究推演進度、檢索來源與論文級報告成果。
10. **🎨 畫畫工作室 (Painter Studio)**：視覺化畫布預覽、筆刷調色盤即時調試與雪莉歷史創作畫廊。
11. **💬 雪莉吐槽管理 (Roast Manager)**：關鍵字監聽規則配置、命中機率與冷卻調節、名偵探吐槽風格切換與即時測試。
12. **📈 雪莉虛擬炒股 (Stock Trader)**：即時美股持倉、未實現損益、AI 決策歷程看板，支援一鍵手動評估看盤。
13. **🛡️ 管理員審計 (Admin Audit Log)**：完整記錄登入 IP、來源網路標籤 (Localhost / Radmin / LAN)、裝置作業系統與各項操作時間軸歷程。

---

## 📁 專案目錄結構

```text
Sherry-An-AI-Companion-Platform/
├── core/                  # 🎼 SherryCore 中央調度微核心 (Orchestrator, Registry, Guard, State)
├── data/                  # 儲存資料庫、JSONL 日誌、記憶與排程
├── file/                  # 人設定義 (AI_personality.py) 與 Soul 人格檔
├── handlers/              # 核心業務邏輯 (Cog Extensions)
│   ├── admin/             # 管理員指令與伺服器設定
│   ├── bot_brain/         # 大腦推理引擎、優先權佇列與健康檢查
│   ├── executor/          # /execute 指令中樞預覽版 (Command Hub)
│   ├── heartbeat/         # 心跳定時主動任務
│   ├── memory/            # 長期記憶萃取、記憶物理消毒與每日摘要
│   ├── music/             # 留聲機音樂播放引擎與互動 View 控制面板
│   ├── roast/             # 雪莉毒舌銳評與吐槽子系統
│   ├── scheduler/         # 自然語言定時提醒與 Cron 排程
│   ├── social/            # DispatchAgent 調度器、LiveThinking 動態思維流與工具庫
│   │   ├── actions/       # 動作定義與執行器
│   │   └── tools/         # Chat, Draw, Image, Music, Schedule, Search, Stock, Weather 工具庫
│   ├── vibe_manager/      # 頻道熱門話題與氛圍追蹤
│   ├── voice/             # 語音通話 (STT / TTS / VAD)
│   ├── web_explorer/      # 網頁內容檢索與提取
│   ├── help_cog.py        # /help 分類幫助選單
│   └── welcome_cog.py     # 新人加入動態歡迎
├── mod/                   # 11 大擴充外掛模組 (可插拔)
│   ├── spark/             # ⚡ SPARK 條件觸發推播與情報探勘引擎
│   ├── sys_monitor/       # 📊 系統資源採樣與效能診斷監控
│   ├── deep_search/       # 🔬 地端 LLM / SLM 深度研究主控台
│   ├── painter/           # 🎨 雪莉數位畫布繪圖引擎與工作室
│   ├── livestream/        # 📺 實況劇院與影音播放系統
│   ├── feed_sentinel/     # 📰 AI 社群情報哨兵
│   ├── stock_trader/      # 📈 美股虛擬量化交易系統
│   ├── video_clipper/   # 🎬 YouTube 轉 GIF 工具
│   ├── dnd/               # 🎲 D&D TRPG 跑團系統
│   ├── game_center/       # 🎮 小遊戲遊樂場
│   └── market_plugin/     # 📊 股市與卡價追蹤
├── tools/                 # 🛠️ 獨立終端監控運維工具 (monitor_brain, monitor_sentry, monitor_web)
├── web_ui/                # Quart Web 管理後台 (HTML/CSS/JS)
├── Docs/                  # 專案詳細技術架構與歷史版本文件
├── main.py                # 程式主入口
├── settings.py            # 全域設定中心
└── requirements.txt       # Python 相依套件清單
```

---

## 📋 v1.8.0 更新日誌 (Release Notes)

### 🎼 1. 全新架構：SherryCore 中央調度微核心 (`core/`)
- **微核心架構升級**：建立統一請求生命週期標準 (`SherryRequest` -> `SherryResponse`)，以 Orchestrator 作為樂團指揮家統籌全生命週期。
- **雙層能力註冊隔離 (`CapabilityRegistry`)**：將精選日常 AI 工具與專題 Mod 外掛嚴格隔離，大幅避免 Prompt 污染並節省 15% 以上 Token 開銷。
- **安全守衛與智慧發送 (`CoreGuard` & `SmartMessageDispatcher`)**：強化頻率限制與顯存協調，智慧聚合 Embed 與附件，精準自適應處理 2000 字分段發送。

### ⚡ 2. 全新模組：SPARK 條件觸發推播與情報探勘引擎 (`mod/spark`)
- **4 大動態觸發器**：支援股價/加密貨幣行情 (`price`)、定時與排程事項 (`schedule`)、RSS 即時新聞巡邏 (`rss`) 與氣象劇變監測 (`weather`)。
- **智慧 NLP 意圖解析**：支援以自然語言直接建立推播規則，具備智慧冷卻與防洗版風控。
- **Web UI 專屬管理面板 (`spark.html`)**：支援視覺化啟停、新增與立即檢查觸發器。

### 📊 3. 全新模組：SysMonitor 系統效能診斷與採樣監控 (`mod/sys_monitor`)
- **低干擾零頻繁開關檔架構**：記憶體緩衝高頻採樣，每 30 秒批次落盤 JSONL，精確監控 CPU、DRAM、NVIDIA GPU、主程式 RSS 與磁碟寫入。
- **Discord `/sys_status` 狀態儀表**：一鍵輸出伺服器即時負載與硬體健康狀態。
- **Web UI 效能趨勢圖 (`sys_monitor.html`)**：提供即時時間序列圖表與診斷日誌。

### 🧠 4. DispatchAgent 統合調度與 LiveThinking 即時動態思維流
- **統合調度 Agent 重構**：合併重構 `parallel_agent` 與 `react_agent` 為強大的 `dispatch_agent.py`，保持極致模組清晰度。
- **LiveThinking 動態思考展開 (`streaming_thinking.py`)**：以 Discord `message.edit` 原地動態更新技術模擬現代 AI 逐階段展開思考的視覺反饋，搭配非同步節流防抖，0 延遲且 0 阻塞。
- **可觀測性全面強化 (`observability.py`)**：全鏈路追蹤 Trace ID、耗時度量與結構化審計。

### 🎛️ 5. Discord 現代化 View 交互式介面全面升級
- **音樂留聲機面板 (`handlers/music/ui.py`)**：全新設計播放、暫停、切歌、音量調節、單曲循環與播放清單 View 按鈕與選單。
- **D&D 跑團冒險互動 (`mod/dnd/views.py`)**：將 TRPG 動作、擲骰判定與角色卡檢視完整轉化為 Discord View 交互按鈕。
- **Command Hub 預覽中樞 (`handlers/executor/cog.py`)**：升級 `/execute` 指令，支援下拉選單動態預覽各模組介面與自訂意圖測試。

### 🛠️ 6. 獨立終端監控與運維工具組 (`tools/`)
- **新增 3 大終端專用監控腳本**：`monitor_brain.py` (大腦推理佇列實時深度)、`monitor_sentry.py` (情報哨兵巡邏狀態)、`monitor_web.py` (Web UI 與連線狀態)。
- **一鍵多分頁啟動腳本**：新增 `start_sherry_tabs.bat`、`open_browser.bat`、`open_livestream.bat` 等運維輔助工具。

### 🌟 7. 人設深化與長對話話題連續性增強
- **話題連續性優化**：調整 Prompt 人設架構與上下文記憶檢索策略，讓雪莉在多輪對話中具備更強的話題延續性與自然語氣。
- **靈動吐槽精準度提升**：優化 Roast 觸發規則與名偵探人設注入。

### 🌐 8. Web UI 2.0 擴充至 13 大控制面板
- **全面整合 SPARK 與 SysMonitor 模組**：Web 導航列無縫嵌入全新管理分頁，提供更完整的視覺化運維體驗。
- **修復多項前端參數缺失與樣式適配**。

---

## 📚 歷史版本技術文件 (Historical Versions)

所有歷史版本的詳細規格與更新日誌已歸檔至 `Docs/` 目錄：

| 版本 | 文件連結 | 核心里程碑 |
|---|---|---|
| **v1.7.0** | [Docs/README_V1.7.0.md](./Docs/README_V1.7.0.md) | 地端深度研究 (Deep Research)、數位畫布繪圖 (Painter)、名偵探毒舌銳評 (Roast)、實況劇院、OpenRouter 支援 |
| **v1.6.0** | [Docs/README_V1.6.0.md](./Docs/README_V1.6.0.md) | Parallel/ReAct 雙智能架構、多模態視覺與自我認知、6大外掛模組、記憶物理消毒 |
| **v1.5.0** | [Docs/README_V1.5.0.md](./Docs/README_V1.5.0.md) | Linux/Docker 容器化相容、vLLM 推理引擎支援、.env 優先級、NumPy 2.x 相容 |
| **v1.4.0** | [Docs/README_V1.4.0.md](./Docs/README_V1.4.0.md) | 大腦即時推理佇列、地端 AI 服務監測、管理員審計落盤、延遲追蹤與 Dashboard |

---

## 🔐 Discord Bot 權限與 Intents 設定

在 [Discord Developer Portal](https://discord.com/developers/applications) 開啟以下設定：

- **Privileged Gateway Intents**：
  - ✅ `MESSAGE CONTENT INTENT` (讀取訊息以進行對話與指令解析)
  - ✅ `SERVER MEMBERS INTENT` (新人加入歡迎系統與成員辨識)
- **OAuth2 URL Generator Scopes**：
  - `bot` · `applications.commands`
- **Bot Permissions**：
  - Send Messages · Read Message History · Add Reactions · Embed Links · Attach Files · Connect · Speak · Use Slash Commands

---

## 📄 License

本專案採用 [MIT License](LICENSE) 授權開源。