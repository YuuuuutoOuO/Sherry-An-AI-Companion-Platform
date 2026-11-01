# 25 — 全系統模組相依有向圖與分層驗證 (Dependency Graph)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面繪製雪莉專案內部各套件的模組相依有向無環圖 (DAG)，分析跨模組 import 關係。依據《科技公司軟體架構與開發規範 v2.2》第 0 節與第 8 節，檢驗架構分層的依賴方向性，識別任何違規的依賴與潛在循環引用風險點。

---

## 🧭 Prerequisites (先備知識)
- 有向無環圖 (DAG) 與拓撲排序 (Topological Sorting)。
- Python import 機制（絕對引用 vs. 相對引用、模組快取 `sys.modules`）。
- 架構依賴檢查工具 `import-linter` 的合約配置。

---

## 💡 Concept (依賴反轉與分層單向原則)
健全的軟體架構必須嚴格恪守**單向依賴原則 (Unidirectional Dependency Principle)**：
- **頂層（展示層 `handlers/`, `web_ui/`）** 依賴 **底層（核心服務 `core/`, `app/`）**。
- **核心領域業務層 (`mod/`)** 必須維持純粹，**禁止反向依賴展示層**，且非 Client/Repository 層**禁止直接 import I/O 套件**。
- 若出現逆向引用（例如 `core/` 引用了 `handlers/`），會形成緊密耦合，導致無法對核心邏輯進行獨立單元測試。

---

## 🛠️ Sherry Implementation (雪莉相依分層與架構規範)
雪莉在實體代碼組織上貫徹嚴格的單向依賴拓撲：
1. **進入點組裝 (`main.py`)**：統一負責依賴注入、資源配置載入與生命週期排程。
2. **展示層 (`handlers/`, `web_ui/`)**：透過依賴注入獲取 `SherryCore` 或具體 Service，不反向被核心引用。
3. **微核心層 (`app/orchestrator.py`)**：協調記憶、意圖、工具派發，透過 Protocol 定義能力。
4. **領域外掛層 (`mod/`)**：外掛間嚴格平行隔離，業務邏輯下沉，外部 I/O 必須委派給 `mod/clients/` 或 `mod/repositories/`。

---

## 📊 Flow Diagram (全系統模組相依有向圖 DAG)

```text
                  +-------------------------+
                  |         main.py         | (啟動進入點)
                  +------------+------------+
                               |
         +---------------------+---------------------+
         |                                           |
         v                                           v
+-----------------+                         +-----------------+
|    handlers/    | (Presentation Cogs)     |     web_ui/     | (Quart 控制台)
+--------+--------+                         +--------+--------+
         |                                           |
         +---------------------+---------------------+
                               |
                               v
                  +-------------------------+
                  |           app/          | (微核心 Orchestrator)
                  +------------+------------+
                               |
         +---------------------+---------------------+
         |                                           |
         v                                           v
+-----------------+                         +-----------------+
|      core/      | (共用契約/DB/安全守衛)   |      mod/       | (可選業務外掛)
+-----------------+                         +--------+--------+
                                                     |
                                                     v
                                            +-----------------+
                                            |  mod/clients/   | (外部 I/O 客戶端)
                                            +-----------------+
```

---

### 二、Import-Linter 合約配置佐證
在專案根目錄的 `.importlinter` 設定中，定義了全專案 4 大根套件 (`app`, `core`, `handlers`, `mod`) 與三大架構合約規則：

```ini
[importlinter]
root_packages =
    app
    core
    handlers
    mod
include_external_packages = True

[importlinter:contract:1]
name = I/O packages only inside adapter layers
type = forbidden
source_modules =
    mod
forbidden_modules =
    sqlite3
    requests
    aiohttp
    discord
allow_indirect_imports = True
ignore_imports =
    mod.clients.* -> *
    mod.*.clients.* -> *
    mod.*.repositories.* -> *
    mod.*.repository -> *
    mod.*.cog -> *
    mod.*.ui -> *
    mod.*.views -> *
    mod.*.reports.* -> *
    mod.dnd.world_sim -> discord
    mod.market_plugin.utils -> discord
    mod.feed_sentinel.fetcher -> *
    mod.market_plugin.data_fetcher -> *

[importlinter:contract:2]
name = Core foundation must not import upper layers
type = forbidden
source_modules =
    core
forbidden_modules =
    app
    handlers
    mod

[importlinter:contract:3]
name = Handlers must not import the application layer
type = forbidden
source_modules =
    handlers
forbidden_modules =
    app
```

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **.importlinter 配置檔案**：
   - 專案根目錄 [`.importlinter`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/.importlinter) 包含上述合約規則。
2. `[CONFIRMED]` **微核心延遲載入以切斷靜態逆向依賴**：
   - 見 [`app/orchestrator.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/app/orchestrator.py#L61-L65)：
     ```python
     @staticmethod
     def _create_default_cloud_client() -> CloudInferenceProtocol:
         """安全延遲載入預設雲端推論客戶端，避免微核心頂層直接靜態依賴 mod 層。"""
         from mod.clients.cloud_inference_client import CloudInferenceClient
         return CloudInferenceClient()
     ```
     該處採用局部延遲載入 (Lazy Import)，確保 `app/` 在模組層級不直接靜態綁死 `mod/clients/`。

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **模組級別局部 Import 引發的隱性循環引用**：
   - 在 Python 中，若模組 A 的函式內部 `from B import x`，而模組 B 在函式內部 `from A import y`，靜態分析工具不易抓出，但在特定執行緒併發初始化時可能引發 `ImportError: cannot import name ... from partially initialized module`。
2. **Repository 洩漏案例**：
   - `[CONFIRMED]` 如前述調查，`mod/market_plugin/repository.py` 內部存在 8 處 `with sqlite3.connect(...)`，雖然在 `ignore_imports = mod.repositories.* -> *` 豁免範圍內，但因繞過了 `core/db.py`，導致其連線未享受到全域 WAL 模式設定。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **依賴倒置 (Dependency Inversion) 搭配 Protocol** | `core/contracts.py` 定義 `CloudInferenceProtocol`，高層模組不需認識具體 Client 實作。 | 需要撰寫更多抽象 Protocol 定義，型別標註需保持嚴格一致。 | 直接以具體類別傳遞或使用裸字典傳參。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在大型 Python 專案中，如何使用 CI 自動化防範工程師破壞架構分層？**
  - **Ans**: 純靠 Code Review 難免有漏網之魚。業界標準作法是在 Pre-commit 或 CI 中引入 `import-linter` 或 `pytest-archon`。透過宣告式規則禁止「Service 層 import I/O」或「底層反向 import 控制層」，一旦有違規 import，CI 立即阻擋合併，將架構紀律機器化。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Architecture/26-architecture-boundaries.md](./26-architecture-boundaries.md) 研讀架構邊界守則與封裝防禦規範。
