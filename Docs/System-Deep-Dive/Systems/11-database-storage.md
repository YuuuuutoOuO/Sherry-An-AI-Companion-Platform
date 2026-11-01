# 11 — 13 個 SQLite 資料庫拓撲、WAL 模式與連線洩漏 (Database Storage)

> **難度標記**：`Difficulty: Expert`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度剖析雪莉系統持久化儲存層架構。全面解構分佈於 `data/` 目錄下的 13 個 SQLite 資料庫拓撲、`core/db.py` 的統一連線工廠與交易管理機制、WAL (Write-Ahead Logging) 模式落地、`PRAGMA busy_timeout` 併發鎖定防護，並明確揭露 `mod/market_plugin/repository.py` 中繞過核心工廠的 8 處直連 `sqlite3.connect` 洩漏事實。

---

## 🧭 Prerequisites (先備知識)
- SQLite 儲存引擎底層原理（Rollback Journal vs. WAL 模式、B-Tree 頁面組織）。
- SQLite 讀寫鎖定層級（SHARED, RESERVED, PENDING, EXCLUSIVE）。
- WAL Checkpoint 機制（PASSIVE, FULL, RESTART, TRUNCATE）。

---

## 💡 Concept (SQLite 併發模型與 WAL 機制理論)
傳統 SQLite 在使用 Rollback Journal 模式時，**讀寫互斥**（寫入時不可讀取，讀取時不可寫入）。在非同步高併發的 Bot 與 Web 整合應用中，極易引發 `database is locked` 異常。

**WAL (Write-Ahead Logging) 模式**帶來了架構級突破：
1. **讀寫不互斥**：讀操作讀取主資料庫與 WAL 索引，寫操作直接追加至 WAL 日誌檔（`-wal`），讀者與寫者可完全並行。
2. **單一寫者**：同時間仍僅允許一個寫入交易。
3. **Busy Timeout 救命閥**：透過 `PRAGMA busy_timeout = 5000;`，當遇到寫入鎖競爭時，SQLite 會在底層自動退避睡眠重試最多 5 秒，而非立即拋出例外。

---

## 🛠️ Sherry Implementation (雪莉資料庫拓撲與實作)

### 一、全系統 SQLite 資料庫拓撲矩陣與儲存拓撲
`[CONFIRMED]` 經全代碼庫掃描、`tools/db_manager.py` 與 `data/` 實體驗證，系統共計定義並使用 12 個獨立業務 SQLite 資料庫（加計根目錄歷史相容別名 `data/deep_search.db` 則磁碟上共計 13 個實體 `.db` 檔）。

**資料庫治理職責劃分**：
- **`tools/db_manager.py` 統一納管之 9 個規範資料庫 (Canonical Databases)**：納入全系統 Schema 定義（`CANONICAL_TABLES`）、軟刪除四本柱審計欄位檢查、備份與自動結構遷移（編號 1~5, 7~10）。
- **模組獨立自理維護之 3 個資料庫**：由所屬模組內部自行初始化連線與資料表生命週期（編號 6 `deep_search.db`、編號 11 `sys_monitor.db`、編號 12 `audit.db`），未納入 `db_manager.py` 集中遷移管線。

| 編號 | 資料庫相對路徑 | 所屬模組 | 管理模式 | 核心資料表 (Tables) | 用途與描述 |
|---|---|---|---|---|---|
| 1 | `data/chat_history.db` | `handlers.social` / 觀測 | `db_manager 納管` | `chat_messages`, `runtime_events`, `error_events`, `audit_logs`, `roast_rules` | 對話訊息持久化、即時觀測日誌與吐槽規則 |
| 2 | `data/sherry_memory.db` | `handlers.memory` | `db_manager 納管` | `user_profiles`, `user_facts`, `user_aliases`, `user_relations` | 使用者個人檔案、好感度、萃取事實條目 |
| 3 | `data/world_memory.db` | 記憶核心 | `db_manager 納管` | `world_events`, `world_summary` | 世界記憶重大事件與每日跨群摘要 |
| 4 | `data/spark/spark.db` | `handlers.spark` | `db_manager 納管` | `triggers` | SPARK 條件推播規則與觸發狀態 |
| 5 | `data/schedules.db` | `handlers.spark` / 排程 | `db_manager 納管` | `schedules` | 定時鬧鐘、排程提醒與 Cron 週期任務 |
| 6 | `data/deep_search/deep_search.db` | `mod.deep_search` | `模組自理` | `sessions`, `search_stages`, `findings` | 多輪深度主題研究、檢索記錄與文獻 (註：`data/deep_search.db` 為相容歷史路徑副本) |
| 7 | `data/feed_sentinel/feed_sentinel.db`| `mod.feed_sentinel` | `db_manager 納管` | `subscriptions`, `feed_items` | RSS/情報哨兵訂閱來源與推送去重紀錄 |
| 8 | `data/sherry_stock/stock_trader.db` | 股票模組 | `db_manager 納管` | `user_portfolios`, `stock_positions` | 虛擬美股交易量化帳戶與持股庫存 |
| 9 | `data/market_stock_bot.db` | `mod.market_plugin` | `db_manager 納管` | `stock_predictions`, `crypto_predictions`, `stock_watchlists`, `card_watchlist` | 美股/加密貨幣/PTCG 卡牌預測與追蹤 |
| 10 | `data/dnd/dnd.db` | `mod.dnd` | `db_manager 納管` | `dnd_characters`, `campaign_sessions` | TRPG 跑團角色卡、冒險存檔與擲骰紀錄 |
| 11 | `data/sys_inspector/sys_monitor.db` | 系統巡檢 | `模組自理` | `metric_snapshots` | 主機 CPU/RAM/GPU 硬體採樣時間序列 |
| 12 | `data/sys_inspector/audit.db` | 系統審計 | `模組自理` | `security_audit_events` | 外部命令執行、鑑權日誌與特權操作審計 |
| 特殊 | *(純記憶體暫存)* | `mod.livestream` | `記憶體暫存` | `_chat_history` (In-Memory List) | 直播間彈幕與互動訊息，採長度 100 筆有限串列暫存，無實體 `.db` 檔 |

> **⚠️ 嚴格證據校驗備註 `[CONFIRMED]`**：
> 先前調查報告初稿曾將直播模組推論為擁有第 13 個資料庫 `data/livestream/livestream.db`。經深入源碼 [`mod/livestream/service.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/mod/livestream/service.py#L58-L661) 嚴格查核，該模組彈幕歷史實為純記憶體暫存物件 (`self._chat_history: list[ChatMessage] = []`)，系統並無 `livestream.db` 檔案與資料庫連線。全系統實體業務資料庫為 12 個，磁碟包含 legacy alias 共有 13 個 `.db` 檔案。其中 9 個由 `tools/db_manager.py` 納管，3 個由專屬模組自理。

---

### 二、核心統一連線工廠 (`core/db.py`)
為了避免分散連接造成設定不一，雪莉在 `core/db.py` 實作了統一工廠：

- `[CONFIRMED]` [`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L24-L62)：
  ```python
  def get_db_connection(
      db_path: Path | str,
      wal_mode: bool = True,
      row_factory: bool = True,
      timeout: float = 10.0,
  ) -> sqlite3.Connection:
      ...
      conn = sqlite3.connect(canonical_path, timeout=timeout)
      if row_factory:
          conn.row_factory = sqlite3.Row
      if wal_mode and canonical_path not in _CONFIGURED_DBS:
          conn.execute("PRAGMA journal_mode = WAL;")
          conn.execute("PRAGMA synchronous = NORMAL;")
          _CONFIGURED_DBS.add(canonical_path)
      conn.execute("PRAGMA busy_timeout = 5000;")
      conn.execute("PRAGMA foreign_keys = ON;")
      return conn
  ```
- **極致優化**：利用模組級集合 `_CONFIGURED_DBS` 追蹤已設定過 WAL 的檔案路徑，避免高頻讀寫時重複執行 `PRAGMA journal_mode = WAL` 帶來額外的磁碟 I/O 往返開銷。

---

### 三、關鍵事實揭露：8 處直連 sqlite3.connect 洩漏
`[CONFIRMED]` 經代碼審查實證，在可選外掛模組 [`mod/market_plugin/repository.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/mod/market_plugin/repository.py) 中，存在 **8 處直接使用原生語法** 的違規連線：
- 行號實證：L35, L94, L130, L150, L170, L224, L241, L286。
- 形式：
  ```python
  with sqlite3.connect(self.db_path) as conn:
      ...
  ```
- **架構影響**：
  1. 繞過了 `core/db.py` 的統一工廠。
  2. 該資料庫連線**未啟用 WAL 模式**，亦**未配置 `PRAGMA busy_timeout = 5000;`**。
  3. 當 Web UI 與行情背景抓取任務同時存取 `market_stock_bot.db` 時，此 8 處呼叫在遇到並發寫入時會立即發生崩潰（拋出 `database is locked`），屬於實證已確認的歷史架構債務。

---

### 四、全資料庫 WAL Checkpoint 機制
- `[CONFIRMED]` [`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L101-L114) 提供 `checkpoint_all_dbs()` 函式：
  ```python
  def checkpoint_all_dbs() -> None:
      for canonical_path in list(_CONFIGURED_DBS):
          with sqlite3.connect(canonical_path, timeout=5.0) as conn:
              conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
  ```
  使用 `TRUNCATE` 模式可確保 WAL 日誌大小被歸零，所有資料安全合併至主檔案，便於備份與防止磁碟無界膨脹。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **core/db.py 統一連線工廠與 WAL 邏輯**：
   - 見 [`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L24-L62)。
2. `[CONFIRMED]` **mod/market_plugin/repository.py 8 處連線洩漏**：
   - 見 [`mod/market_plugin/repository.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/mod/market_plugin/repository.py#L35,L94,L130,L150,L170,L224,L241,L286)。
3. `[CONFIRMED]` **tools/db_manager.py 13 資料庫 Schema 統一驗證**：
   - 見 [`tools/db_manager.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/tools/db_manager.py#L83-L473)。

---

## 📊 Flow Diagram (資料庫連線流向對比圖)

```text
【推薦規範路徑】
  Service / Cog  ──►  core.db.get_db_connection()
                             │
                             +---> PRAGMA journal_mode = WAL (僅一次)
                             +---> PRAGMA busy_timeout = 5000 (自動退避)
                             +---> PRAGMA foreign_keys = ON
                             │
                             ▼
                      [SQLite .db (WAL Safe)]

-------------------------------------------------------------------------

【已知技術債路徑：mod/market_plugin/repository.py (8處)】
  Repository     ──►  sqlite3.connect(self.db_path)  [直連洩漏!]
                             │
                             ▼
                      [SQLite .db (預設 Journal，無 busy_timeout)]
                             │
                             💥 併發寫入時立即引發 database is locked !
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **WAL 檔案膨脹失控 (WAL File Bloat)**：
   - 若系統中有一個長效讀取連線未釋放（例如一個長查詢或未關閉的 Cursor），SQLite 引擎將無法推進 Checkpoint，導致 `-wal` 檔案不斷增長至數 GB，最終耗盡伺服器磁碟。
2. **Schema 漂移 (Schema Drift)**：
   - 歷史債務 `TD-004`：早期缺乏 `PRAGMA user_version` 自動遷移機制，新欄位常由業務代碼手動 ALTER TABLE，現已透過 `core/db_migration.py` 與 `tools/db_manager.py` 解決。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **拆分為 13 個專用資料庫檔案** | 寫入鎖競爭半徑被徹底隔離（聊天的寫入不影響跑團與股市）；備份可按模組單獨拷貝。 | 無法使用標準跨資料庫外鍵約束 (`FOREIGN KEY`)；維運時需要巡檢 13 個實體檔案。 | 單一全量 `sherry_monolith.db` 大資料庫。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在高併發系統中選擇 SQLite 作為儲存引擎，必須配置哪些關鍵 PRAGMA 指令？為什麼？**
  - **Ans**:
    1. `PRAGMA journal_mode = WAL;`：開啟預寫式日誌，消除讀寫互斥，實現併發讀寫。
    2. `PRAGMA synchronous = NORMAL;`：在 WAL 模式下，NORMAL 級別兼具極高寫入效能與崩潰安全性（OS 崩潰不損壞資料）。
    3. `PRAGMA busy_timeout = 5000;`：當遭遇短暫的寫入排他鎖時，在底層等待最長 5 秒，避免直接拋出 `database is locked`。
    4. `PRAGMA foreign_keys = ON;`：SQLite 預設不強制外鍵檢查，必須顯式啟用以確保關聯完整性。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Systems/18-resource-management.md](./18-resource-management.md) 研讀地端 GPU 顯存排它鎖與生圖雲端繞過機制。
