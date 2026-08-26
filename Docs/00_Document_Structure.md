# 📚 Docs — 技術文件目錄

> 最後更新：2026-08-26 20:13

本資料夾為專案技術文件的統一存放位置，所有文件均由 `file/auto/` 中的腳本自動生成，並可手動補充說明。

> **命名規範**：小寫資料夾 = 程式碼；大寫開頭資料夾 = 技術文件

## 📁 文件清單

| 檔案名稱 | 說明 |
|---|---|
| [00_Document_Structure.md](./00_Document_Structure.md) | **本文件**。說明 Docs/ 的架構與各文件用途。 |
| [01_Project_Snapshot.md](./01_Project_Snapshot.md) | 專案高層次總覽：目標、技術棧、架構圖、資料流、核心設計原則、插件系統說明。 |
| [02_TODO_and_Improvements.md](./02_TODO_and_Improvements.md) | 整合後的待辦事項、改進方向與已完成功能紀錄。 |
| [module_root.md](./module_root.md) | 根目錄 `.py`（`main.py`, `settings.py`, `build_tag_dict.py`）的詳細 function 與變數紀錄。 |
| [module_file.md](./module_file.md) | `file/` 目錄（`AI_personality.py` 等）的詳細變數紀錄。 |
| [module_handlers.md](./module_handlers.md) | `handlers/` 所有子模組的詳細 class、function、參數紀錄。 |
| [README_V1.6.0.md](./README_V1.6.0.md) | v1.6.0 歷史版本完整技術文件（Parallel/ReAct 雙智能、多模態自我認知、6大外掛）。 |
| [README_V1.5.0.md](./README_V1.5.0.md) | v1.5.0 歷史版本完整技術文件（Linux/Docker 容器化、vLLM 引擎、.env 優先級）。 |
| [README_V1.4.0.md](./README_V1.4.0.md) | v1.4.0 / v1.4.1 歷史版本完整技術文件（大腦推理佇列、地端 AI 監測、審計落盤）。 |

## 🔄 更新方式

程式碼有變動時，在專案根目錄執行以下 .bat 即可自動重新生成所有文件：

```
update_docs.bat
```

或手動分步執行：
```bash
python file/auto/scan_ast.py
python file/auto/generate_docs.py
```
