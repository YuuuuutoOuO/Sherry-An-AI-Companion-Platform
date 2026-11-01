# 29 — 端到端典型請求穿越案例剖析 (End-to-End Case Study)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
透過一個真實且涵蓋多項子系統的複合性對話情境，帶領讀者完整穿越雪莉的每一行關鍵執行路徑。以情境：「**@雪莉 今天台北天氣如何？順便幫我畫一張雨中女孩的插畫！**」為例，追蹤其在非同步事件循環、記憶組裝、並行調度、GPU 顯存鎖互斥以及雙輸出渲染中的全鏈路行為。

---

## 🧭 Prerequisites (先備知識)
- 前述 01 至 28 章節之所有系統基礎知識。
- 熟悉 Discord Gateway、Quart、SQLite WAL 與 LLM 推論互動時序。

---

## 💡 Concept (系統端到端追蹤方法論)
一個健壯的系統設計，必須能經受住「跨邊界高並發」情境的考驗。此案例同時觸發了：
1. 自然語言社交對話 (Social & Persona)。
2. 結構化外部聯網搜尋 (`SEARCH` 工具 / Google Search API 查天氣)。
3. 重度硬體資源搶佔 (`DRAW` Tool 觸發繪圖引擎)。
4. 訊息智慧分段與 UI View 元件裝配。

---

## 🛠️ Sherry Implementation (端到端完整時序穿越紀錄)

### 階段一：網關接入與上下文追蹤 (Ingress & Observability)
- **T = 0 ms**：Discord 伺服器向雪莉發送 `MESSAGE_CREATE` WebSocket 封包。
- **T = 2 ms**：`handlers/social/__init__.py:SocialCog.on_message` 接收 `@雪莉` 訊息，呼叫 `new_trace_id("chat")` 生成追蹤碼 `chat-8f92a1c0`，存入 `_trace_id_ctx` (ContextVar)。
- **T = 4 ms**：`handlers.social.SocialCog` 接收事件，`core/guard.py` 檢查該使用者與該頻道的調用頻率，未超標，放行。

---

### 階段二：記憶檢索與動態人設組裝 (Context Assembly)
- **T = 8 ms**：`memory_builder.py` 啟動：
  - 查詢 `sherry_memory.db`：讀取該使用者好感度為 `72 (close_friend)`，近 3 條重要記憶：「喜歡動漫」、「居住在北部」。
  - 查詢 `chat_history.db`：撈取前 10 輪短期對話。
- **T = 16 ms**：`persona.py` 拼裝雙層 Prompt：
  - `head_persona`：載入 `sherry_default.md` 靈魂人設 + 互動鐵則（100% 靜態）。
  - `tail_lore`：注入當前台灣時間 `2026-10-07 10:48` + 關鍵字 Lorebook。

---

### 階段三：Agent 意圖規劃與多工具調度 (Planning & Execution)
- **T = 25 ms**：進入調度器（以 `ParallelStrategy` 雙階段模式為例）：
- **T = 28 ms**：發送任務給 Planner 模型，解析出兩項子任務：
  1. `PlannedTask(tool="SEARCH", input="台北 天氣")`
  2. `PlannedTask(tool="DRAW", input="a girl in the rain, anime illustration")`
- **T = 220 ms**：Planner 回傳 JSON Plan，系統以 `asyncio.gather` 並行觸發兩大工具：
  - **支線 A (SearchTool / SEARCH)**：呼叫 Google Search API 檢索即時氣象（耗時 280ms），取得「台北市目前氣溫 24°C，陰有陣雨，降雨機率 80%」，標記模式為 `DATA_PROVIDER`。
  - **支線 B (DrawTool)**：
    1. 標記 `brain.sd_generating = True`（觸發 `self._sd_event.clear()`，鎖定本地 VRAM）。
    2. 發起繪圖生成（耗時 8,500ms）。
    3. 生成完成後建立 `DrawControlView`（附加「🔄 重畫一張」按鈕），直接向頻道發送圖片 Embed（模式為 `DIRECT_UI`）。
    4. 進入 `finally` 區塊，標記 `brain.sd_generating = False`（觸發 `self._sd_event.set()`，釋放 VRAM 鎖）。

---

### 階段四：角色回覆合成與智慧派發 (Synthesis & Egress)
- **T = 8,600 ms**：繪圖完成，Synthesizer 模型（主大腦）接收天氣資料，帶入摯友親暱語氣合成回覆：
  > 「台北現在正下著雨呢，氣溫大概 24 度左右～出門記得帶傘喔！對了，你剛剛要的雨中女孩我也畫好囉，快看看喜不喜歡？☔」
- **T = 9,400 ms**：`SmartMessageDispatcher` 檢查回覆長度未超過 1900 字元，呼叫 `channel.send()` 派發。
- **T = 9,450 ms**：將完整對話記錄與 Trace ID 寫入 `chat_history.db`，全流程圓滿結束。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **生圖鎖定與釋放閉環**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L106-L112)。
2. `[CONFIRMED]` **DrawControlView 互動按鈕掛載**：
   - 見 [`handlers/social/tools/draw_tool.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/tools/draw_tool.py#L22-L65)。
3. `[CONFIRMED]` **雙階段並行調度執行體**：
   - 見 [`handlers/social/dispatch/parallel.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/parallel.py#L60-L150)。

---

## 📊 Flow Diagram (全鏈路泳道循序圖)

```text
User            Discord GW        handlers.social       DrawTool / SearchTool        BotBrain / LLM
 │                  │                    │                         │                      │
 ├─(發送訊息)──────►│                    │                         │                      │
 │                  ├─(MESSAGE_CREATE)──►│                         │                      │
 │                  │                    ├─(產生 Trace ID)         │                      │
 │                  │                    ├─(讀取記憶與人設)        │                      │
 │                  │                    │                         │                      │
 │                  │                    ├─(規劃 Phase 1)─────────┼─────────────────────►│
 │                  │                    │◄────────────────────────┼─────(回傳 Plan JSON)─┤
 │                  │                    │                         │                      │
 │                  │                    ├─(並行發動 Tool)────────►│                      │
 │                  │                    │                         ├─(Google Search: 280ms)│
 │                  │                    │                         ├─(鎖定 sd_event)      │
 │                  │                    │                         ├─(生圖渲染: 8500ms)    │
 │                  │                    │                         ├─(釋放 sd_event)      │
 │                  │                    │◄──(回傳圖片與天氣)──────┤                      │
 │                  │                    │                         │                      │
 │                  │                    ├─(合成 Phase 2)─────────┼─────────────────────►│
 │                  │                    │◄────────────────────────┼─────(回傳伴侶回覆)───┤
 │                  │                    │                         │                      │
 │                  │◄─(發送 Discord 訊息)┤                         │                      │
 │◄─(看見文字與圖片)─┤                    │                         │                      │
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

- **情境分析：若在 T = 3,000 ms (生圖中)，另一位群友在另一個頻道發送「@雪莉 早安！」**
  - **行為驗證**：
    1. 該早安任務進入 `BotBrain._queue`。
    2. Worker 出列時偵測到 `self._sd_generating == True`。
    3. `[CONFIRMED]` 依據 `FALLBACK_MODELS`，該早安任務自動切換至雲端模型（如 Gemini-1.5-Flash），繞過 `self._lock`，在背景非同步發送。
    4. 該群友在 1 秒內順利收到早安回覆，絲毫不受第一位使用者的 8.5 秒生圖所阻礙！

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

- 此案例完美展現了雪莉「混成架構 (Hybrid Architecture)」的強大生命力：重型計算（繪圖）使用本地 GPU，輕量且高頻的任務在資源緊張時彈性爆發至雲端，兼顧了隱私、成本與極致的使用者體驗。

---

## 💼 Interview Relevance (面試考點)

- **Q: 請描述一個你設計過最複雜的非同步跨系統工作流，並說明如何防止死鎖？**
  - **Ans**: 本案例即為標準範本。重點在於（1）明確定義同步原語職責（`Lock` 管串行，`Event` 管狀態通知）；（2）所有非同步狀態變更必須具備 `try...finally` 保證；（3）提供降級通道（如雲端繞過）避免關鍵業務因鎖爭用而陷入佇列雪崩。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Learning/30-sherry-system-mental-model.md](../Learning/30-sherry-system-mental-model.md) 建立系統工程總體心智模型。
