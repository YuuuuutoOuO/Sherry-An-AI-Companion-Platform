# 🤖 橘雪莉 — Discord AI Agent (v1.5.0)

一個擁有獨立個性、會讀空氣、還會自己找樂子的 Discord 虛擬群友。  
基於 **discord.py 2.x** + **Ollama / LM Studio / vLLM / 多 LLM 本地與雲端推理** + **asyncio 優先權佇列**，支援 Linux / Docker 容器化與 Windows 跨平台部署，針對企業級與消費級 GPU 進行深度效能優化。

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
| **系統與後台** | VRAM 互斥防爆 · 多 LLM 動態切換 (**Ollama/LMS/vLLM**) · **地端 AI 服務即時監測** · **耗時 `latency_ms` 追蹤** |
| **容器與跨平台** | **Linux / Docker 容器化完整支援** · **`0.0.0.0` 綁定自動降級** · **`.sh` / `.bat` 跨平台重啟腳本** |
| **插件** | `./mod/` 動態掃描，插件失敗不影響主程式 |

---

## 🚀 快速開始

### 環境需求

- Python 3.10+
- [Ollama](https://ollama.com) / LM Studio / vLLM 本地運行中（或外部 LLM API Key）
- FFmpeg（音樂功能）
- CUDA GPU（語音 STT/TTS 功能）
- Stable Diffusion WebUI `--api` 模式（圖片生成功能）

### 安裝

```bash
git clone <repo>
cd Sherry-An-AI-Companion-Platform

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
LMS_BASE_URL=http://localhost:1234/v1
VLLM_BASE_URL=http://localhost:8000/v1
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
ollama pull qwen2.5:3b
```

### 啟動

* **Windows**:
  ```bash
  python main.py
  # 或點擊 restart_sherry.bat
  ```
* **Linux / Docker**:
  ```bash
  chmod +x restart_sherry.sh update_and_restart.sh
  ./restart_sherry.sh
  ```

Web 後台：[https://localhost:8763](https://localhost:8763)（預設預置 Port 為 `8763`，帳號：`admin`，密碼同 `WEB_UI_PASSWORD`）

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
| **小寫資料夾** = 程式碼 | `handlers/` · `mod/` · `data/` · `file/` · `web_ui/` |
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

## 📋 v1.5.0 更新日誌 (Release Notes)

### 🐧 1. Linux & Docker 容器化跨平台相容性支援 (Linux / Docker Integration)
- **跨平台重啟腳本 (`restart_sherry.sh` & `update_and_restart.sh`)**：新增 Linux 專用 Shell 腳本，具備多重進程殺除與 8763 Socket 強制解鎖能力，支援在容器與伺服器背景無縫重啟。
- **跨平台 Web UI 後台呼叫**：`/api/restart` 與 `/api/update` 端點自動偵測操作系統 (`sys.platform`)，Windows 呼叫 `.bat`，Linux 呼叫 `.sh` (並使用 `start_new_session=True`)。
- **Socket 綁定防護與自動降級**：Web UI 監聽特定 IP 失敗時（如 Windows `WinError 10013` 或 Linux `Errno 99`），系統會自動捕捉例外並安全降級綁定至 `0.0.0.0`。

### 🚀 2. vLLM 高效能推理引擎全支援 (`vllm:` 前綴)
- **`vllm:` 專用前綴與動態路由**：`OllamaClient` 支援 `vllm:` 前綴，可直接對接 vLLM OpenAI 相容端點 (`/v1/chat/completions`)。
- **極速算力釋放**：充分利用 PagedAttention、CUDA Graphs 與硬體級 FP8 / BF16 Tensor Cores，針對 NVIDIA L40S / H100 等企業級 GPU 釋放數倍的超高速 Token 生成能力。
- **Web UI 模型代理 API**：新增 `/api/models/vllm` 代理端點，即時同步 vLLM 目前掛載之模型名稱。

### ⚙️ 3. 環境變數優先級與 Unicode 編碼容錯
- **`.env` 最高優先級保護**：修復舊版 `data/config_override.json` 會強行覆蓋 `.env` 容器網路設定的問題，確保 `.env` 中設定的 `OLLAMA_BASE_URL` / `LMS_BASE_URL` / `VLLM_BASE_URL` 擁有最高優先級。
- **Unicode / ANSI 解碼容錯**：`settings.py` 的 `load_dotenv` 加上 `UTF-8` 強制編碼與 Exception 捕獲保護，防止非標準 UTF-8 `.env` 檔案導致程式崩潰。
- **動態 Base URL 屬性**：`OllamaClient` 改用 Dynamic Property 屬性讀取，避免舊版在 `import` 階段固定預設值導致的連線位址滯後問題。

### 🛠️ 4. NumPy 2.x 相容性與 FFmpeg 音樂播放日誌淨化
- **NumPy C-API 相容**：鎖定 `numpy>=1.24.0,<2.0.0` 與 `soxr==0.3.7`，解決 `TTS` / `librosa` / `transformers` 語音模組在 NumPy 2.x 下 `cimport` 崩潰的問題。
- **FFmpeg 無害日誌過濾**：音樂播放引擎加入 `-loglevel error` 與跨平台 `ffmpeg` 執行檔自動搜尋，徹底屏蔽 YouTube HLS/Live 串流在 Google CDN 節點切換時的無害警告洗版。

---

## 📋 歷史更新紀錄

<details>
<summary>點擊展開 v1.4.1 歷史更新日誌</summary>

### 🧠 1. 大腦即時推理佇列監控 (LLM Task Queue)
- **實時任務佇列**：Web UI「⏰ 任務與佇列」新增 **「🧠 大腦即時推理佇列 (LLM Task Queue)」** 面板，實時展示 `BotBrain._queue` 中正在等待 GPU 運算與派發執行的 `InferenceTask` 佇列。
- **詳細任務資訊**：顯示 Priority Priority、序號 Sequence ID、指定模型 Model 名稱與 Prompt 前 120 字元預覽。

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

### 📊 6. Dashboard 總覽主控台 & 實時 Git 版本雙重比對
- **預設 Landing 首頁**：連線 Web UI 後優先展示「📊 總覽主控台」，一目瞭然掌握全系統運作與 Git 代碼庫快照。
- **實時雙重 Git 版本比對**：精確比對「🤖 目前運作中的雪莉 Commit (Bot Process)」與「📦 本地磁碟 Commit (Latest Code)」。

</details>

---

## 📄 License

MIT