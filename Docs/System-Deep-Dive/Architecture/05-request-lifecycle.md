# 05 — 請求與事件處理生命週期流 (Request Lifecycle)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面解剖單一 Discord 互動請求（包含 `@雪莉` 提及、文字訊息、Slash Command 與按鈕互動）從進入 Gateway 到最終回傳 Discord 頻道的端到端生命週期，分析 Trace ID 生成、頻率限制過濾、意圖路由、上下文拼裝、推論佇列排隊、工具執行與智慧分段發送的完整調度路徑。

---

## 🧭 Prerequisites (先備知識)
- Discord Gateway 事件模型 (`MESSAGE_CREATE`, `INTERACTION_CREATE`)。
- 分散式追蹤 (Distributed Tracing) 與 `ContextVar` 傳遞機制。
- LLM Agent 工具呼叫流程 (Tool Calling Loop)。

---

## 💡 Concept (非同步請求處理管線理論)
現代對話代理人的請求管線通常由七個循序階段組成：
1. **Ingress & Observability (接入與追蹤)**：提取事件中繼資料，生成關聯 Trace ID。
2. **Security & Rate Limiting (安全防禦與限流)**：防禦洪水攻擊 (Flooding) 與惡意輸入。
3. **Context Assembly (上下文裝配)**：拉取長期記憶、短期對話紀錄、頻道氛圍與動態人設。
4. **Planning & Routing (規劃與路由)**：判斷為純對話、單工具呼叫或多步驟 Plan。
5. **Inference Execution (推論執行)**：將組裝好的 Prompt 送交 GPU/API 推論佇列。
6. **Tool Invocation & Feedback (工具呼叫與反饋)**：執行沙盒工具，並將結果回填或直接渲染 UI。
7. **Egress & Dispatch (渲染與派發)**：Discord 訊息字數截斷、Embed 格式化與 HTTP 回傳。

---

## 🛠️ Sherry Implementation (雪莉請求處理架構實作)
雪莉的事件與請求管線實作遵循六大嚴格階段：
1. **進入點攔截與 Trace ID 生成**：`handlers/social/__init__.py:SocialCog.on_message` 接收 Gateway 事件後呼叫 `new_trace_id("chat")`（斜線指令則由 `main.py:on_interaction` 呼叫 `new_trace_id("cmd")`）並綁定 `ContextVar`。
2. **安全守衛與速率限制**：`handlers/social/__init__.py` 透過 `core/guard.py` 實施群級/用戶級滑動視窗限流與黑名單過濾。
3. **動態記憶與上下文組裝**：`memory_builder.py` 提取 `sherry_memory.db` 與 `chat_history.db`，由 `persona.py` 分離靜態 `head_persona` 與動態 `tail_lore`。
4. **推論排程與任務入列**：`BotBrain` 透過 `PriorityQueue` 進行分級排隊，指派全域單調遞增 `sequence_id`。
5. **資源約束與推論執行**：`_inference_worker` 根據地端生圖狀態 (`_sd_event`) 判定是否需雲端繞過，地端任務獲取 `_lock` 排它執行。
6. **結果解析與智慧聚合**：解析 `<action>` 工具標籤，經 `SmartMessageDispatcher` 依 2000 字元長度自動分段派發至 Discord。

---

## 📊 Flow Diagram (完整請求處理時序圖)

```text
[Discord Gateway]
       |
       | 1. MESSAGE_CREATE
       v
[handlers/social/SocialCog : on_message]
       |
       | 2. new_trace_id("chat") -> ContextVar
       v
[handlers/social/SocialCog]
       |
       +---> 3. Rate Limit Check (core/guard.py) ──(超出上限)──> 發送沙漏冷卻提示並中止
       |
       +---> 4. 是否為自身訊息 / 忽略機器人 (Author check)
       |
       +---> 5. 記憶建構 (handlers/social/memory_builder.py)
       |           - 讀取 sherry_memory.db (好感度、使用者事實)
       |           - 讀取 chat_history.db (近 N 輪短期對話)
       |           - 讀取 vibe_manager (近期情緒傾向)
       |
       +---> 6. 動態人設組裝 (handlers/social/persona.py)
       |           - head_persona: 靜態 SOUL + 互動鐵則
       |           - tail_lore: 當前時間戳記 + Lorebook 觸發關鍵字
       |
       +---> 7. 進入 DispatchAgent (handlers/social/dispatch_agent.py)
                   |
                   +---> 依設定選擇策略 (INTEGRATED / PARALLEL / REACT)
                   |
                   +---> 建構 InferenceTask (設定優先度、System Prompt、User Prompt)
                   |
                   v
       [handlers/bot_brain : enqueue_task]
                   |
                   | 8. PriorityQueue 排隊
                   v
       [_inference_worker]
                   |
                   +---> [地端生圖中?] ──(是)──> 依 FALLBACK_MODELS 轉雲端模型 [CONFIRMED]
                   |
                   +---> [地端模型?] ──(是)──> await _sd_event.wait() -> async with _lock
                   |                          (保證 GPU 顯存排它串行執行)
                   |
                   +---> [雲端模型?] ──(是)──> asyncio.create_task (背景並行發送)
                   |
                   v
       [OllamaClient / CloudClient] ──> 完成推論字串回傳
                   |
                   v
       [Result Parsing & Action Dispatch]
                   |
                   +---> 解析 <action> 標籤 (若有發動工具)
                   |        - 呼叫 Tool (如 DrawTool, SearchTool, StockTool)
                   |        - 取得 ToolResult
                   |
                   v
       [SmartMessageDispatcher : send_aggregated_message]
                   |
                   | 9. 超過 2000 字元自動切分段落、發送 Discord Channel
                   v
       [Discord REST API]
```

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **互動 Trace ID 攔截**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L306-L320)：
     ```python
     if itype == discord.InteractionType.application_command:
         tid = new_trace_id("cmd")
         set_trace_id(tid)
     ```
2. `[CONFIRMED]` **優先佇列入列**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L133-L144)，任務分配 `sequence_id` 後寫入 `self._queue.put(task)`。
3. `[CONFIRMED]` **智慧訊息分段派發**：
   - 見 [`core/dispatcher.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/dispatcher.py#L1-L80)，強制檢查 `len(content) > 1900` 進行智慧換行切分。

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **3 秒 Interaction Token 過期 (Discord Timeout 10062)**：
   - Discord 要求所有 Slash Command 互動必須在 **3 秒** 內收到 ACK（即 `defer()` 或 `respond()`）。
   - 當本地 LLM 推論需耗時 10~20 秒時，若未在進入推論前呼叫 `await interaction.response.defer()`，Discord 將直接拋出 `404 Not Found (Unknown interaction)`。
2. **排隊幽靈任務 (Ghost Task Cancelled on Timeout)**：
   - `[CONFIRMED]` 若使用者在佇列排隊過久時自行刪除訊息或超時，上層觸發取消。`BotBrain._process_task()` 在拿到 GPU 鎖前後均會檢查 `getattr(task, "is_cancelled", False)`，若已取消則直接跳過推論釋放資源。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **單一優先佇列 (PriorityQueue) 串行本地推理** | 徹底杜絕地端 GPU 發生 CUDA Out of Memory (OOM)，保證顯示卡穩定性。 | 高並發時請求延遲排隊線性增加，後進請求等候時間拉長。 | 部署 vLLM / TensorRT-LLM 啟用動態連續批次處理 (Continuous Batching)。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在 Discord Bot 結合大語言模型時，如何優雅處理 Discord 3 秒 ACK 限制與 LLM 延遲？**
  - **Ans**:
    1. 第一時間在 Controller 層調用 `await interaction.response.defer()`，向 Discord 取得 15 分鐘的非同步 followup 視窗。
    2. 若採用前綴對話 (`!xxx` 或 `@Bot`)，則在接收到訊息後立即調用 `async with channel.typing():` 發送「正在輸入中」打字反饋，維持使用者介面響應性。
    3. 推論完成後，以 `interaction.followup.send()` 或 `channel.send()` 派發最終回覆。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Architecture/06-data-flow.md](./06-data-flow.md) 研讀跨模組資料流轉與狀態轉移拓撲。
