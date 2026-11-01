# Change Request (CR) 模板

> 本模板用於規範重大代碼變更、架構調整與風險性功能更新。
> 任何跨模組改動或行為變更均需先經 CR 提案、評審並指派 Agent 角色。

---

# CR-XXXX — [變更標題]

## 1. 變更目標 (Objective)

簡要描述本次變更的主要目的與預期成果。

## 2. 問題背景與動機 (Problem & Motivation)

- **現狀問題**：
- **變更動機**：

## 3. 範圍界定 (Scope)

### 本次涵蓋 (In Scope)
- 

### 本次排除 (Out of Scope)
- 

## 4. 行為比對 (Behavior Comparison)

| 維度 | 當前行為 (Current Behavior) | 變更後行為 (Proposed Behavior) |
|---|---|---|
| 功能行為 | | |
| 錯誤處理 | | |
| 日誌與可觀測性 | | |
| 安全邊界 | | |

## 5. 涉及檔案與模組清單 (Files & Modules)

- `[NEW]` `[file.py](file:///path/to/file.py)`
- `[MODIFY]` `[file.py](file:///path/to/file.py)`
- `[DELETE]` `[file.py](file:///path/to/file.py)`

## 6. 依賴與潛在風險 (Dependencies & Risks)

- **外部依賴變更**：
- **破壞性變更 (Breaking Changes)**：
- **風險評估與因應**：

## 7. 驗證與品質保證計畫 (Verification & QA Plan)

### 7.1 單元與整合測試 (Test Plan)
- 

### 7.2 效能與基準測試 (Benchmark Plan)
- 

### 7.3 資安檢測 (Security Plan)
- 

## 8. 驗收條件 (Acceptance Criteria)

- [ ] **功能驗收**：核心業務邏輯符合預期且通過測試。
- [ ] **回歸測試**：既有測試 100% 通過（無新增 broken tests）。
- [ ] **覆蓋率門檻**：Diff Coverage $\ge 80\%$。
- [ ] **靜態檢查**：`ruff check .` 與 `mypy --strict` 無錯誤。
- [ ] **分層契約**：`importlinter` 0 違規。
- [ ] **資安無漏洞**：無明文密鑰、無未防護 prompt injection / SSRF。
- [ ] **文檔同步**：`Docs/` 相應文檔完成更新。

## 9. 回滾計畫 (Rollback Plan)

明確列出若變更失敗時的還原步驟與資料庫回滾指令。

## 10. Agent 角色指派 (Agent Assignment)

- **Research Agent**: 
- **Builder Agent**: 
- **QA Agent**: 
- **Security Auditor**: 
- **Performance Specialist**: 

## 11. 最終決策 (Final Decision)

- [ ] **APPROVED**（已核准）
- [ ] **REJECTED**（已駁回）
- [ ] **DEFERRED**（暫緩）
