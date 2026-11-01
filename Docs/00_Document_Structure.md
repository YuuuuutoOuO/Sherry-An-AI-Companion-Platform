# 📚 Docs — 技術文件目錄

> 最後更新：2026-09-23 02:15

本資料夾為專案技術文件的統一存放位置，收錄架構決策 (ADR)、變更申請 (CR)、技術債務追蹤、系統規格與自動化 AST 模組文件。

> **命名規範**：小寫資料夾 = 程式碼；大寫開頭資料夾 = 技術文件

---

## 📁 核心架構與治理文件清單

| 檔案名稱 | 說明 |
|---|---|
| [00_Document_Structure.md](./00_Document_Structure.md) | **本文件**。說明 `Docs/` 的結構與各技術文件用途導覽。 |
| [01_Project_Snapshot.md](./01_Project_Snapshot.md) | 專案高層次總覽：系統願景、技術棧、分層架構圖、資料流、核心設計原則與外掛系統。 |
| [02_TODO_and_Improvements.md](./02_TODO_and_Improvements.md) | 待辦事項與演進方向：各階段升級歷程與改進目標。 |
| [02_Technical_Debt_Register.md](./02_Technical_Debt_Register.md) | **技術債登記冊**：記錄全系統既有技術債、影響面、緩解方案與目標消除版本。 |

---

## 🏛️ 架構決策記錄 (Architecture Decision Records, ADR)

| 檔案名稱 | 決策標題 / 核心主題 | 狀態 | 決策日期 |
|---|---|---|---|
| [ADR-0001-template.md](./adr/ADR-0001-template.md) | 架構決策記錄標準範本 (Architecture Decision Record Template) | - | - |
| [ADR-0002-governance-and-ai-evaluation.md](./adr/ADR-0002-governance-and-ai-evaluation.md) | 工程治理、AI 評測體系與受控自主性架構決策 | ACCEPTED | 2026-09-23 |

---

## 📋 變更申請與升級計畫 (Change Requests, CR)

| 檔案名稱 | 變更主題 / 升級範圍 | 決策狀態 | 關聯版本 |
|---|---|---|---|
| [CR-0001-template.md](./cr/CR-0001-template.md) | 變更申請標準範本 (Change Request Template) | - | - |
| [CR-0002-phase0-to-phase4-system-upgrade.md](./cr/CR-0002-phase0-to-phase4-system-upgrade.md) | 全維度工程治理、AI 評測實驗室與自主性升級實施計畫 | APPROVED | Phase 0 ~ Phase 4 |

---

## 🧩 模組詳解與 AST 自動生成文件

| 檔案名稱 | 說明 |
|---|---|
| [module_root.md](./module_root.md) | 根目錄 `.py`（`main.py`, `settings.py`, `build_tag_dict.py`）的詳細 function 與變數紀錄。 |
| [module_file.md](./module_file.md) | `file/` 目錄（`AI_personality.py` 等）的詳細變數紀錄。 |
| [module_handlers.md](./module_handlers.md) | `handlers/` 所有子模組的詳細 class、function、參數紀錄。 |

---

## 📜 專利、技術揭露與演進計畫

| 檔案名稱 / 目錄 | 說明 |
|---|---|
| [專利_技術揭露書.md](./專利_技術揭露書.md) | 專利技術揭露書完整版（多層級認知與伴侶架構）。 |
| [專利_技術揭露書_簡版.md](./專利_技術揭露書_簡版.md) | 專利技術揭露書精簡版。 |
| [技術摘要_全系統.md](./技術摘要_全系統.md) | 全系統技術架構核心摘要。 |
| [技術摘要_Web_UI.md](./技術摘要_Web_UI.md) | Web 控制台與視覺化介面技術摘要。 |
| [plans/](./plans/) | 系統架構巡檢、天氣服務整合等實施計畫目錄。 |
| [templates/](./templates/) | ADR、CR 與 Agent Task 標準文件範本庫。 |
| [README_V1.x.md](./) | V1.4.0 ~ V1.9.0 歷史版本 Release Notes。 |

---

## 🌳 文件目錄結構樹

```text
Docs/
├── 00_Document_Structure.md                 # 技術文件目錄導覽 (本檔)
├── 01_Project_Snapshot.md                   # 專案架構與系統高層總覽
├── 02_TODO_and_Improvements.md              # 待辦事項與演進方向
├── 02_Technical_Debt_Register.md            # 技術債登記冊
├── module_root.md                           # 根目錄模組 AST 詳解
├── module_file.md                           # file/ 模組詳解
├── module_handlers.md                       # handlers/ 模組詳解
├── adr/                                     # 架構決策記錄 (ADR)
│   ├── ADR-0001-template.md
│   └── ADR-0002-governance-and-ai-evaluation.md
├── cr/                                      # 變更申請記錄 (CR)
│   ├── CR-0001-template.md
│   └── CR-0002-phase0-to-phase4-system-upgrade.md
├── plans/                                   # 系統演進與整合專案計畫
│   └── 2026-09-09-system-inspector-and-weather-consolidation.md
├── templates/                               # 標準範本庫
│   ├── adr_template.md
│   ├── agent_task_template.md
│   └── change_request_template.md
├── 專利_技術揭露書.md
├── 專利_技術揭露書_簡版.md
├── 技術摘要_Web_UI.md
├── 技術摘要_全系統.md
└── README_V1.x.md                           # 歷史版本更新紀錄 (V1.4.0 ~ V1.9.0)
```

---

## 🔄 更新方式

程式碼有變動時，在專案根目錄執行以下 `.bat` 即可自動重新生成 AST 文件：

```bash
update_docs.bat
```

或手動分步執行：
```bash
python file/auto/scan_ast.py
python file/auto/generate_docs.py
```

