# 13 — 動態 Context Engine 與 Prefix 快取優化 (Context Engine)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]` (Prompt 前綴恆定結構) / `[REQUIRES RUNTIME VERIFICATION]` (後端真實 KV 快取重用)

---

## 🎯 Purpose (核心目的)
深度剖析雪莉的 Context Engine（上下文組裝引擎）。詳解其借鑒 SillyTavern「雙層注入 (Dual-Layer Injection)」架構設計：**頭部靜態人設 (`head_persona`)** 與**尾部動態世界書 (`tail_lore`)**。分析為何將動態時間標籤刻意移出前綴以最大化後端 Prefix/KV 快取命中率，並客觀評估各推論後端對 KV 快取的實際重用支援狀況。

---

## 🧭 Prerequisites (先備知識)
- Transformer 解碼器架構中的 Key-Value Cache (KV Cache) 與 Attention 機制。
- 現代推論引擎（vLLM PagedAttention、Ollama/llama.cpp Radix Tree Cache、Claude/Gemini Prompt Caching）的前綴共享原理。
- SillyTavern Author's Note 深度注入機制。

---

## 💡 Concept (Prefix Caching 與動態 Prompt 工程理論)
在 LLM 互動系統中，Prompt 處理 (Prefill Phase) 通常佔據首字延遲 (TTFT) 的 70% 以上：
1. **Prefix Caching 理論**：若多個連續請求的 Prompt 開頭（前 N 個 Token）**字元級完全一致**，推論引擎可以直接重用已計算完成的 Key-Value 向量張量，省去高昂的 Attention 矩陣計算，大幅降低 TTFT 與伺服器負擔。
2. **「動態時間」破壞快取的反模式 (Anti-Pattern)**：許多初學者習慣在 System Prompt 最開頭寫入 `目前時間：2026-10-07 10:45:01`。每一秒鐘的時間變動都會導致開頭 Token 改變，**徹底粉碎所有前綴 KV 快取**，強迫 GPU 每次重新 Prefill。

---

## 🛠️ Sherry Implementation (雪莉雙層 Prompt 拼裝架構)

### 一、動態人格拼裝系統 (`handlers/social/persona.py`)
雪莉在 `handlers/social/persona.py` 中實作了精巧的雙層分離：

- `[CONFIRMED]` [`handlers/social/persona.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/persona.py#L138-L186)：
  ```python
  # ── 頭部人設（從 SOUL 檔載入或 fallback 至 CORE_PERSONA + 通用互動規則）──────────────
  # 為了最大化 LLM 前綴快取 (Prefix/KV Cache) 命中率，動態時間標籤改移至尾部 tail_lore，
  # 確保 head_persona 內容在相同頻道與意圖下完全靜態恆定。

  soul_text = _load_soul_persona(channel_id)
  rule_text = _load_common_rules()
  head_blocks: list[str] = [soul_text]
  if rule_text:
      head_blocks.append(rule_text)

  # 附加意圖狀態設定 (CHAT, STOCK, DRAW, SEARCH 等)
  if intent_name == "CHAT":
      head_blocks.append("💬【狀態設定】：正在與身邊重要的人面對面輕鬆交談！...")

  # ── head_persona 保持 100% 靜態恆定（僅包含 SOUL 靈魂設定與意圖狀態）───────
  head_persona = "\n\n".join(head_blocks)
  ```

---

### 二、尾部動態世界書注入 (`tail_lore`)
- `[CONFIRMED]` 易變動的動態資訊全部下沉至尾部：
  ```python
  time_block = f"⏰【目前時間】：{now_tw.strftime('%Y-%m-%d %H:%M')}（台灣時間，{weekday_names[now_tw.weekday()]}）"
  # 高優先度 Lorebook 關鍵字匹配（性暗示過濾、親密互動指令、專屬條款）
  # 緊貼生成點，模仿 SillyTavern Author's Note
  tail_lore = "\n\n".join([time_block, *matched_lores])
  ```

---

### 三、後端真實 KV 快取驗證與現狀分析
`[CONFIRMED]` 程式碼結構上確已做到「前綴 100% 靜態一致」。  
`[REQUIRES RUNTIME VERIFICATION]` **後端真實重用成效需依賴具體執行環境**：
1. **地端 Ollama / llama.cpp**：
   - `[LIKELY]` Ollama 在相同 session 且未切換 model 時具備 Context Shift 與 Prompt Cache 機制；但若多群組請求交替抵達，Ollama 的 slot 分配可能需要重新 prefill。
2. **雲端 Anthropic Claude**：
   - `[CONFIRMED]` Claude 需要顯式傳遞 `"cache_control": {"type": "ephemeral"}` 標頭才能啟用 Prompt Caching。若客戶端未配置該標頭，雲端後端不會自動快取。
3. **雲端 Google Gemini**：
   - `[REQUIRES RUNTIME VERIFICATION]` Gemini 官方快取需透過特定 Cache API 建立快取資源 (Context Caching)，標準 REST 請求是否命中內部隱式快取取決於 Google 伺服器端排程。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **head_persona 靜態保證註釋與邏輯**：
   - 見 [`handlers/social/persona.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/persona.py#L138-L141,L185-L186)。
2. `[CONFIRMED]` **動態時間移至尾部實證**：
   - 見 [`handlers/social/persona.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/persona.py#L141-L145)。
3. `[CONFIRMED]` **LRU 記憶體快取加速靈魂檔案讀取**：
   - 見 [`handlers/social/persona.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/persona.py#L20,L69)：`@functools.lru_cache(maxsize=16)` 確保磁碟 Markdown 靈魂檔不重複 I/O。

---

## 📊 Flow Diagram (雙層 Prompt 拼裝與 KV 快取關係圖)

```text
+-----------------------------------------------------------------------------------+
|                        Prompt Tokens Sequence Layout                              |
+-----------------------------------------------------------------------------------+

 [Token 0 ──────────────────────────────────────────────► Token 1200]
  ┌───────────────────────────────────────────────────────────────┐
  │                   head_persona (100% 靜態恆定)                 │
  │   - SOUL 核心人設 (sherry_default.md)                          │
  │   - 通用防幻覺鐵則 (common_rules.md)                           │
  │   - 當前意圖指示 (CHAT / STOCK / MUSIC 狀態)                   │
  └───────────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
                    ⚡ 完美命中 Prefix KV Cache !
                    ⚡ (直接重用 GPU 顯存張量，跳過計算)
                                  │
 [Token 1201 ─────────────────────┴──────────────────────► Token 2500]
  ┌───────────────────────────────────────────────────────────────┐
  │                   中間對話歷史 (Sliding Window)                │
  │   - 使用者個人記憶 (<user_memory> 好感度、事實)               │
  │   - 近期多輪對話歷史 (User / Assistant Turn)                  │
  └───────────────────────────────────────────────────────────────┘
  ┌───────────────────────────────────────────────────────────────┐
  │                   tail_lore (動態高權重尾部)                   │
  │   - ⏰ 當前精確時間戳記 (年-月-日 時:分)                     │
  │   - 🎯 觸發的高優先度 Lorebook (緊貼生成點)                   │
  └───────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                         [LLM 開始生成回覆]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **SOUL 檔案熱更動引發快取失效**：
   - 若管理員編輯了 `file/soul/sherry_default.md`，由於 `@functools.lru_cache` 存在，在未手動清空快取前仍會讀取舊人設。呼叫 `_load_soul_persona_by_name.cache_clear()` 刷新後，因 Token 產生實質變更，後端 GPU 首次推論會強制重新計算 KV Cache。
2. **不同頻道專屬人設分流**：
   - 若頻道 A 設定了傲嬌人格，頻道 B 設定了偵探人格，兩者的前綴不同，在 GPU 上分別建立獨立的快取樹分支。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **時間戳記移至尾部** | 徹底解放前綴靜態性，理論上達成最高 Prefix Cache 命中率。 | 時間資訊距離生成點極近，模型對時間極度敏感，有時會在回覆中過度強調當前時間。 | 在 System Prompt 中完全不放時間，改由工具或動態函式查詢。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在生產環境 LLM 系統中，如何設計 Prompt 架構以最大化利用現代推論引擎的 Prefix Caching 特性？**
  - **Ans**:
    1. **分層排列**：嚴格按照「恆定不變 -> 低頻變動 -> 高頻變動」的順序組織 Token。將全域系統人設、工具規格放在最開頭；將使用者個人資訊放在中段；將當前輪次輸入、動態時間與最新資訊放在最末尾。
    2. **消除前綴微小雜訊**：確保前綴字元完全一致，避免動態 UUID、隨機數、浮動時間出現在 Prompt 前半部。
    3. **顯式標記 Cache Point**：若使用 Claude API，在靜態人設末端顯式加上 `cache_control` 標記。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [AI/14-llm-inference.md](./14-llm-inference.md) 研讀多後端推論客戶端抽象與模型備援策略。
