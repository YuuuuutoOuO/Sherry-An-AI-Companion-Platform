# 🤖 橘雪莉 — Discord AI Companion & Agent Platform (v1.9.0)

> 📌 **本文件記錄 Sherry V1.9.0 架構與核心技術規格**。

Sherry V1.9 聚焦於 **「LLM Context, Prefill, KV Cache & Inference Architecture 最佳化」**，在確保人格特質一致性、工具調用準確度、記憶連續性與安全性的前提下，大幅降低每次推理重複 Prefill 的 Context 成本與 Token 消耗，並全面提升本機與雲端 LLM 後端的 KV Cache 命中率。

---

## ✨ V1.9 核心技術變革 (Inference & Prompt Architecture)

```text
User Request
    ↓
Context Selection & Selective Top-K Retrieval
    ↓
Pass-1 (Single-Pass Planning / Chitchat Direct Output)
    ↓
Tool Execution (Parallel + Tier-1 In-Memory Cache)
    ↓
Pass-2 (Optimized Persona Synthesis without Micro-CoT / Redundant Tool Manuals)
    ↓
Final Response
```

### 1. Zone 1 / Zone 2 前綴與動態內容分離 (Prefix Caching Alignment)
- **Zone 1: 靜態系統前綴 (Static Prefix)**
  - 包含 SOUL 角色核心定義、通用交互守則與 Compact Tool Index。
  - 前綴在同一伺服器與頻道跨輪次保持 100% 相同，直接命中 vLLM、Ollama、LM Studio、Claude、OpenAI 的 Prompt Caching 機制。
- **Zone 2: 動態情境 (Dynamic Context)**
  - 順序標準化：
    1. 頻道近期氛圍 (Vibe)
    2. 頻道對話歷史 (Append-Only Context Script)
    3. 視覺/圖片線索 (Vision Context)
    4. 當前發話者記憶 (Selective Top-K Facts + 好感度關係狀態)
    5. 作者備註 / 即時世界記憶 / 時間戳記 (緊貼生成點，不破壞快取)

### 2. 記憶系統檢索加權 (Selective Top-K Scoring)
- 捨棄盲目定額注入或無差別截斷，引入 `_calculate_query_relevance` 演算法。
- 記憶事實依據綜合分數排序：`_fact_rank_score = base_imp + (rel * 5.0)`。
- 頂部無縫整合好感度與關係標籤 (`get_relationship_snippet`)，如 `❤️ 關係狀態：朋友（好感度 52.0）`。

### 3. 工具精簡與 Tier-1 TTL 快取 (Compact Tool Index & Tool Caching)
- 將工具規則精簡約 900 tokens/turn，保留完整負向約束（如 `2026 != stock`）。
- 股市模組 (`stock_tool.py`) 引入 120s TTL 快取，加速 OHLCV 查價與技術線圖生成。
- 天氣查詢（WEATHER）已全面收斂由 `SEARCH` 聯網檢索管道承接（`_should_trigger_search` 包含氣象關鍵字自動觸發）。

### 4. Pass-1 與 Pass-2 靜態前綴 100% 嚴格對齊 (Prefix Alignment)
- Pass-1 與 Pass-2 共享 **100% 位元組一致的 System Prompt (Static Prefix + Dynamic Context)**。
- Pass-2 直接命中 Pass-1 剛快取的 5,500+ tokens，免除二次 Prefill，Pass-2 TTFT 延遲降至 < 200ms。
- 工具數據與第二階段指令（「上述工具已執行完畢，請直接輸出對話」）統一置於 User Prompt 尾部，完全不破壞前綴快取。

### 5. APM 細粒度可觀測性指標 (Observability)
`handlers/social/observability.py` 擴充支援：
- `prompt_tokens_cached`
- `prompt_tokens_uncached`
- `decode_tokens`
- `ttft_ms`
- `prefill_latency_ms`
- `decode_latency_ms`
- `cache_hit_ratio`

---

## 🧪 驗證與測試

全專案 507 項單元與整合測試 100% 通過：

```powershell
pytest tests/unit/ -q
# 507 passed in 31.39s
```
