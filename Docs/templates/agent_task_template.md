# Agent 任務分派模板 (Agent Task Assignment Template)

> 用於向多 Agent 團隊指派具體任務，確保 Context 邊界清晰、約束具體、交付標準可檢測。

---

# Task-XXXX — [任務名稱]

## 1. 任務基本資訊

- **指派對象 (Agent Role)**：`[Architect | Builder | QA | Security | Performance]`
- **關聯 CR/ADR**：`[CR-XXXX]` / `[ADR-XXXX]`
- **優先順序**：`[P0 - 緊急阻斷 | P1 - 高 | P2 - 一般 | P3 - 低]`

## 2. 任務背景與目標 (Context & Goal)

清楚說明任務背景與必須達成的核心目標。

## 3. 輸入資訊與參考檔案 (Inputs & References)

- `[file1.py](file:///path/to/file1.py#L1-L50)`
- `[doc1.md](file:///path/to/doc1.md)`

## 4. 嚴格邊界與約束 (Boundaries & Constraints)

- **只允許修改的檔案**：
- **禁止修改的檔案**：
- **分層架構限制**：遵循 `AGENTS.md`（Service 層禁止 direct I/O）。
- **讀檔成本上限**：優先精準讀取必要行段，嚴禁無限制掃描。

## 5. 具體執行步驟 (Execution Steps)

1. 
2. 
3. 

## 6. 交付物清單 (Deliverables)

1. 程式碼修改 / 新增檔案。
2. 對應單元測試（遵從 AAA 結構）。
3. 驗證報告與 Lint 結果。

## 7. 完成判定標準 (Definition of Done)

- [ ] 單元測試通過 (`pytest`)。
- [ ] Diff coverage $\ge 80\%$。
- [ ] 靜態檢查通過 (`ruff`, `mypy`)。
- [ ] 無半成品代碼 (`TODO`, `FIXME`, `pass`)。
