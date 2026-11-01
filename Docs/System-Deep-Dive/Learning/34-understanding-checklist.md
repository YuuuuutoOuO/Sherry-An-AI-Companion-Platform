# 34 — 架構理解自檢清單 (Understanding Checklist)

> **難度標記**：`Difficulty: Intermediate`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
提供研讀《Sherry System Deep-Dive Documentation》後的自我評估清單 (Self-Assessment Checklist)。讀者可藉此檢驗自己是否已完全掌握雪莉系統的關鍵架構細節、非同步邊界與設計決策。

---

## ✅ 全維度架構自檢表

### 一、架構分層與生命週期 (Architecture & Lifecycle)
- [ ] 能清楚說明 `main.py` 如何在單一進程中引導 Discord Client 與 Quart Web UI。
- [ ] 理解 15 個核心 Cog 採用 Fail-Fast (`sys.exit(1)`) 與可選 Mod 插件採用 Fault-Tolerant (WARNING 跳過) 的設計考量。
- [ ] 能說出為什麼 `settings.py` 曾是 God-file，以及如何重構為 `core/config/` 向後相容層。
- [ ] 清楚知道 Discord 互動 3 秒 ACK 限制與 `defer()` 在推論鏈路中的必要性。

### 二、非同步並發與系統底層 (Systems & Concurrency)
- [ ] 能解釋為什麼在 Quart 路由處理器中調用同步 `requests.get()` 會導致 Discord 機器人心跳中斷斷線。
- [ ] 清楚 `BotBrain` 如何利用 `self._background_tasks.add(t)` 與 `done_callback` 防止非同步任務被 GC 意外回收。
- [ ] 理解 Piper TTS 為什麼必須透過獨立 Subprocess 執行，而非直接在主循環中呼叫。
- [ ] 能描述 `main.py` 缺少全域 `SIGINT`/`SIGTERM` 處理器時，可能對 SQLite WAL 造成的影響。

### 三、儲存拓撲與資料庫治理 (Storage & Database)
- [ ] 能列舉系統中至少 8 個專用 SQLite 資料庫的名稱與對應職責。
- [ ] 深入理解 `PRAGMA journal_mode = WAL;` 與 `PRAGMA busy_timeout = 5000;` 如何解決並發鎖定。
- [ ] 清楚知道 `core/db.py` 中 `_CONFIGURED_DBS` 集合如何避免重複執行 PRAGMA 造成的 I/O 浪費。
- [ ] 能指出 `mod/market_plugin/repository.py` 中 8 處直連 `sqlite3.connect` 的技術債位置與潛在風險。

### 四、AI 推論、記憶與 Agent 調度 (AI & Cognition)
- [ ] 深刻理解為什麼動態時間標籤必須放在 `tail_lore` 而非 `head_persona`，以及其對 Prefix KV Cache 的影響。
- [ ] 能完整說明當本地 Stable Diffusion 生圖觸發時，本地文字推論如何透過 `FALLBACK_MODELS` 實施「零等待雲端繞過」。
- [ ] 清楚三層記憶（短期對話、使用者個人事實、世界宏觀經驗）的儲存與檢索管線。
- [ ] 能對比 `INTEGRATED`（單階段內嵌標籤）與 `PARALLEL`（雙階段規劃合成）的適用情境與 Early Termination 邏輯。

### 五、資安防禦與可觀測性 (Security & Observability)
- [ ] 能解釋 `core/guard.py` 的 SSRF 防禦為何必須顯式阻擋 `169.254.0.0/16` (Cloud Metadata) 與十進制整數 IP。
- [ ] 理解外部搜尋爬蟲資料為何必須以 `<untrusted_external_content>` XML 標籤隔離以防範 Prompt Injection。
- [ ] 清楚 `contextvars.ContextVar` 如何在單執行緒事件循環中安全傳遞 Trace ID，以及 `TraceIdFilter` 的裝配方式。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Evidence/36-evidence-conflicts.md](../Evidence/36-evidence-conflicts.md) 研讀規範承諾與源碼實證衝突分析手冊。
