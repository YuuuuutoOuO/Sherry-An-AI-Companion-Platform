# 33 — 全景架構地圖集與多重視角對照 (Architecture Atlas)

> **難度標記**：`Difficulty: Advanced`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
以 IEEE 42010 軟體架構標準的多重視角模型 (Architectural Views)，從「邏輯視角」、「進程/並發視角」、「實體部署視角」、「儲存視角」與「安全邊界視角」全方位對照雪莉系統，提供立體全景圖鑑，消除盲人摸象的局部認知偏差。

---

## 🧭 Prerequisites (先備知識)
- IEEE 42010 系統架構描述標準與 Philippe Kruchten 的 4+1 視圖模型。
- 非同步單體應用 (Asynchronous Modular Monolith) 之進程與執行緒拓撲。
- SQLite 嵌入式資料庫拓撲與 Linux/Windows 跨平台環境變數隔離。

---

## 💡 Concept (IEEE 42010 軟體架構視圖理論)
複雜軟體系統無法用單一圖表完整表達。依照 IEEE 42010 標準，架構師必須針對不同利害關係人 (Stakeholders) 建立專屬視圖：
1. **邏輯視角 (Logical View)**：服務於開發者，呈現模組職責、抽象合約與分層關係。
2. **進程/並發視角 (Process & Concurrency View)**：服務於系統工程師，呈現行程、執行緒、協程、IPC 與排程狀態。
3. **儲存視角 (Storage Topology View)**：服務於 DBA 與維運人員，呈現資料實體分佈、日誌模式與備份邊界。
4. **安全與邊界視角 (Security & Boundary View)**：服務於資安工程師，呈現信任邊界 (Trust Boundaries)、防禦過濾網與鑑權沙盒。

---

## 🛠️ Sherry Implementation (雪莉五大維度架構全景矩陣)

### 視角一：邏輯分層視角 (Logical Layer View)
```text
[Presentation Layer]    handlers/*.py (15 Cogs) | web_ui/server.py (Quart)
         │
         ▼ (依賴注入 / Capability Protocol)
[Micro-Kernel Layer]    app/orchestrator.py (SherryCore)
         │
         ▼
[Core Services Layer]   core/db.py | core/guard.py | core/dispatcher.py | core/config/
         │
         ▼ (抽象契約)
[Mod Plugins / Clients] mod/clients/* | mod/market_plugin | mod/dnd | mod/deep_search
```

---

### 視角二：進程與並發視角 (Process & Concurrency View)
```text
+-----------------------------------------------------------------------------------+
|                        OS Process: python.exe (PID in data/sherry.pid)            |
+-----------------------------------------------------------------------------------+
|  [Main Thread]                                                                    |
|  Asyncio Event Loop                                                               |
|  ├── Task: Discord Gateway WebSocket Connection                                   |
|  ├── Task: Quart Uvicorn ASGI Server (Port 8000)                                   |
|  ├── Task: BotBrain Inference Worker (_queue.get())                               |
|  ├── Task: SPARK Condition Evaluator Loop                                         |
|  └── Task: Discord Dynamic Presence Loop                                          |
|                                                                                   |
|  [Worker Threads Pool] (ThreadPoolExecutor)                                       |
|  ├── 磁碟大日誌遍歷讀取 (web_ui/routes/logs.py)                                   |
|  └── 同步 blocking 檔案操作                                                       |
+-----------------------------------------------------------------------------------+
       │
       ▼ (Subprocess Pipe)
[Child Process: piper.exe] (CPU 密集型神經網路語音合成)
```

---

### 視角三：儲存拓撲視角 (Storage Topology View)
```text
data/
├── 核心對話與觀測 ──► chat_history.db (chat_messages, runtime_events, audit_logs)
├── 認知好感度 ──────► sherry_memory.db (user_profiles, user_facts, user_relations)
├── 世界集體經驗 ────► world_memory.db (world_events, world_summary)
├── 條件推播 ────────► spark/spark.db (triggers)
├── 週期定時 ────────► schedules.db (schedules)
├── 深度研究文獻 ────► deep_search/deep_search.db (及 root 相容副本 deep_search.db)
├── 情報哨兵 ────────► feed_sentinel/feed_sentinel.db
├── 虛擬美股持股 ────► sherry_stock/stock_trader.db
├── 行情與卡牌預測 ──► market_stock_bot.db
├── 跑團冒險角色 ────► dnd/dnd.db
├── 系統硬體時間序列 ──► sys_inspector/sys_monitor.db
├── 安全命令審計 ────► sys_inspector/audit.db
└── 直播彈幕暫存 ────► 記憶體串列 (mod/livestream/service.py: list[ChatMessage])
```

---

### 視角四：安全與隔離視角 (Security Boundary View)
```text
[外部不可信世界: Discord 群友 / 網際網路 URL]
                       │
                       ▼ (SSRF 守衛: core/guard.is_safe_public_url)
                 [安全過濾檢查]
                       │
                       ▼ (資料隔離: XML 圍欄)
             <untrusted_external_content>
                       │
                       ▼ (權限防禦: is_ai_callable 旗標)
         [專題 Mod 不注入日常 Prompt，防止上下文污染]
                       │
                       ▼ (運維防護: Web UI 10分鐘過期 Token + IP 鎖)
                 [管理控制台]
```

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)
1. `[CONFIRMED]` **邏輯分層契約**：[`core/contracts.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/contracts.py#L20-L45)。
2. `[CONFIRMED]` **進程共用 Event Loop**：[`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L300) 與 [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2788)。
3. `[CONFIRMED]` **儲存拓撲實證**：[`tools/db_manager.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/tools/db_manager.py#L88-L470) 與 [`mod/livestream/service.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/mod/livestream/service.py#L58-L661)。
4. `[CONFIRMED]` **SSRF 與 URL 防禦**：[`core/guard.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/core/guard.py#L112-L135)。

---

## 📊 Flow Diagram (全景架構立體交織流圖)

```text
[外部用戶請求 / WebSocket]
          │
          ▼
   [主執行緒 Event Loop]
   ┌──────────────────────────────────────────────┐
   │ 1. 網路解包 (Discord Gateway / Quart HTTP)   │
   │ 2. 安全過濾 (core/guard.py)                  │
   │ 3. 意圖路由 (SherryCore / DispatchAgent)      │
   └───────┬───────────────────────────────┬──────┘
           │ (CPU 密集型卸載)              │ (I/O 卸載)
           ▼                               ▼
    [子進程 Piper TTS]             [ThreadPoolExecutor]
           │                               │
           │                               ▼
           │                      [12 個 SQLite WAL 資料庫]
           ▼
[SmartMessageDispatcher 格式化與發送]
```

---

## ⚠️ Failure Cases & Edge Cases (視圖孤立與失真之工程風險)
1. **文檔與源碼漂移 (Drift)**：例如未經代碼審查便將 `mod/livestream` 的記憶體彈幕標註為 `livestream.db`，造成維運人員無效搜尋與備份缺失。
2. **進程視角忽視阻塞**：開發者誤以為有 15 個 Cog 就是多進程並行，進而在協程中執行同步大檔案讀取，導致整個 Event Loop 停擺。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)
| 視角維度 | 集中式單一架構 | 雪莉實作之分片/隔離架構 | 工程代價與收益 |
|---|---|---|---|
| **儲存視角** | 單一大型 Monolith DB | 12 個業務領域獨立 SQLite | **收益**：避免全域寫入鎖阻塞；**代價**：跨領域關聯需在應用層進行。 |
| **並發視角** | 多進程微服務集 | 單進程多協程 + 外部子進程 | **收益**：零 IPC 負擔、極低記憶體開銷；**代價**：事件循環對阻塞呼叫極度敏感。 |

---

## 💼 Interview Relevance (面試考點)
- **Q: 在高階架構評審中，為何不能只畫一張「架構框圖」充數？**
  - **Ans**: 單一框圖通常混淆了「靜態程式碼依賴 (Logical View)」與「動態進程生命週期 (Process View)」以及「實體資料落盤位置 (Storage View)」。唯有透過 IEEE 42010 多重視角對照，才能同時滿足業務開發者、SRE 運維、資安審計與 DBA 各自的工程驗證需求。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Learning/34-understanding-checklist.md](./34-understanding-checklist.md) 進行學習成效自我檢測。
- 前往 [Evidence/36-evidence-conflicts.md](../Evidence/36-evidence-conflicts.md) 深入了解架構承諾與原始碼實證之六大關鍵衝突。
