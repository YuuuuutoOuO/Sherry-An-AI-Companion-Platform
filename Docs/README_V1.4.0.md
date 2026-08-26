# 🤖 橘雪莉 — Discord AI Companion & Agent Platform (v1.4.1)

> 📌 **本文件為歷史版本存檔 (Archive)**。若要查看最新版規格與文件，請參閱根目錄 [README.md](../README.md)。

一個擁有獨立個性、會讀空氣、還會自己找樂子的 Discord 虛擬群友與全能 AI 代理人。  
基於 **discord.py 2.x** + **Ollama / 多 LLM 本地與雲端推論** + **asyncio 優先權佇列**，針對 16 GB VRAM 顯卡進行最佳化調優。

---

## ✨ v1.4.1 主要能力矩陣 (Core Capabilities)

| 類別 | 功能亮點 |
|---|---|
| 💬 **對話與感知** | @Tag 回應 · 複合指令拆解 · 主動插話 (Interject) · 讀空氣 (VibeManager) · 心跳定時主動任務 (HeartbeatCog) |
| 🛠️ **全能工具庫** | 台美股票 K 線技術分析 (`/stock`) · 天氣即時查詢 (`/weather`) · 網頁深度 RAG 搜尋 (`/search`) · Safebooru 搜圖 · `/execute` 工具獨立測試 |
| 🎨 **創作與多媒體** | Stable Diffusion 圖片生成 (`/draw`) · YouTube 音樂串流點播 (`/play`) · WebExplorer 影音提取 |
| 🎤 **語音系統 (Voice)** | 加入語音頻道 · faster-whisper STT 語音辨識 · XTTS-v2 聲音克隆 TTS · Silero VAD 語音活動檢測 · **語音房與音樂播放實時監控** |
| 🧠 **動態記憶與社交** | UID 長期記憶萃取 · 好感度追蹤 (`RelationshipManager`) · 每日聊天精華摘要 (`NightlyDigest`) |
| ⏰ **排程與佇列** | 自然語言單次/多任務提醒 · cron 定時任務 · **大腦推理佇列 (LLM Task Queue) 實時監控** |
| 🛡️ **資安與審計** | **管理員登入與操作歷史落盤 (`./data/admin_audit.jsonl`)** · IP 暴力破解防護 · 時序攻擊防禦 · XSS 防護 |
| ⚙️ **系統與運維** | VRAM 互斥鎖 · 多 LLM 引擎分工 (**Ollama / LM Studio / SD WebUI**) · **地端 AI 服務即時監控** · **全管線 `latency_ms` 耗時追蹤** |
| 🔌 **外掛插件** | `./mod/` 外掛系統，插件出錯絕不影響核心主程序 |

---

## 🖥️ Web UI 視覺化控制台 (v1.4.1)

雪莉內建基於 **Quart** 的 Web 管理後台 (`https://localhost:8763`)，主要功能包含：

1. **📊 總覽主控台 (Dashboard)**：實時監控 CPU / GPU / RAM / 網路資源，精確比對「🤖 運作中的雪莉 Commit」與「📦 本地最新 Git 代碼」，支援一鍵 Git Pull + 重啟更新。
2. **📋 即時日誌 (Live Logs)**：支援依事件類型 (CHAT, 插話, ERROR) 與分頁過濾，具備 Guest 日誌白名單防護。
3. **💻 系統資源 (System Metrics)**：硬體資源即時動態圖表 (Chart.js)。
4. **⏰ 任務與大腦佇列 (Tasks & LLM Queue)**：即時掌握排程提醒任務與 **「🧠 大腦即時推理佇列 (LLM Task Queue)」** 優先級與深度。
5. **📡 地端 AI 服務 (Local AI Monitor)**：每 5 秒自動診斷 Ollama (`11434`)、LM Studio (`1234`)、Stable Diffusion (`7860`) 之連線狀態、Ping 延遲與掛載模型。
6. **🎵 語音與音樂監控 (Voice & Music Monitor)**：即時掌握雪莉所在語音房成員、音樂播放狀態與佇列預覽。
7. **🛡️ 管理員審計 (Admin Audit Log)**：完整記錄登入 IP、來源網路標籤 (Localhost / Radmin / LAN)、裝置作業系統與各項操作時間軸歷程。
8. **⚙️ 系統設定 (Settings)**：即時修改 LLM 模型分配、Prompt 參數與功能開關。

---

## 📋 v1.4.1 更新日誌 (Release Notes)

### 🧠 1. 大腦即時推理佇列監控 (LLM Task Queue)
- **實時任務佇列**：Web UI「⏰ 任務與佇列」新增 **「🧠 大腦即時推理佇列 (LLM Task Queue)」** 面板，實時展示 `BotBrain._queue` 中正在等待 GPU 運算與派發執行的 `InferenceTask` 佇列。
- **詳細任務資訊**：顯示 Priority 優先級、序號 Sequence ID、指定模型 Model 名稱與 Prompt 前 120 字元預覽。

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
