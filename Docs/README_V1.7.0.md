# 🤖 橘雪莉 — Discord AI Companion & Agent Platform (v1.7.0)

> 📌 **本文件為歷史版本存檔 (Archive)**。若要查看最新版規格與文件，請參閱根目錄 [README.md](../README.md)。

一個擁有獨立靈魂、會讀空氣、懂得自我反思、精通深度探研與數位藝術創作的 Discord 虛擬實況群友與全能 AI 代理人。  
基於 **discord.py 2.x** + **ReAct / Parallel Agent 雙智能推理架構** + **8 大 LLM 推理引擎 (Ollama / LM Studio / vLLM / OpenRouter / OpenAI / Gemini / DeepSeek / Claude)** + **asyncio 優先權佇列**，支援 Linux / Docker 容器化與 Windows 跨平台部署，具備地端深度研究、數位畫布創作、實況劇院、個性化毒舌吐槽、多模態視覺自我認知、豐富擴充模組系統 (Mod System) 與全功能 Web 視覺化主控台。

---

## ✨ 主要能力矩陣 (Core Capabilities)

| 類別 | 功能亮點 |
|---|---|
| 🧠 **智慧 Agent 架構** | **Parallel Agent** 單次規劃並行調度 · **ReAct Agent** 多輪推理工具鏈 · **Action Early Termination** 動作自適應秒結 · 複合指令自動拆解 · 自然語言 @Tag 回應 · 主動插話 (Interject) · 讀空氣 (VibeManager) · 心跳定時主動任務 (Heartbeat) |
| 🔬 **地端深度研究 (Deep Research)** | **10~30 分鐘級多輪自主深思推演** · **全網實時檢索與交叉文獻比對** · **批判性反思與論點自我驗證** · **論文級繁中學術報告生成** · **Web UI 專屬深度研究推演主控台** |
| 🎨 **藝術與創作工作室 (Creative Studio)** | **雪莉數位畫布繪圖引擎 (`mod/painter`)** (多筆刷系統 / 調色盤 / Gemma 多模態視覺臨摹 / 歷史畫廊) · Stable Diffusion 圖片生成 (`/draw`) · **實況劇院與大螢幕影音播放 (`mod/livestream`)** · YouTube 音樂串流 (`/play`) · 影片指定秒數轉 GIF (`/video`) |
| 💬 **毒舌銳評與吐槽 (Roast Engine)** | **關鍵字自動監聽與機率擲骰判定** · **雪莉名偵探毒舌銳評 / 傲嬌吐槽動態人設** · 頻道冷卻風控機制 · **Web UI 吐槽專屬管理看板 (`roast.html`)** (支援規則配置、風格切換與即時發送) |
| 🌐 **8 大 LLM 推理矩陣** | **全新支援 OpenRouter (`openrouter:`)** · **支援 FreeToken 雲端端點** · 本地主力 (**Ollama / LM Studio / vLLM**) · 雲端主力 (**OpenAI / Gemini / DeepSeek / Claude**) · ClientSession 連線池複用與動態前綴路由 |
| 👁️ **多模態視覺與自我認知** | **多模態圖片深度解析 (`VISION_MODEL`)** · **跨模組自我形象辨識 (`⚠️自我辨識` Lorebook 觸發)** · **引用回覆上下文感知 (Quote Context)** |
| 🛠️ **全能工具與技能庫** | 台美股即時 K 線技術分析 (`/stock`) · 即時天氣預報 (`/weather`) · 網頁深度 RAG 搜尋 (`/search`) · Safebooru/Pixiv 搜圖 · 自然語言與 cron 定時排程提醒 (`/schedule`) · `/execute` 工具獨立測試 |
| 🔌 **擴充模組系統 (Mod System)** | **9 大外掛模組熱插拔**：深度研究 (`deep_search`) · 畫布工作室 (`painter`) · 實況劇院 (`livestream`) · AI 社群哨兵 (`feed_sentinel`) · 美股虛擬量化交易 (`stock_trader`) · D&D 跑團 (`dnd`) · 小遊戲遊樂場 (`game_center`) · 股市卡價追蹤 (`market_plugin`) · 影片 GIF (`video_clipper`) |
| 🎤 **語音系統 (Voice)** | Discord 語音通話 · faster-whisper STT 語音辨識 · XTTS-v2 聲音克隆 TTS · Silero VAD 語音活動檢測 · **語音房與音樂動態實時監控** |
| 🧠 **動態記憶與社交** | UID 長期記憶萃取 · **記憶物理消毒 (`sanitize_memory`) 防錯亂與杜絕幻覺** · 好感度等級追蹤 (`RelationshipManager`) · 每日聊天精華摘要 (`NightlyDigest`) · **新人加入動態歡迎 (`WelcomeCog`)** |
| 🖥️ **Web 控制台 2.0** | **總覽主控台 Dashboard** (CPU/RAM/GPU/Network + Git 双版本比對) · **地端 AI 服務即時監測** (Ollama/LMS/vLLM/SD) · **深度研究主控台** · **畫畫工作室** · **雪莉吐槽管理** · **美股炒股看板** · **模組管理 (Mod Manager)** · **大腦推理佇列 (LLM Task Queue)** |
| 🛡️ **資安與審計** | **管理員登入與操作歷史審計 (`./data/admin_audit.jsonl`)** · IP 暴力破解防禦鎖定 · 時序攻擊防禦 · Guest 唯讀模式 · 敏感 Token 掩碼防洩漏 · XSS 防護 |
| 🐧 **容器與跨平台** | **Linux / Docker 容器化完整支援** · **`0.0.0.0` 綁定自動降級** · **`.sh` / `.bat` 跨平台重啟與自動更新守護腳本** |

---

## 🧠 核心 Agent 架構與調度機制

雪莉具備業界領先的三模態動態調度架構，可透過 `.env` 的 `AGENT_DISPATCH_MODE` 或 Web UI 即時切換：

```mermaid
flowchart TD
    UserMsg["群友訊息 / 圖片 @雪莉"] --> RateLimit{"Rate Limiter 檢查"}
    RateLimit -->|"允許"| VisionCheck{"是否夾帶圖片？"}
    VisionCheck -->|"是"| VisionModel["VISION_MODEL 視覺解析與特徵辨識"]
    VisionCheck -->|"否"| QuoteContext["擷取引用回覆上下文"]
    VisionModel --> QuoteContext
    QuoteContext --> DispatchMode{"AGENT_DISPATCH_MODE"}
    
    DispatchMode -->|"PARALLEL (預設推薦)"| ParallelAgent["Parallel Agent (單次規劃 + 並行執行 + 動作秒結)"]
    DispatchMode -->|"REACT"| ReActAgent["ReAct Agent (思考 → 行動 → 觀察 迭代推理)"]
    DispatchMode -->|"CLASSIC"| ClassicRouter["經典硬路由 (正則快路徑 → LLM 分類器)"]
    
    ParallelAgent --> PersonaSynth["結合三層 Prompt + Lorebook 合成人格回覆"]
    ReActAgent --> PersonaSynth
    ClassicRouter --> PersonaSynth
    PersonaSynth --> Queue["BotBrain PriorityQueue 推理佇列"]
    Queue --> DiscordReply["發送回覆至 Discord / Web UI"]
```

1. **Parallel Agent (並行任務調度器，預設模式)**：
   - 採用單次規劃（Single-Pass Inference）由 `PARALLEL_PLANNER_MODEL` 輸出結構化任務 JSON 與對話抑制標記 (`needs_persona_reply`)。
   - 使用 `asyncio.gather` 並行同時發動互不依賴的工具（例如：同時搜圖 + 查天氣 + 查台股走勢）。
   - 支援 **行為自適應終止 (Action Early Termination)**：若發動的工具全為 DIRECT_UI（如點歌、搜圖、畫圖）且無對話需求，動作完成即刻秒結，不產生多餘廢話文字。
2. **ReAct Agent (多輪推理與工具鏈模式)**：
   - 採用經典 Reasoning + Acting「思考→行動→觀察」迭代迴圈（`REACT_MAX_STEPS`），支援 `<thought>`、`<action>`、`<action_input>`、`<final_answer>`。
   - 適用於複雜多步驟相依推理（如：先搜尋特定新聞，再根據搜尋結果總結並畫圖）。
3. **Classic Agent (經典單意圖硬路由模式)**：
   - Phase 1 正則極速命中 → Phase 2 分類器神經判斷 → 專用工具執行。

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
  # 或直接雙擊 restart_sherry.bat / start_sherry_tabs.bat
  ```
* **Linux / Docker**:
  ```bash
  chmod +x restart_sherry.sh update_and_restart.sh
  ./restart_sherry.sh
  ```

啟動完成後，可手動開啟瀏覽器造訪 Web 後台控制台（或直接雙擊 `open_web_ui.bat` / `open_browser.bat`）：  
🔗 **[https://localhost:8763](https://localhost:8763)**  
（預設帳號：`admin`，密碼：同 `.env` 中之 `WEB_UI_PASSWORD`）

---

## 🔌 擴充模組系統 (Mod System)

雪莉採用鬆散耦合的 Plugin 架構，系統於啟動時自動掃描 `./mod/` 目錄下所有包含 `cog.py` 的子資料夾並嘗試載入。**任一插件出錯絕不影響雪莉核心運作！**

```text
mod/
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

#### 1. 🔬 `deep_search` — 深度研究主控台
- **功能**：支援 10~30 分鐘級地端 LLM / SLM 多輪自主思考推演、全網搜尋、文獻精讀、批判性反思與論文級研究報告產出，並可在 Web UI 即時觀看思考進度與思維樹！
- **指令**：
  - `/deep_search query:<研究主題> depth:<深/淺>`：發起深度研究任務。

#### 2. 🎨 `painter` — 畫畫工作室 (數位畫布繪圖引擎)
- **功能**：雪莉專屬數位畫布繪圖引擎 (`VirtualCanvas`)、多筆刷系統 (`BrushEngine`)、調色盤管理 (`PaletteManager`)、指令直譯器 (`CommandInterpreter`)，支援 Gemma 多模態視覺臨摹、歷史畫廊與自定義手繪生成。

#### 3. 📺 `livestream` — 實況劇院與影音播放
- **功能**：支援 Discord 點播 YouTube 與本地影片至實況大螢幕（獨立伺服器 PORT 8764），具備選集連播、影音狀態即時控制與播放器伺服器。

#### 4. 📰 `feed_sentinel` — AI 社群情報哨兵 & 主動話題探勘
- **功能**：每日 08:00~23:00 每 15 分鐘自動巡邏 RSS/情報來源，自動去重並由 BotBrain 提煉 3 點繁中重點與雪莉犀利銳評，主動推播至 Discord 頻道，解決群組冷場！
- **指令**：
  - `/sentinel_fetch`：手動立即觸發情報哨兵抓取與推播。

#### 5. 📈 `stock_trader` — 雪莉美股虛擬量化交易系統
- **功能**：自帶 $10,000 美金虛擬本金，美股交易時段 (台灣時間 23:00~04:00) 自動巡邏，03:50 臨關盤強制看盤。結合 AI 策略分析師 (`TradeStrategist`) 與演算法交易員 (`AlgoTrader`) 執行自動交易與風控，成交自動推播至頻道。
- **指令與 Web 支援**：
  - `!stock_status`：查看當前美股持倉、資產淨值與收益率。
  - **Web UI 炒股專屬看板**：提供視覺化未實現損益、AI 交易決策思路日誌、一鍵「刷新行情損益」與「手動評估看盤」。

#### 6. 🎬 `video_clipper` — YouTube 影片轉 GIF 動態截取
- **功能**：快速將 YouTube 指定時間片段轉換為高畫質 GIF 動圖發送至頻道。
- **指令**：
  - `/video url:<網址> start_time:<開始時間> end_time:<結束時間>`  
    *範例：`/video url:https://youtu.be/xxx start_time:1:30 end_time:1:45`*

#### 7. 🎲 `dnd` — D&D TRPG AI 地下城主跑團遊戲引擎
- **功能**：完整整合地下城主 AI (DM)、角色卡、屬性骰判定 (優勢/劣勢)、冒險行動互動與動態世界模擬。
- **指令**：
  - `/dnd_join`：創建專屬 D&D 冒險者角色卡。
  - `/dnd_session`：開啟/關閉本頻道的 D&D 自動遊戲模式。
  - `/dnd_action`：向 DM 提出冒險動作描述。
  - `/dnd_roll`：進行 D&D 擲骰判定 (支援 d20, d6, 優勢/劣勢)。
  - `/dnd_status`：查看角色卡狀態與生命值。
  - `/dnd_inventory`：查看背包裝備與物品。
  - `/dnd_world`：查看世界時間與陣營勢力狀態。

#### 8. 🎮 `game_center` — 小遊戲遊樂場
- **功能**：支援 21 點撲克 (`blackjack`)、猜拳大對決 (`rps`)、踩地雷 (`minesweeper`)、海龜湯 (`turtle_soup`)、井字棋 (`tic_tac_toe`)、密室逃脫 (`escape_room`)，並結合地端 LLM 進行雪莉名偵探個性對弈與調侃！

#### 9. 📊 `market_plugin` — 股市與卡價追蹤
- **功能**：技術指標晨報 (08:00)、Gemini AI 晚間分析 (21:30)、PTCG 卡片即時報價 (10:00)、ETF 溢折價監控 (盤中每 10 分鐘)。
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
| 💬 **強制執行** | `/execute intent:<TOOL> query:<內容>` | 繞過分類器直接強制指派特定工具執行 |
| 🎵 **音樂系統** | `/play` | 播放 YouTube 音樂、加入播放清單或切換曲目 |
| 🎤 **語音通話** | `/join_voice` · `/leave_voice` | 讓雪莉加入/離開目前語音頻道進行語音交談 |
| 🧠 **記憶管理** | `/memory_show` · `/memory_clear` | 查看或清除雪莉對自己的長期記憶檔案 (僅自己可見) |
| 👥 **氛圍感知** | `/vibe_show` · `/vibe_clear` | 查看或重設頻道當前熱門話題與氛圍狀態 |
| ⚙️ **系統維運** | `/restart` · `/reload` · `/stats` · `/config` | 熱重啟指定模組、查看系統統計儀表板與伺服器端配置 |

---

## 🖥️ Web UI 視覺化控制台 2.0

雪莉內建基於 **Quart** 的高質感現代化 Web 管理後台 (`https://localhost:8763`)，包含以下 11 大核心分頁：

1. **📊 總覽主控台 (Dashboard)**：實時監控 CPU / GPU / RAM / 網路資源，精確比對「🤖 運作中的雪莉 Commit」與「📦 本地最新 Git 代碼」，支援一鍵 Git Pull + 重啟更新。
2. **📋 即時日誌 (Live Logs)**：支援依事件類型 (CHAT, 插話, ERROR) 與分頁過濾，具備 Guest 日誌白名單防護。
3. **💻 系統資源 (System Metrics)**：硬體資源即時動態圖表 (Chart.js)。
4. **⏰ 任務排程 (Tasks & LLM Queue)**：即時掌握排程提醒任務與 **「🧠 大腦即時推理佇列 (LLM Task Queue)」** 優先級與深度。
5. **💬 雪莉測試室 (Chat Playground)**：免開 Discord 直接在瀏覽器與雪莉對話，**支援視覺化展開 ReAct / Parallel 思考過程標籤與 Tool Invocation**。
6. **📡 地端 AI 服務 (Local AI Monitor)**：每 5 秒自動診斷 Ollama (`11434`)、LM Studio (`1234`)、vLLM (`8000`)、Stable Diffusion (`7860`) 之連線狀態、Ping 延遲與掛載模型。
7. **🔬 深度研究控制台 (Deep Research)**：即時查看多輪深度主題研究推演進度、檢索來源與論文級報告成果。
8. **🎨 畫畫工作室 (Painter Studio)**：視覺化畫布預覽、筆刷調色盤即時調試與雪莉歷史創作畫廊。
9. **💬 雪莉吐槽管理 (Roast Manager)**：關鍵字監聽規則配置、命中機率與冷卻調節、名偵探吐槽風格切換與即時測試。
10. **📈 雪莉虛擬炒股 (Stock Trader)**：即時美股持倉、未實現損益、AI 決策歷程看板，支援一鍵手動評估看盤。
11. **🛡️ 管理員審計 (Admin Audit Log)**：完整記錄登入 IP、來源網路標籤 (Localhost / Radmin / LAN)、裝置作業系統與各項操作時間軸歷程。

---

## 📁 專案目錄結構

```text
Sherry-An-AI-Companion-Platform/
├── data/                  # 儲存資料庫、JSONL 日誌、記憶與排程
├── file/                  # 人設定義 (AI_personality.py) 與 Soul 人格檔
├── handlers/              # 核心業務邏輯 (Cog Extensions)
│   ├── admin/             # 管理員指令與伺服器設定
│   ├── bot_brain/         # 大腦推理引擎、優先權佇列與健康檢查
│   ├── executor/          # /execute 強制執行指令
│   ├── heartbeat/         # 心跳定時主動任務
│   ├── memory/            # 長期記憶萃取、記憶物理消毒與每日摘要
│   ├── music/             # YouTube 留聲機音樂播放引擎
│   ├── roast/             # 雪莉毒舌銳評與吐槽子系統 (RoastCog / RoastManager)
│   ├── scheduler/         # 自然語言定時提醒與 Cron 排程
│   ├── social/            # Parallel/ReAct 調度器、動態人格與各項工具
│   │   ├── actions/       # 動作定義與執行器
│   │   └── tools/         # Chat, Draw, Image, Music, Schedule, Search, Stock, Weather 工具庫
│   ├── vibe_manager/      # 頻道熱門話題與氛圍追蹤
│   ├── voice/             # 語音通話 (STT / TTS / VAD)
│   ├── web_explorer/      # 網頁內容檢索與提取
│   ├── help_cog.py        # /help 分類幫助選單
│   └── welcome_cog.py     # 新人加入動態歡迎
├── mod/                   # 擴充外掛模組 (可插拔)
│   ├── deep_search/       # 🔬 地端 LLM / SLM 深度研究主控台
│   ├── painter/           # 🎨 雪莉數位畫布繪圖引擎與工作室
│   ├── livestream/        # 📺 實況劇院與影音播放系統
│   ├── feed_sentinel/     # 📰 AI 社群情報哨兵
│   ├── stock_trader/      # 📈 美股虛擬量化交易系統
│   ├── video_clipper/     # 🎬 YouTube 轉 GIF 工具
│   ├── dnd/               # 🎲 D&D TRPG 跑團系統
│   ├── game_center/       # 🎮 小遊戲遊樂場
│   └── market_plugin/     # 📊 股市與卡價追蹤
├── web_ui/                # Quart Web 管理後台 (HTML/CSS/JS)
├── Docs/                  # 專案詳細技術架構與歷史版本文件
├── main.py                # 程式主入口
├── settings.py            # 全域設定中心
└── requirements.txt       # Python 相依套件清單
```

---

## 📋 v1.7.0 更新日誌 (Release Notes)

### 🔬 1. 地端 LLM / SLM 深度自主研究推演模組 (`mod/deep_search`)
- **10~30 分鐘級深度推演**：支援地端模型進行多輪「自主思考 → 檢索規劃 → 文獻精讀 → 交叉比對 → 批判性反思 → 論文級報告產出」之完整研究迴圈。
- **Web UI 專屬推演控制台**：即時視覺化呈現研究思維樹、文獻引用清單、推演步驟進度與 Markdown 格式論文級報告。

### 🎨 2. 雪莉數位畫布繪圖引擎與藝術工作室 (`mod/painter`)
- **數位畫布引擎 (`VirtualCanvas`)**：具備多筆刷系統 (`BrushEngine`)、調色盤管理 (`PaletteManager`) 與繪畫直譯器 (`CommandInterpreter`)。
- **多模態臨摹與歷史畫廊**：結合 Gemma 等多模態視覺模型進行畫面特徵臨摹，並於 Web UI 提供專屬畫布工作室與作品畫廊展示。

### 💬 3. 雪莉毒舌銳評與吐槽子系統 (`handlers/roast`)
- **動態關鍵字監聽與機率判定**：監聽頻道對話內容，自動依設定關鍵字、權重與機率擲骰發動雪莉毒舌銳評。
- **名偵探吐槽人設注入**：以雪莉特有的犀利視角、傲嬌吐槽語氣對群友對話或新聞進行幽默插話。
- **Web UI 吐槽管理面板 (`roast.html`)**：支援在後台自由增刪觸發詞、調整觸發機率與冷卻秒數，並提供手動即時發送測試功能。

### 📺 4. 實況劇院與影音大螢幕播放系統 (`mod/livestream`)
- **獨立播放伺服器 (PORT 8764)**：支援 Discord 點播 YouTube 與本地影音檔案至大螢幕實況播放器。
- **實況連播控制**：支援集數連續播放、即時暫停/切換與狀態廣播。

### 🌐 5. 雲端 LLM 生態擴展 — 支援 OpenRouter 與 FreeToken
- **OpenRouter 完整支援 (`openrouter:`)**：新增 `OPENROUTER_API_KEY` 與專用前綴，一鍵接入 OpenRouter 海量開源與商用旗艦模型。
- **FreeToken / 雲端代理端點相容**：提供更彈性的模型接入選擇，強化系統高可用性。

### 🧠 6. 記憶系統進階物理消毒與幻覺過濾
- **記憶萃取演算法優化**：深度修復 UID 記憶萃取時的幻覺與角色人設混淆問題。
- **物理消毒演算法升級**：在 Python 端進行多重語意過濾與重複去重，進一步降低 10~15% 的 Token 浪費並保證記憶純淨度。

### 🖥️ 7. Web UI 控制台 2.0 模組化整合與穩定性提升
- **外掛模組 Web UI 動態整合**：畫畫工作室、深度研究、美股炒股、吐槽管理等模組無縫集成至 Web UI 導航欄。
- **修復鎖定黑屏與版面適配**：修復螢幕鎖定重開後的黑屏問題，並重構 CSS/JS 以提升各螢幕解析度下的響應速度。

### 🛠️ 8. 專案架構規範化與穩定性修復
- **全方位修復不合規代碼與分層優化**：嚴格遵循專案架構規範，優化 I/O 邊界與非同步呼叫。
- **多媒體與搜圖穩定度提升**：修復 YouTube 音訊播放中斷問題與 Safebooru/Pixiv 搜圖檢索邏輯。

---

## 📚 歷史版本技術文件 (Historical Versions)

所有歷史版本的詳細規格與更新日誌已歸檔至 `Docs/` 目錄：

| 版本 | 文件連結 | 核心里程碑 |
|---|---|---|
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