# ADR-0001 — ADR Template & Specification

## Status

ACCEPTED

## Date

2026-09-20

## Context

Sherry 專案進入多模組、多 Agent 協作開發階段，為避免口頭或臨時性架構決策導致技術債累積與文檔漂移，需要標準化架構決策記錄 (ADR) 格式。

## Problem

過去架構決策分散於 Commit 訊息或即時對話中，難以追溯歷史脈絡、約束條件、權衡取捨以及回滾路徑。

## Decision

專案統一採用 `Docs/adr/ADR-XXXX-title.md` 格式管理所有重大架構決策。

## Consequences

- 提升架構變更透明度與可追溯性。
- 降低跨 Agent 或新協作者交接成本。
