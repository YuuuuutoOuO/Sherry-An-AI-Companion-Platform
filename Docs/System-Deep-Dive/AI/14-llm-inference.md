# 14 — 多後端推論引擎與備援策略 (LLM Inference Engine)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面解構雪莉的大語言模型推論核心。分析其對多推論後端（本地 Ollama / LM Studio、雲端 Google Gemini、Anthropic Claude、OpenAI、OpenRouter）的抽象客戶端封裝、串流生成 (Token Streaming) 回呼機制、模型前綴正規化 (`_canonical_model_name`)、指數退避重試以及基於 `FALLBACK_MODELS` 的自適應模型切換架構。

---

## 🧭 Prerequisites (先備知識)
- 大語言模型 REST API 通訊格式（OpenAI-compatible `/v1/chat/completions` vs. Ollama `/api/chat`）。
- Server-Sent Events (SSE) 與非同步產生器 (`async for ... in stream`)。
- 策略模式 (Strategy Pattern) 與適配器模式 (Adapter Pattern)。

---

## 💡 Concept (推論可靠性與備援矩陣理論)
在現代 LLM 系統中，單一模型供應商是最大的單點故障 (SPOF)：
1. **地端故障**：Ollama 顯存崩潰、主機負載過高超時。
2. **雲端故障**：API Rate Limit (HTTP 429)、伺服器過載 (HTTP 503)、內容安全過濾器攔截 (Safety Filter / Content Censorship)。
3. **多階備援矩陣 (Multi-tier Fallback Matrix)**：
   - 第一階：原模型短暫重試（解決網路波動）。
   - 第二階：同供應商輕量模型降級（如 70B 降至 8B 或切換至 `LIGHT_MODEL`）。
   - 第三階：跨供應商備援（本地失敗切換至雲端，Gemini 失敗切換至 Claude/GPT-4o）。

---

## 🛠️ Sherry Implementation (雪莉推論引擎實作細節)

### 一、模型名稱正規化 (`_canonical_model_name`)
- 歷史問題：設定檔中若同時出現 `"gemini-1.5-flash"` 與 `"gemini:gemini-1.5-flash"`，直接字串比對會誤判為不同模型，導致備援時重複請求同一個失敗目標。
- `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L25-L33)：
  ```python
  def _canonical_model_name(model_name: str) -> str:
      """去除供應商前綴後的模型正規化名稱，用於比較是否為同一個模型。"""
      m = str(model_name).strip().lower()
      for prefix in ("gemini:", "openai:", "deepseek:", "claude:", "openrouter:", "groq:", "models/"):
          if m.startswith(prefix):
              m = m[len(prefix):]
              break
      return m
  ```

---

### 二、備援候選隊列生成 (`_fallback_candidates`)
- `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L35-L58)：
  - 自動剔除與當前失敗模型重複的候選項目。
  - 當 `FALLBACK_ENABLED = False` 時，強制回傳空清單，落實純地端部署「寧可明確失敗，絕不外送雲端」的隱私防護保證。

---

### 三、多重重試與動態切換迴圈
- `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L237-L288)：
  1. `attempt = 1`：以原始模型發起推論。
  2. `attempt = 2`：若為本地純文字任務，優先轉向 `settings.LIGHT_MODEL` 降低使用者等待延遲。
  3. `attempt >= 3`：依據 `FALLBACK_MODELS` 依序切換不同模型。
  4. 每次嘗試前，均檢查 `task.is_cancelled`，及時終止已放棄的無效計算。

---

### 四、串流推論中斷機制
- `[CONFIRMED]` [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L297-L318)：
  在 `async for chunk in self._ollama.chat_stream(...)` 內部，每個 chunk 到達時均檢查是否被上層取消。一旦被取消，立即 `break` 退出串流並中斷連線，迅速釋放 GPU 算力。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **正規化與候選計算代碼**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L25-L58)。
2. `[CONFIRMED]` **串流回呼與及時中斷**：
   - 見 [`handlers/bot_brain/cog.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/cog.py#L306-L313)。
3. `[CONFIRMED]` **多後端客戶端代碼結構**：
   - 見 [`handlers/bot_brain/client.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/bot_brain/client.py)，內部封裝了 OllamaClient 與多後端轉發（技術債 `TD-002` 記錄未來將進一步抽象為獨立策略）。

---

## 📊 Flow Diagram (推論重試與備援狀態機)

```text
[Task Ingress] ──► Attempt 1 (Primary Model)
                         │
                    (推論失敗 / 逾時)
                         │
                         ▼
                   /───────────────────────\
                  < 本地文字且 attempt == 2 ? >
                   \───────────────────────/
                         /          \
                   (是) /            \ (否)
                       /              ▼
                      ▼        /───────────────────────\
              [切換 LIGHT_MODEL] <   是否有可用 FALLBACK?   >
                      │        \───────────────────────/
                      │              /          \
                      │        (是) /            \ (否)
                      │            ▼              ▼
                      │    [切換下一順位模型]   [原模型繼續嘗試]
                      │            │              │
                      +────────────┴──────────────+
                                   │
                                   ▼
                             [執行 Chat 推論]
                                   │
                           (成功) / \ (再次失敗)
                                 /   \
                                ▼     ▼
                           [回傳結果] [達到 MAX_RETRIES -> 拋出最終錯誤]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **內容審查安全過濾 (Safety Block)**：
   - 雲端模型（如 Gemini / Claude）在遭遇特定涉敏話題時會拋出 `SafetyException` 或回傳 `finish_reason: SAFETY`。
   - `[CONFIRMED]` `_is_content_filtered()` 偵測到此情況時，判定「原模型重試必定重複失敗」，直接觸發跳過重試或嘗試切換至地端無審查模型。
2. **網路抖動與超時疊加**：
   - 若 `BOT_BRAIN_MAX_RETRIES = 3` 且每次超時設為 60 秒，最壞情況下一次請求會卡死 180 秒。雪莉在代碼中限制了 `max_timeout_retries = 1`，避免逾時任務無限堆疊延遲。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **自研 Client 封裝而非使用 LangChain / LiteLLM** | 完全掌控程式碼、零肥大第三方依賴、啟動速度極快、易於精確排錯。 | 每次新型號或 API 規範變動時需手動維護適配代碼。 | 引入 LiteLLM 作為統一推論 Proxy。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在生產環境設計多 LLM 備援機制時，為什麼需要區分「暫態錯誤 (Transient Errors)」與「確定性錯誤 (Deterministic Errors)」？**
  - **Ans**:
    - **暫態錯誤**（如網路短暫中斷、HTTP 502/503/504、HTTP 429 限流）：原模型稍後重試極有可能成功，應執行指數退避 (Exponential Backoff)。
    - **確定性錯誤**（如 Context 長度超標、安全審查過濾、Prompt 格式無效、無效 API Key）：使用完全相同的參數對原模型進行重試 100% 仍會失敗，只會浪費算力與時間。此時必須「立即熔斷跳過」，直接切換架構或替換模型。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [AI/15-two-pass-inference.md](./15-two-pass-inference.md) 研讀雙階段推論 (Two-Pass Inference) 與單階段內嵌標籤調度的架構對比。
