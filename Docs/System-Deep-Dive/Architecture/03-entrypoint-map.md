# 03 — 系統進入點地圖與啟動腳本矩陣 (Entrypoint Map)

> **難度標記**：`Difficulty: Intermediate`  
> **證據等級**：`[CONFIRMED]`

---

## 🎯 Purpose (核心目的)
全面梳理雪莉系統的所有啟動入口，包含主程序進入點 (`main.py`)、多環境啟動腳本 (`.bat` / `.sh`)、Web UI 單獨引導入口與 Docker 容器化入口，分析各進入點的引導順序、PID 管理與環境變數注入邏輯。

---

## 🧭 Prerequisites (先備知識)
- Windows Batch 腳本 (`.bat`) 與 Linux Shell 腳本 (`.sh`) 基本語法。
- Python 命令列引數與 `sys.argv` / `argparse`。
- Dockerfile 與 docker-compose 容器引導原理。

---

## 💡 Concept (系統啟動入口工程理論)
健全的軟體系統需要考量多種運行拓撲 (Run Topologies)：
1. **開發者本地模式 (Local Development)**：直連本地終端，即時印出除錯訊息與熱重載。
2. **多分頁終端監控 (Multi-tab Terminal Launcher)**：分離主核心、日誌串流與瀏覽器，以提升維運可見度。
3. **無人值守生產環境 (Headless / Containerized)**：Docker / Systemd / Supervisor 自動重啟與環境隔離。

---

## 🛠️ Sherry Implementation (雪莉進入點矩陣)

### 一、程式碼與腳本進入點清單
| 進入點檔案 | 類型 | 核心職責 | 啟動指令 |
|---|---|---|---|
| `main.py` | [CONFIRMED] Python 主程式碼 | 核心 Bootstrapper，初始化 Logging、建立 Bot、啟動 Event Loop、掛載 Web UI | `python main.py` |
| `run_core.bat` | [CONFIRMED] Windows Batch | 本地生產主核心啟動腳本，檢查虛擬環境、自動安裝依賴、設定 UTF-8 | `run_core.bat` |
| `start_sherry_tabs.bat` | [CONFIRMED] Windows Batch | Windows Terminal 多分頁同時啟動（主核心、動態日誌串流、Web 瀏覽器） | `start_sherry_tabs.bat` |
| `open_web_ui.bat` | [CONFIRMED] Windows Batch | 單獨開啟瀏覽器並跳轉至本地 Web UI 管理後台 | `open_web_ui.bat` |
| `restart_sherry.bat` / `.sh` | [CONFIRMED] 跨平台腳本 | 讀取 `data/sherry.pid` 終止舊進程並重啟新進程 | `restart_sherry.bat` |
| `update_and_restart.bat` / `.sh` | [CONFIRMED] 跨平台腳本 | Git pull 最新代碼、自動執行 DB Migration、安全重啟系統 | `update_and_restart.bat` |
| `Dockerfile` | [CONFIRMED] 容器化定義 | 基於 Python 3.11 映像檔建置無人值守容器 | `docker build -t sherry .` |
| `docker-compose.yml` | [CONFIRMED] 容器編排 | 掛載本地 `data/` 目錄，暴露 Web UI 埠號 (8000) | `docker compose up -d` |

---

### 二、main.py 啟動時序分析
在 `main.py` 執行時，其同步至非同步引導時序如下：

1. **目錄與 IO 編碼前置準備 (Synchronous Phase)**：
   - 強制建立 `data/`, `file/`, `soul/`, `chat_log/`, `video/` 目錄。
   - 配置 `sys.stdout` 與 `sys.stderr` 為 `utf-8` 編碼，消除 Windows cp950 亂碼。
   - `[CONFIRMED]` 設定 Windows 控制台標題 `os.system("title 🤖 雪莉主核心 (Sherry Core)")`。
   - `[CONFIRMED]` 將當前行程 PID 寫入 `data/sherry.pid`，供外部腳本進行精確終止。
2. **結構化日誌裝配**：
   - 配置 `RotatingFileHandler` (10MB, 5個備份) 與 `StreamHandler`。
   - 掛載 `TraceIdFilter`，將 ContextVar 的 `trace_id` 動態注入每筆日誌。
3. **進入非同步循環 (`asyncio.run(main())`)**：
   - 捕捉 Windows 平台 `WinError 10054` 網路中斷噪音，設置自訂 loop exception handler。
   - 建構 `commands.Bot` 實例並注入 `bot.core = SherryCore(bot)`。
   - 順序載入 15 個核心 Cog，動態載入 `./mod/` 外掛。
   - 透過 `bot.start(settings.DISCORD_BOT_TOKEN)` 連線至 Discord Gateway。
   - 在 `on_ready` 事件中，以 `bot.loop.create_task(start_server_async(bot))` 啟動 Web UI。

---

## 🔍 Source Evidence & Ground Truth (原始碼佐證)

1. `[CONFIRMED]` **PID 落盤機制**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L43-L46)：
     ```python
     try:
         (settings.DATA_DIR / "sherry.pid").write_text(str(os.getpid()), encoding="utf-8")
     except Exception:
         pass
     ```
2. `[CONFIRMED]` **UTF-8 強制重定向**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L35-L38)。
3. `[CONFIRMED]` **WinError 10054 異常過濾器**：
   - 見 [`main.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/main.py#L190-L203)。
4. `[CONFIRMED]` **重啟腳本之 PID 終止邏輯**：
   - 見 [`restart_sherry.bat`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/restart_sherry.bat#L1-L20)，腳本讀取 `data/sherry.pid` 並執行 `taskkill /F /PID %PID%`。

---

## 📊 Flow Diagram (引導進入點時序圖)

```text
[User / CLI]
     |
     +---> run_core.bat / start_sherry_tabs.bat
                 |
                 v
           [python main.py]
                 |
                 +---> (1) 建立資料夾、設定 UTF-8、記錄 data/sherry.pid
                 |
                 +---> (2) 配置 TraceIdFilter 與 RotatingFileHandler
                 |
                 +---> (3) asyncio.run(main())
                             |
                             +---> build_bot() & new SherryCore()
                             |
                             +---> 依序 await bot.load_extension(...) [15 核心 Cogs]
                             |
                             +---> 動態掃描並載入 mod/*.cog [可選外掛]
                             |
                             +---> await bot.start(DISCORD_BOT_TOKEN)
                                         |
                                         v
                                    [on_ready]
                                         |
                                         +---> bot.tree.sync() [斜線指令同步]
                                         |
                                         +---> bot.loop.create_task(presence_loop())
                                         |
                                         +---> bot.loop.create_task(start_server_async()) [Web UI]
```

---

## ⚠️ Failure Cases & Edge Cases (異常邊界案例)

1. **孤兒進程與 PID 檔案殘留**：
   - 若主機發生硬體斷電或進程被強制終止 (`kill -9`)，`data/sherry.pid` 中會殘留無效 PID。重啟腳本若未檢查 PID 對應的進程名稱是否為 `python.exe`，可能誤殺重新分配該 PID 的無關程序。
2. **多重啟動衝突 (Port 8000 佔用)**：
   - 若使用者同時執行兩次 `main.py`，前一個進程已綁定 Uvicorn 8000 埠號，第二個進程會在 `start_server_async()` 拋出 `OSError: [WinError 10048] 通常每個通訊協定地址只允許使用一次`。
   - `[CONFIRMED]` [`web_ui/server.py`](file:///d:/程式/Python/-雪莉-/Sherry-An-AI-Companion-Platform/web_ui/server.py#L2789-L2796) 具備降級捕捉邏輯，但若 fallback 仍失敗將記錄錯誤日誌。

---

## ⚖️ Trade-offs & Engineering Decisions (架構權衡)

| 設計選擇 | 優點 | 缺點 / 潛在代價 | 替代方案 |
|---|---|---|---|
| **單一進程同時啟動 Discord 與 Quart** | 部署極簡，不需跨進程 IPC，記憶體開銷小，兩者共用記憶體狀態與 Bot 實例。 | 任一方發生阻塞性計算 (CPU-bound) 或同步 I/O，兩者同時凍結；難以單獨水平擴展 Web 流量。 | 將 Web 管理後台與 Discord Bot 拆為獨立微服務，透過 Redis/NATS 進行跨程序通訊。 |

---

## 💼 Interview Relevance (面試考點)

- **Q: 在 Windows 環境下撰寫 Python 常駐守護進程，常見的底層地雷有哪些？雪莉如何解決？**
  - **Ans**: 
    1. **終端編碼問題**：Windows 預設 CP950/CP437 無法輸出 Emoji 與日韓文字，導致 `UnicodeEncodeError`。雪莉在入口處調用 `sys.stdout.reconfigure(encoding="utf-8")`。
    2. **網路重置例外**：Windows 的 `ProactorEventLoop` 在 TCP 連線被對端斷開時會向 event loop 拋出未捕捉的 `WinError 10054`。雪莉設置自訂 `_asyncio_loop_exception_handler` 優雅過濾。
    3. **進程追蹤困難**：Windows 缺乏 Unix daemonize 特性，雪莉在啟動第一時間將 PID 寫入 `data/sherry.pid` 供運維管理。

---

## 🔗 What to Study Next (後續章節銜接)
- 前往 [Architecture/04-application-lifecycle.md](./04-application-lifecycle.md) 研讀系統運行時的完整生命週期狀態機與狀態轉移。
