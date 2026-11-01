# 31 — 高階系統設計面試指南 (System Interview Guide)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
彙整 6 道源自雪莉真實架構的頂級系統設計面試題 (Senior System Design Interview Questions)。針對非同步並發模型、GPU 顯存爭用調度、多資料庫儲存治理、Prompt KV 快取工程、Agent 排程策略與生命週期可靠性工程，提供架構師級別的「標準應答範本」、「實證依據」與「技術深度陷阱提示」。

---

## 🧭 Prerequisites (先備知識)
- 分散式與單體高並發系統設計 (System Design Interview) 評審標準。
- FAANG 等級面試框架：需求釐清 (Clarification) ➔ 頂層設計 (High-level Design) ➔ 深度剖析 (Deep Dive) ➔ 瓶頸與權衡 (Bottlenecks & Trade-offs)。
- Asyncio 單執行緒事件循環與底層作業系統資源排程。

---

## 💡 Concept (系統設計面試評估理論與推演框架)
高階系統架構面試並非背誦名詞，而是考察候選人在面對**極端資源約束**（如單機 12GB 顯存、單一 Event Loop 同時跑 WebSocket 與 Web、無分散式快取）時的**系統性權衡能力**：
1. **深度認知物理邊界**：清楚記憶體、GPU VRAM、磁碟 I/O 與 CPU 運算各自的物理瓶頸。
2. **防禦性設計思維**：如何避免單一慢查詢或同步呼叫搞垮整個常駐服務。
3. **優雅降級意識 (Graceful Degradation)**：在資源緊繃或外部服務崩潰時，系統如何無感自愈或退避。

---

## 🛠️ Sherry Implementation (雪莉架構師應答標準題集)

### Q1: 在單機非同步架構中，如何讓一個 WebSocket 常駐應用 (Discord Bot) 與一個 ASGI Web 伺服器 (Quart) 同時運行，且不造成互相凍結？
- **核心考點**：Asyncio 事件循環模型、協程協同、阻塞卸載。
- **架構師滿分回答**：
  1. **共用 Event Loop 機制**：透過 `bot.loop.create_task(server.serve())` 將 Uvicorn ASGI 伺服器作為一個常駐協程掛載進 Discord.py 的主循環中。
  2. **消除阻塞操作 (Zero Blocking I/O)**：嚴格禁止在協程中調用任何同步檔案讀寫、密集 CPU 計算或同步 HTTP 請求。
  3. **非同步卸載**：所有磁碟日誌遍歷、TTS 語音合成等操作，強制使用 `loop.run_in_executor` 委託執行緒池，或使用 `create_subprocess_exec` 移至獨立進程。
  4. **陷阱警示**：若未設限流，Web 管理員的高頻日誌搜尋可能搶佔 CPU，導致 Discord Gateway 心跳超時掉線。

---

### Q2: 在只有一張 12GB 顯存的消費級 GPU 上，如何同時支援本地文字生成 (LLM) 與擴散模型生圖 (Stable Diffusion)，且不發生 CUDA OOM？
- **核心考點**：GPU 顯存爭用、非同步同步原語、零等待降級 (Zero-Wait Degradation)。
- **架構師滿分回答**：
  1. **排它互斥鎖 (`asyncio.Lock`)**：對本地 LLM 推論進行全域串行化，保證同一時間僅存在單一上下文佔用顯存。
  2. **事件狀態機 (`asyncio.Event`)**：維護 `_sd_generating` 旗標與 `_sd_event`。當生圖發動時，清除事件，通知所有地端任務暫停進入 GPU。
  3. **動態雲端繞過 (Cloud Bypass)**：當偵測到地端生圖中時，新進的對話任務自動切換至雲端模型（如 Gemini / OpenAI），**完全繞過本地 GPU 鎖**，並以 `asyncio.create_task` 在背景非同步並行執行，讓使用者完全無感等待。

---

### Q3: 為什麼在對話系統中要將 Prompt 拆為「頭部靜態」與「尾部動態」？這如何影響大語言模型的推論延遲？
- **核心考點**：Transformer KV Cache、Prefix Caching (前綴快取)、首字延遲 (TTFT)。
- **架構師滿分回答**：
  1. **Prefill 計算重用**：現代推論引擎（vLLM、Ollama、Claude）支援前綴快取。若連續請求的開頭 Token 完全相同，引擎可直接重用顯存中的 Key-Value 張量。
  2. **反模式消除**：傳統做法將「當前時間」放在 Prompt 開頭，導致每一分鐘時間變動時，前綴快取 100% 失效。
  3. **雙層注入設計**：雪莉將靈魂人設 (`head_persona`) 維持 100% 靜態置於最前端；將當前時間與動態世界書 (`tail_lore`) 移至對話歷史的最尾端，緊貼生成點。既維持了最大快取命中率，又實現了強約束的世界書注入。

---

### Q4: 為什麼在單體應用中使用多個 SQLite 資料庫時必須啟用 WAL 模式？如何防範並發寫入衝突？
- **核心考點**：SQLite 鎖機制、WAL 模式、PRAGMA busy_timeout、連線管理。
- **架構師滿分回答**：
  1. **讀寫分離**：預設 Rollback Journal 模式下讀寫互斥。WAL 模式透過將寫入追加至 `-wal` 日誌檔，實現讀者與寫者完全並行，大幅提升並發輸送量。
  2. **Busy Timeout 緩衝**：必須配置 `PRAGMA busy_timeout = 5000;`。當兩個執行緒同時嘗試寫入時，後者會在底層退避重試最長 5 秒，避免立即拋出 `database is locked`。
  3. **模組級隔離**：雪莉將對話、記憶、股市、排程拆分為 12 個獨立業務 SQLite 資料庫，將鎖競爭範圍限制在各業務領域內。
  4. **反面實證**：若有代碼繞過工廠使用未設定 WAL 與 timeout 的原生連線（如 `market_plugin` 8 處洩漏），在並發寫入時仍會產生崩潰，需嚴格透過 Linter 阻斷。

---

### Q5: 在 Agent 調度中，Single-Pass（單階段整合）與 Two-Pass（雙階段並行）架構的工程權衡為何？
- **核心考點**：Agent 延遲模型、JSON 結構化輸出、行為自適應對話抑制。
- **架構師滿分回答**：
  1. **Single-Pass (Integrated)**：單次推論內嵌 `<action>` 標籤。延遲低 (1 次往返)，但模型難以在生成對話的同時處理多個複合工具的輸出數據。
  2. **Two-Pass (Parallel)**：第一階段用輕量小模型輸出 JSON Plan；並行執行多工具；第二階段用主模型合成回覆。適合查天氣+查股票+查新聞等複合任務。
  3. **Early Termination (早期對話抑制)**：若雙階段發動的工具全數為 Direct UI（如已直接彈出音樂播放器或圖片），且使用者未要求聊天，系統自動跳過第二階段合成，節省 100% 二階段 Token 與 2~4 秒延遲。

---

### Q6: 當生產環境系統缺乏 SIGINT/SIGTERM 優雅關機處理器時，會引發哪些具體的工程災難？如何補救？
- **核心考點**：OS 信號處理、WAL Checkpoint、進程終止生命週期。
- **架構師滿分回答**：
  1. **資料滯留與冷備份損壞**：SQLite 在 WAL 模式下，最新交易可能只停留在 `-wal` 檔中。若關機未執行 `PRAGMA wal_checkpoint(TRUNCATE);`，維運人員拷貝主 `.db` 檔備份時將遺失最新資料。
  2. **未排空佇列遺失**：推論佇列與背景 Worker 任務被 OS 強制終止，使用者請求無任何錯誤回饋直接懸掛。
  3. **補救方案**：在進入點註冊信號處理器，依序執行（a）停止 Gateway 與 Web 監聽；（b）取消並排空所有背景 Tasks；（c）調用 `core/db.py:checkpoint_all_dbs()` 強制刷盤；（d）刪除 `.pid` 檔案後乾淨退出。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)
1. `[CONFIRMED]` **Q1 共用 Event Loop**：[`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L300) 與 [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2788)。
2. `[CONFIRMED]` **Q2 GPU 顯存排它與繞過**：[`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L178-L203)。
3. `[CONFIRMED]` **Q3 前綴快取分離**：[`handlers/social/persona.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/persona.py#L138-L145)。
4. `[CONFIRMED]` **Q4 WAL 與 Busy Timeout 工廠**：[`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L42-L55)。
5. `[CONFIRMED]` **Q5 雙階段推論與早期抑制**：[`handlers/social/dispatch_agent.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch_agent.py#L160-L210)。
6. `[CONFIRMED]` **Q6 關機缺漏與 Checkpoint 函式**：[`core/db.py:checkpoint_all_dbs()`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L96-L108)。

---

## 📊 Flow Diagram (系統設計面試推演決策流程圖)

```text
               [面試官拋出系統設計情境 (如單機 GPU 爭用)]
                                  │
                                  ▼
                    [Clarification: 釐清硬體物理邊界]
              - VRAM 容量: 12GB (LLM 8GB + SD 6GB > 12GB OOM)
              - 延遲 SLA: 對話 < 3s, 生圖 < 15s
                                  │
                                  ▼
                 [架構分層拆解: 資源調度狀態機]
                                  │
                 +────────────────┴────────────────+
                 │                                 │
                 ▼                                 ▼
         [地端任務排程]                    [使用者體驗保護]
     - asyncio.Lock (全域串行)       - 零等待雲端降級 (Cloud Bypass)
     - asyncio.Event (_sd_event)     - asyncio.create_task (背景派發)
                 │                                 │
                 +────────────────┬────────────────+
                                  │
                                  ▼
                   [深挖瓶頸與 Trade-offs: 權衡總結]
              - 本地隱私 vs. 雲端可用性
              - 前綴 KV Cache 命中 vs. 動態時間戳記位置
```

---

## ⚠️ Failure Cases & Edge Cases (面試常見陷阱與扣分點)
1. **死鎖陷阱 (Deadlock)**：在協程內調用同步鎖或等待事件時未設定逾時 (`asyncio.wait_for`)，當 Worker 崩潰時所有後續排隊任務永遠掛起。
2. **事件循環凍結**：在 Asyncio 函式中直接執行 `open().read()` 大檔案或 `requests.get()`，導致整個伺服器心跳停止。
3. **快取完全失效**：面試時提出「在 System Prompt 開頭加上 `當前時間: 2026-10-07 10:00:00`」，直接破壞 Prefix Caching，被考官判定缺乏現代 LLM 系統常識。

---

## ⚖️ Trade-offs & Engineering Decisions (核心架構權衡總覽)
| 面試維度 | 設計選擇 A | 設計選擇 B | 雪莉工程抉擇與理由 |
|---|---|---|---|
| **Web 伺服器並發** | 獨立 OS Process (Multi-process) | 共用 Event Loop (In-Process Task) | 選擇 B：零 IPC 成本，直接透過記憶體存取 Bot 狀態與排程，透過嚴格禁止 blocking 消除風險。 |
| **GPU 資源排程** | 嚴格排隊等待生圖完成 | 動態轉移至雲端模型 (Cloud Bypass) | 選擇 B：聊天體驗是第一優先，避免使用者因生圖佔用 GPU 而枯等 30 秒。 |
| **儲存架構** | 單一大型 SQLite/PostgreSQL | 12 個業務隔離 SQLite 檔案 | 選擇 B：零維運開銷，避免單一資料庫鎖爭用影響全系統，各模組可獨立備份。 |

---

## 💼 Interview Relevance (各大科技廠面試評量維度)
- **FAANG 評量維度**：
  - **Drive & Scope**：能否從業務需求推導出底層系統容量需求 (Capacity Estimation)。
  - **Trade-off Awareness**：能否主動指出自身設計的缺點（如 SQLite 單寫者限制、缺乏分散式橫向擴展能力）並給出緩解方案。
  - **Failure Mode Analysis**：主動分析斷電、網路抖動、GPU OOM 等極端場景的容災策略。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Learning/32-glossary.md](./32-glossary.md) 研讀全系統領域術語定義。
- 前往 [Learning/33-architecture-atlas.md](./33-architecture-atlas.md) 對照五大維度的全景架構圖鑑。
