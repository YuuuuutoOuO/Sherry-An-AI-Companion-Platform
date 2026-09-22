# 雪莉核心系統監控 (SYSTEM_INSPECT / SENTRY) 升級與天氣工具 (WEATHER) 收斂至 SEARCH — 實作計畫 v4

> 本計畫整合了前端對話調度與後台維運架構，採用**雙輪驅動設計**：
> 1. **前端對話層（Interactive Tool）**：提供 `SYSTEM_INSPECT` 工具，透過既有 `tool_rules.md` 讓雪莉在對話中以角色口吻回答系統狀況。
> 2. **後台守衛層（Automated Sentry & Cloud Diagnostics）**：將 `mod/self_monitoring` 的核心哨兵功能收斂至 `core/sys_inspector/`，在系統阻塞、推論崩潰或慢速回應時，自動抓取 5 分鐘日誌、調用雲端大模型（`CLOUD_SEARCH_MODEL`）進行根因診斷，並推播 Discord 警報與寫入稽核資料庫。
> 3. **天氣工具收斂**：將受限的 `WeatherTool` 退役，全域由 `ChatTool` / `SEARCH` 聯網管線承接。

---

## 系統架構總覽（雙輪驅動架構）

```
                                  ┌─────────────────────────────┐
                                  │      雪莉雙輪系統監控體系     │
                                  └──────────────┬──────────────┘
                                                 │
                  ┌──────────────────────────────┴──────────────────────────────┐
                  ▼                                                             ▼
  【前端對話層 (Interactive Tool)】                             【後台守衛層 (Automated Sentry)】
  • 觸發：群友主動詢問（「你會不會卡？」）                          • 觸發：背景定期心跳 / 事故事件攔截
  • 流程：Stage 1 讀取 tool_rules.md 輸出 ACTION                 • 流程：
    └─ SystemInspectTool.execute()                                ├─ Event Loop Lag > 3s 或 佇列阻塞 > 60s
    └─ 隨需輕量採樣（CPU/RAM/GPU）                                ├─ 單次回覆慢速 > 90s 或 超時失敗 > 120s / 崩潰
    └─ Stage 2 雪莉以角色性格回覆對話                              ├─ 自動擷取過去 5 分鐘日誌快照 (嚴格隱私脫敏)
                                                                 ├─ 調用雲端模型 (CLOUD_SEARCH_MODEL) 剖析根因
                                                                 ├─ 產生富文字 Embed 警報推播至 Discord 管理員頻道
                                                                 └─ 寫入 SQLite audit.db 持久化紀錄
```

---

## 架構決策摘要

| 決策項 | 結論 | 理由 |
|--------|------|------|
| **SYSTEM_INSPECT 觸發方式** | **Tool 並行調用**（跟 STOCK、SEARCH 同級） | 完全沿用既有 `tool_rules.md` + `TOOL_LIST` 機制，LLM Stage 1 自主判斷，支援多工具並行（如「分析0050 + 看系統狀態」） |
| **權限控管** | **Tool execute() 內建** — 從 `message.author` 判斷 scope | 不需改動 router 層，`BaseTool.execute()` 已傳入 `message` 物件（管理員完整數據 / 一般用戶一句話） |
| **後台自動哨兵收斂** | **核心自包含化** — 整合至 `core/sys_inspector/` | 徹底繼承 `mod/self_monitoring` 之自動守衛與雲端模型診斷能力，不依賴任何即將刪除的舊 mod |
| **雲端事故診斷** | **非同步背景執行** — 呼叫 `CLOUD_SEARCH_MODEL` (如 DeepSeek) | 發生延遲或阻塞時自動分析代碼路徑、資源競爭、鎖卡頓，嚴格去識別化保護用戶隱私 |
| **天氣查詢後繼** | **收斂至 SEARCH** — 補強 `_should_trigger_search()` 天氣正則 | OpenWeatherMap Free API 受限固定城市，聯網搜尋引擎與氣象資料源覆蓋更廣 |
| **部件度量插樁** | **獨立 Phase C** — 延後實施 | 需改動每一個 Cog/Tool，範圍較大，待基礎功能穩定後實施 |

---

## 實作階段規劃

本計畫分為四個清晰階段：

1. **Phase A：退役 WeatherTool ＋ SEARCH 承接天氣（🟢 低風險）**
2. **Phase B-1：建立 SYSTEM_INSPECT 對話工具 ＋ 核心硬體採樣（🟡 中風險）**
3. **Phase B-2：建立後台自動健康哨兵 ＋ 雲端 LLM 事故診斷（🟡 中風險）**
4. **Phase C：部件級度量插樁（🔴 未來評估）**

---

## Phase A：退役 WeatherTool + SEARCH 承接天氣

**風險等級**：🟢 低 — 僅改動 4 個檔案，不涉及新模組建立。

### 變更清單

| # | 檔案 | 動作 | 改動內容 |
|---|------|------|----------|
| 1 | `handlers/social/tools/chat_tools.py` | MODIFY | `_should_trigger_search()` regex 加入 `天氣\|氣溫\|溫度\|下雨\|降雨\|紫外線\|颱風\|氣象\|體感\|幾度` |
| 2 | `handlers/social/registry.py` | MODIFY | 從 `TOOL_LIST` 移除 `WeatherTool()` 引用與 import |
| 3 | `core/orchestrator.py` | MODIFY | 從 `_register_default_capabilities` 移除 `WEATHER` 能力描述子 |
| 4 | `handlers/social/dispatch/prompts.py` | MODIFY | 從 `_TOOL_ALIASES` 移除 `WEATHER_LOOKUP`/`WEATHER_REPORT` 映射；從範例區移除 WEATHER 範例 |

> **`weather_tool.py` 保留不刪除** — 僅從觸發流程中下線，避免破壞潛在外部 import。

---

## Phase B-1：建立 SYSTEM_INSPECT 對話工具 ＋ 核心硬體採樣

**風險等級**：🟡 中等 — 建立 `core/sys_inspector/` 基礎模組與對話工具。

### 架構流程

```
使用者：@雪莉 你現在會卡嗎？
    │
    ▼
tool_rules.md 規則 5：「查詢系統狀態時調用 SYSTEM_INSPECT」
    │
    ▼
LLM Stage 1 → <action name="SYSTEM_INSPECT">performance</action>
    │
    ▼
SystemInspectTool.execute(bot, message, ...)
    ├─ is_admin_user(message.author)? → scope=ALL（完整）或 PUBLIC（基本）
    ├─ SysInspectorService.get_compact_report(scope)
    │   ├─ sampler (psutil + nvidia-smi via asyncio.to_thread)
    │   └─ compact_formatter.format_report()
    └─ return ToolResult.success(compact_text)
    │
    ▼
Pass 2：LLM 以雪莉傲嬌/陪伴口吻回覆系統狀態
```

### 變更清單

#### 1. 新增檔案（`core/sys_inspector/`）

- `core/sys_inspector/__init__.py`：模組入口，匯出核心服務與契約。
- `core/sys_inspector/contracts.py`：強型別資料結構（`HardwareSummary`、`ProcessSummary`、`HealthSummary`、`SystemReport`、`ReportScope`）。
- `core/sys_inspector/sampler.py`：獨立硬體/進程/GPU 採樣（使用 `psutil` 與 `asyncio.to_thread` 執行 `nvidia-smi`，不阻塞 Event Loop）。
- `core/sys_inspector/compact_formatter.py`：將 `SystemReport` 壓縮為 LLM 友善純文字（~40-150 tokens）。
- `core/sys_inspector/service.py`：統一門面（單例）：採樣 → 組裝 → 格式化。

#### 2. 新增工具與設定

- `handlers/social/tools/sys_inspect_tool.py`：`SystemInspectTool(BaseTool)` — `intent_name="SYSTEM_INSPECT"`，內建權限分級。
- `handlers/social/registry.py`：在 `TOOL_LIST` 中加入 `SystemInspectTool()`。
- `file/soul/tool_rules.md`：新增第 5 條規則：`SYSTEM_INSPECT` 使用時機與格式範例。

---

## Phase B-2：建立後台自動健康哨兵 ＋ 雲端 LLM 事故診斷

**風險等級**：🟡 中等 — 完整繼承並收斂 `mod/self_monitoring` 的自動化事故稽核能力。

### 核心運作機制

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. 後台自動健康哨兵 (HealthSentry)                                          │
│    • 背景心跳任務 (每 10 秒)：                                                │
│      - Event Loop Lag 延遲 >= 3.0s                                          │
│      - BotBrain 任務佇列阻塞 >= 60.0s 且有排隊任務                          │
│    • 外部事件同步攔截：                                                      │
│      - 單次回覆耗時 >= 90s (慢速回應 Warning)                                │
│      - 單次回覆耗時 >= 120s / 發生崩潰斷線 (重大事故 Critical)               │
│    • 具備同一類型警報防抖冷卻 (60s)                                          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (偵測到異常且未處於冷卻期)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. 日誌回溯與嚴格脫敏 (LogSnapshotRepository)                                │
│    • 自動擷取事故發生前 5 分鐘運行日誌 (預設讀取 logs/ 輪轉檔案)              │
│    • 【資安與隱私防護】：全面過濾使用者聊天內容，僅保留系統錯誤、Trace ID、  │
│      執行耗時 (ms)、函式名稱與佇列度量數據                                  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. 雲端大模型深度診斷 (CloudAnalysisClient)                                  │
│    • 調用 settings.CLOUD_SEARCH_MODEL (如 deepseek-v4-flash / 雲端模型)     │
│    • 結構化技術剖析：                                                        │
│      - 【瓶頸分類】：GPU_LOCK / API_TIMEOUT / PROMPT_OVERFLOW / QUEUE...    │
│      - 【根因剖析】：客觀說明代碼路徑、資源競爭或架構病灶                   │
│      - 【關鍵跡證】：引述 1~3 筆關鍵系統日誌                                │
│      - 【修復建議】：應急復原措施與長期調優方針                              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. 警報推播與稽核持久化 (AlertNotifier & AuditDBRepository)                  │
│    • 自動建構 Discord Embed 富文字警報卡片 (依嚴重度標註黃色/紅色)           │
│    • 發送至 settings.HEALTH_ALERT_CHANNEL_ID                                 │
│    • 寫入 data/sys_inspector/audit.db SQLite 資料庫持久化儲存                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 變更清單（`core/sys_inspector/` 擴充）

| # | 檔案 | 職責 |
|---|------|------|
| 1 | `core/sys_inspector/sentry.py` | `HealthSentry` 類別：管理 10s 背景心跳迴圈、Event Loop Lag 量測、佇列阻塞檢測與防抖冷卻 |
| 2 | `core/sys_inspector/log_repository.py` | 5 分鐘日誌擷取器：從日誌檔案截取時間視窗內容，並執行隱私脫敏遮蔽 |
| 3 | `core/sys_inspector/cloud_diagnostician.py` | 雲端模型診斷器：組裝脫敏 Prompt，調用 `CLOUD_SEARCH_MODEL`，解析結構化診斷結果（含 Fallback） |
| 4 | `core/sys_inspector/alert_notifier.py` | 警報通知器：組裝 Discord Embed 診斷卡片，推播至管理員指定頻道 |
| 5 | `core/sys_inspector/audit_repository.py` | SQLite 稽核資料庫：管理 `data/sys_inspector/audit.db`，儲存每次事故紀錄與診斷報告 |
| 6 | `handlers/cogs/sys_inspector_cog.py` | Discord Cog：生命週期掛載背景 Sentry，並提供 `/sys_inspect [scope]` 管理指令 |

---

## 核心程式碼設計細節 (Phase B-2)

### 1. 雲端診斷 System Prompt（嚴格隱私與技術客觀）

```python
DIAGNOSTIC_SYSTEM_PROMPT = """你是一位客觀、嚴謹且高度專業的「系統架構與效能稽核員（System Performance Auditor）」。
你的職責是依據系統發生的「嚴重延遲 (>90s)」、「推論超時/崩潰 (>120s)」或「系統阻塞 (Event Loop Lag / Queue Block)」事故，徹底審查並稽核所提供的【5 分鐘系統運行日誌】與【事件快照】。

【稽核原則與立場】：
1. 嚴格保持第三方專業稽核員的客觀、中立與技術嚴謹立場，絕不具備任何聊天伴侶或情感人設。
2. 直指技術病灶，精確剖析資源競爭、鎖阻塞、長文字 Prefill、外部依賴超時、請求排隊或非同步卡頓之技術根因。

【資安與隱私防護原則（最高優先級強制執行）】：
1. 嚴格禁止在診斷報告中的任何欄位（【瓶頸分類】、【根因剖析】、【關鍵日誌跡證】、【具體修復建議】）輸出、引述或洩露任何使用者的聊天對話原文！
2. 所有跡證引用一律僅限於系統級日誌、Trace ID、執行耗時 (ms)、錯誤類型、函數/模組名稱與佇列長度。

【輸出格式規範】：
【瓶頸分類】：[GPU_LOCK | EXTERNAL_API_TIMEOUT | PROMPT_OVERFLOW | EVENT_LOOP_BLOCK | QUEUE_CONGESTION | MEMORY_PRESSURE | UNKNOWN]
【根因剖析】：(客觀說明導致本次延遲或阻塞的代碼路徑、資源競爭、請求排隊或架構問題，2~3 句話)
【關鍵日誌跡證】：
- (引述日誌中最關鍵的 1~3 筆系統級 Log / Trace 行，不得含對話內容)
【具體修復建議】：
1. (立即可採取的應急復原措施)
2. (長期架構調優、限流或配置改善建議)

請一律使用繁體中文（台灣工程用語）撰寫，切中要害，嚴禁冗餘贅詞。"""
```

### 2. 外部事件同步通知掛載介面

在 `core/sys_inspector/__init__.py` 提供乾淨的同步/非同步入口，供日誌與推論管線在發生異常時呼叫：

```python
def notify_slow_response(
    latency_ms: int,
    trace_id: str = "",
    channel_id: int | str = 0,
    user_name: str = "",
    raw_prompt: str = "",
) -> None:
    """當單次回覆生成耗時 >= 90s 時由日誌或回覆流程觸發。"""

def notify_failure(
    latency_ms: int = 0,
    error_msg: str = "",
    trace_id: str = "",
    channel_id: int | str = 0,
    user_name: str = "",
    raw_prompt: str = "",
    task_id: str = "",
    is_fatal: bool = False,
) -> None:
    """當推論中斷、API 崩潰或耗時 >= 120s 時觸發。"""
```

---

## Phase C：部件級度量插樁（未來）

**風險等級**：🔴 高範圍 — 需改動每個 Cog 與 Tool 的內部流程。

待 Phase A、B-1、B-2 穩定後再行評估：
1. **`core/sys_inspector/metrics_collector.py`**：滑動視窗度量收集器（P50/P95 延遲、Token 消耗）。
2. **插樁位置**：`BotBrain`（Token 歸因）、各 Tool `execute()`（延遲量測）。
3. **擴充輸出**：在管理員報告中加入耗時排行榜。

---

## 檔案變更總覽

### Phase A（4 個檔案改動）
| 檔案 | 動作 | 說明 |
|------|------|------|
| `handlers/social/tools/chat_tools.py` | MODIFY | 天氣正則擴充 |
| `handlers/social/registry.py` | MODIFY | 移除 WeatherTool |
| `core/orchestrator.py` | MODIFY | 移除 WEATHER 能力 |
| `handlers/social/dispatch/prompts.py` | MODIFY | 移除 WEATHER 別名與範例 |

### Phase B-1（前端對話工具：5 新增 ＋ 2 改動）
| 檔案 | 動作 | 說明 |
|------|------|------|
| `core/sys_inspector/__init__.py` | NEW | 模組入口 |
| `core/sys_inspector/contracts.py` | NEW | 資料結構契約 |
| `core/sys_inspector/sampler.py` | NEW | 硬體/GPU/進程採樣器 |
| `core/sys_inspector/compact_formatter.py` | NEW | LLM 友善輸出壓縮器 |
| `core/sys_inspector/service.py` | NEW | 系統監控整合服務門面 |
| `handlers/social/tools/sys_inspect_tool.py` | NEW | SYSTEM_INSPECT 對話工具 |
| `handlers/social/registry.py` | MODIFY | 註冊 SystemInspectTool |
| `file/soul/tool_rules.md` | MODIFY | 加入 SYSTEM_INSPECT 規則 |

### Phase B-2（後台哨兵與雲端診斷：5 新增 ＋ 1 改動）
| 檔案 | 動作 | 說明 |
|------|------|------|
| `core/sys_inspector/sentry.py` | NEW | 背景心跳與阻塞檢測哨兵 |
| `core/sys_inspector/log_repository.py` | NEW | 5 分鐘日誌擷取與脫敏 |
| `core/sys_inspector/cloud_diagnostician.py` | NEW | 雲端模型診斷器 |
| `core/sys_inspector/alert_notifier.py` | NEW | Discord Embed 警報推播 |
| `core/sys_inspector/audit_repository.py` | NEW | SQLite 稽核資料庫倉儲 |
| `handlers/cogs/sys_inspector_cog.py` | NEW | Discord Cog 載入哨兵背景任務 |

---

## 與舊 Mod 的關係與遷移策略

| 舊模組 | 處理方式 |
|--------|----------|
| `mod/sys_monitor/` | 核心採樣邏輯已全數收斂至 `core/sys_inspector/`，可安全除役 |
| `mod/self_monitoring/` | 背景哨兵、日誌脫敏、雲端診斷與警報已全數收斂至 `core/sys_inspector/`，可安全除役 |
| `/sys_status` 與 `/self-monitoring` 指令 | 統一收斂至 `sys_inspector_cog.py` 提供之 `/sys_inspect` 指令 |

---

## 完整驗證計畫

### 1. 單元與整合測試
```powershell
# Phase A：天氣正則測試
pytest tests/ -v -k "weather" --tb=short

# Phase B-1：採樣器與對話工具測試
pytest tests/unit/test_sys_inspector_sampler.py tests/unit/test_sys_inspect_tool.py -v

# Phase B-2：哨兵、日誌脫敏與雲端診斷測試
pytest tests/unit/test_sys_inspector_sentry.py tests/unit/test_cloud_diagnostician.py -v

# 全體回歸與覆蓋率
pytest --cov=core.sys_inspector --cov=handlers.social.tools.sys_inspect_tool --cov-report=term-missing --cov-fail-under=80

# 型別與代碼風格檢查
mypy core/sys_inspector/ handlers/social/tools/sys_inspect_tool.py
ruff check core/sys_inspector/ handlers/social/tools/sys_inspect_tool.py
```

### 2. 手動情境驗證

| 測試場景 | 操作方式 | 預期行為 |
|---|---|---|
| **天氣查詢遷移** | `@雪莉 台北明天會下雨嗎？` | 由 `ChatTool` 聯網檢索 CWA 氣象並由雪莉回答 |
| **對話系統狀態** | `@雪莉 你會不會卡？` | LLM 調用 `SYSTEM_INSPECT`，回覆「雪莉目前運作很順暢哦！」 |
| **多工具並行** | `@雪莉 分析0050，順便看你會不會lag` | LLM 同時調用 `STOCK` ＋ `SYSTEM_INSPECT` 並在 Pass 2 合併回覆 |
| **管理員權限測試** | 管理員詢問硬體狀態 | 回傳包含 CPU、RAM、GPU VRAM、PID 與 Event Loop 完整數據 |
| **後台手動觸發診斷** | 輸入 `/sys_inspect action:diagnose` | 立即擷取 5 分鐘 Log，呼叫雲端模型診斷並在頻道輸出 Embed 報告卡片 |
| **模擬佇列阻塞** | 人為製造佇列卡死 > 60s | Sentry 哨兵自動觸發，Discord 管理員頻道收到紅色警報診斷卡片 |
