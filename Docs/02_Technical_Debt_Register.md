# 02 — 技術債登記冊 (Technical Debt Register)

> 本檔依據工程治理規範記錄系統既有技術債、影響面、緩解方案與目標消除版本。

| 債務 ID | 模組 / 位置 | 說明與問題 | 嚴重度 | 影響面與風險 | 工作替代/緩解方案 | 目標解決版本 | 狀態 |
|---|---|---|---|---|---|---|---|
| **TD-001** | `settings.py` (832行) | 設定巨石檔案，混合常數、檔案路徑、動態覆寫、環境變數與推論模型配置。 | Medium | 跨模組高度依賴，改動設定易引起不可預期循環引用。 | 拆解為 `core/config/` 子模組 (`paths.py`, `env.py`, `models.py`, `soul.py`, `manager.py`)，由 `settings.py` 提供向後相容 re-exports。 | V2.4.0 | Resolved (V2.4.0) |
| **TD-002** | `handlers/bot_brain/client.py` (39KB) | 多推論後端 (Ollama, Gemini, Claude, OpenAI) 耦合於單一檔案，缺乏標準策略模式抽象。 | Medium | 新增或更換推論後端時測試成本高、維護困難。 | 抽象 `BaseLLMClient` 介面，拆分為 `mod/clients/` 策略實作與工廠封裝。 | V2.5.0 | Open |
| **TD-003** | `handlers/social/utils.py` (38KB) | 輔助函式 God-file，混雜權限檢查、文字處理、Discord 渲染與 Embed 邏輯。 | Low | 函式職責分散，單元測試需要過多 mock。 | 依職責拆分至 `handlers/social/formatters/` 與 `handlers/social/auth/`。 | V2.5.0 | Open |
| **TD-004** | `core/db.py` & `handlers/memory/storage.py` | SQLite 資料庫缺乏 schema 遷移機制 (`PRAGMA user_version`)，手動 ALTER TABLE 易發生結構漂移。 | High | 部署時如果資料庫 schema 變更可能造成資料遺失或運行時 crash。 | 實裝 `core/db_migration.py`，支援自動備份與基於版本的 migration runner。 | V2.0.0 | Resolved (V2.0.0) |
| **TD-005** | `data/*.jsonl` | 日誌與對話快取缺乏生命週期自動保留與清理機制，磁碟長期無界增長。 | Medium | 磁碟耗盡風險與違反資料最小化隱私原則。 | 實作 `handlers/memory/retention_worker.py` 定期清理過期記錄。 | V2.3.0 | Resolved (V2.3.0) |
| **TD-006** | `handlers/social/tools/search_tools.py` | 外部爬取內容與搜尋摘要未嚴格以 XML 標籤隔離，存在潛在 Prompt Injection 風險。 | High | 惡意網頁可能覆寫 System Prompt 誘騙 LLM 執行越權操作。 | 引入 `<untrusted_external_content>` XML 隔離邊界與 SSRF 防禦機制。 | V2.4.0 | Resolved (V2.4.0) |
| **TD-007** | `web_ui/` | UI 模組日誌分離與 Web API 認證收緊。 | Medium | Web UI 與 Bot 共用部分日誌通道，缺乏獨立 Session 過期機制與專用 Audit Trail。 | 建立獨立 `web_ui` 日誌記錄器與更收緊的 Token 驗證機制。 | V2.5.0 | Open |
| **TD-008** | `handlers/voice/` | 語音模組 (STT/TTS) 非同步流式佇列與錯誤退避優化。 | Low | 語音合成與辨識在網路波動時缺乏自適應重試與併發排隊佇列。 | 導入基於 `asyncio.Queue` 的語音任務排程與指數退避策略。 | V2.6.0 | Open |
| **TD-009** | `tests/evaluation/` | AI 評測資料集擴充與長文本壓力測試。 | Low | 現有黃金資料集主要涵蓋單輪/短多輪場景，缺乏 100+ 輪超長上下文回憶壓力測試。 | 擴充 `tests/evaluation/golden_conversations.jsonl`，收錄極端邊界與長記憶壓力案例。 | V2.6.0 | Open |

