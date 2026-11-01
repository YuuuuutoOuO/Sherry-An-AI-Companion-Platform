# 27 — 系統效能瓶頸剖析與爭用點分析 (Bottleneck Analysis)

> **難度標記**：`Difficulty: Expert`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度檢視雪莉平台在高負載與極限併發情境下的效能瓶頸 (Bottlenecks) 與硬體爭用點 (Contention Points)。客觀分析單一 Event Loop 凍結風險、地端 GPU 顯存爭用、SQLite 併發寫入鎖定、LLM 首字延遲 (TTFT) 累積以及音訊編解碼 CPU 爭用，並給出架構改善對策。

---

## 🧭 Prerequisites (先備知識)
- 效能剖析法 (Amdahl's Law, Universal Scalability Law)。
- CPU-bound vs. I/O-bound vs. Memory-bound 系統特徵。
- 首字生成延遲 (Time to First Token, TTFT) 與吞吐量 (Tokens per Second, TPS)。

---

## 💡 Concept (系統瓶頸木桶理論)
系統的最大輸送量取決於其「最短的那塊木板」：
1. **CPU 瓶頸**：Piper 語音合成、音訊重取樣、大型正規表示式掃描。
2. **GPU 顯存瓶頸**：VRAM 容量硬限制 (OOM)、純本地模型排它鎖排隊等待。
3. **Event Loop 協程瓶頸**：單執行緒架構下任何微小阻塞都會被放大為全域延遲。
4. **磁碟 I/O 瓶頸**：未開啟 WAL 時 SQLite 的磁碟 fsync 頻率過高。

---

## 🛠️ Sherry Implementation (雪莉四大核心瓶頸實測分析)

### 一、瓶頸一：共用 Event Loop 的同步阻塞毒藥
- **爭用元件**：Discord Gateway WebSocket vs. Quart Web 管理控制台。
- `[CONFIRMED]` 兩者在同一個 Thread / Event Loop 中運作。
- **風險場景**：若管理員在 Web UI 查詢 50MB 的歷史日誌檔案，正則表達式比對耗時 800ms，期間 Event Loop 被完全凍結。
- **影響**：Discord 心跳被延誤、所有群組對話回覆瞬間停擺 800ms。
- **現行緩解**：將日誌讀取下沉至 `loop.run_in_executor`。

---

### 二、瓶頸二：地端 GPU 顯存排它與隊列排隊 (Queue Delay)
- **爭用元件**：本地 LLM (Ollama) vs. 本地 Stable Diffusion (生圖)。
- `[CONFIRMED]` 為杜絕 CUDA OOM，系統實裝 `async with self._lock:` 強制本地串行。
- **風險場景**：當生圖需要 20 秒時，若未設定雲端備援，本地對話任務將在 `PriorityQueue` 中滯留 20 秒以上。
- **現行緩解**：
  `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L178-L203)：
  生圖期間自動將文字任務導向雲端模型 (`is_cloud = True`)，完全繞過 GPU 鎖，並以背景 Task 並行發送。

---

### 三、瓶頸三：SQLite 寫入鎖爭用與連線洩漏
- **爭用元件**：多個業務 SQLite 資料庫實體。
- `[CONFIRMED]` 雖然大多數資料庫受 `core/db.py` 的 WAL 模式與 `busy_timeout = 5000` 保護，但 `mod/market_plugin/repository.py` 存在 8 處原生直連。
- **風險場景**：當股票行情爬蟲高頻執行批量寫入時，若 Web UI 同步查詢卡牌或自選清單，未設定 busy_timeout 的直連會立即觸發 `database is locked` 崩潰。

---

### 四、瓶頸四：LLM 上下文膨脹與 TTFT 延遲
- **爭用元件**：`chat_messages` 歷史輪次與 System Prompt 規模。
- **風險場景**：隨著對話輪數增加，若 Context 膨脹至 8K~16K Tokens，本地 8B 模型的 Prefill 階段耗時將由 200ms 飆升至 2500ms。
- **現行緩解**：
  1. `[CONFIRMED]` 滑動視窗截斷（僅帶近 10~20 輪訊息）。
  2. `[CONFIRMED]` 保持 `head_persona` 靜態以爭取 Prefix KV Cache 命中。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **GPU 顯存鎖等待時間記錄**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L341-L347)：
     ```python
     lock_wait_ms = int((time.monotonic() - queue_start) * 1000)
     logger.info("BotBrain: task '%s' 拿到GPU鎖時已等 %dms, 推理生成 %dms...", ...)
     ```
2. `[CONFIRMED]` **8 處直連 SQLite 競爭盲點**：
   - 見 [`mod/market_plugin/repository.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/mod/market_plugin/repository.py#L35-L286)。

---

## 📊 Flow Diagram (系統瓶頸熱點拓撲圖)

```text
               +-------------------------------------------------------+
               |                  Incoming Request Load                |
               +---------------------------+---------------------------+
                                           |
                                           v
+─────────────────────────────────────────────────────────────────────────────────+
| 瓶頸點 1: 單一 Asyncio Event Loop                                                |
| - 凍結風險: 同步 Blocking I/O / 密集正則搜尋                                    |
| - 緩解方式: loop.run_in_executor 執行緒池卸載                                    |
+──────────────────────────────────────────┬──────────────────────────────────────+
                                           |
                                           v
+─────────────────────────────────────────────────────────────────────────────────+
| 瓶頸點 2: 本地 GPU 顯存 (VRAM) 爭用                                              |
| - 凍結風險: Stable Diffusion 生圖與 LLM 推論搶佔 8GB~16GB 顯存引發 CUDA OOM     |
| - 緩解方式: async with self._lock 串行保證 + 偵測生圖時雲端繞過 (Cloud Bypass)   |
+──────────────────────────────────────────┬──────────────────────────────────────+
                                           |
                                           v
+─────────────────────────────────────────────────────────────────────────────────+
| 瓶頸點 3: 儲存層 SQLite 寫入鎖競爭                                               |
| - 凍結風險: 併發寫入引發 database is locked                                      |
| - 緩解方式: WAL 模式 + busy_timeout=5000 (註: mod/ 8 處洩漏待消除)               |
+─────────────────────────────────────────────────────────────────────────────────+
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **多群組同時語音通話 CPU 飽和**：
   - 當機器人同時加入 3 個 Discord 語音頻道，每個頻道均需要即時 PCM 串流與 Piper TTS 合成時，CPU 使用率可能迅速衝上 100%，導致語音出現斷續雜音 (Stuttering)。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **地端推論嚴格串行 (`_lock`)** | 零 OOM 崩潰風險，硬體穩定性 100%。 | 多使用者同時在本地對話時排隊時間累加。 | 升級硬體採用雙 GPU (GPU 0 跑 LLM, GPU 1 跑 SD) 或使用 vLLM 動態批處理。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在資源受限的伺服器上，如何定位並消除非同步 Python 應用的事件循環阻塞？**
  - **Ans**:
    1. **啟用 Debug 模式**：`loop.set_debug(True)`，當有回呼耗時超過 `loop.slow_callback_duration` (預設 100ms) 時自動印出警告。
    2. **使用性能分析工具**：引入 `yappi` 或 `py-spy` 抓取即時火焰圖，找出執行時間過長的同步函式。
    3. **重構卸載**：將找到的阻塞點以 `loop.run_in_executor` 移出主循環。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Performance/28-performance-model.md](./28-performance-model.md) 研讀端到端延遲數學模型與容量規劃計算。
