# Architecture Decision Record (ADR) 模板

> 本模板依據《科技公司軟體架構與開發規範》與專案工程治理規範建立。
> 用於記錄重要架構設計、技術選型決策、權衡與回滾策略。

---

# ADR-XXXX — [決策標題]

## 狀態 (Status)

- [ ] PROPOSED（提議中）
- [ ] ACCEPTED（已通過/採納）
- [ ] REJECTED（已駁回）
- [ ] SUPERSEDED（已被新決策取代）
- [ ] DEFERRED（暫緩）

## 日期 (Date)

YYYY-MM-DD

## 背景與現況 (Context)

描述目前的系統狀態、背景脈絡與促成此決策的前提條件。

## 問題陳述 (Problem Statement)

明確定義本架構決策要解決的核心問題是什麼？

## 約束條件 (Constraints)

- **硬體/運行環境**：
- **延遲與效能要求**：
- **向下相容性 (Backward Compatibility)**：
- **資安與隱私邊界 (Security & Privacy)**：
- **分層架構限制 (Layering Rules)**：依據 `AGENTS.md` 第 0 節。

## 評估方案 (Options)

### 方案 A: [名稱]
- **說明**：
- **優勢**：
- **劣勢**：

### 方案 B: [名稱]
- **說明**：
- **優勢**：
- **劣勢**：

### 方案 C: [名稱]
- **說明**：
- **優勢**：
- **劣勢**：

## 最終決策 (Decision)

選擇方案：**[方案名稱]**
決策依據與核心考量：

## 證據與驗證 (Evidence & Verification)

- **原始碼證據**：`[file.py](file:///path/to/file.py#L1-L10)`
- **測試驗證**：
- **基準測試/效能指標**：
- **運行時觀測**：

## 權衡分析 (Trade-offs)

### 效益 (Benefits)
1. 

### 成本 (Costs)
1. 

### 風險與緩解 (Risks & Mitigations)
1. 

## 被駁回的替代方案 (Rejected Alternatives)

記錄為何不選擇其他方案，以供未來審查參考。

## 影響與後續行動 (Consequences)

### 短期影響 (Short-term)
- 

### 長期影響 (Long-term)
- 

## 回滾策略 (Rollback Strategy)

若變更在生產環境出現嚴重問題時的快速回退步驟與相容性保證。

## 複核觸發條件 (Review Trigger)

在何種情況下（如流量增長、依賴升級、架構重構）需要重新檢視本決策？
