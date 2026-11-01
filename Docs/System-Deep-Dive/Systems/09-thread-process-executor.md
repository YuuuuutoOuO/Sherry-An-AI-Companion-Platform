# 09 — 執行緒與進程池執行器 (Thread & Process Executor)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面分析雪莉系統中如何妥善處理 CPU 密集型運算、音訊編解碼、文字轉語音 (Piper TTS) 與自定義沙盒指令。解構 `handlers/executor/` 的實作、`loop.run_in_executor` 的使用邊界、GIL (Global Interpreter Lock) 爭用抑制與執行緒池容量控制。

---

## 🧭 Prerequisites (先備知識)
- Python GIL 限制與多執行緒 vs. 多處理程序 (Multiprocessing) 差異。
- `concurrent.futures.ThreadPoolExecutor` 與 `ProcessPoolExecutor`。
- `asyncio.loop.run_in_executor` 的非同步包裝原理。

---

## 💡 Concept (非同步架構中的 Offloading 理論)
在以單一事件循環為核心的系統中，遇到非協程操作時必須進行**卸載 (Offloading)**：
1. **I/O 阻塞型操作 (I/O-bound)**：卸載至 `ThreadPoolExecutor`。Python 在進行底層 Socket/Disk C-level 呼叫時會釋放 GIL，因此多執行緒可有效並發。
2. **CPU 密集型操作 (CPU-bound)**：若在 ThreadPool 中執行，由於 GIL 存在，會導致所有 Python 執行緒爭奪單核心 CPU，嚴重拖慢主事件循環的排程週期。應卸載至 `ProcessPoolExecutor` 或獨立子進程 (`subprocess.Popen` / `asyncio.create_subprocess_exec`)。

---

## 🛠️ Sherry Implementation (雪莉執行器架構實作)

### 一、Piper TTS 語音合成卸載
在語音模組 (`handlers/voice/tts.py`) 中，Piper TTS 模型推理屬於本機 CPU 密集型運算：
- 若直接在協程中呼叫 Piper 進行音訊合成，將造成 500ms~1500ms 的 Event Loop 假死。
- `[CONFIRMED]` 雪莉透過 `asyncio.create_subprocess_exec` 或 `loop.run_in_executor` 將 Piper 推理下沉至獨立背景子進程或執行緒池中，並透過 Pipe 讀取 PCM Raw 音訊資料。

---

### 二、Executor Cog 隔離命令執行 (`handlers/executor/`)
- `handlers/executor/` 提供給管理員執行系統診斷腳本與自動化維護任務的能力。
- `[CONFIRMED]` 其內部利用非同步 Subprocess 協程 (`asyncio.subprocess.PIPE`)，設定執行逾時（如 30 秒），防止惡意或死循環的 Shell 腳本永久佔據系統資源。

---

### 三、歷史日誌搜尋與磁碟壓縮卸載
在 `web_ui/server.py` 中，讀取超過 10MB 的歷史滾動日誌檔案：
- 使用 `loop.run_in_executor(None, sync_log_reader)` 委派給預設執行緒池。
- 避免因字串正則比對百萬行日誌造成 Web 控制台與 Discord 聊天同步卡死。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **語音合成背景處理**：
   - 見 [`handlers/voice/tts.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/voice/tts.py)，以非同步串流或非同步行程調用音訊合成。
2. `[CONFIRMED]` **管理員腳本執行器**：
   - 見 [`handlers/executor/`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/executor/)。

---

## 📊 Flow Diagram (卸載模型時序圖)

```text
[Asyncio Event Loop (Main Thread)]
              │
              │ 收到語音合成委託 / 大檔案搜尋
              ▼
    loop.run_in_executor()  /  create_subprocess_exec()
              │
              +--------------------------+
              │ (釋放主循環，立即返回)    │
              v                          v
    [ThreadPoolExecutor]       [Subprocess: Piper TTS]
     (I/O-bound: 檔案讀取)       (CPU-bound: ONNX 聲音神經網路)
              │                          │
              │ (工作完成)                │ (音訊串流輸出)
              +--------------------------+
              │
              ▼
[Future Done -> 排入 Event Loop Ready Queue -> 觸發回呼]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **預設執行緒池耗盡 (Thread Pool Starvation)**：
   - 若大量呼叫未限制容量的 `loop.run_in_executor(None, ...)`，Python 預設執行緒池大小通常為 `min(32, os.cpu_count() + 4)`。若有大量慢速 I/O 填滿執行緒池，後續的卸載任務會被強制排隊等候。
2. **Subprocess 殭屍進程 (Zombie Processes)**：
   - 若子進程執行時父進程未正確 `await process.wait()` 讀取退出碼，在 Linux 容器環境下可能產生孤兒殭屍進程，耗盡系統 PID 配額。雪莉在關機與異常時均具備子進程 terminate 防禦。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **Subprocess 呼叫 Piper CLI** | 記憶體與崩潰完全隔離，C++ 底層核心 crash 不會拉垮 Python 主程序。 | 每次啟動進程有額外的 Process Fork 與動態庫載入開銷。 | 引入 `piper-tts` Python C-extension 模組並在 ThreadPool 中常駐。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 為什麼在 Python 非同步系統中，即使呼叫了 `run_in_executor`，純 CPU 密集型計算依然可能影響 Event Loop 的效能？**
  - **Ans**: 因為 Python 的 GIL 是全執行緒共用的。雖然執行緒在不同的 OS 核心執行，但同一時間只有一個執行緒能持有 GIL 執行 Python bytecode。如果工作執行緒進行純 Python 的密集計算（未釋放 GIL），它會高頻搶佔 GIL，導致主執行緒的 Event Loop 協程難以獲得排程執行，因此 CPU 密集型計算必須轉移到獨立 Process。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Systems/10-web-ui-architecture.md](./10-web-ui-architecture.md) 研讀 Quart Web 管理後台的雙軌整合與 API 架構。
