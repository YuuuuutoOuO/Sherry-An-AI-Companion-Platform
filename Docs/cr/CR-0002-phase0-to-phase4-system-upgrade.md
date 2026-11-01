# CR-0002 — 全維度工程治理、AI 評測實驗室與自主性升級實施計畫

## 1. 變更目標 (Objective)
依據 2026-09-20 規劃書與四大 Auditor 檢測結論，全面推進系統 Phase 0 ~ Phase 4 升級落地。

## 2. 涵蓋範圍 (Scope)
- **Phase 0 (P0)**：緊急資安阻斷（Web UI 日誌隔離、跨用戶記憶保護、管理員權限收緊、SSRF 防禦、Session 遮罩）。
- **Phase 1 (P1)**：工程治理與 CI 收緊（ADR/CR 體系、Technical Debt Register、importlinter 收緊、資料庫遷移器 `core/db_migration.py`）。
- **Phase 2 (P1)**：AI 評測實驗室（10 大場景標註集 `tests/evaluation/golden_conversations.jsonl`、評測執行器 `eval_runner.py`）。
- **Phase 3 (P2)**：受控自主性與運維體系（`core/system_state.py`, `core/risk_engine.py`, `core/autonomy_guard.py`, `handlers/memory/retention_worker.py`）。
- **Phase 4 (P2)**：架構深度解耦與安全加固（XML Delimiter 外部內容隔離、`settings.py` 拆解模組化）。

## 3. 驗收標準 (Acceptance Criteria)
- [x] Phase 0：緊急資安阻斷與安全性加固，544+ 項單元測試 100% 通過。
- [x] Phase 1：SQLite 遷移器 (`core/db_migration.py`) 具備自動升級與自動備份能力，測試 100% 通過。
- [x] Phase 2：AI 評測實驗室 (`tests/evaluation/`) 自動化評測 10 大核心場景並產出量化指標報告。
- [x] Phase 3：三級風險策略引擎、全域 Kill Switch 與資料保留清理 Worker (`retention_worker.py`) 運作正常且通過測試。
- [x] Phase 4：外部搜尋與摘要工具導入 XML 隔離標籤與 SSRF 防禦，設定模組完成 `core/config/` 子模組化重構且向後相容。

## 4. 驗收數據與指標 (Verification Metrics)
- **單元測試全數通過**：643/643 單元測試 100% 通過 (`pytest`)。
- **架構分層合約合規**：`lint-imports --config .importlinter` 3/3 合約 100% 遵守（分析 299 個檔案、1616 條依賴關係，0 broken）。
- **Diff Coverage / 涵蓋率**：新增與重構模組涵蓋率均達標準門檻 (≥ 80%)。
- **半成品與作弊掃描**：全專案無殘留未實現之 TODO / FIXME / NotImplementedError 佔位符。

## 5. 決策 (Final Decision)
APPROVED (升級實施全數驗收完成，正式合流)
