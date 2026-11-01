# ADR-0002 — 工程治理、AI 評測體系與受控自主性架構決策

## Status

ACCEPTED

### Implementation Status（三段制）

- **Designed**：決策條文自 2026-09-23 確立——`core/risk_engine.py` 三級風險映射、`core/autonomy_guard.py` 全域熔斷與時間窗預算、`core/system_state.py` 健康等級聚合（`HEALTHY` / `DEGRADED` / `WARNING` / `INCIDENT`）。設計階段此三件套尚未接入任何生產路徑。
- **Implemented**：R8「治理接線與 SPARK 預算」已將三件套接入兩個 choke point：
  - `handlers/spark/cog.py`：每輪巡檢於行動派發前呼叫 `AutonomyGuard.check("spark_push", ...)`，通過才建立動作任務；動作具長生命週期 semaphore、強引用集合與 `asyncio.wait_for` timeout（`SPARK_MAX_CONCURRENT_ACTIONS` / `SPARK_ACTION_TIMEOUT_SECONDS`）。
  - `handlers/bot_brain/cog.py`：`BotBrain.enqueue_task` 對自主任務（`priority >= settings.PRIORITY_INTERJECT` 或顯式 `is_autonomous`）提交前呼叫 `check("bot_brain_autonomous", ...)`；拒絕時以空回覆回呼（fail-closed）。
  - `handlers/spark/quota.py` ＋ `handlers/spark/service.py`：per-user trigger 數上限、週期分級下限（LLM 型 ≥30 分鐘、通知型 ≥5 分鐘）與每日推送配額。
  - `core/risk_engine.py`：`bot_brain_autonomous` 映射為 MEDIUM；未註冊行動代碼維持預設 HIGH（fail-closed）。
- **Verified**：`tests/unit/test_governance_wiring_r8.py` 為 R8 專屬驗證（guard 串接 risk/state、SPARK `spark_push` 派發 choke point、BotBrain 閘門、配額與並發上限），並以既有 `test_autonomy_guard.py` / `test_risk_engine.py` / `test_system_state.py` / `test_spark_*` 作回歸。

## Date

2026-09-23

## Context

根據 2026-09-20 規劃書體系，Sherry 平台需由單純的 Discord Bot 演進為高可用、具備安全評測與受控自主行動能力的 Companion Platform。經四大專屬 Auditor（架構、評測、自主性、資安）深度檢測，盤點出包含 God-Files 耦合、缺乏 AI 自動化評測標註集、缺少全域自主性熔斷與資料保留機制等技術缺口。

## Problem

1. **AI 品質無量化回歸保護**：提示詞或工具調度邏輯更動時，難以自動化驗證工具命中率與人設一致性。
2. **缺乏自主性安全熔斷**：主動行為與排程推播缺乏三級風險控制（Low / Medium / High）與全域緊急停止機制。
3. **缺少資料庫遷移與資料保留機制**：SQLite 資料庫缺乏 `PRAGMA user_version` 自動遷移，且日誌無限期增長。

## Decision

1. **AI 評測實驗室 (AI Evaluation Lab)**：
   - 建立 `tests/evaluation/golden_conversations.jsonl`，收錄 10 大核心場景（CHAT, STOCK, SEARCH, DRAW, MUSIC, SCHEDULE, MEMORY, MULTITOOL, PERSONA, EDGE_CASE）。
   - 建立 `tests/evaluation/eval_runner.py`，提供自動化工具精準度、人設評分與回歸測試。
2. **三級受控自主性與全域熔斷 (Autonomy Control)**：
   - 建立 `core/risk_engine.py` 與 `core/autonomy_guard.py`，強制實施 `GLOBAL_AUTONOMY_KILL_SWITCH` 與時間窗預算上限。
   - 建立 `core/system_state.py`，將底層監控指標標準化收斂為 `HEALTHY` / `DEGRADED` / `WARNING` / `INCIDENT`。
3. **資料庫遷移與資料生命週期治理**：
   - 導入 `core/db_migration.py`，基於 SQLite `PRAGMA user_version` 實現安全的 Schema 遞增遷移與自動備份。
   - 建立 `handlers/memory/retention_worker.py`，定期清理超過保存期限之運行日誌與暫存資料。

## Consequences

- 顯著提升 AI 提示詞調優與工具重構時的安全性與回歸防護。
- 提供生產環境主動推播與自主行動之最高安全防護。
- 達成生產環境資料庫安全遷移與磁碟空間受控。
