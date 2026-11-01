# 16 — 智慧代理調度策略矩陣 (Agent Architecture)

> **難度標記**：`Difficulty: Expert`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉系統內建的動態 Agent 調度策略矩陣。解析基於物件導向策略模式 (Strategy Pattern) 實作的三大調度策略：`INTEGRATED`（單階段整合）、`PARALLEL`（雙階段並行）與 `REACT`（經典推理-行動多輪迴圈）。探討其共同基底介面 `BaseDispatchStrategy`、動態策略切換機制與多模態感知 (Vision Awareness) 注入。

---

## 🧭 Prerequisites (先備知識)
- 軟體設計模式：策略模式 (Strategy Pattern)。
- ReAct 框架 (Reasoning + Acting in Language Models)。
- 多模態 Vision 上下文注入與圖像辨識路由。

---

## 💡 Concept (Agent 調度策略演進理論)
LLM Agent 的架構演進經歷了三個典型世代：
1. **第一代：ReAct (Reason + Act)**：模型交替輸出 Thought -> Action -> Observation -> Thought...，適合複雜探索與錯誤自我修正，但延遲長且 Token 開銷大。
2. **第二代：Parallel Planning (DAG / Parallel)**：一次性產出任務清單並發執行，解決了多工具順序等待的延遲瓶頸。
3. **第三代：Integrated Native Streaming**：模型在原生對話流中自然觸發 Action，以最自然的對話節奏混合多項能力。

雪莉並未押注單一技術，而是將其封裝為**策略矩陣 (Strategy Matrix)**，允許管理員在設定檔中動態切換。

---

## 🛠️ Sherry Implementation (雪莉策略矩陣架構實作)

### 一、策略基底契約 (`handlers/social/dispatch/base.py`)
- `[CONFIRMED]` [`handlers/social/dispatch/base.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/base.py)：
  ```python
  class BaseDispatchStrategy(abc.ABC):
      @property
      @abc.abstractmethod
      def mode_name(self) -> str: ...

      @abc.abstractmethod
      async def execute(
          self,
          brain: Any,
          bot: commands.Bot,
          message: discord.Message,
          user_query: str,
          vibe_snippet: str,
          full_context: str,
          images: list[str] | None,
          start_time: float,
          visual_context: str = "",
      ) -> None: ...
  ```

---

### 二、三大策略對比矩陣

| 策略名稱 | 實作檔案 | 核心特點 | 典型延遲 | 適用場景 |
|---|---|---|---|---|
| **`INTEGRATED`** | `dispatch/integrated.py` | [CONFIRMED] 單階段內嵌 `<action>` 標籤，純對話一次回覆 | 1.0x (基準) | 日常高速閒聊、單一工具委託 |
| **`PARALLEL`** | `dispatch/parallel.py` | [CONFIRMED] 輕量模型生成 JSON Plan，並行執行多工具，主模型合成 | 1.5x ~ 2.0x | 跨工具複合查詢（如同時查股價、天氣、新聞） |
| **`REACT`** | `dispatch/react.py` | [CONFIRMED] 經典多輪 Thought-Action-Observation 迴圈 (最多 3 輪) | 2.5x ~ 4.0x | 具備依賴關係的深度問題排除與自愈探針 |

---

### 三、多模態視覺上下文注入 (Vision Awareness)
當使用者在 Discord 訊息中附帶圖片時：
- `[CONFIRMED]` [`handlers/social/dispatch/prompts.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/prompts.py)：
  - 自動觸發 `extract_vision_context()`。
  - 將 base64 圖片編碼填入 `InferenceTask.images` 陣列。
  - 注入 `VISION_AWARENESS_RULE` 提示詞，告知模型：「使用者已上傳圖片，請觀察圖中細節並結合妳的角色性格做出反應」。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **策略基底與派生類別**：
   - 見 [`handlers/social/dispatch/base.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/base.py)、[`integrated.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/integrated.py)、[`parallel.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/parallel.py)、[`react.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch/react.py)。
2. `[CONFIRMED]` **動態策略工廠**：
   - 見 [`handlers/social/dispatch_agent.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/dispatch_agent.py)，讀取 `settings.AGENT_DISPATCH_MODE` (預設 `"INTEGRATED"`) 取得對應 Strategy 實體。

---

## 📊 Flow Diagram (策略矩陣分流時序圖)

```text
[Incoming Message] ──► [DispatchAgent]
                             │
                             │ 讀取 settings.AGENT_DISPATCH_MODE
                             ▼
               /───────────────────────────\
              <    當前配置是哪一種調度策略?   >
               \───────────────────────────/
                     /         │         \
        INTEGRATED  /   PARALLEL│          \  REACT
                   /           │           \
                  ▼            ▼            ▼
       +-------------+  +-------------+  +-------------+
       | Integrated  |  |  Parallel   |  |    ReAct    |
       |  Strategy   |  |  Strategy   |  |  Strategy   |
       +------+------+  +------+------+  +------+------+
              │                │                │
              │ 單推論內嵌     │ Plan + 並行    │ 多輪迭代探查
              │ <action>       │ 工具 + 合成    │ (最多 3 輪)
              │                │                │
              +----------------+----------------+
                               │
                               ▼
                   [SmartMessageDispatcher]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **ReAct 模式死循環 (Infinite Action Loop)**：
   - 在 ReAct 模式中，若模型反覆產生相同 Action 但 Observation 一直為錯誤訊息，可能陷入無休止的迴圈。雪莉在 `react.py` 中設定了 `MAX_REACT_STEPS = 3` 的硬性安全防禦，超過步數立即終止迴圈並強制進入總結回覆。
2. **多模態大圖 OOM**：
   - 使用者上傳 8K 超高解析度圖片時，base64 字串會佔用數十 MB 記憶體。若直接送交視覺模型，會擠爆 GPU 顯存。雪莉在工具層對過大圖片進行等比壓縮縮圖後再行編碼。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **以設定檔統一指定全域 Strategy** | 系統行為可預測、除錯單純、各策略邏輯完全隔離。 | 無法依據單一使用者的輸入問題難度動態在 Integrated 與 ReAct 間自動昇級 (Auto-escalation)。 | 引入 Meta-Agent 評估任務複雜度動態選擇 Strategy。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在生產環境落地 LLM Agent 時，如何防止 ReAct 迴圈耗盡 API 預算與產生失控延遲？**
  - **Ans**:
    1. **步數硬限制 (Max Iterations)**：設定最大執行輪次（如 3 輪），達到即強制截斷。
    2. **超時熔斷器 (Per-request Timeout)**：針對整個 Agent 流程設置全域 `asyncio.wait_for(timeout=30)`。
    3. **重複動作偵測 (Deduplication Check)**：記錄前一輪的 (Action, Args)，若連續兩輪出現完全相同的呼叫參數，判定為邏輯卡死，直接阻斷。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [AI/17-tool-architecture.md](./17-tool-architecture.md) 研讀工具呼叫底層合約、工具註冊表與沙盒安全性。
