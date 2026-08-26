# 🤖 橘雪莉 — Discord AI Companion & Agent Platform (v1.5.0)

> 📌 **本文件為歷史版本存檔 (Archive)**。若要查看最新版規格與文件，請參閱根目錄 [README.md](../README.md)。

一個擁有獨立個性、會讀空氣、還會自己找樂子的 Discord 虛擬群友與全能 AI 代理人。  
基於 **discord.py 2.x** + **Ollama / LM Studio / vLLM / 多 LLM 本地與雲端推論** + **asyncio 優先權佇列**，支援 Linux / Docker 容器化與 Windows 跨平台部署，兼顧企業級與消費級 GPU 本地深度效能最佳化。

---

## ✨ v1.5.0 主要能力矩陣 (Core Capabilities)

| 類別 | 功能亮點 |
|---|---|
| 💬 **對話與感知** | @Tag 回應 · 複合指令自動拆解 · 主動插話 (Interject) · 讀空氣 (VibeManager) · 心跳定時主動任務 (HeartbeatCog) |
| 🛠️ **全能工具庫** | 台美股票 K 線技術分析 (`/stock`) · 即時天氣查詢 (`/weather`) · 網頁深度 RAG 搜尋 (`/search`) · Safebooru 搜圖 · `/execute` 工具獨立測試 |
| 🎨 **創作與多媒體** | Stable Diffusion 圖片生成 (`/draw`) · YouTube 音樂串流點播 (`/play`) · WebExplorer 影音提取 |
| 🎤 **語音系統 (Voice)** | 加入語音頻道 · faster-whisper STT 語音辨識 · XTTS-v2 聲音克隆 TTS · Silero VAD 語音活動檢測 · **語音房與留聲機音樂實時監控** |
| 🧠 **動態記憶與社交** | UID 長期記憶萃取 · 好感度等級追蹤 (`RelationshipManager`) · 每日聊天精華摘要 (`NightlyDigest`) |
| ⏰ **排程與佇列** | 自然語言單次/多任務提醒 (`/schedule`) · cron 定時排程 · **大腦推理佇列 (LLM Task Queue) 實時監控** |
| 🛡️ **資安與審計** | **管理員登入與操作歷史落盤 (`./data/admin_audit.jsonl`)** · IP 暴力破解防護 · 時序攻擊防禦 · XSS 防護 |
| ⚙️ **系統與多引擎** | VRAM 互斥鎖 · 多 LLM 引擎支援 (**Ollama / LM Studio / vLLM**) · **地端 AI 服務即時監測** · **全管線 `latency_ms` 追蹤** |
| 🐧 **容器與跨平台** | **Linux / Docker 容器化完整支援** · **`0.0.0.0` 綁定自動降級** · **`.sh` / `.bat` 跨平台重啟守護腳本** |
| 🔌 **外掛插件** | `./mod/` 外掛模組系統，任一插件出錯絕不影響核心主程式 |

---

## 🖥️ Web UI 視覺化控制台 (v1.5.0)

雪莉內建基於 **Quart** 的 Web 管理後台 (`https://localhost:8763`)，主要功能包含：

1. **📊 總覽主控台 (Dashboard)**：實時監控 CPU / GPU / RAM / 網路資源，精確比對「🤖 運作中的雪莉 Commit」與「📦 本地最新 Git 代碼」，支援一鍵 Git Pull + 重啟更新。
2. **📋 即時日誌 (Live Logs)**：支援依事件類型 (CHAT, 插話, ERROR) 與分頁過濾，具備 Guest 日誌白名單防護。
3. **💻 系統資源 (System Metrics)**：硬體資源即時動態圖表 (Chart.js)。
4. **⏰ 任務與大腦佇列 (Tasks & LLM Queue)**：即時掌握排程提醒任務與 **「🧠 大腦即時推理佇列 (LLM Task Queue)」** 優先級與深度。
5. **📡 地端 AI 服務 (Local AI Monitor)**：每 5 秒自動診斷 Ollama (`11434`)、LM Studio (`1234`)、vLLM (`8000`)、Stable Diffusion (`7860`) 之連線狀態、Ping 延遲與掛載模型。
6. **🎵 語音與音樂監控 (Voice & Music Monitor)**：即時掌握雪莉所在語音房成員、音樂播放狀態與佇列預覽。
7. **🛡️ 管理員審計 (Admin Audit Log)**：完整記錄登入 IP、來源網路標籤 (Localhost / Radmin / LAN)、裝置作業系統與各項操作時間軸歷程。
8. **⚙️ 系統設定 (Settings)**：即時修改 LLM 模型分配、Prompt 參數與功能開關。

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
