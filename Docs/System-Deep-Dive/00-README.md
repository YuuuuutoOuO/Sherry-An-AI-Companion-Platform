# 🎓 Sherry System Deep-Dive Documentation
## 系統工程架構技術教材與全景剖析導讀

> **版本**：v2.6.0-deep-dive  
> **維護規範**：遵循《科技公司軟體架構與開發規範 v2.2》與嚴格證據原則  
> **證據等級標記**：`[CONFIRMED]` (源碼可驗證實證)、`[LIKELY]` (靜態結構高度相符)、`[UNKNOWN]` (無可查依據)、`[REQUIRES RUNTIME VERIFICATION]` (需實際運行環境動態觀測)

---

## 🧭 教材願景與定位

《Sherry System Deep-Dive Documentation》是一套針對混合型 AI 伴侶平台——**雪莉 (Sherry)** 所撰寫的高階系統工程技術教材。

本教材不以一般「使用者手冊」或「API 文件」為滿足，而是以**資深後端架構師、分散式系統工程師與 LLM 系統架構師**的視角，對系統進行解構剖析：
- 剖析非同步事件驅動架構（Discord Gateway + Quart Web UI）共用單一 Event Loop 的工程真相。
- 剖析地端 GPU 顯存排它鎖與雲端推論動態分流機制。
- 剖析全系統 SQLite 資料庫拓撲、WAL 併發寫入控制與連線治理真相。
- 剖析從 Prompt Prefix 恆定組裝到後端推論的認知記憶流。
- 深入檢驗真實技術債與防禦機制，記錄架構權衡與實戰教訓。

---

## 🗺️ 推薦閱讀路徑 (Learning Paths)

本教材共收錄 36 個核心技術專題，根據讀者工程背景與學習目標，提供三條推薦學習路徑：

### 徑一：全端與核心系統架構師路徑 (Systems Engineer Path)
適合關注非同步並發、儲存系統、網路協定與生命週期管理的工程師：
1. [Architecture/01-repository-map.md](./Architecture/01-repository-map.md) — 倉庫全景與目錄地圖
2. [Architecture/03-entrypoint-map.md](./Architecture/03-entrypoint-map.md) — 啟動入口與多模式分流
3. [Architecture/04-application-lifecycle.md](./Architecture/04-application-lifecycle.md) — 系統啟動、就緒與事件循環
4. [Systems/08-async-concurrency.md](./Systems/08-async-concurrency.md) — Asyncio 協程與單一事件循環核心
5. [Systems/07-networking.md](./Systems/07-networking.md) — Discord Gateway WebSocket 與 HTTP Client
6. [Systems/10-web-ui-architecture.md](./Systems/10-web-ui-architecture.md) — Quart Web UI 與 Uvicorn 雙軌整合
7. [Systems/11-database-storage.md](./Systems/11-database-storage.md) — SQLite 多庫儲存拓撲、WAL 模式與連線治理深入剖析
8. [Systems/18-resource-management.md](./Systems/18-resource-management.md) — GPU VRAM 排它鎖與資源排程
9. [Systems/23-shutdown-and-recovery.md](./Systems/23-shutdown-and-recovery.md) — 關機中斷與崩潰復原機制
10. [Performance/27-bottleneck-analysis.md](./Performance/27-bottleneck-analysis.md) — 系統效能瓶頸與併發熱點

---

### 徑二：AI 與 Agent 系統工程師路徑 (AI/LLM Engineer Path)
適合專注於 Prompt Engineering、記憶檢索、Agent 調度與 LLM 推論吞吐的工程師：
1. [Architecture/05-request-lifecycle.md](./Architecture/05-request-lifecycle.md) — 互動請求端到端生命週期
2. [AI/12-memory-architecture.md](./AI/12-memory-architecture.md) — 三層式認知記憶與事實庫
3. [AI/13-context-engine.md](./AI/13-context-engine.md) — 動態 Context Engine 與 Prefix 快取優化
4. [AI/14-llm-inference.md](./AI/14-llm-inference.md) — 多後端推論引擎與降級策略
5. [AI/15-two-pass-inference.md](./AI/15-two-pass-inference.md) — 雙階段推論 (Planner + Primary) 與單階段內嵌
6. [AI/16-agent-architecture.md](./AI/16-agent-architecture.md) — 自主調度策略矩陣 (Integrated / Parallel / ReAct)
7. [AI/17-tool-architecture.md](./AI/17-tool-architecture.md) — 工具抽象層、沙盒與動態註冊
8. [Case-Studies/29-end-to-end-case-study.md](./Case-Studies/29-end-to-end-case-study.md) — 典型請求端到端時序實戰案例

---

### 徑三：安全、可靠性與工程治理路徑 (Reliability & Security Path)
適合負責系統維運、資安防禦與架構評審的工程師：
1. [Architecture/26-architecture-boundaries.md](./Architecture/26-architecture-boundaries.md) — 架構邊界與 Import 分層規範
2. [Observability/20-observability.md](./Observability/20-observability.md) — 分散式 Trace ID 與可觀測性體系
3. [Security/21-security-architecture.md](./Security/21-security-architecture.md) — SSRF 防禦、XML 隔離與驗證安全
4. [Reliability/22-error-handling.md](./Reliability/22-error-handling.md) — 容錯熔斷與異常退避機制
5. [Evidence/36-evidence-conflicts.md](./Evidence/36-evidence-conflicts.md) — 規範承諾 vs. 實作真實現狀對比
6. [37-final-system-assessment.md](./37-final-system-assessment.md) — 系統總體成熟度與技術架構評估

---

## 📚 完整目錄索引 (Documentation Catalog)

| 章節編號 | 文件路徑 | 核心主題 | 難度級別 | 關聯架構維度 |
|---|---|---|---|---|
| 00 | `00-README.md` | 本導讀索引與學習路線圖 | Foundational | 全景概覽 |
| 01 | [`Architecture/01-repository-map.md`](./Architecture/01-repository-map.md) | 倉庫全景結構與目錄映射地圖 | Foundational | 程式碼架構 |
| 02 | [`Architecture/02-module-inventory.md`](./Architecture/02-module-inventory.md) | 模組庫存清單與職責邊界矩陣 | Intermediate | 模組化職責 |
| 03 | [`Architecture/03-entrypoint-map.md`](./Architecture/03-entrypoint-map.md) | 系統進入點地圖與啟動腳本矩陣 | Intermediate | 系統引導 |
| 04 | [`Architecture/04-application-lifecycle.md`](./Architecture/04-application-lifecycle.md) | 應用程式全生命週期狀態機 | Advanced | 生命週期 |
| 05 | [`Architecture/05-request-lifecycle.md`](./Architecture/05-request-lifecycle.md) | 請求與事件處理生命週期流 | Advanced | 事件調度 |
| 06 | [`Architecture/06-data-flow.md`](./Architecture/06-data-flow.md) | 系統全域資料流與狀態轉移拓撲 | Advanced | 資料管線 |
| 07 | [`Systems/07-networking.md`](./Systems/07-networking.md) | 網路協定棧 (Discord Gateway / HTTP / aiohttp) | Advanced | 網路工程 |
| 08 | [`Systems/08-async-concurrency.md`](./Systems/08-async-concurrency.md) | 非同步並發模型與共用 Event Loop | Expert | 並發控制 |
| 09 | [`Systems/09-thread-process-executor.md`](./Systems/09-thread-process-executor.md) | 執行緒與進程池執行器 (Executor) | Advanced | 多執行緒/處理程序 |
| 10 | [`Systems/10-web-ui-architecture.md`](./Systems/10-web-ui-architecture.md) | Quart Web 控制台與 Uvicorn 雙軌架構 | Advanced | 系統整合 |
| 11 | [`Systems/11-database-storage.md`](./Systems/11-database-storage.md) | SQLite 多庫拓撲、WAL 模式與連線治理 | Expert | 儲存架構 |
| 12 | [`AI/12-memory-architecture.md`](./AI/12-memory-architecture.md) | 三層式認知記憶系統 (短期/長期/世界記憶) | Advanced | AI 記憶 |
| 13 | [`AI/13-context-engine.md`](./AI/13-context-engine.md) | 動態 Context Engine 與 Prefix KV 快取優化 | Advanced | Prompt Engineering |
| 14 | [`AI/14-llm-inference.md`](./AI/14-llm-inference.md) | LLM 推論引擎客戶端抽象與模型備援策略 | Advanced | 深度學習推論 |
| 15 | [`AI/15-two-pass-inference.md`](./AI/15-two-pass-inference.md) | 雙階段推論架構 (Planner-Execution-Synthesis) | Expert | 複合 AI 系統 |
| 16 | [`AI/16-agent-architecture.md`](./AI/16-agent-architecture.md) | 智慧代理調度策略矩陣 (Integrated / Parallel / ReAct) | Expert | Agent 設計 |
| 17 | [`AI/17-tool-architecture.md`](./AI/17-tool-architecture.md) | 工具呼叫架構、合約定義與生命週期 | Advanced | Tool Use |
| 18 | [`Systems/18-resource-management.md`](./Systems/18-resource-management.md) | GPU VRAM 排它鎖與雲端繞過降級架構 | Expert | 資源調度 |
| 19 | [`Systems/19-background-task-system.md`](./Systems/19-background-task-system.md) | 背景任務系統、SPARK 條件推播與週期巡檢 | Advanced | 排程架構 |
| 20 | [`Observability/20-observability.md`](./Observability/20-observability.md) | 結構化日誌、分散式 Trace ID 與觀測指標 | Intermediate | 可觀測性 |
| 21 | [`Security/21-security-architecture.md`](./Security/21-security-architecture.md) | SSRF 防禦、Prompt 注入阻隔與憑證安全 | Advanced | 系統資安 |
| 22 | [`Reliability/22-error-handling.md`](./Reliability/22-error-handling.md) | 異常處理矩陣、熔斷降級與優雅降級 | Advanced | 系統彈性 |
| 23 | [`Systems/23-shutdown-and-recovery.md`](./Systems/23-shutdown-and-recovery.md) | 關機信號中斷、WAL 落盤與崩潰復原機制 | Advanced | 容災工程 |
| 25 | [`Architecture/25-dependency-graph.md`](./Architecture/25-dependency-graph.md) | 全系統模組相依有向圖與分層驗證 | Advanced | 相依架構 |
| 26 | [`Architecture/26-architecture-boundaries.md`](./Architecture/26-architecture-boundaries.md) | 架構邊界規範與 Import-Linter 規則 | Advanced | 邊界治理 |
| 27 | [`Performance/27-bottleneck-analysis.md`](./Performance/27-bottleneck-analysis.md) | 系統效能瓶頸剖析與 CPU/GPU/IO 爭用點 | Expert | 效能分析 |
| 28 | [`Performance/28-performance-model.md`](./Performance/28-performance-model.md) | 效能數學模型與延遲分解分析 | Advanced | 容量規劃 |
| 29 | [`Case-Studies/29-end-to-end-case-study.md`](./Case-Studies/29-end-to-end-case-study.md) | 端到端典型請求穿越案例剖析 | Advanced | 案例研究 |
| 30 | [`Learning/30-sherry-system-mental-model.md`](./Learning/30-sherry-system-mental-model.md) | 雪莉系統工程核心心智模型 (Mental Model) | Intermediate | 觀念建模 |
| 31 | [`Learning/31-system-interview-guide.md`](./Learning/31-system-interview-guide.md) | 高階系統設計面試指南 (System Design QA) | Advanced | 面試準備 |
| 32 | [`Learning/32-glossary.md`](./Learning/32-glossary.md) | 專用名詞定義庫 (Domain Glossary) | Foundational | 術語手冊 |
| 33 | [`Learning/33-architecture-atlas.md`](./Learning/33-architecture-atlas.md) | 架構地圖集與多維度視角對照 | Advanced | 視覺架構 |
| 34 | [`Learning/34-understanding-checklist.md`](./Learning/34-understanding-checklist.md) | 架構理解自檢清單 (Self-Assessment Checklist) | Intermediate | 學習自評 |
| 36 | [`Evidence/36-evidence-conflicts.md`](./Evidence/36-evidence-conflicts.md) | 規範承諾 vs. 原始碼實證衝突分析手冊 | Expert | 架構稽核 |
| 37 | `37-final-system-assessment.md` | 系統成熟度綜合評估與演進路線圖 | Advanced | 架構綜述 |

---

## ⚖️ 嚴格證據原則與標記說明

在閱讀本手冊時，所有關鍵架構主張均帶有以下四種等級之證據標記：

1. **`[CONFIRMED]` (實證已確認)**：
   - 原始碼中有明確對應的具體實作，具有精確檔案路徑與行號佐證。
   - 例：`[CONFIRMED] main.py (L298-301) 透過 bot.loop.create_task(start_server_async(bot)) 啟動 Quart。`
2. **`[LIKELY]` (高度推斷與靜態吻合)**：
   - 根據程式碼靜態語法、命名與控制流可高度推導，但未經單元測試或特定邊界測試窮舉驗證。
   - 例：`[LIKELY] Prompt head_persona 保持靜態有助於 vLLM / Ollama 的 Radix Tree KV 快取。`
3. **`[UNKNOWN]` (無可查證依據)**：
   - 規格書中有所提及，但在當前專案原始碼中尚未實作或缺少任何跡證。
4. **`[REQUIRES RUNTIME VERIFICATION]` (需運行時動態驗證)**：
   - 取決於硬體環境、外部 API 反應或作業系統底層行為，靜態分析無法給出絕對保證。
   - 例：`[REQUIRES RUNTIME VERIFICATION] 雲端供應商 (如 Gemini API) 是否真正重用 Prompt Cache。`
