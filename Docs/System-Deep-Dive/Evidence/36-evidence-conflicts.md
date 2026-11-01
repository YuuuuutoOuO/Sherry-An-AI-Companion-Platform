# 36 — 規範承諾 vs. 原始碼實證衝突分析手冊 (Evidence Conflicts)

> **難度標記**：`Difficulty: Expert`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
秉持嚴格工程證據原則，客觀比對專案前期文件、規劃規格書承諾（Specification Claims）與當前程式碼庫真實落實現狀（Source Ground Truth）之間的差異與衝突點。不隱瞞、不粉飾，記錄系統真實現狀，作為架構治理與後續演進的權威依據。

---

## 🧭 Prerequisites (先備知識)
- 架構漂移 (Architectural Drift) 與技術債務治理。
- 靜態源碼審查與白箱證據提取方法。

---

## ⚖️ 五大核心承諾 vs. 實證衝突對比表

| 項目 | 規格書 / 規劃承諾 (Claim) | 原始碼實證 (Ground Truth) | 證據等級 | 衝擊評估與技術債判定 |
|---|---|---|---|---|
| **1. KV 快取重用** | 宣稱「SillyTavern 雙層注入全面啟動 Prefix KV Cache，大幅降低推論延遲」 | 程式碼確實將 `head_persona` 做成 100% 靜態字串 (`persona.py`)；但後端（Ollama / Gemini / Claude）是否在真實運行時命中快取需動態驗證，Claude 甚至缺少顯式 `cache_control` 標頭 | `[CONFIRMED]` (代碼前綴恆定) / `[REQUIRES RUNTIME VERIFICATION]` (後端快取命中) | **中**：靜態結構達標，但缺少與雲端供應商快取標頭的深度對接 |
| **2. 資料庫統一管理** | 宣稱「全系統資料庫 100% 封裝於 `core/db.py`，標配 WAL 模式與 busy_timeout」 | 核心 12 個資料庫確實由 `core/db.py` 管理；但 `mod/market_plugin/repository.py` 內部存在 **8 處直連 `sqlite3.connect`**，完全繞過 WAL 與逾時設定 | `[CONFIRMED]` (行號 L35, L94, L130, L150, L170, L224, L241, L286) | **高**：高併發行情爬蟲與 Web 查詢同時發生時，存在 `database is locked` 崩潰風險 |
| **3. 優雅關機與落盤** | 宣稱「系統具備健全的優雅關機流程，關機前自動將 WAL 日誌 TRUNCATE 刷盤」 | `core/db.py` 實作了 `checkpoint_all_dbs()`；但 `main.py` 頂層**完全沒有捕捉 SIGINT / SIGTERM**，該函式未掛入任何退出勾點 | `[CONFIRMED]` (`main.py` L509-L513 直跑 `asyncio.run`) | **中**：依賴 SQLite 重新啟動時的自動重放，若冷備份時遺漏 `-wal` 檔可能丟失最新資料 |
| **4. Web 與 Bot 服務隔離** | 宣稱「Web 控制台與 Discord Bot 為獨立運維模組」 | 兩者並非獨立進程，而是透過 `bot.loop.create_task(server.serve())` **共用同一個單執行緒 Asyncio Event Loop** | `[CONFIRMED]` (`main.py` L300, `web_ui/server.py` L2788) | **高**：Web 端若有未卸載的同步 blocking 操作，會直接導致 Discord 機器人斷線 |
| **5. GPU 顯存完全排它** | 宣稱「本地推論與本地繪圖嚴格串行，不外送內容」 | `BotBrain` 實作了 `_lock`；但當偵測到生圖進行時，系統會依 `FALLBACK_MODELS` **主動將本地對話導向雲端 API** 以達成零等待 | `[CONFIRMED]` (`handlers/bot_brain/cog.py` L178-L203) | **低 (架構優勢)**：使用者體驗大幅提升，但需注意在純內網環境下需明確配置 `FALLBACK_ENABLED=False` |
| **6. 資料庫總數與直播儲存** | 宣稱「系統擁有 13 個 SQLite 資料庫，包含 livestream.db」 | 系統實質擁有 12 個業務 SQLite 資料庫（加計 `deep_search.db` 根目錄歷史別名檔共 13 個 `.db` 檔）；而 `mod/livestream` 的彈幕為純記憶體串列 (`_chat_history`)，並無 `livestream.db` | `[CONFIRMED]` (`mod/livestream/service.py` L58-L661) | **低 (認知校正)**：實證消除了對直播間 SQLite 持久化的虛構推論，確認其為記憶體有限緩衝區 |

---

## 🔍 Detailed Evidence Breakdown (詳細實證剖析)

### 衝突一：KV Cache 結構 vs. 後端真實行為
- **源碼位置**：[`handlers/social/persona.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/handlers/social/persona.py#L138-L145)。
- **分析**：
  在 Prompt Engineering 上，雪莉確實做到了極致：把容易變動的時間戳記下沉至 `tail_lore`，`head_persona` 保證是靜態字串。
  然而，Anthropic 官方 Prompt Caching 規範要求必須傳入特定的 JSON 區塊：
  ```json
  "system": [
    {
      "type": "text",
      "text": "...",
      "cache_control": {"type": "ephemeral"}
    }
  ]
  ```
  雪莉的 `handlers/bot_brain/client.py` 目前主要對接 Ollama 與原生 REST 格式，未對 Claude 注入 `cache_control`。因此，該優化對於地端 Ollama/vLLM 有顯著潛在助益，但對 Claude 雲端 API 尚未真正激活用戶端快取。

---

### 衝突二：`mod/market_plugin/repository.py` 8 處連線洩漏
- **源碼位置**：[`mod/market_plugin/repository.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/mod/market_plugin/repository.py)。
- **具體違規行號**：
  - L35: `with sqlite3.connect(self.db_path) as conn:`
  - L94: `with sqlite3.connect(self.db_path) as conn:`
  - L130: `with sqlite3.connect(self.db_path) as conn:`
  - L150: `with sqlite3.connect(self.db_path) as conn:`
  - L170: `with sqlite3.connect(self.db_path) as conn:`
  - L224: `with sqlite3.connect(self.db_path) as conn:`
  - L241: `with sqlite3.connect(self.db_path) as conn:`
  - L286: `with sqlite3.connect(self.db_path) as conn:`
- **改善建議**：應統一替換為 `from core.db import get_db_connection` 或 `core.db.db_transaction`，使該資料庫全面納入 WAL 模式與 5000ms busy_timeout 管理。

---

### 衝突六：資料庫數量與直播間儲存實證
- **源碼位置**：[`mod/livestream/service.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/mod/livestream/service.py#L58-L661)。
- **分析**：
  先前架構推論誤認為 `mod/livestream` 會比照其他業務模組建立 `data/livestream/livestream.db` 並維護彈幕歷史。
  深入源碼實證後確認：`LiveStreamService` 完全採用記憶體串列管理彈幕：
  ```python
  self._chat_history: list[ChatMessage] = []
  self._max_chat_history: int = config.MAX_CHAT_HISTORY
  ```
  系統並不存在 `livestream.db`。全系統獨立業務 SQLite 資料庫總數為 12 個，磁碟上若計入相容副本 `data/deep_search.db` 與 `data/deep_search/deep_search.db` 則為 13 個 `.db` 檔。這項發現展示了嚴格源碼審查對杜絕「合理化腦補」的重要性。

---

## 💼 Interview Relevance (面試考點)

- **Q: 在接手大型既有系統時，如何進行架構真實性稽核 (Architecture Audit)？**
  - **Ans**:
    1. **不輕信文檔**：文檔往往落後於代碼演進。必須以靜態 AST 掃描、Import-linter 與實際程式碼實證為準。
    2. **對照邊界承諾**：檢查文檔宣稱的「單一入口」、「連線池」與「優雅關機」，用 `grep` 尋找是否有未封裝的直接連線或缺漏的信號捕捉。
    3. **建立技術債清單**：將衝突誠實記錄於《技術債登記冊》與《衝突分析手冊》，排定迭代修復優先級。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往最終總結章節 [37-final-system-assessment.md](../37-final-system-assessment.md) 研讀全系統架構評估與未來演進路線圖。
