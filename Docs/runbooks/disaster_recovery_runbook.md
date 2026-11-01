# 事故手冊與災難還原 SOP (Disaster Recovery Runbook)

| 事故情境 | 涵蓋範圍 | 狀態標籤 (三段制) | 演練指令 |
|---|---|---|---|
| **D1: 單一雲端供應商故障** | Gemini / Groq / OpenAI / Claude API 單點超時或故障 | `[Verified]` | `python tools/drill_recovery.py --scenario d1` |
| **D2: 全雲端 LLM 癱瘓** | 全外部 AI Provider 失效、網路中斷 | `[Verified]` | `python tools/drill_recovery.py --scenario d2` |
| **D3: SQLite 資料庫損毀** | `chat_history.db` / `care.db` / `spark.db` 損毀 | `[Verified]` | `python tools/drill_recovery.py --scenario d3` |

---

## 1. D1: 單一雲端供應商故障與路由容錯

### 1.1 觸發條件
- 雲端 LLM 供應商回傳 HTTP 429 (Rate Limit)、503 (Service Unavailable)、504 (Gateway Timeout)。
- 單次推論耗時超過逾時門檻（例如 60 秒）或拋出網路連線中斷（ClientConnectorError / ConnectionReset）。

### 1.2 自動應變機制
1. **多軌 Provider 自動回退**：
   - 派發器（`handlers/social/dispatch/`）捕捉 Provider 異常後，自動輪詢備用 Provider（例如 Primary: Gemini 2.5 Flash -> Secondary: Groq Llama-3.3-70b -> Tertiary: 本地 Ollama）。
2. **可觀測性紀錄**：
   - 呼叫 `handlers.social.observability.log_error(event="provider_fallback", ...)`，記錄事故 Trace ID 與失敗 Provider。
3. **降級語氣保護**：
   - 若回退後回應出現生硬格式或系統報錯，由 `PersonaDriftGuard` 攔截並改以橘雪莉典範短句回應。

---

## 2. D2: 全雲端 LLM 癱瘓與安全離線模式

### 2.1 觸發條件
- 外部所有 API Key 額度耗盡，或伺服器外網連線完全中斷。

### 2.2 應變與降級流程
1. **觸發自主性熔斷 (Autonomy Kill Switch)**：
   - 呼叫 `core.autonomy_guard.global_autonomy_guard.trigger_kill_switch("All LLM providers unreachable")`。
   - 立即阻斷所有高耗能背景推播（SPARK、主動關懷、深夜記憶整合）。
2. **本地 Ollama / Safe Offline 回退**：
   - 若主機運行有本地 Ollama 服務（`http://127.0.0.1:11434`），轉入本地模型推論。
   - 若本地模型亦無法服務，對話層回退至安全的「罐頭人設離線應答」（例如：「嗚……雪莉現在腦袋有點暈暈的，連不上思考中樞，等我一下下好嗎？」），禁止對外拋出裸露的 Python Traceback。

---

## 3. D3: SQLite 資料庫損毀與備份還原

### 3.1 損毀偵測
- 系統啟動或連線時拋出 `sqlite3.DatabaseError: file is not a database` 或 `database disk image is malformed`。
- 呼叫 `core.db.verify_database_integrity(db_path)`，PRAGMA integrity_check 回傳非 `"ok"`。

### 3.2 標準還原作業步驟 (SOP)
1. **暫停寫入與關閉連線**：
   - 停止相關 Cog / Worker，呼叫 `core.db.checkpoint_all_dbs()` 嘗試排空現有 WAL 日誌。
2. **隔離損毀檔案**：
   - 將受損之 `.db`、`.db-wal`、`.db-shm` 檔案更名移至 `data/corrupted/<db_name>_<timestamp>.corrupt` 作為調查佐證。
3. **檢驗最新備份檔完整性**：
   - 鎖定 `data/backups/` 中最新之 `.db` 備份，使用 `core.db.verify_database_integrity(backup_path)` 驗證其完整性（必須回傳 `(True, "ok")`）。
4. **執行原子還原**：
   - 呼叫 `core.db.restore_database(backup_path, target_db_path)`。還原過程使用 SQLite Online Backup API，並於替換後再次執行 PRAGMA 完整性查驗。
5. **重啟服務與驗證**：
   - 重啟 Bot 或 WebUI，確認 `PRAGMA integrity_check` 與關鍵資料表計數正常。

---

## 4. 季度災難還原演練 SOP

平台維運團隊應每季執行一次自動化演練：

```bash
# 執行全情境 (D1, D2, D3) 災難還原演練
python tools/drill_recovery.py --all

# 僅執行資料庫損毀還原演練
python tools/drill_recovery.py --scenario d3
```

演練腳本將自動產生結構化演練報告於 `data/drills/drill_report_<timestamp>.json`，留存於觀測日誌供資安審計備查。
