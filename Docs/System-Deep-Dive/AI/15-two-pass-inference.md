# 15 — 雙階段推論架構 (Two-Pass Inference Architecture)

> **難度標記**：`Difficulty: Expert`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉伴侶系統中的核心 Agent 執行架構：**雙階段推論 (Two-Pass Inference: Planner-Execution-Synthesis)** 與**單階段內嵌標籤推論 (Integrated Single-Pass)** 的架構對比。探討快速小模型規劃 (Planner) 與高階大模型合成 (Synthesizer) 的職責劃分、多工具並行執行以及行為自適應對話抑制 (Early Termination) 機制。

---

## 🧭 Prerequisites (先備知識)
- 大語言模型規劃器 (LLM Planner) 與工作流編排 (Orchestration)。
- 單階段內嵌 Function Call vs. 雙階段 ReAct 模式。
- JSON Schema 結構化輸出 (Structured Outputs) 與非同步並行執行。

---

## 💡 Concept (雙階段 vs. 單階段推論架構理論)
在複雜 AI Agent 系統中，讓大模型同時處理「工具決策」與「角色扮演回覆」常面臨兩難：
1. **單階段推論 (Single-Pass)**：模型在回覆中直接內嵌工具指令（如 `<action name="DRAW">...</action>`）。
   - 優點：延遲極低（僅需一次 LLM 往返）。
   - 缺點：若需要多個工具協同，或工具回傳結果需要被模型進一步消化解讀時，模型容易手忙腳亂，破壞角色語氣。
2. **雙階段推論 (Two-Pass / Split-Phase)**：
   - **第一階段 (Planning Pass)**：由輕量高速模型（或特定工具調用 Prompt）純粹分析使用者意圖，產出精確的 JSON 執行計畫與工具清單。
   - **工具執行 (Execution Phase)**：非同步並行 (`asyncio.gather`) 觸發所有需要的外部工具。
   - **第二階段 (Synthesis Pass)**：將所有工具執行結果彙整回填，由主靈魂模型 (Primary Model) 帶入完整角色人設，生成兼具專業資料與生動人設的最終回覆。

---

## 🛠️ Sherry Implementation (雪莉雙階段調度實作)

### 一、ParallelStrategy 雙階段調度 (`handlers/social/dispatch/parallel.py`)
- `[CONFIRMED]` [`handlers/social/dispatch/parallel.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/parallel.py#L34-L120)：
  1. **第一階段 (Planner)**：
     - 使用 `_PARALLEL_PLANNER_SYSTEM_TEMPLATE` 指引模型輸出 JSON 格式的 `ExecutionPlan`：
       ```json
       {
         "tasks": [
           {"tool": "SEARCH", "input": "台北天氣"},
           {"tool": "STOCK", "input": "2330"}
         ],
         "needs_persona_reply": true
       }
       ```
  2. **並行工具發動**：
     - 透過 `asyncio.gather(*[execute_tool(t) for t in plan.tasks])` 同步執行多個工具。
  3. **第二階段 (Synthesis)**：
     - 將搜尋結果（天氣資訊）與股票資料包裝為 `<tool_results>` 上下文，再次呼叫大腦主模型合成繁體中文陪伴回覆。

---

### 二、行為自適應對話抑制 (Early Termination)
在某些場景下（例如使用者僅說「放這首歌」或「畫一張貓咪圖」），工具執行完成後（Discord 已經直接彈出播放清單 Embed 或圖片視圖），若 AI 又回覆一句「好的，我已經幫你播放囉」，往往顯得囉嗦且浪費 Token。

- `[CONFIRMED]` [`handlers/social/dispatch/parallel.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/parallel.py#L41-L59)：
  ```python
  def should_early_terminate(
      self,
      plan: ExecutionPlan,
      executed_results: list[tuple[str, ToolResponseMode, str]],
      user_query: str,
  ) -> bool:
      if not getattr(settings, "ACTION_EARLY_TERMINATION_ENABLED", True):
          return False
      if not executed_results:
          return False
      # 若全數工具皆為 DIRECT_UI (如音樂、畫圖)，且計畫不需要人設回覆
      if any(mode != ToolCategory.DIRECT_UI for _, mode, _ in executed_results):
          return False
      if plan.needs_persona_reply:
          return False
      # 使用者有明確要求閒聊或詢問看法時，不提前抑制
      if re.search(r"(並|而且|順便|跟我說|告訴我|分享|覺得|看法|心得|對話|聊天)", user_query):
          return False
      return True
  ```
  此邏輯省去了不必要的第二階段推論，省下 100% 的二階段 Token 與 2~5 秒延遲。

---

### 三、單階段整合策略對照 (`handlers/social/dispatch/integrated.py`)
- 為維持日常對話極致低延遲，雪莉的預設模式通常設為 `INTEGRATED`（單階段整合式）：
  - 模型直接生成正文，在正文中夾帶 `<action name="...">...</action>`。
  - 正則表達式動態抽取標籤並發動工具，純聊天時只需一次推論即可完成回覆。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **雙階段策略類別定義**：
   - 見 [`handlers/social/dispatch/parallel.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/parallel.py#L34-L40)。
2. `[CONFIRMED]` **早期對話抑制邏輯**：
   - 見 [`handlers/social/dispatch/parallel.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/parallel.py#L41-L59)。
3. `[CONFIRMED]` **單階段內嵌標籤策略**：
   - 見 [`handlers/social/dispatch/integrated.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/integrated.py#L36-L56)。

---

## 📊 Flow Diagram (雙階段 vs. 單階段對照圖)

```text
【模式 A：雙階段並行調度 (ParallelStrategy)】
[User Input]
     │
     ▼ (Pass 1)
[Planner Model] ──► 產出 JSON Plan: [Tool A, Tool B]
                         │
                         ▼ (asyncio.gather 並行執行)
              +----------+----------+
              │                     │
              ▼                     ▼
         [Execute Tool A]      [Execute Tool B]
              │                     │
              +----------+----------+
                         │
                         ▼
             /───────────────────────\
            <  觸發 Early Termination? >
             \───────────────────────/
                   /          \
             (是) /            \ (否)
                 ▼              ▼ (Pass 2)
           [直接結束]      [Synthesizer Model]
                           (帶入人設與工具輸出合成回覆)

-------------------------------------------------------------------------

【模式 B：單階段整合調度 (IntegratedStrategy)】
[User Input]
     │
     ▼ (Single Pass: 僅推論一次)
[Primary Model] ──► 生成對話文本 + 內嵌 <action> 標籤
                         │
                         ▼ (正則解析並非同步發動)
                    [Execute Tool]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **Planner 輸出無效 JSON (Malformed JSON)**：
   - 若 Planner 小模型在輸出 JSON 時夾帶了額外開場白或括號不閉合，正則解析器 `_parse_plan_json` 會啟動修復邏輯。若修復依然失敗，系統會優雅降級為純聊天文字回覆，避免拋出 500 例外。
2. **多工具局部失敗 (Partial Tool Failure)**：
   - 若 Planner 同時請求了查天氣與查股票，股票 API 成功但天氣 API 逾時，合成階段會將天氣錯誤訊息包裝告知 Synthesizer，由雪莉以人設口吻告知群友「股票查到了，但氣象站好像連不上耶！」。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **提供雙模式切換 (Integrated vs. Parallel)** | 平衡了日常極速對話 (Integrated) 與複合複雜工具任務 (Parallel) 的需求。 | 維護兩套獨立的 Strategy 代碼與 Prompt 模板，增加了維護複雜度。 | 僅保留單一 ReAct 模式。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在 Agent 系統設計中，何時應選擇 Single-Pass 內嵌呼叫？何時應選擇 Two-Pass 雙階段推論？**
  - **Ans**:
    - **Single-Pass 適用情境**：單一工具、日常閒聊、對首字延遲 (TTFT) 極端敏感的場景（如語音對話），可省下 50% 以上推論時間。
    - **Two-Pass 適用情境**：需要多個非同質工具協同計算（如同時檢索資料庫、網路搜尋與呼叫計算器）、或工具輸出資料龐大需要精準提煉摘要才能回覆使用者的複合工作流。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [AI/16-agent-architecture.md](./16-agent-architecture.md) 研讀智慧代理調度策略矩陣 (Integrated / Parallel / ReAct) 的實作全貌。
