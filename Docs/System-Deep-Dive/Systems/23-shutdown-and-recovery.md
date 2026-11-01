# 23 — 關機信號中斷、WAL 落盤與崩潰復原機制 (Shutdown & Recovery)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度檢視雪莉系統的終止中斷處理與災難復原能力。客觀評估目前在信號監聽 (`SIGINT`, `SIGTERM`) 上的現況缺失、未呼叫 `checkpoint_all_dbs()` 造成的潛在資料滯留風險、SQLite WAL 自動重放機制帶來的崩潰保證以及熱重載腳本對 `data/sherry.pid` 的管理。

---

## 🧭 Prerequisites (先備知識)
- POSIX 信號機制 (`SIGINT`, `SIGTERM`, `SIGKILL`) 與 Windows 控制台事件 (`CTRL_C_EVENT`, `CTRL_BREAK_EVENT`)。
- SQLite WAL 崩潰復原原理（Crash Recovery on Open）。
- Python `signal` 模組與 `asyncio.get_event_loop().add_signal_handler()`。

---

## 💡 Concept (容災復原與優雅關機工程理論)
關機與重啟是分散式後端系統中最脆弱的時刻：
1. **優雅關機 (Graceful Shutdown)**：在收到退出信號後，拒絕新連線、等待在途請求完成、將所有記憶體快取與日誌刷入磁碟，確保零資料遺失。
2. **強制終止 (Hard Kill)**：若行程在幾秒內未正常退出，或直接收到 `SIGKILL` (`kill -9`)，作業系統立即收回資源。
3. **崩潰自愈 (Crash Resilience)**：系統重啟時必須假設「上次進程是非正常死亡」，自動執行未完成交易復原與一致性檢查。

---

## 🛠️ Sherry Implementation (雪莉關機與復原真實現狀)

### 一、實證現況：頂層缺少信號捕捉器
- `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L509-L513)：
  ```python
          await bot.start(settings.DISCORD_BOT_TOKEN)

  if __name__ == "__main__":
      asyncio.run(main())
  ```
  **架構事實**：`main.py` 頂層**完全沒有註冊任何 `signal.signal()` 或 `loop.add_signal_handler()`**。
- 當使用者在終端按下 `Ctrl+C` 時，Python 會在當前協程拋出 `KeyboardInterrupt`。若正巧在執行 SQLite 寫入，行程會被中斷退出。

---

### 二、WAL 磁碟落盤工具與斷鏈現狀
- `[CONFIRMED]` [`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L101-L114) 實作了非常健全的落盤函式：
  ```python
  def checkpoint_all_dbs() -> None:
      """對全數已註冊之 WAL SQLite 資料庫執行 TRUNCATE checkpoint。"""
      for canonical_path in list(_CONFIGURED_DBS):
          try:
              with sqlite3.connect(canonical_path, timeout=5.0) as conn:
                  conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
              logger.info("[CoreDB] 資料庫 WAL checkpoint 完成: %s", canonical_path)
          except Exception as exc:
              logger.warning("[CoreDB] 資料庫 WAL checkpoint 失敗: %s", exc)
  ```
- **關鍵斷鏈**：由於頂層缺少優雅關機勾點，`checkpoint_all_dbs()` **並未在 `main()` 結束的 `finally` 區塊中被自動調用**！
  目前該函式僅能由特定維運工具或手動調用。

---

### 三、崩潰復原救命索：SQLite WAL 自主重放
雖然未主動呼叫 Checkpoint，但受惠於 SQLite WAL 模式的核心特性：
1. **原子性提交**：只要交易在 WAL 日誌中已標記 Commit，即使尚未寫入主資料庫檔，資料也不會損毀。
2. **重啟自檢**：雪莉下一次啟動時，當任何模組呼叫 `sqlite3.connect()` 打開資料庫時，SQLite 引擎會自動檢查並重放同目錄下的 `-wal` 檔案，自動完成未落盤交易的合併。
3. **風險警告**：若維運人員在關機後只拷貝了 `.db` 檔案而遺漏了 `-wal` 檔案進行異地備份，該次未 Checkpoint 的最新資料將永久遺失！

---

### 四、PID 行程管理與熱重啟腳本
- `[CONFIRMED]` [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L43-L46)：
  啟動時立即寫入 PID 至 `data/sherry.pid`。
- `[CONFIRMED]` [`restart_sherry.bat`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/restart_sherry.bat)：
  腳本透過 `set /p PID=<data\sherry.pid` 取得 PID，並透過 `taskkill /F /PID %PID%` 終止進程後重啟。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **main.py 缺少信號處理**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L509-L513)。
2. `[CONFIRMED]` **checkpoint_all_dbs 定義但未掛入關機勾點**：
   - 見 [`core/db.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/db.py#L101-L114)。
3. `[CONFIRMED]` **PID 寫入與 Taskkill 重啟**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L44) 與 [`restart_sherry.bat`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/restart_sherry.bat#L1-L20)。

---

## 📊 Flow Diagram (關機與復原路徑對比)

```text
【理想優雅關機路徑】
  [SIGINT / SIGTERM]
          │
          ▼
  [Signal Handler 捕捉]
          │
          +---> bot.close() (通知 Gateway 離線)
          +---> cancel all background tasks
          +---> await server.shutdown() (關閉 Quart)
          +---> checkpoint_all_dbs() (所有 WAL 刷入主檔，WAL 尺寸歸零)
          │
          ▼
  [乾淨退出，磁碟 100% 同步]

-------------------------------------------------------------------------

【雪莉當前現狀路徑】
  [Ctrl+C / taskkill /F]
          │
          ▼
  [OS 強制終止 Python 進程]
          │
          +---> (未執行 checkpoint_all_dbs，最新資料殘留在 -wal 檔)
          │
          ▼
  [下一次重開機 (main.py)]
          │
          ▼
  [SQLite 引擎在 open() 時自動讀取 -wal 重放 (Crash Recovery)]
          │
          ▼
  [資料恢復正常，但若手動誤刪 -wal 則遺失資料]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **備份丟失 WAL 檔案陷阱**：
   - 許多初階工程師手動備份時只複製 `chat_history.db`，若此時剛好存在 5MB 的 `chat_history.db-wal` 且未 Checkpoint，將備份檔還原到另一台機器時會發現最新的對話與事件紀錄全部憑空消失。
2. **Windows Taskkill 強制終止**：
   - `restart_sherry.bat` 使用了 `/F` (Force) 參數，直接向行程發出終止命令，不給予 Python 執行 `finally` 區塊的機會。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **依靠 SQLite 引擎自主 Crash Recovery** | 實作最少，不需在 Windows/Linux 處理繁瑣的信號與跨平台差異。 | 關機時有未落盤 WAL 殘留，異地冷備份風險高；重啟腳本暴力 kill 易殘留鎖。 | 在 `main.py` 加入 `try...finally: checkpoint_all_dbs()`，並以 `bot.close()` 優雅收尾。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在使用 SQLite WAL 模式的生產系統中，標準的關機落盤 (Graceful Flush) 流程應該如何設計？**
  - **Ans**:
    1. 阻斷所有新的寫入請求，完成在途的交易。
    2. 對所有開啟的資料庫執行 `PRAGMA wal_checkpoint(TRUNCATE);`，此指令會將 WAL 的所有頁面強制刷回主資料庫檔案，並將 WAL 檔案截斷為 0 位元組。
    3. 關閉所有連線池與 `sqlite3.Connection` 物件。
    4. 移除 `.pid` 檔案並正常退出進程。

---

## 🔗 What to Study Next (後續章節銜接)
- 進入 AI 認知核心章節，前往 [AI/12-memory-architecture.md](../AI/12-memory-architecture.md) 研讀三層式記憶與認知提煉架構。
