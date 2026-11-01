# 26 — 架構邊界規範與 Import-Linter 規則 (Architecture Boundaries)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
深度檢視雪莉架構邊界的封裝性與防禦策略。以《科技公司軟體架構與開發規範 v2.2》為標準，評估 Service、Presentation、Repository 三層間的通訊合約，檢視依賴注入 (Dependency Injection) 的落實程度，並分析邊界穿透 (Boundary Leaks) 的防範做法。

---

## 🧭 Prerequisites (先備知識)
- 六角架構 (Hexagonal Architecture / Ports and Adapters)。
- Python `typing.Protocol` 結構子型別 (Structural Subtyping) 與 ABC 抽象基底類別。
- 靜態邊界測試與架構防腐層 (Anti-Corruption Layer)。

---

## 💡 Concept (架構邊界與防腐層理論)
架構邊界是將「高階業務策略」與「低階實作細節」隔開的防護網：
1. **Ports (通訊埠/介面)**：業務核心所要求的輸入與輸出契約。
2. **Adapters (配接器/客戶端)**：連接真實世界（HTTP、SQLite、Discord、Ollama）的膠水代碼。
3. **邊界防護**：業務核心不應感知外部是 Discord 還是 Telegram，也不應感知底層是 SQLite 還是 PostgreSQL。

---

## 🛠️ Sherry Implementation (雪莉架構邊界實作)

### 一、邊界契約定義 (`core/contracts.py`)
雪莉在 `core/contracts.py` 中明確宣告各系統邊界的 Protocol：

```python
@runtime_checkable
class CloudInferenceProtocol(Protocol):
    """雲端推論客戶端抽象協定。"""
    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str: ...
```

透過此 Protocol，微核心 (`SherryCore`) 與系統巡檢 (`audit_service.py`) 僅需依賴此抽象介面，真實的 `CloudInferenceClient` 則置於 `mod/clients/` 下，透過建構子注入傳遞。

---

### 二、三層分界與存取矩陣

| 系統層級 | 代表目錄 | 允許的操作與引用 | 嚴格禁止的行為 |
|---|---|---|---|
| **Presentation 層** | `handlers/`, `web_ui/` | 處理 Discord 事件、解析 HTTP 參數、渲染 Embed | 直接撰寫原生 SQL 操作 DB 或直連第三方 API |
| **Service / Domain 層**| `mod/`, `app/` | 業務規則、狀態轉移、意圖決策、記憶演進 | `MUST NOT` import `sqlite3`, `discord`, `aiohttp`, `requests` |
| **Repository / Client 層**| `mod/clients/`, `mod/repositories/` | 實作具體 I/O、封裝 SQL、呼叫第三方 HTTP API | 包含核心業務狀態決策或渲染 Discord 訊息視圖 |

---

## 📊 Flow Diagram (架構邊界調用與防腐隔離拓撲圖)

```text
+──────────────────────────────────────────────────────────+
│ Presentation Layer (handlers/*, web_ui/*)                 │
│  - 只負責接收使用者事件與視圖渲染                        │
+──────────────────────────┬───────────────────────────────+
                           │ (呼叫 Service / Orchestrator)
                           ▼
+──────────────────────────────────────────────────────────+
│ Domain / Service Layer (app/*, mod/*)                    │
│  - 純粹業務邏輯，嚴禁 import I/O (sqlite3/aiohttp/discord)│
+──────────────────────────┬───────────────────────────────+
                           │ (依賴注入 Protocol 抽象合約)
                           ▼
+──────────────────────────────────────────────────────────+
│ Repository / Client Layer (mod/clients/*, mod/repos/*)   │
│  - 唯一允許實體 I/O、HTTP Session、SQL 執行的防腐邊界    │
+──────────────────────────────────────────────────────────+
```

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **Protocol 契約定義**：
   - 見 [`core/contracts.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/contracts.py#L20-L45)。
2. `[CONFIRMED]` **依賴注入實證**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L208-L209)：
     ```python
     from core.sys_inspector.audit_service import set_cloud_inference_client
     set_cloud_inference_client(bot.core.cloud_client)
     ```
     明確透過 Setter / DI 注入雲端推論客戶端，未讓 `audit_service` 直接靜態實例化具體客戶端。

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **邊界穿透 (Boundary Penetration)**：
   - 歷史實作中，部分 handler 工具（如 `handlers/social/tools/draw_tool.py`）內部直接建立 `aiohttp.ClientSession()` 直連繪圖後端。
   - 依規範理想狀態應將繪圖 HTTP 請求完全下沉至 `mod/clients/draw_client.py`，由 Tool 呼叫 Client。此處屬於輕微的邊界混合技術債。
2. **動態 Mock 假通過**：
   - 若單元測試中大量使用 `unittest.mock.patch` 覆寫跨層呼叫，但真實環境下底層回傳的型別與 Protocol 不符，靜態檢查通過但動態運行時會拋出 `AttributeError`。因此需搭配 `@runtime_checkable` 與整合測試確保型別保真度。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **`typing.Protocol` 鴨子型別契約** | 實作類別不需強制繼承 BaseClass，降低模組耦合度，輕量易測。 | 靜態 IDE 自動跳轉至實作類別的體驗略弱於繼承體系。 | `abc.ABC` 抽象基底類別繼承。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在 Python 中使用 `typing.Protocol` 與 `abc.ABC` 定義系統架構邊界有何異同？何時該用何者？**
  - **Ans**:
    - `abc.ABC` 是名義型別 (Nominal Subtyping)，子類別必須顯式繼承，適合需要共用部分預設方法實作或強制在執行期執行抽象方法覆寫驗證時。
    - `typing.Protocol` 是結構型別 (Structural Subtyping / Duck Typing)，只要物件具備相符的函式簽章即可相容，無需顯式繼承。在微核心與外掛架構中，使用 `Protocol` 可讓第三方外掛免於依賴核心基底類別庫，達到真正的無侵入式解耦。

---

## 🔗 What to Study Next (後續章節銜接)
- 進入系統底層核心，前往 [Systems/07-networking.md](../Systems/07-networking.md) 研讀網路協定棧與通訊架構。
