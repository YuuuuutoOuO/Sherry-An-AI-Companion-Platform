# 18 — GPU 顯存排它鎖與生圖雲端繞過架構 (Resource Management)

> **難度標記**：`Difficulty: Expert`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉系統在消費級與個人主機上最關鍵的硬體資源管理難題：**GPU 顯存 (VRAM) 爭用抑制與協調**。詳解 `BotBrain` 模組中的 `_lock` (非同步互斥鎖)、`_sd_event` (生圖事件通知) 以及當本地 Stable Diffusion (生圖) 佔用 VRAM 時，本地對話任務如何透過備援候選列表動態轉移至雲端推論，從而達成零等待並行繞過的精妙工程架構。

---

## 🧭 Prerequisites (先備知識)
- GPU 顯存架構與 CUDA Out-of-Memory (OOM) 觸發機制。
- Asyncio 同步原語：`asyncio.Lock` 與 `asyncio.Event` 的使用差異。
- 邊緣與本地 AI 部署場景下，文字生成 (LLM) 與擴散模型 (Diffusion) 共存挑戰。

---

## 💡 Concept (消費級 GPU 顯存爭用困境與解決方案)
在消費級 GPU（例如 8GB ~ 16GB VRAM 的 RTX 3060/4070）上同時運行本地 LLM（如 7B/8B Q4 量化模型需佔 5~8GB）與 Stable Diffusion 繪圖模型（需佔 4~8GB）時：
1. **硬體死穴**：若在 SD 生成圖片的同時發起本地 LLM 推論，CUDA 顯存將直接被灌爆，引發不可挽回的 CUDA OOM 崩潰，甚至導致 Ollama 服務或 Python 主進程重啟。
2. **直覺但低效的解法**：強制 LLM 排隊等待生圖完畢（生圖耗時 10~30 秒），導致對話使用者產生極嚴重的卡頓體驗。
3. **雪莉的架構解法（雙軌資源調度）**：
   - 本地模型嚴格**排它串行**（以 `_lock` 保證同一時間僅有一筆推論在本地 GPU 上執行）。
   - 當生圖啟動時，標記 `_sd_generating = True`。
   - 若此時有本地對話進入，系統自動依 `FALLBACK_MODELS` 將任務**動態升級/切換至雲端 API 模型**（如 Gemini / OpenAI），**繞過 GPU 鎖**，以背景非同步協程並行發送，達成使用者無感知的「零等待降級 (Zero-Wait Degradation)」。

---

## 🛠️ Sherry Implementation (雪莉顯存鎖與雲端繞過實作)

### 一、核心同步原語定義
- `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L90-L98)：
  ```python
  self._queue: asyncio.PriorityQueue[InferenceTask] = asyncio.PriorityQueue()
  self._lock = asyncio.Lock()
  ...
  # VRAM state flags & Event
  self._sd_generating = False
  self._sd_event = asyncio.Event()
  self._sd_event.set()  # 初始狀態：SD 未生圖，Event 為 Set 狀態
  ```

---

### 二、生圖觸發時的動態雲端分流
- `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L177-L206)：
  ```python
  is_cloud = _is_cloud_model(task.model)

  # VRAM 防護與零等待降級：地端生圖中時自動將本地對話導向雲端模型
  if not is_cloud and self._sd_generating:
      cloud_fallback = _fallback_candidates(task.model, task.model)
      if cloud_fallback:
          target = cloud_fallback[0]
          logger.info("BotBrain: 偵測到地端生圖中 (VRAM 鎖定)，任務 '%s' 依 FALLBACK_MODELS 切換至 %s", task.task_id, target)
          task.model = target
          is_cloud = True
      else:
          logger.info("BotBrain: 偵測到地端生圖中 (VRAM 鎖定)，任務 '%s' 未設定可用備援模型，維持本地排隊等待。", task.task_id)

  if is_cloud:
      # 雲端模型不佔用本地 GPU/Lock，背景並行放行（保持強引用防 GC）
      t = asyncio.create_task(self._process_task(task, MAX_RETRIES, RETRY_DELAY))
      self._background_tasks.add(t)
      t.add_done_callback(self._background_tasks.discard)
  else:
      # 本地模型依 PriorityQueue 優先級順序串行執行，落實 GPU 鎖
      await self._process_task(task, MAX_RETRIES, RETRY_DELAY)
  ```

---

### 三、本地推論排它鎖與零 CPU 輪詢等待
若任務維持地端執行，在 `_process_task` 內部：
- `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L330-L351)：
  ```python
  if is_local:
      # 等待 SD 生圖結束事件通知（零 CPU 輪詢開銷）
      await self._sd_event.wait()

      async with self._lock:  # [強保證：永遠僅存在一列單排等待處理任務送往本地]
          lock_wait_ms = int((time.monotonic() - queue_start) * 1000)
          gen_start = make_timer()
          reply = await _execute_chat()
          ...
  else:
      # 雲端模型直接併發放行，不獲取鎖
      reply = await _execute_chat()
  ```

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **雲端模型識別邏輯**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L16-L23)，包含 `openai:`, `gemini:`, `claude:`, `deepseek:`, `openrouter:` 等前綴。
2. `[CONFIRMED]` **Event 驅動與 Property 聯動**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L106-L112)：
     ```python
     @sd_generating.setter
     def sd_generating(self, value: bool) -> None:
         self._sd_generating = value
         if value:
             self._sd_event.clear()
         else:
             self._sd_event.set()
     ```
3. `[CONFIRMED]` **幽靈任務取消檢查**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L334-L340)，在 `await self._sd_event.wait()` 之後以及獲取 `async with self._lock:` 之後，兩度檢查 `task.is_cancelled`，避免已超時放棄的請求浪費珍貴的 GPU 算力。

---

## 📊 Flow Diagram (顯存排它與雲端分流決策樹)

```text
               [InferenceTask 出列 (PriorityQueue)]
                                 │
                                 ▼
                     /───────────────────────\
                    <   task.model 是雲端模型?  >
                     \───────────────────────/
                             /       \
                       (是) /         \ (否: 本地模型)
                           /           ▼
                          /   /─────────────────────────\
                         /   <  self._sd_generating == True ? >
                        /     \─────────────────────────/
                       /               /       \
                      /          (是) /         \ (否: 無生圖)
                     /               ▼           \
                    /    /──────────────────────\ \
                   /    <  有可用 FALLBACK_MODELS? > \
                  /      \──────────────────────/   \
                 /               /       \           \
                /          (是) /         \ (否)      \
               /               ▼           │           │
              /        [task.model = 備援]  │           │
             /         [is_cloud = True]   ▼           ▼
            /                  │        [維持地端等待] [本地排隊]
           /                   │               │           │
          v                    v               v           v
+-----------------------------------+     +-----------------------------------+
|      Cloud Inference Route        |     |       Local Inference Route       |
|                                   |     |                                   |
|   1. 繞過 self._lock               |     |   1. await self._sd_event.wait()  |
|   2. asyncio.create_task() 並行   |     |   2. async with self._lock:       |
|   3. 零等待立刻發送至雲端 API     |     |   3. 串行獨佔呼叫本地 Ollama      |
+-----------------------------------+     +-----------------------------------+
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **生圖狀態死鎖遺留 (Stale SD Event Lock)**：
   - 若 `DrawTool` 在開始生圖時設定了 `brain.sd_generating = True`，但在隨後的繪圖過程中拋出未捕捉的致命異常（如 HTTP 500、連線重置）而未進入 `finally: brain.sd_generating = False`，`self._sd_event` 將永久維持在 clear 狀態，導致後續所有本地推論任務永久掛死在 `await self._sd_event.wait()`。
   - 防禦措施：所有修改 `sd_generating` 的區塊必須強制以 `try...finally` 包裹。
2. **純地端環境無網路下的備援失效**：
   - 若主機運行於純內網無對外網路環境（且未設定雲端 API Key），生圖時本地對話無法轉移雲端，必須在本地排隊等待，此時若生圖佇列過長，對話延遲會上升至數十秒。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **動態轉移至雲端模型 (Cloud Bypass)** | 極大化使用者體驗，對話絕不卡頓，徹底杜絕 CUDA OOM。 | 生圖時的對話可能消耗少額雲端 API Token 費用；對隱私有極致要求者需在設定中關閉備援。 | 強制地端排隊，並向 Discord 頻道發送「雪莉正在專心作畫中，請稍候」等待指示。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在資源極端受限的 Edge/Local 系統中，如何協調多個深度學習模型對同一張 GPU 的搶佔？**
  - **Ans**:
    1. **排它互斥 (Mutual Exclusion)**：使用 `asyncio.Lock` 限制對模型推論 runtime 的最大並發為 1，避免同時多 context 載入顯存。
    2. **事件通知而非輪詢 (Event-driven Notification)**：使用 `asyncio.Event` 替代 `while is_generating: sleep()`，達到零 CPU 消耗的協程掛起與即時喚醒。
    3. **異質運算分流 (Heterogeneous Offloading / Cloud Bursting)**：在本地硬體負載飽和時，自動將彈性任務分流至雲端彈性算力，保證關鍵服務 SLA。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Systems/19-background-task-system.md](./19-background-task-system.md) 研讀系統背景排程、SPARK 條件推播與週期巡檢架構。
