# 🤖 橘雪莉 — Discord AI Agent (v1.4.1)

一個擁有獨立個性、會讀空氣、還會自己找樂子的 Discord 虛擬群友。  
基於 **discord.py 2.x** + **Ollama / 多 LLM 本地與雲端推理** + **asyncio 優先權佇列**，針對 16 GB VRAM 單卡環境最佳化。

---

## ✨ 主要能力

| 類別 | 功能 |
|---|---|
| **對話** | @Tag 回應 · 複合指令解析 · 主動插話 · 讀空氣 (VibeManager) · 心跳主動任務 (HeartbeatCog) |
| **工具** | 即時股票 K 線 · 天氣查詢 · 網頁 RAG 搜尋 · Safebooru 找圖 · `/execute` 工具獨立測試 |
| **創作** | Stable Diffusion 圖片生成 · YouTube 音樂串流 · WebExplorer 影片動態探勘 |
| **語音** | 加入語音頻道 · faster-whisper STT · XTTS-v2 TTS · VAD 語音活動檢測 · **語音房間與音樂播放動態監控** |
| **記憶** | UID 長期記憶萃取 · 好感度追蹤 (RelationshipManager) · 每日聊天摘要 (NightlyDigest) |
| **排程與佇列** | 自然語言單次/多任務提醒 · cron 週期任務 · **大腦推理佇列 (LLM Queue) 實時監控** |
| **資安與審計** | **管理員登入紀錄落盤 (`./data/admin_audit.jsonl`)** · IP 暴力破解防護 · 時序攻擊防禦 · XSS 防護 |
| **系統與後台** | VRAM 互斥防爆 · 多 LLM 動態切換 · **地端 AI 服務即時監測 (Ollama/LM Studio/SD)** · **耗時 `latency_ms` 追蹤** |
| **插件** | `./mod/` 動態掃描，插件失敗不影響主程式 |

---

## 🚀 快速開始

### 環境需求

- Python 3.10+
- [Ollama](https://ollama.com) 本地運行中（或外部 LLM API Key）
- FFmpeg（音樂功能）
- CUDA GPU（語音 STT/TTS 功能）
- Stable Diffusion WebUI `--api` 模式（圖片生成功能）

### 安裝

```bash
git clone <repo>
cd -Discord--Bot-Agent

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt
```

### 設定 `.env`

```env
DISCORD_BOT_TOKEN=你的 Token           # 必填

# 選填 (LLM 後端與 API)
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_API_KEY=...
GEMINI_API_KEY=...
DEEPSEEK_API_KEY=...
CLAUDE_API_KEY=...
OPENWEATHERMAP_API_KEY=...
WEB_UI_PASSWORD=sherry123

# 選填（market_plugin 插件）
FINNHUB_API_KEY=...
```

### 拉取模型

```bash
ollama pull krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS
ollama pull mannix/llama3.1-8b-abliterated
```

### 啟動

```bash
python main.py
```

Web 後台：[http://localhost:8080](http://localhost:8080)（帳號：`admin`，密碼同 `WEB_UI_PASSWORD`）

---

## 🔌 插件系統 (Mod System)

在 `./mod/` 下建立子資料夾並加入 `cog.py`，重啟後自動載入。  
插件失敗只輸出 Warning，主程式照常運行。

**現有插件：`market_plugin`**

| 功能 | 排程 |
|---|---|
| 技術指標晨報 | 每日 08:00 |
| Gemini AI 晚間分析 | 每日 21:30 |
| PTCG 卡片報價 | 每日 10:00 |
| ETF 溢折價監控 | 每 10 分鐘（台股開盤） |

插件指令（同時支援 `!前綴` 與 `/斜線`）：`!raw` · `!add` · `!remove` · `!watchlist` · `!addcard` · `!removecard` · `!listcard` · `!updatecard`

> 詳細設定與指令說明請見 [Docs/01_Project_Snapshot.md](./Docs/01_Project_Snapshot.md)

---

## 📁 命名規範

| 規則 | 例子 |
|---|---|
| **小寫資料夾** = 程式碼 | `handlers/` · `mod/` · `data/` · `file/` |
| **大寫開頭資料夾** = 技術文件 | `Docs/` |

---

## 📚 技術文件

詳細架構設計、所有參數說明、模組 API 參考，請查閱 `Docs/` 目錄：

| 文件 | 內容 |
|---|---|
| [Docs/01_Project_Snapshot.md](./Docs/01_Project_Snapshot.md) | 架構設計 · 資料流 · 插件系統 · 日誌架構 |
| [Docs/02_TODO_and_Improvements.md](./Docs/02_TODO_and_Improvements.md) | 開發待辦 · 已完成功能 |
| [Docs/module_handlers.md](./Docs/module_handlers.md) | 自動生成：所有 handlers/ 的 class / function 詳細紀錄 |

更新文件：

```bash
update_docs.bat
```

---

## 🔐 Discord Bot 權限

Intents：`MESSAGE CONTENT` · `SERVER MEMBERS`  
Scopes：`bot` · `applications.commands`  
權限：Send Messages · Read History · Add Reactions · Embed Links · Connect · Speak

---

## 📋 v1.4.0 更新日誌 (Release Notes)

### 🧠 1. 大腦即時推理佇列監控 (LLM Task Queue)
- **實時任務佇列**：Web UI「⏰ 任務與佇列」新增 **「🧠 大腦即時推理佇列 (LLM Task Queue)」** 面板，實時展示 `BotBrain._queue` 中正在等待 GPU 運算與派發執行的 `InferenceTask` 佇列。
- **詳細任務資訊**：顯示 Priority 優先權、序號 Sequence ID、指定模型 Model 名稱與 Prompt 前 120 字元預覽。

### 📡 2. 地端 AI 服務即時監測 (Local AI Services Monitor)
- **多引擎健康度與延遲診斷**：新增「📡 地端 AI 服務」分頁，每 5 秒自動診斷與輪詢：
  - **Ollama Engine** (埠號 `11434`)：監測連線 Ping 延遲與目前已載入記憶體之模型。
  - **LM Studio Engine** (埠號 `1234`)：監測連線 Ping 延遲與預設載入模型。
  - **Stable Diffusion WebUI** (埠號 `7860`)：監測 API 連線狀態與當前預設 Checkpoint / VAE 模式。

### 🛡️ 3. 管理員登入與操作歷史審計紀錄 (Admin Audit Log)
- **多管理員帳號識別 (`username`)**：Web UI 登入 Modal 新增帳號輸入欄位，支援未來多位管理員分別登入。
- **JSONL 落盤記錄 (`./data/admin_audit.jsonl`)**：
  - **IP 與來源網路標籤**：智慧解析與區分 `Localhost`、`Radmin VPN (26.x.x.x)` 與 `LAN` 區域網路。
  - **裝置與瀏覽器解析**：拆解 User-Agent 記錄操作作業系統 (Windows/Mac/Linux/iOS/Android) 與瀏覽器。
  - **操作履歷歷程 (Actions Timeline Modal)**：每次 Session 中修改系統設定、測試對話、重啟 Bot、Git Pull 等操作全數紀錄，並可在 Web UI 點擊彈窗展開時間軸歷程！

### 🎵 4. 語音頻道與留聲機音樂動態監控 (Voice & Music Live Monitor)
- **語音房與成員列表**：即時輪詢雪莉當前加入的 Discord 伺服器名稱、語音房間 (Voice Channel) 與現場通話成員。
- **播放器狀態與歌單預覽**：呈現「▶️ 播放中 / ⏸️ 暫停中 / ⏹️ 空閒中」狀態標籤、當前曲目點歌者、時間長度以及預覽佇列剩餘曲目 (Queue)。

### ⚡ 5. 耗時與極速回應優化 (`latency_ms`)
- **全管線耗時紀錄**：記錄由使用者發送訊息至大腦生成完畢的真實毫秒數 (`latency_ms`)，落盤至 `runtime_log.jsonl` 並在 Web UI 卡片標示 `⚡ 耗時 XXX ms`。

### 🔐 6. 資安防護與 Web UI 體驗升級
- **IP 登入失敗鎖定**：1 分鐘內連續失敗 5 次自動鎖定該 IP 15 分鐘，防範暴力破解。
- **設定鍵安全性過濾**：設定更新 API 強制檢驗設定鍵必須為合法的 `isidentifier() and isupper()` 大寫標識符，防範屬性注入。
- **多行 Prompt 編輯器**：設定頁面的 Persona / Lorebook 輸入框支援自動換行與垂直彈性拉伸。
- **一對多批量排程解析**：自然語言解析支援一口氣建立多個定時提醒 (`schedule_tool.py`)。

### 📊 7. Dashboard 總覽主控台 & 實時 Git 版本雙重比對 (v1.4.1)
- **預設 Landing 首頁**：連線 Web UI 後優先展示「📊 總覽主控台」，一目瞭然掌握全系統運作與 Git 代碼庫快照。
- **實時雙重 Git 版本比對**：精確比對「🤖 目前運作中的雪莉 Commit (Bot Process)」與「📦 本地磁碟 Commit (Latest Code)」。
- **一線重啟提示**：若按下「Git Pull 更新」，系統會即時檢測到版本不一致並高亮提示 `⚠️ 檢測到代碼已更新，請點擊「🚀 立即重啟雪莉」完成套用！`
- **4 大核心狀態摘要**：整合 Bot 延遲/房間數、CPU/RAM 資源、GPU 顯存/溫度、AI 服務狀態與佇列數，並提供視覺化一鍵捷徑跳轉。

---

## 📄 License

MIT