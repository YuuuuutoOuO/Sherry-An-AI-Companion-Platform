# 雪莉 Sherry AI 伴侶平台 — 深度靜態分析與企業級上線審查報告
## Production Readiness Review (PRR) & Deep Static Audit

| 項目 | 內容 |
|---|---|
| **報告日期** | 2026-10-19 |
| **審查對象** | `Sherry-An-AI-Companion-Platform`（Discord AI 伴侶平台） |
| **審查基準** | git 分支 `dev`、commit `eefe7fc`（2026-10-17 23:55 +0800） |
| **審查方法** | 全專案唯讀靜態分析（原始碼逐行閱讀 + 全庫 grep 交叉比對 + 資料庫 schema/列數唯讀查驗）。**未修改任何業務程式碼** |
| **專案規模** | 328 個 Python 檔（247 個原始碼檔、81 個測試檔）、原始碼 53,941 行 / 含測試 71,707 行；89 個 .db 檔（含 12 組手動備份、9 個業務資料庫） |
| **審查維度** | ①效能 ②擴展性與解耦 ③新功能深化 ④資安與軟刪除 ⑤企業級可靠性、AI 治理與可觀測性 |

> **免責與方法宣告**：本報告所有問題點均附「檔案相對路徑、Class/函式名稱、行號」佐證；行號以本次掃描當下之檔案內容為準。審查過程嚴格唯讀，未新增、修改、刪除或移動任何檔案，亦未執行任何具副作用之指令（含資料庫寫入）。

---

# 0. 執行摘要 (Executive Summary)

本平台在「單機消費級硬體上運行完整 AI 伴侶」的工程目標上完成度相當高：微核心 `SherryCore`、雙層能力註冊、WAL SQLite、SSRF 防護、Prompt Cache 友善的 prompt 結構、深夜記憶整合、SPARK 條件排程等，均屬水準以上的設計。然而，以「企業級上線（Production Readiness）」標準檢視，本次審查發現 **5 個 P0 級、約 30+ 個 P1 級**問題，集中在四大面向：

1. **資安鏈式風險（可遠端利用）**：一個公開的反射式 XSS（`/api/mod/<mod_key>/view`）＋管理員 token 同時存放於 `localStorage`（抵銷 HttpOnly 設計）＋`/api/settings` 可經 `CURRENT_SOUL_NAME`/`SOUL_CONTENT` 對任意既存檔案做寫入 → 構成「**XSS → 竊取管理員 Token → 任意檔案覆寫 → RCE/持久化後門**」的完整攻擊鏈。
2. **軟刪除/GDPR 契約破裂**：`/forget` 宣稱「已清除所有長期記憶」但實際上好感度（`user_relations`）、對話全文（`chat_messages`）、JSON 舊快照與歸檔全部保留；`save_user_memory` 以全表 `DELETE FROM user_facts` 硬刪，徹底滅失軟刪軌跡；JSON fallback 會「復活」已刪記憶。
3. **受控自主性名存實亡**：`core/autonomy_guard.py`、`core/risk_engine.py`、`core/system_state.py` 在生產程式碼中 **零呼叫**（僅測試引用），但 ADR-0002 文件宣稱「強制實施全域熔斷與預算上限」；同時 SPARK 對任何成員開放、最短 1 分鐘週期、無配額／無每日上限，理論上單一使用者即可造成 1,440 次/日 LLM 推播。
4. **效能與韌性缺陷互相放大**：LLM 串流的 chunk timeout 被 `max()` 抵銷而失效、整體 timeout 會硬切長生成、取消僅是旗標輪詢（不傳播 `task.cancel()`）、雲端任務無界並發、本地任務單一 worker 序列造成頭部阻塞；背景排程器（SPARK）單輪無 timeout、動作 fire-and-forget 無界累積，任一 checker 卡死即永久停擺。

## 0.1 最高風險 Top-10（依修復優先序）

| # | 嚴重度 | 標題 | 關鍵位置 |
|---|---|---|---|
| 1 | **[P0 - Critical]** | 公開反射式 XSS × Token 存 localStorage × 設定任意檔覆寫 = RCE 鏈 | `web_ui/server.py:2253-2267`、`web_ui/static/app.js:38,2582`、`settings.py:813-842` |
| 2 | **[P0 - Critical]** | `/forget` 刪除不完整（關係/對話/備份/歸檔全留）＋非同步未等待，GDPR 聲明不實 | `handlers/memory/cog.py:201-208`、`handlers/memory/storage.py:747-778` |
| 3 | **[P0 - Critical]** | `chat_messages` 無任何刪除/保留途徑，對話 PII 無界滯留 | `handlers/memory/chat_logger.py:43-56`、`handlers/memory/retention_worker.py:46-53` |
| 4 | **[P0 - Critical]** | `autonomy_guard`/`risk_engine` 未接線；SPARK 無配額、最短 1 分鐘 LLM 推播 | `core/autonomy_guard.py:120`、`handlers/spark/cog.py:1461-1491`、`handlers/spark/nlp.py:1088` |
| 5 | **[P0 - Critical]** | 推播動作 fire-and-forget 無界累積（無 semaphore/無追蹤/無 timeout） | `handlers/spark/cog.py:1480-1482,1293,1411` |
| 6 | [P1 - High] | 串流逾時模型失效＋取消不傳播＋雲端無界並發（成本/算力失控） | `handlers/bot_brain/client.py:499-509`、`handlers/bot_brain/cog.py:199-206,231-240` |
| 7 | [P1 - High] | 未認證端點過多：任務 prompt、成員個資、稽核紀錄公開；模組 `public_routes` 全方法放行（含 LLM 遊戲與直播控制） | `web_ui/server.py:306-378,1863-1885,2004-2086`；`mod/game_center/__init__.py:29`；`mod/livestream/__init__.py:27` |
| 8 | [P1 - High] | Livestream 獨立伺服器 `0.0.0.0:8764` 零認證；WebUI 綁定失敗靜默回退 `0.0.0.0` | `mod/livestream/config.py:32-38`、`mod/livestream/web.py:528-534`、`web_ui/server.py:2789-2797` |
| 9 | [P1 - High] | 軟刪除一致性斷裂：全表硬刪、JSON 復活、world 表無 `is_deleted` 過濾、`schedule_storage` 無欄位 | `handlers/memory/storage.py:720,814`、`handlers/memory/world_memory.py:265-412,479-502`、`handlers/schedule_storage.py:154-169` |
| 10 | [P1 - High] | SPARK `check_trigger` 以「檢查前快照」回寫 → 復活已軟刪/已暫停任務；「立即測試」改寫狀態、關閉 one-shot 任務 | `handlers/spark/service.py:100-163`、`handlers/spark/repository.py:125-133`、`handlers/spark/cog.py:887,1778` |

## 0.2 各維度評分（1–10，對標本次發現）

| 維度 | 評分 | 說明 |
|---|---|---|
| 效能與效率 | **5.5 / 10** | 快取與壓縮化做得不錯，但逾時/取消/並發模型有系統性缺陷，背景任務可無限累積 |
| 擴展性與解耦 | **6.0 / 10** | `core` 分層骨架明確，但雙註冊表、雙限流器、`web_ui` 治理缺口、handlers⇄mod 循環使「換模型/換儲存/換前端」都需要大範圍改動 |
| 新功能潛力 | **8.0 / 10** | 事件匯流排式設計（Cog + Listener + SPARK）非常適合掛載進階功能，路線詳見維度三 |
| 資安與軟刪除 | **4.0 / 10** | 單點防護（SSRF、恆定時間比對、鎖定）表現佳，但邊界（未認證面、檔案寫入、XSS、軟刪一致性）缺口嚴重 |
| 企業可靠性/治理/可觀測性 | **5.0 / 10** | Trace ID/觀測基礎存在，但關鍵治理機制未接線、排程器無熔斷、背景系統零結構化觀測、PII 無遮罩 |

---

# 1. 專案全景 (System Overview)

## 1.1 架構速覽

```text
main.py ──► discord.py Bot（15 個核心 Cog + ./mod/ 動態外掛）
             │
             ├─ app/orchestrator.py :: SherryCore（微核心：rate limit、能力註冊、分段發送）
             │
             ├─ handlers/bot_brain/（LLM 推論佇列：PriorityQueue + 單一 worker + fallback 鏈）
             │     └─ handlers/bot_brain/client.py（Ollama/LM Studio/vLLM/OpenAI/Gemini/Claude… SSE 串流）
             │
             ├─ handlers/social/（調度大腦：router → dispatch_agent → Integrated/Parallel/ReAct 策略 → 工具）
             │
             ├─ handlers/memory/（長期記憶 JSON+SQLite、聊天日誌、關係、世界記憶、深夜整合、保留清理）
             │
             ├─ handlers/spark/（條件排程：checkers + action_runner + NLP 解析 + Web 面板）
             │
             └─ web_ui/server.py（Quart + Uvicorn，HTTPS 8080，動態掛載各 mod 的 web.py Blueprint）
                   └─ mod/livestream/web.py（獨立 0.0.0.0:8764 伺服器，SSE + 影片串流）
```

**資料儲存拓撲**：9 個業務 SQLite（`data/chat_history.db` 16.7MB、`data/sherry_memory.db`、`data/world_memory.db`、`data/schedules.db`、`data/spark/spark.db`、`data/dnd/dnd.db`、`data/feed_sentinel/feed_sentinel.db`、`data/sherry_stock/stock_trader.db`、`data/deep_search/deep_search.db`）＋大量 JSON/JSONL（`runtime_log.jsonl` 5MB、`error_log.jsonl`、`admin_audit.jsonl` 547KB、`bot.log` 22.5MB、`archive_legacy_json/` 含整月原始對話）＋12 組 `data/backups/manual_db_backup_*`（每組含 12MB 的 chat_history.db）。**目前尚無任何向量庫/embedding（RAG 檢索為純字串啟發式，見維度四）**。

## 1.2 五大維度問題總量

| 維度 | P0 | P1 | P2 | P3 |
|---|---|---|---|---|
| ① 效能 | 0 | 10 | 30+ | 4 |
| ② 擴展性解耦 | 0 | 5 | 8 | 2 |
| ④ 資安/軟刪除 | 4 | 6 | 12 | 2 |
| ⑤ 可靠性/治理/觀測 | 1 | 11 | 8 | 3 |

---

# 2. 維度一：執行效率與效能優化 (Performance & Efficiency)

## 2.1 LLM 推論鏈路（最關鍵路徑）

### [P1 - High] 2.1.1 串流 chunk timeout 被 `max()` 抵銷，形同虛設；整體 timeout 會硬切長生成並觸發重試疊加
**位置**：`handlers/bot_brain/client.py` → `OllamaClient._stream_openai_compatible` L499–509；`OllamaClient.chat_stream` L929–935；`handlers/bot_brain/cog.py` → `BotBrain._process_task` L219、L409–438；設定 `settings.py:234-238`。

```python
# client.py L499-509
total_timeout = timeout if timeout is not None else getattr(settings, "LLM_TIMEOUT", 180)
chunk_timeout = getattr(settings, "LLM_STREAM_CHUNK_TIMEOUT", 120.0)
sock_timeout = max(chunk_timeout, total_timeout)          # ← 120 永遠被 180 蓋掉
req_timeout = aiohttp.ClientTimeout(
    total=total_timeout,        # ← aiohttp total 涵蓋「整個回應讀取」，含 SSE 串流
    connect=30.0,
    sock_read=sock_timeout,
)
```
**影響**：`LLM_STREAM_CHUNK_TIMEOUT`（單 chunk 寬限）在預設值下永遠無效；單次 >180 秒的長回覆在串流中被硬切 → 被 `cog.py` 的重試迴圈接住（timeout 重試上限 1 次、非負載錯誤固定等 2 秒），最壞情況 3 次 × 180 秒 ≈ 9 分鐘且期間 Worker 被獨佔。
**建議修法**：
```python
# 概念示意：把「整體上限」與「單 chunk 寬限」解耦，整體 deadline 由呼叫端掌控
req_timeout = aiohttp.ClientTimeout(total=None, connect=30.0, sock_read=chunk_timeout)
async with asyncio.timeout(total_timeout):          # Python 3.11+
    async with session.post(url, json=payload, headers=headers, timeout=req_timeout) as resp:
        ...
```
並將重試改為「指數退避 + 抖動 + 全域熔斷計數」（見 2.1.4）。

### [P1 - High] 2.1.2 「取消」只是旗標輪詢，無真正的取消傳播（算力持續消耗）
**位置**：`handlers/bot_brain/cog.py` L231–240、L294–327（唯一中斷手段如下）；`handlers/social/dispatch_agent.py::DispatchAgentTool._call_llm` L190–200；`handlers/social/tools/stock_tool.py` L855–863。

```python
# cog.py L307-312：串流中偵測取消 → 只是「下一個 chunk 到來時」才 break
if getattr(task, "is_cancelled", False):
    logger.info("BotBrain: 串流推論中偵測到 Task '%s' 被取消，中斷串流！", task.task_id)
    break
```
```python
# dispatch_agent.py L192-200：逾時只設旗標，未 cancel 底層 aiohttp 請求
except asyncio.TimeoutError:
    setattr(task, "is_cancelled", True)
```
**影響**：「停止生成」/上游逾時後，若模型停止吐 chunk，任務將一路跑到 `LLM_TIMEOUT`；本地 GPU 或雲端額度持續被佔用，且本地 worker 為序列執行（L206），後續任務全部排隊等待。
**建議修法**：`InferenceTask` 保存 `asyncio.Task` 強引用；取消入口改為 `task_ref.cancel()`；`_stream_openai_compatible` 對 `asyncio.CancelledError` 做 `finally` 資源回收（aiohttp 請求自動取消）；上游用 `asyncio.timeout()` 使取消自然傳播。

### [P1 - High] 2.1.3 雲端任務無界並發；本地任務單一 worker 序列形成頭部阻塞
**位置**：`handlers/bot_brain/cog.py` → `BotBrain._inference_worker` L199–206。

```python
# cog.py L199-206
if is_cloud:
    # 雲端模型不佔用本地 GPU/Lock，背景並行放行（保持強引用防 GC）
    t = asyncio.create_task(self._process_task(task, MAX_RETRIES, RETRY_DELAY))
    self._background_tasks.add(t)
    t.add_done_callback(self._background_tasks.discard)
else:
    await self._process_task(task, MAX_RETRIES, RETRY_DELAY)   # 序列
```
**影響**：同一使用者連續 @Tag 可同時打出多個雲端請求（Gemini/OpenRouter 等）→ 429 與費用放大；反向，本地任務（含重試 `asyncio.sleep`）阻塞整個 worker，其間所有人排隊；`typing` 模擬延遲（L458–460 → `handlers/social/utils.py:862-871`，預設「1.0s + 每 50 字 0.3s」，2000 字約 13 秒）也被計入序列路徑，放大頭部阻塞。
**建議修法**：
```python
# 雲端：per-user + 全域雙層 Semaphore；本地：逾時/重試移出 worker 熱路徑
self._cloud_gate_global = asyncio.Semaphore(4)
self._cloud_gate_user: dict[int, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(1))
async with self._cloud_gate_global, self._cloud_gate_user[task.user_id]:
    ...
```

### [P2 - Medium] 2.1.4 退避為線性、timeout 重試上限僅 1 次；重試睡眠期間 Worker 被佔住
**位置**：`handlers/bot_brain/cog.py` L219、L409–438。`wait_delay = retry_delay * attempt`（僅負載類）或固定 2s；建議改為 `min(base * 2**attempt, cap) * random.uniform(0.8,1.2)`，並考慮把重試排程回佇列而非原地 sleep。

### [P1 - High] 2.1.5 錯誤分類誤判：SSE error frame 被轉成 `ClientResponseError(status=200)`，任何 4xx 與內容過濾都被當「可重試/可切換模型」
**位置**：`handlers/bot_brain/client.py` L546–554；`handlers/bot_brain/cog.py` L409–427。

```python
# client.py L546-554
if chunk.get("error"):
    err_detail = str(chunk["error"])[:200]
    raise aiohttp.ClientResponseError(resp.request_info, history=(), status=200,
                                      message=f"{backend_label} SSE error frame: {err_detail}")
```
```python
# cog.py L409-412：靠「關鍵字黑名單」判斷是否為負載問題
is_rate_or_overload = any(keyword in last_error_msg.lower() for keyword in
    ("503","429","high demand","service unavailable","overloaded","quota",
     "clientresponseerror","clientconnectorerror"))
```
**影響**：401 金鑰錯誤、400 參數錯誤、內容安全過濾全被歸類為「可重試/可換模型」，且 `_is_content_filtered` 分析被 `not is_rate_or_overload` 排除；確定性錯誤重試 3 次純屬浪費，錯誤根因被掩蓋。
**建議修法**：定義專用例外層級（`LLMStreamError / LLMAuthError / LLMRateLimitError / LLMContentFiltered`），依 `HTTP status + err.code` 結構化分類；移除 `"clientresponseerror"` 這類廣義關鍵字。

### [P2 - Medium] 2.1.6 其他推論層問題
| 嚴重度 | 位置 | 問題 |
|---|---|---|
| P2 | `handlers/bot_brain/cog.py:146-148` | `get_pending_tasks()` 讀取 `PriorityQueue._queue` 私有屬性，依賴 CPython 實作細節 |
| P2 | `handlers/bot_brain/health_monitor.py:146-209` | `record_tok_speed()` 全庫無呼叫端 → `PERF_WATCH_*` 效能衰退告警為**死代碼**；健康巡檢只驗 Ollama |
| P2 | `handlers/bot_brain/models.py:6-29`、`cog.py:475` | `InferenceTask` 無 `trace_id/channel_id/user_name` 欄位，推論日誌 trace_id 幾乎必為空 → 全鏈路追蹤斷鏈 |
| P2 | `handlers/social/utils.py:500-535` | `_summarize_context` 使用 `priority=20` 硬編碼（繞過設定）、主力模型、6 秒逾時且不傳播取消，拉高 tag 熱路徑 TTFT |

## 2.2 非同步阻塞 I/O 清單（會直接凍結整個 Event Loop）

> 本系統 Discord Bot 與 WebUI 共用同一 event loop（`main.py:298-300`），任何同步阻塞都會同時凍結對話、排程與管理介面。

| 嚴重度 | 位置（檔案:行號） | 阻塞內容 | 建議 |
|---|---|---|---|
| **[P1]** | `handlers/spark/clients/rss_client.py:52`（經 L113 呼叫） | `socket.getaddrinfo()` 同步 DNS，RSS trigger **每分鐘**重做一次 | 改 `loop.getaddrinfo` / `asyncio.to_thread`（R13 ✅ `75100a8`：`is_safe_feed_url_async` 走 `loop.getaddrinfo`，同步 API 保留） |
| **[P1]** | `handlers/social/tools/search_tools.py:239` | `is_safe_public_url` 同步 DNS（經 L314/L320） | 同上（R13 ✅ `75100a8`：`is_safe_public_url_async`＋`to_thread`，SSRF／跳轉預檢全面非同步） |
| [P2] | `handlers/social/tools/stock_tool.py:683-702`、`search_tools.py:683-686` | `run_in_executor` 包 yfinance/matplotlib；`wait_for` 逾時無法中止執行緒；`search_tools` 版本**完全無逾時** | 移入專用 ThreadPool + 全域併發上限；matplotlib 改用 Agg + 鎖（R13 ✅ `75100a8`：`fetch_and_plot_bounded`＋`STOCK_MAX_CONCURRENT_FETCHES=3` 全域 semaphore，`search_tools` 亦共用；Agg 既有） |
| [P2] | `handlers/social/tools/search_tools.py:355-368` | trafilatura 擷取逾時後執行緒續跑（滯留累積） | 專用 pool + 結果丟棄（R13 ✅ `75100a8`：`web_extract_pool`＋bounded slot；逾時 `shield` 保留底層 Future、結果丟棄，slot 於執行緒結束後釋放） |
| [P2] | `core/sys_inspector/audit_service.py:631-637` | async 函式內同步 SQLite 寫入（事故期間最不該卡） | `asyncio.to_thread`（R12 ✅ `7d3a2b1`） |
| [P2] | `web_ui/settings_schema.py:1605-1633` | `GET /api/settings` 同步讀取人設檔（數十 KB） | `to_thread`＋`mtime_ns` 快取（R12 ✅ `7d3a2b1`） |
| [P2] | `web_ui/server.py:574-611` | `after_request` 對所有 >400B 回應同步 `gzip.compress`（app.js 98KB…） | `asyncio.to_thread`（R12 ✅ `7d3a2b1`） |
| [P2] | `web_ui/server.py:1620-1883,1727-1826` | 稽核寫入、mod_config 寫檔、shutdown 訊號寫檔、`checkpoint_all_dbs()` 全在 event loop 內同步執行 | `to_thread`＋async task（R12 ✅ `7d3a2b1`） |
| [P2] | `web_ui/server.py:223-240,626-662` | import 期同步 `git subprocess.run`（阻塞啟動 0–6 秒）；`server.py:441` sw.js 同步讀檔 | 背景預熱＋`mtime_ns` 快取（R12 ✅ `7d3a2b1`） |
| [P2] | `handlers/vibe_manager/storage.py:108-118`、`vibe_manager/cog.py:104,140` | 每次 vibe 更新/decay 同步 SQLite 全頻道交易 | 批次化 + `to_thread`（R13 ✅ `75100a8`：`executemany`＋`save_vibe_channel`＋快照／鎖＋`to_thread`） |
| [P2] | `handlers/heartbeat/cog.py:133` | 每個 condition 任務同步等待 `channel.history(limit=1)`（Discord API） | 已有 async API，OK；但需加每輪任務數上限（R13 ✅ `75100a8`：`HEARTBEAT_MAX_TASKS_PER_ROUND=10`） |
| **[P1]** | `web_ui/server.py:2022-2027` → `core/sys_inspector/metrics.py:458-465` | `metrics.get_summary` 全表 bootstrap 掃描直接在 async handler 內跑 | 啟動時預熱＋`to_thread`＋bootstrap 鎖（R12 ✅ `7d3a2b1`） |

**已正確 offload 的正面案例**（維持良好）：`web_ui/server.py:552,826,846-857,1295,1729-1803,2262,2361`（`asyncio.to_thread`）、`core/sys_inspector/sampler.py:164-182`（to_thread + TTL + lock）。

## 2.3 資源與記憶體管理

### [P1 - High] 2.3.1 記憶寫入全面 fire-and-forget：失敗無補償、順序無保證
**位置**：`handlers/memory/storage.py` L739–744（單筆）、L773–778（刪除）、L823–828（全量）；`handlers/memory/relationship.py:157-162`。

```python
# storage.py L823-828
try:
    import asyncio
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _do_sqlite_write)   # ← 未 await、無 callback、失敗僅 log
except RuntimeError:
    _do_sqlite_write()
```
**影響**：`/forget` 回覆「已成功清除」時，後端刪除可能尚未執行或已失敗；與 2.3.2 的競態疊加後造成「刪了又復活」。
**建議修法**：改為 `asyncio.create_task` 保存引用 + `add_done_callback` 記錄失敗並回寫狀態；刪除路徑改同步 await（使用者明確等待付出成本可接受）。

### [P1 - High] 2.3.2 全表重寫 vs 單筆重寫的 Lost Update；nightly `reload_store` 競態
**位置**：`handlers/memory/storage.py:814`（`DELETE FROM user_facts` 全表重建）vs `:720`（單 user 重建）；`handlers/memory/nightly_cog.py:582-589`（先丟 executor 再立即 `reload_store()`）。

**影響**：兩條寫入路徑各自交易原子，但全表重寫使用「較舊的 store 快照」，後 commit 即覆蓋新 facts；nightly 的立即 reload 可能讀到尚未 commit 的舊狀態覆蓋 RAM，再被寫回。
**建議修法**：廢除全表重建，改用 `UPSERT + 差異化軟刪`（見維度四 5.2 節範例）；nightly 改 `await` 寫入完成後再 reload。

### [P2 - Medium] 2.3.3 其他資源問題
| 嚴重度 | 位置 | 問題 |
|---|---|---|
| [P2] | `core/db.py:45` | 每次 `get_db_connection` 都新建 SQLite 連線（無連線池）；高頻路徑建立/關閉成本與 fd 壓力 |
| [P2] | `handlers/spark/cog.py:1455-1459` | `cog_unload` 未關閉 4 個 HTTP client session（`StockClient.close()` 等定義存在但零呼叫端）→ 熱重載洩漏 socket |
| [P2] | `handlers/social/observability.py:151,218-246` | 觀測寫入佇列無 `maxsize`、失敗靜默 `debug` 丟棄；`flush_observability_logs`（L272-275）唯一呼叫端是測試 → **程序結束時記憶體佇列事件全部遺失** |
| [P2] | `handlers/social/__init__.py:81-91` | 插話評估 `asyncio.create_task` 未保存引用（CPython 可 GC 掉執行中任務） |
| [P2] | `handlers/spark/cog.py:1480-1491` | 動作派發 fire-and-forget；semaphore(8) 每輪重建且只蓋「檢查」不蓋「動作」→ 跨輪無界累積 |
| [P2] | `handlers/spark/checkers/schedule_checker.py:99-124` | 「唯讀評估」中 `cond["_cached_ts"] = target_ts` 就地改寫共用 dict（會經 service save 寫回 DB） |
| [P3] | `web_ui/server.py:56-57,994-998` | `_login_attempts`/`_locked_ips` 無上限不清掃（配合 XFF 偽造可無限注入鍵值） |

## 2.4 資料存取瓶頸與快取機會

### [P1 - High] 2.4.1 SPARK 排程回寫競態（兼效能問題）：以「檢查前快照」覆寫整個 Trigger
**位置**：`handlers/spark/service.py` L100–163；`handlers/spark/repository.py` L125–133。

```python
# service.py L105：await 期間（最長數十秒的網路 I/O）trigger 是舊快照
result = await checker.should_fire(trigger, now_dt=now_dt)
...
# L147-162：用舊快照重建並覆寫
updated_trigger = Trigger(trigger_id=trigger.trigger_id, ..., is_active=trigger.is_active, ...)
saved = self._repo.save(updated_trigger)     # ← 會把並行發生的「暫停/刪除」覆蓋回去
```
```python
# repository.py L125-133（UPSERT 無條件復活）
... ON CONFLICT(trigger_id) DO UPDATE SET
    ...
    is_deleted = 0,          # ← 已軟刪的 trigger 被復活
```
**建議修法**：改為樂觀鎖（`WHERE trigger_id=? AND updated_at=?`）或僅更新差異欄位（`last_triggered_at/run_count/condition 的 last_* 欄位`），不要整物件覆寫。

### [P2 - Medium] 2.4.2 其他資料存取問題
| 嚴重度 | 位置 | 問題與建議 |
|---|---|---|
| [P2] | `handlers/spark/clients/stock_client.py:85-97`、`weather_client.py:100-105` | 同一 ticker/城市被 N 個 trigger 監控 → N 倍重複請求；建議加 TTL 快取（60s）＋ 單輪去重 |
| [P2] | `handlers/spark/clients/stock_client.py:134-151` | `yfinance` + `to_thread` 無法取消、無 timeout；thread pool 飽和拖慢全系統 |
| [P2] | `mod/deep_search/repositories/search_client.py:57-62` | 每次搜尋新建/關閉 ClientSession（連線 churn） |
| [P2] | `handlers/memory/storage.py:585-618` | `user_facts` 無 `(user_id, fact)` 唯一索引 → 重複列累積；aliases 存 JSON 陣列無法 SQL 查詢 |
| [P3] | `handlers/spark/repository.py:93-97` | `get()` 命中快取後永不重驗 `is_deleted/is_active`（外部工具改 DB 後為髒資料）；`reload()` 全量重建成本高 |
| [P3] | `core/db.py:51-59`、`core/sys_inspector/metrics.py:229` | WAL/checkpoint 僅涵蓋經 `core/db.py` 註冊的 DB；`metrics.bootstrap_from_db` 用原生 `sqlite3.connect` 未註冊 → 不受 checkpoint 管理 |

**正面確認**：`fetch_context_script`（`handlers/social/utils.py:208-288,369-410`）同時處理 token 硬上限 + LRU/TTL + 水位節流；`StockTool` 有 2 分鐘 TTL 容量上限快取（`stock_tool.py:48-63`）；`app/orchestrator.py:166-206` 有 60 秒 LRU 快取（雲端摘要去重）。

## 2.5 前端與傳輸層

| 嚴重度 | 位置 | 問題 |
|---|---|---|
| [P2] | `web_ui/static/app.js`（4081 行） | 單體式前端：83 處 `innerHTML`、大量 inline `onclick`；無模組化/bundler，難以替換框架 |
| [P2] | `core/dispatcher.py:28-97` | `split_text_smart`：行內反引號導致 100% 破版（實測 1743/1743）、4 反引號 fence 誤判、語言標記 `\w+` 不支援 `objective-c`/`c++` |
| [P3] | `handlers/bot_brain/client.py:655` | 每輪推論做完整字串格式化 logging（高頻 CPU 小浪費） |

# 3. 維度二：擴展性與解耦 (Extensibility & Decoupling)

> 維度評分 **6.0 / 10**。`core` 分層骨架與 `.importlinter` 合約是正向資產，但護欄覆蓋不全（缺 `web_ui`、缺 `handlers⇄mod`）、工具註冊雙軌並行、外掛系統兩套慣例、全域單機狀態遍地，使「換模型、換儲存、換前端、多實例部署」四種典型演進皆需大範圍改動。
> 本章計 **P1 ×5、P2 ×8、P3 ×2**。

## 3.1 架構護欄（import-linter）覆蓋不全

### [P1 - High] 3.1.1 `.importlinter` 的 root_packages 不含 `web_ui` → 全部合約對 WebUI 層不設防
**位置**：`.importlinter:1-7`。

```ini
[importlinter]
root_packages =
    app
    core
    handlers
    mod
include_external_packages = True
```
**影響**：`web_ui/server.py`（近 2,900 行、事實上的第五層）可任意 import 任意層（並直接 import `handlers.*`、`settings`、`core.sys_inspector`……），契約掃描完全看不到；任何「拆前端 / 抽服務」重構都無護欄可依循，WebUI 的堆積也只能靠人工自律。
**建議修法**：將 `web_ui` 加入 root_packages，新增契約「`web_ui` 僅可依賴 `core.contracts` 與 `handlers` 的公開門面；`core`/`mod` 不得 import `web_ui`」。

### [P1 - High] 3.1.2 `handlers` ⇄ `mod` 雙向依賴無合約禁止（循環灰區）
兩個方向的實際 import 皆存在（多以函式內 lazy import 規避啟動期循環，但依賴關係客觀存在）：

- **`mod` → `handlers`（13 處）**，例：`mod/feed_sentinel/cog.py:23-24`、`mod/game_center/sherry_ai.py:14`、`mod/deep_search/repositories/llm_client.py:12`、`mod/stock_trader/engine/strategist.py:481,512`、`mod/painter/cog.py:18`、`mod/dnd/views.py:19`。
- **`handlers` → `mod`（7 處）**，例：`handlers/executor/cog.py:357,374,384`、`handlers/social/tools/search_tools.py:31`、`handlers/social/tools/draw_tool.py:18`、`handlers/social/tools/image_tool.py:23`、`handlers/spark/clients/web_monitor_client.py:12`。

```python
# mod/feed_sentinel/cog.py L23-24（外掛依賴核心推論與人設層）
from handlers.bot_brain import InferenceTask
from handlers.social.persona import build_dynamic_persona
```
```python
# handlers/social/tools/search_tools.py L31（精選工具依賴外掛內部基礎設施）
from mod.deep_search.repositories.search_client import DuckDuckGoSearchClient
```
**影響**：「精選工具層」與「專題外掛層」實質纏繞；改動 `bot_brain.InferenceTask`、`mod/clients` 或 `mod.deep_search.repositories` 會同時震動兩層。lazy import 只把爆點延到執行期，linter 與 IDE 皆不可見——這正是「新增外掛/工具成本高、出事難追」的根因之一。
**建議修法**：立契約「`handlers` 不得 import `mod.*`」（工具需要的通用能力下沉 `core/` 或 `mod/clients` 的穩定介面）；「`mod` 只可經 `core/contracts` + 純 DTO（如 `handlers.bot_brain.models`）依賴核心」，並禁止函式內 import 作為繞道（linter 可設 `allow_indirect_imports` 檢查）。

### [P2 - Medium] 3.1.3 護欄豁免清單持續膨脹（Contract Drift）
**位置**：`.importlinter:20-32`，`ignore_imports` 已累積 12 條。

```ini
ignore_imports =
    mod.clients.* -> *
    mod.*.clients.* -> *
    mod.*.repositories.* -> *
    ...
    mod.feed_sentinel.fetcher -> *
    mod.market_plugin.data_fetcher -> *
```
**影響**：每一條豁免都是「先放行、再修依賴」的借據；目前清單已覆蓋 repositories、fetcher、cog、ui 整類路徑，Contract 1（mod 禁 I/O 庫）實質只剩形式意義。「Clean Architecture」宣稱與豁免清單已明顯背離。
**建議修法**：為每條豁免加註到期日/追蹤議題編號；CI 對 `ignore_imports` 條目數設「只准下降」門檻。

## 3.2 工具/能力註冊雙軌制

### [P1 - High] 3.2.1 雙註冊表各自為政：`TOOL_LIST`（真正在用）vs `CapabilityRegistry`（平行宇宙）
**位置**：`handlers/social/registry.py:15`（真來源）；`core/registry.py:20-101`（宣稱的來源）。

```python
# handlers/social/registry.py L15
TOOL_LIST: list[BaseTool] = [
```
`TOOL_LIST` 的實際消費端（節錄）：`handlers/social/router.py:15,177`、`handlers/executor/cog.py:17,33-40`、`handlers/admin/cog.py:560-572`、`handlers/spark/cog.py:1264,1328-1331`、`handlers/social/tools/draw_tool.py:36-60`。

而 `core/registry.py` 的 `CapabilityRegistry` 全庫僅出現於：
```python
# app/orchestrator.py L51（實例化）、L80/L99/L143（register ×3）、L271/L275（get_capability，Context 摘要/翻譯用）
self.registry: CapabilityRegistry = CapabilityRegistry()
```
其「精選 AI 工具」查詢 `get_ai_callable_tools()` 與 `get_dedicated_mods()`（`core/registry.py:71-93`）**全庫零呼叫**——AI 規劃器實際是拿 `TOOL_LIST` 硬編清單，而非查註冊表。
**影響**：兩種「能力真相來源」並存；文件與 `app/orchestrator.py` 開頭註解（:9「全域能力 (CapabilityRegistry) 雙層管理」）宣稱的治理模型，實務上只有 Context Menu 三項走註冊表。新增/改名 AI 工具時，該改哪裡沒有單一答案——擴展路徑分裂。
**建議修法**：以 `CapabilityRegistry` 為唯一來源：啟動時由各工具自帶 `CapabilityDescriptor` 註冊（`TOOL_LIST` 改為「查註冊表所得清單」的衍生視圖），並加 CI 測試斷言「AI 可調用集合 == TOOL_LIST」。

### [P2 - Medium] 3.2.2 新增一個工具需手動同步多處；別名表另一份硬編
**位置**：`handlers/social/dispatch/prompts.py:114`。

```python
# dispatch/prompts.py L114
_TOOL_ALIASES: dict[str, str] = {
```
新增/改名一個 intent 目前至少要動：`handlers/social/registry.py:15`（TOOL_LIST）、`dispatch/prompts.py:114`（別名表）、策略與路由層的散落判斷、以及 `draw_tool.py:36-60` 這類「自我反查」模式：

```python
# draw_tool.py L36-37：從 TOOL_LIST 反查自身，rename 後 next(...) 靜默回 None
from handlers.social.registry import TOOL_LIST
draw_tool = next((t for t in TOOL_LIST if t.intent_name == "DRAW"), None)
```
**影響**：漏改任何一處即「LLM 檢索不到 / 執行時 None」；且無中央清冊可供一致性驗證，新工具開發者極易踩坑。
**建議修法**：以單一 `ToolSpec`（name/aliases/prompt/schema/strategy 旗標）為唯一聲明，別名與 prompt 由它生成；補「別名皆指向有效 intent、prompt 皆有對應工具」的測試。

### [P2 - Medium] 3.2.3 Dispatch 策略對 Coordinator 私有成員白箱耦合
**位置**：`handlers/social/dispatch/base.py:37-50`。

```python
@property
def _tools(self) -> dict[str, BaseTool]:
    """可用工具字典。"""
    return cast(dict[str, BaseTool], self._agent._tools)          # ← 私有成員
...
def _build_tool_descriptions(self, *, include_chat: bool = False) -> str:
    return cast(str, self._agent._build_tool_descriptions(...))    # ← 私有方法
```
**影響**：Integrated/Parallel/ReAct 策略均透過 `DispatchStrategy` 白箱存取 Coordinator 內部欄位；新增第五種策略時無法只依賴公開介面，重構 Coordinator 內部結構即連帶破壞所有策略。
**建議修法**：抽出顯式 `ToolCatalog` 介面（`get_tool(name)` / `describe(include_chat)` / `is_ai_callable(name)`），策略僅依賴介面。

## 3.3 模組化系統的不一致（Cog vs Mod）

### [P2 - Medium] 3.3.1 兩套外掛生命週期、契約語意不明（擴展點與安全邊界同一顆按鈕）
`handlers/` 走 discord.py Cog（型別安全、載入即註冊）；`mod/` 走目錄掃描 + `MOD_INFO` + Blueprint 掛載（`web_ui/server.py:306-314`）。`MOD_INFO` 的 `public_routes` 是**純字串清單**：

```python
# mod/game_center/__init__.py L29
"public_routes": ["/api/games/"],
```
```python
# mod/livestream/__init__.py L27（同型宣告）
```
而 server 端語意是「前綴命中 → 免認證、**全方法**放行」（詳見維度四 §5.4.2）。**影響**：宣告無 method、無權限級別；任何新模組作者都可能無意間開出免認證 POST 端點（LLM 成本/濫用風險）。擴展性與安全性在此糾纏，是「加模組容易、加得安全難」的直接原因。
**建議修法**：`public_routes` 改結構化宣告 `{path, methods, auth_level}`；default deny；由 `web_ui/server.py` 統一強制。

### [P2 - Medium] 3.3.2 模組級可變單例 + import 期副作用（慣例不一）
**位置**：`handlers/music/config.py:65`；消費端 `handlers/music/cog.py:564-565,666-667`。

```python
# handlers/music/config.py L65：模組級共享 singleton
ytdl_client = yt_dlp.YoutubeDL(YTDL_OPTIONS)
```
```python
# handlers/music/cog.py L564-565：以預設 executor 併發使用同一實例
data: dict[str, Any] | None = await loop.run_in_executor(
    None, lambda: ytdl_client.extract_info(search_query, download=False)
)
```
且 `config.py` 在 import 期就有副作用（偵測 ffmpeg、載入 `cookies.txt`、寫 log，`config.py:11-26,37-63`）。`YoutubeDL` 實例持有內部狀態（params、cookiejar），多 guild 同時點歌時併發共用為競態風險。
對照組（正確示範）：`mod/video_clipper/clients/yt_gif_client.py:31,37-38` 以**專屬** `ThreadPoolExecutor` 隔離重負載並例外管理。
**影響**：兩種慣例並存（裸 singleton vs 專用 pool），新人難以判斷何者為準；音樂模組是最高頻互動之一，競態風險暴露面大。
**建議修法**：統一「重資源=實例內持有 + 專用 pool + `close()` 生命週期」規範；music 改為每次抽取新 `YoutubeDL` 或在專用 pool 內序列化。

### [P3 - Low] 3.3.3 幽靈封包 `handlers/social/actions/`：只剩 `__pycache__/*.pyc`
實測該目錄下僅存 `actions/__pycache__/{__init__,image_action,web_search_action,registry,finance_action,base}.cpython-312.pyc`，**無任何 .py 原始碼**。
**影響**：grep/rg 掃描看不到、import 依賴圖出現幽靈節點、重構者可能被誤導「此模組仍存在」。
**建議修法**：清除殘留 `.pyc`；檢查 `.gitignore` 確保 `__pycache__` 永不入庫。

## 3.4 單機假設：關鍵狀態遍地 in-process

### [P1 - High] 3.4.1 水平擴展不可能：session／鎖定／限流／佇列／排程全在行程記憶體
| 狀態 | 位置 | 影響 |
|---|---|---|
| WebUI 管理員 session | `web_ui/server.py:53-54`（`_admin_sessions: dict[str, float]`、TTL 600s） | 重啟即全員登出；多實例互不認知 |
| 登入失敗鎖定 | `web_ui/server.py:56-57`（`_login_attempts`/`_locked_ips`） | 行程內計數（且可被 XFF 偽造繞過，見維度四） |
| 遊戲限流 | `mod/game_center/web.py:44-52`（`_IP_RATE_LIMITS`） | 同上；跨實例無效 |
| 限流器（雙套） | `core/guard.py`、`handlers/social/rate_limiter.py` | 各持行程內狀態；同一使用者依路徑獲得不同限額（見維度五） |
| LLM 佇列 | `handlers/bot_brain/cog.py`（PriorityQueue + 單 worker，L199-206） | 佇列/worker 綁死單行程；多實例=多 worker 搶同一 GPU/額度 |
| SPARK 排程 | `handlers/spark/cog.py`（單一 loop + 每輪重建 semaphore，L1480-1491） | 多實例=重複觸發、重複推播 |

**影響**：起兩個容器 = 兩套 session、兩套限流、兩個排程器各跑一份（**重複推播**）；也無法滾動更新（重啟即丟狀態）。`docker-compose.yml`（全 15 行、單服務、無 healthcheck／無資源限制）正是此限制的沉默證據。
**建議修法**：中期把 `SessionStore`/`RateLimitStore`/`LoginLockout` 抽為介面（先實作 SQLite，可換 Redis）；SPARK 改「DB 租約 leader」模型；短中期先禁止多實例並於文件明示。

### [P2 - Medium] 3.4.2 每 mod 自帶 SQLite + 手動備份維運
9 個業務 DB（`data/*.db`）＋ 12 組手動備份（`data/backups/manual_db_backup_*`，每組含 ~12MB `chat_history.db`）。備份策略實質是「手動複製」——無排程、無增量、無校驗、無還原演練腳本。
**影響**：資料層無法水平擴展（SQLite 單寫者）；RPO/RTO 不可量測；規模與合規（資料保留）壓力會先打在此處。
**建議修法**：短期加自動備份（`VACUUM INTO` 或 backup API + 輪替 + 校驗 + 還原測試）；中期沿 `core/db.py:51-59` 的 DB 註冊機制規劃 PostgreSQL 遷移路徑。

### [P2 - Medium] 3.4.3 預設 executor 被當公用池（與 WebUI `to_thread` 互相排擠）
裸 `run_in_executor(None, ...)` 多處並存：`handlers/music/cog.py:564-565,666-667`、`handlers/memory/storage.py:823-828`、`handlers/social/tools/stock_tool.py:683-702`，與 WebUI 的 `asyncio.to_thread`（同一 default pool）共用。
**影響**：任一重活（yfinance/yt-dlp/全表寫入）可使 default pool 飽和 → 全系統 async 工作停擺（co-scheduling 反效果）。`video_clipper` 的專用 pool 證明正確做法成本極低，但未推廣。
**建議修法**：`core/concurrency.py` 提供 `heavy_io_pool`（固定大小、具名）與 `db_pool`，全面替換裸 `None`。

## 3.5 配置、快取與資料治理耦合

### [P1 - High] 3.5.1 `settings.py` 上帝模組：秘密、路徑、網路、人格同源
單檔混載（節錄族繁不及備載）：密碼生成 `settings.py:711-721`、WebUI 密碼重置旗標 `:810-811`、靈魂檔讀寫 `:813-842`、HTTPS 硬編 `True` `:530`、硬編 VPN IP `26.108.83.196` `:534`、runtime override 無白名單 `:667-707`（落地 `config_override.json` 後回灌）。

```python
# settings.py L813-814：使用者可控字串直接拼為檔案路徑（完整攻擊鏈見維度四）
if key_str == "CURRENT_SOUL_NAME":
    target_path = SOUL_DIR / str(value)
```
**影響**：機密（密碼/憑證/VPN IP）與業務常數同源；WebUI 設定面板實質是「全 Settings 遠端覆寫器」；測試無法隔離環境假設；亦為維度四 P0 攻擊鏈第二段。
**建議修法**：切分 `settings/security.py`、`settings/network.py`、`settings/persona.py`；override 僅允許 schema 白名單中標記 `runtime_safe: true` 的鍵（現況 `:667-707` 無任何白名單）。

### [P2 - Medium] 3.5.2 快取與掃描只做一次：`_MOD_VIEW_CACHE` 永不過期、`_public_mod_routes` 永不重掃
**位置**：`web_ui/server.py:2222`（宣告）、`:2259-2265`（命中即回）、`:317-318`（import 期掃描一次）。

```python
# server.py L2259-2260：無 mtime/hash 驗證，模板更新永不可見
if mod_key in _MOD_VIEW_CACHE:
    return Response(_MOD_VIEW_CACHE[mod_key], mimetype="text/html")
```
**影響**：「動態外掛」名不符實——新增 mod、修改模板、調整 `public_routes` 皆需重啟進程；與熱重載功能（restart 端點）行為不一致，開發迭代成本高。
**建議修法**：cache key 加入 mtime/內容 hash；mod 掃描提供 admin-only 重掃端點。

### [P3 - Low] 3.5.3 升級路徑不可重放（migration 工具非冪等）
`tools/migrate_json_to_sqlite.py` 非冪等、以 `INSERT OR REPLACE` 復活已軟刪記憶、含 `shutil.rmtree` 破壞性分支（`:1066,1076`）。
**影響**：任何 schema 演進都缺「版本化 + dry-run + 可回滾」；在企業環境（多環境晉版）屬硬傷。（與維度四 §5.5.3 同源，此處以「可重複部署」角度記錄。）
**建議修法**：導入 `PRAGMA user_version` 版本化 + 一檔一步 migration + `--dry-run`；廢除 REPLACE 語意。

---

## 3.6 演進情境瓶頸總表（多模型／多角色／換儲存／換前端）

> 對應任務要求：評估「支援多模型動態切換、多角色並行互動、替換底層資料庫/前端框架」的瓶頸與需抽離介面的關鍵位置。

| 演進情境 | 現況瓶頸 | 需抽離的介面／關鍵位置 |
|---|---|---|
| **多模型動態切換** | 供應商已多（Ollama／LM Studio／vLLM／OpenAI／Gemini／Claude／OpenRouter），但選型散落於 `settings` 與各工具（`draw_tool`/`stock_tool` 自帶模型選擇）；優先序語意錯亂（見 §6.1.5）使成本/延遲不可控 | `handlers/bot_brain/client.py` 已有 provider 分支——抽 `BackendProfile`（能力探測＋成本/延遲標籤）；統一模型選擇入口；`settings` 拆出 `models.py` |
| **多角色並行互動** | `settings` 為**全域單態**（`CURRENT_SOUL_NAME`/`DEFAULT_SOUL_PATH` 直接改全域並清全域快取，`:813-842`）；dispatch 策略持有單一 agent；記憶/關係資料以 user 為維度 | `PersonaProvider` 介面（per-guild/per-channel 角色實例）；`clear_soul_cache()` 改 per-key；記憶/關係表增 `character_id` 維度 |
| **替換底層資料庫** | 9 個業務 DB 各自為政；`core/db.py` 僅管 WAL/checkpoint；部分路徑直用原生 `sqlite3.connect`（如 `core/sys_inspector/metrics.py:229` `bootstrap_from_db`）；無統一 Repository 介面（`mod/deep_search/repositories/` 等可作樣板） | 全庫落實 Repository 介面、`core/db.py` 擴為連線工廠/UnitOfWork；查詢集中化後才可能遷 PostgreSQL（見 §3.4.2） |
| **替換前端框架** | `app.js` 單體 4,081 行、83 處 `innerHTML`、大量 inline `onclick`；`/api/*` 無 schema 契約（回應形狀散落各端點） | 先凍結「API 語意契約」（pydantic/OpenAPI 描述自動產生）；前端按視圖切模組（logs/settings/tasks/painter…）；CSP 落地後再遷框架 |

---

**正面確認**：`.importlinter` 三條基礎合約（mod 禁 I/O 庫、core 禁上層、handlers 禁 app）方向正確、值得保留擴充；`mod/video_clipper`（專用 ThreadPool + 逾時 + 無 shell 注入面）是全庫最佳模組實例，建議作為「模組開發樣板」；`core/db.py` 的 DB 註冊 + WAL/checkpoint 機制是未來遷移/集中維運的現成切入點。

# 4. 維度三：新功能深化建議 (Feature Deep-Dive)

> 維度評分 **8.0 / 10**。本平台的架構基因（Cog 事件驅動 + SPARK 條件排程 + BotBrain 優先佇列 + 記憶/關係/世界觀三層資料 + 自我監控面板）非常適合「掛載」進階功能——新增功能的邊際架構成本低，這是它與一般 Discord Bot 的最大差距。
> 以下提出 **5 項**建議功能（依「差異化價值 ÷ 前置修復量」排序於 §4.6），每項均附思路、修改檔案、複雜度與優先級。**注意：所有功能設計均已避開現行 P0 缺陷，且多數功能以「維度四的安全修復」為前置**。

## 4.1 功能一：長短期混合記憶檢索（Hybrid Memory Retrieval）

**一句話**：把目前「純字串啟發式」的記憶回顧升級為「FTS5 關鍵字檢索 + 向量語意檢索 + 關係/時效權重融合」的三段式召回，讓雪莉真的「記得對的事」。

**為什麼是現在**：目前無任何向量庫與 embedding（全專案確認），記憶注入靠關鍵字命中與最近性；`user_facts` 甚至無 `(user_id, fact)` 唯一索引（`handlers/memory/storage.py:585-618`），重複事實會累積。「記憶品質」是 AI 伴侶的第一生命線，此功能的槓桿率最高。

**設計思路**：
1. **資料層**：`sherry_memory.db` 增 `facts_fts`（FTS5，外部內容表掛 `user_facts`）與可選 `facts_vec`（`sqlite-vec` 擴充；不可用時純 FTS5 降級）。
2. **Embedding 來源**：優先用本機 Ollama `/api/embeddings`（零新依賴、與現有 LLM 供應鏈同源）；雲端僅作 fallback。以 `settings.MEMORY_EMBEDDING_MODEL` 配置，寫入時非同步補算（佇列化，失敗可重試）。
3. **召回融合**：`score = w1·bm25_norm + w2·cos_sim + w3·recency_decay + w4·affinity(user_relations) + w5·source_priority`；召回 API 只回傳「未軟刪」資料（**必須與維度四 §5.2 的軟刪一致性修復同步落地**，否則會召回已刪記憶）。
4. **注入點**：`handlers/social/dispatch/prompts.py` 的 context 組裝（保留現有 snippet 機制為快取前綴，檢索結果放「動態尾段」維持 Prompt Cache 友善——與現行 KV Cache 結構測試 `tests/unit/test_prefill_kv_cache_optimization.py` 相容）。
5. **驗收**：以現有 `tests/evaluation/eval_runner.py` 建「記憶問答」評測集（事實召回率、誤召率、P95 延遲 < 300ms 本地檢索）。

**修改/新增檔案**：
| 動作 | 檔案 |
|---|---|
| 新增 | `handlers/memory/retrieval.py`（融合排序與查詢 API） |
| 修改 | `handlers/memory/storage.py`（schema：FTS5/vec 表、唯一索引、寫入 hook；**同時修 `:814` 全表硬刪**） |
| 修改 | `handlers/memory/cog.py`（/forget 與回顧入口接檢索） |
| 修改 | `handlers/social/dispatch/prompts.py`（動態尾段注入） |
| 修改 | `settings.py`（embedding 模型/權重配置） |
| 新增 | `tools/backfill_embeddings.py`（一次性回填） |

**複雜度**：**M（約 5–8 人日）**。檢索/排序 2d、FTS+遷移 1.5d、embedding 佇列 1.5d、評測 1d。
**優先級**：**P1（第一梯隊）**。前置依賴：維度四 §5.2 軟刪一致性（否則檢索會召回已刪資料，加重 GDPR 問題）。
**施工狀態**：✅ **R14 `1ff746c`（FTS5 基礎）＋ R15 `9a37761`（向量融合）**。`facts_fts` 外部內容表（trigram，舊版降級 unicode61）＋同步 triggers＋`tools/backfill_fts.py`；`fact_embeddings`（正規化 float32 BLOB）＋UPDATE／DELETE 失效 triggers＋`tools/backfill_embeddings.py`。檢索層以 FTS5（≥3 字）＋LIKE（短詞）＋Python cosine 三路召回，硬性過濾事實／Profile 軟刪，融合 bm25／cos_sim／recency／affinity／importance／source_priority（權重自動正規化）；Ollama `/api/embeddings` 以 per-loop 佇列批次補算、指數退避重試，`sqlite_vec_available()` 未安裝時自動降級 Python cosine，維度不符的舊向量直接忽略。Prompt 端以 `build_retrieved_memory_tail` 只注入動態尾段並排除既有 snippet，靜態前綴不變、KV cache 結構測試全綠。評測集 v1（13 案例）與 v2（8 案例：純語意、融合、隔離、軟刪、低相似度）皆通過：recall 1.000、誤召率 0.000、已刪召回 0.000、P95 2.9ms；單元測試 R14 16 項＋R15 22 項全綠。

## 4.2 功能二：主動關懷引擎（Proactive Care Engine）

**一句話**：把「雪莉主動找人說話」變成**可治理、有預算、可退出**的一等公民——接線現成但死置的 `core/autonomy_guard.py`，以好感度與情境驅動關懷，而非無界推播。

**為什麼是現在**：這是 AI 伴侶產品的靈魂功能，且零件已存在：`core/autonomy_guard.py`（120 行、全庫零生產呼叫）、`core/risk_engine.py`、`core/system_state.py`（ADR-0002 宣稱強制實施）、SPARK 條件排程（`handlers/spark/`）、關係分數（`handlers/memory/relationship.py`）、深夜整合（`nightly_cog.py`）。缺的是「接線 + 預算 + 同意管理」——以及先把 SPARK 的無配額問題修掉（維度五 §6.2）。

**設計思路**：
1. **治理先行**：`autonomy_guard` 正式接線為「行動准入閘門」（每類主動行為定義額度：每日/每週、靜音時段、每人上限），`risk_engine` 對「私訊/付費動作/公開提及」分級，超限直接拒絕並記錄審計。
2. **觸發來源**：a) 關係分數跨越門檻（`user_relations` ≥ X 且 ≥14 天未互動）；b) SPARK trigger（生日/紀念日/作息）；c) 情緒訊號（見 §4.3）；d) 深夜整合產出的「值得回訪的話題」。
3. **使用者主控**：`/care opt-in|opt-out|frequency`；opt-out 寫入資料庫並被 `autonomy_guard` 強制檢查（**不是 UI 提示，是閘門**）。
4. **回饋循環**：主動訊息的回覆率/靜音率計入預算動態調整（初始保守：每人 ≤2 條/週）。
5. **驗收**：預算 100% 強制（單測斷言超限即拒）、opt-out 100% 生效、沉默率（無回應比例）< 40%。

**修改/新增檔案**：
| 動作 | 檔案 |
|---|---|
| 修改 | `core/autonomy_guard.py`（接線 + 額度表載入）、`core/risk_engine.py`（分級接入） |
| 新增 | `handlers/care/`（engine.py、repository.py、cog.py：`/care` 指令） |
| 修改 | `handlers/spark/cog.py`（action_runner 過閘門）、`handlers/spark/service.py`（配額檢查點） |
| 修改 | `handlers/memory/relationship.py`（只讀輸入） |
| 修改 | `Docs/adr/ADR-0002-governance-and-ai-evaluation.md`（修正「已強制實施」之表述） |

**複雜度**：**M–L（約 8–12 人日）**。閘門接線 3d、care 引擎+指令 4d、驗收與灰度 2d。
**優先級**：**P1（第一梯隊，但排於資安修復後）**。前置依賴：維度四全部 P0、維度五 §6.2（SPARK 配額/熔斷）。

## 4.3 功能三：動態情感狀態機（Dynamic Affect State Machine）

**一句話**：給雪莉一個**會衰減、會累積、可解釋**的情緒狀態（PAD 三軸），讓她的語氣/回覆節奏隨「最近發生的事」自然改變，而不是每輪重置的人格扮演。

**為什麼是現在**：平台已有頻道氛圍（`handlers/vibe_manager/`）、關係分數、深夜整合，但沒有「當下情緒」這一層；伴侶感的核心恰是「情緒有其慣性」。

**設計思路**：
1. **狀態模型**：極簡 PAD（愉悅度、喚起度、支配感，各 [-1,1]）＋ 每軸獨立衰減係數（半衰期 2–12 小時，可配置）；事件來源：訊息情感（以小模型/規則近似）、互動結果（指令成功/失敗）、SPARK 事件、深夜整合。
2. **作用點**：僅經 `handlers/social/persona.py` 的動態尾段（bucketed 分值，如 `mood: cheerful-high`）影響語氣指令；**嚴禁**進入 system prompt 前段（避免 Prompt Cache 全毀——現行有 KV cache 結構測試把關）。
3. **持久化**：`sherry_memory.db` 新表 `affect_state`（channel_id/user_id 粒度、updated_at），隨 WAL 走既有 `core/db.py` 註冊與 checkpoint。
4. **觀測**：WebUI 小面板（先唯讀）顯示當前狀態與近期事件（延用 `/api/system/*` 觀測模式）。
5. **驗收**：狀態有界（不飽和）、衰減可預測（單測）、同輸入下語氣變化可被 A/B 評測感知（`eval_runner`）。

**修改/新增檔案**：新增 `handlers/emotion/`（state.py、decay.py、service.py）；修改 `handlers/social/persona.py`、`dispatch/prompts.py`（尾段）、`handlers/memory/nightly_cog.py`（整合輸入）、`web_ui/server.py`（唯讀 API）。
**複雜度**：**M（約 5–7 人日）**。
**優先級**：**P2（第二梯隊）**。

## 4.4 功能四：角色語氣防崩壞錨點（Persona Drift Guard）

**一句話**：為「雪莉人設」加入自動化風格守門：低優先級 LLM Judge + 風格相似度檢查，偵測到語氣崩壞（串味、變客服、複讀模板）即自動重錨，並把案例餵回評測集。

**為什麼是現在**：`handlers/social/observability.py` 已記錄 raw_input/cot/reply（`:352-361`）、`tests/evaluation/eval_runner.py` 已存在、`tests/unit/test_ai_evaluation_lab.py` 顯示團隊已有人格評測意識——只需補上「線上守門」這一環。人設檔（soul）是平台的核心資產，卻無任何防護網。

**設計思路**：
1. **取樣守門**：對 N% 回覆（或所有「長回覆/情緒化場景」）以**低優先級**推論任務送 Judge（復用 BotBrain 佇列與 PRIORITY 機制，不可搶佔 tag 熱路徑）。
2. **特徵**：a) 風格相似度（與人設範例句的 embedding cosine，與 §4.1 共用 embedding 基建）；b) 規則集（禁用詞、客服腔模板、複讀偵測）；c) Judge 打分（1–5，附理由）。
3. **動作**：分數低 → a) 記錄 + WebUI 告警（先觀察期）；b) 灰度後：自動重錨（重生成一次，附更強風格範例）或降級為「保守短回覆」。
4. **回饋**：每次觸發自動產出評測案例，補進 `eval_runner` 回歸集；月報（延用 SPARK 報表通路）。
5. **驗收**：崩壞檢出率（人工標註集）≥80%、誤報 ≤10%；開啟重錨後用戶抱怨率不升。

**修改/新增檔案**：新增 `handlers/social/persona_guard.py`（檢查器 + 重錨策略）；修改 `handlers/bot_brain/cog.py`（低優先級 judge 任務型別）、`handlers/social/observability.py`（守門事件記錄欄位）、`web_ui/server.py`（面板）、`tests/evaluation/eval_runner.py`（回歸集擴充）。
**複雜度**：**M（約 4–6 人日）**。
**優先級**：**P2（第二梯隊）**。前置依賴：§4.1 的 embedding 基建（可選，規則先行則更快）。

## 4.5 功能五：多模態互動深化（語音串流 + 即時視覺反應）

**一句話**：把語音從「手動 join/leave 的半成品」推進到「喚醒詞常駐 + 低延遲對話」，並讓雪莉對剛貼出的圖片/截圖有**主動的第一反應**（吐槽/關心/梗圖回應），完成「看得到、聽得到、會搭話」的陪伴閉環。

**為什麼是現在**：地基已半成——`handlers/voice/` 有 STT（`stt.py`，含模型管理）、TTS（`tts.py`）、喚醒詞 regex（`cog.py:22`）、VAD 佔位（`vad.py:19-21` 目前 `is_speech() → return True` 是 stub、`recorder.py:21-23` `write()` 亦為 stub）；`requirements.txt:53-55` 已備 `PyNaCl/cryptography/davey`（DAVE E2EE 語音）。缺的不是模型，是**串流管線**。視覺端已有 `image_tool.py` 與 `mod/clients`（Booru/GeminiImage）可掛「主動反應」。
**設計思路（三個可獨立上線的子里程碑）**：
1. **M1 主動視覺反應（S，1–2d）**：當頻道有人貼圖 → `image_tool` 既有 NSFW/安全檢查後，以低優先級任務生成一句短反應（opt-out 可關）；**必須先修** `chat_tools.py:67-75,100,140-141`（NSFW 判定含他人上下文/無頻道檢查）與 `image_tool.py:450-457` 的注入面。
2. **M2 語音管線現代化（M，5–7d）**：引入 `discord-ext-voice-recv`（或自研 sink），實作 `VoiceRecorder.write()`（PCM 緩衝→VAD→utterance 切分），STT 模型改「暖機常駐 + GPU 記憶體預算」；TTS 移除硬編 `.to("cuda")`（`tts.py:13`），加 CPU fallback。
3. **M3 低延遲對話（L，8–12d）**：STT 改 streaming（chunk 級辨識）＋ LLM 串流首段即刻 TTS（句子級切分），目標端到端 P50 < 1.5s；頻道級「對話模式」與 barge-in（打斷）。
**修改/新增檔案**：修改 `handlers/voice/recorder.py`、`vad.py`、`stt.py`、`tts.py`、`cog.py`；新增 `handlers/voice/stream_pipeline.py`；修改 `handlers/social/tools/image_tool.py`、`chat_tools.py`（主動反應 hook）；`requirements.txt`（voice-recv）。
**複雜度**：**L（合計 15–20 人日，可分三階段）**。
**優先級**：**M1=P1、M2=P2、M3=P3（第三梯隊）**。前置依賴：維度四 §5.1（語音/視覺皆為注入與成本放大器，安全整改前勿上線）。

## 4.6 功能優先序總結

| # | 功能 | 差異化價值 | 複雜度 | 前置修復依賴 | 建議階段 |
|---|---|---|---|---|---|
| 1 | 混合記憶檢索 | ★★★★★ | M | 軟刪一致性（維度四 §5.2） | **Phase 2** |
| 2 | 主動關懷引擎 | ★★★★★ | M–L | SPARK 配額/熔斷（維度五 §6.2）、autonomy_guard 接線 | **Phase 2** |
| 3 | 動態情感狀態機 | ★★★★ | M | 無硬依賴（可與 1/2 平行） | **Phase 2–3** |
| 4 | 語氣防崩壞錨點 | ★★★☆ | M | embedding 基建（可選） | **Phase 3** |
| 5 | 多模態深化 | ★★★★ | L（分 M1–M3） | 維度四 §5.1 安全整改 | **M1=Phase 2；M2–M3=Phase 3** |

# 5. 維度四：資訊安全與軟刪除一致性 (Security & Data Lifecycle)

> 維度評分 **4.0 / 10**。單點防護水準不俗（SSRF 防護被正確使用、恆定時間比對、登入鎖定機制、公開路由白名單機制、稽核日誌），但**邊界**缺口嚴重：一個公開渲染端點 + 管理員 token 前端儲存 + 設定檔寫入原語，構成可遠端利用的完整攻擊鏈；資料刪除面則與對外聲明脫節。
> 本章計 **P0 ×4、P1 ×6、P2 ×12、P3 ×2**（詳表見文末 §5.7）。

## 5.1 P0 攻擊鏈：公開 XSS → 竊取管理員 Token → 任意檔案覆寫 → RCE

### [P0 - Critical] 5.1.1 公開反射式 XSS：`/api/mod/<mod_key>/view` 以 `text/html` 原樣回傳模組模板
**位置**：`web_ui/server.py:2253-2267`（路由本體）；`:331`（公開放行）。

```python
# server.py L2253-2267
@app.route("/api/mod/<mod_key>/view", methods=["GET"])
async def get_mod_view_snippet(mod_key: str) -> ...:
    if ".." in mod_key or mod_key.startswith("_"):
        return Response("Invalid mod key", status=400)
    if mod_key in _MOD_VIEW_CACHE:
        return Response(_MOD_VIEW_CACHE[mod_key], mimetype="text/html")
    content = await asyncio.to_thread(_load_snippet_sync, mod_key)
    if content is not None:
        _MOD_VIEW_CACHE[mod_key] = content
        return Response(content, mimetype="text/html")
```
```python
# server.py L331：before_request 直接放行，無任何 Session 要求
if path in ("/", "/sw.js", "/api/version") or (path.startswith("/api/mod/") and path.endswith("/view")):
    return None
```
**影響**：任何未認證訪客可取得模組模板 HTML 片段，且模板內穿插的動態資料未做輸轉義（維度四 §5.4 前端同源問題）；`_MOD_VIEW_CACHE`（`:2222` 宣告）以「第一次請求」為準且永不過期——被污染的內容會被**持續服務**。mod_key 防護（`".."`、`_` 前綴）只擋路徑穿越，與 XSS 無關。
**建議修法**：此端點納入後台 session；輸出改以 `text/plain` 或經模板轉義/DOMPurify 清洗；加 `Content-Security-Policy`（預設 `script-src 'self'`）與 `X-Content-Type-Options: nosniff`。

### [P0 - Critical] 5.1.2 設定任意檔覆寫（`CURRENT_SOUL_NAME`/`SOUL_CONTENT`）+ token 存 `localStorage`＝攻擊鏈閉環
**位置**：`settings.py:813-842`（檔案寫入原語）；`web_ui/static/app.js:38,2582`（token 前端儲存）；`web_ui/server.py:1353`（敏感字過濾**未含**這兩個 key）。

```python
# settings.py L813-819：SOUL_DIR / str(value) 未經正規化/白名單
if key_str == "CURRENT_SOUL_NAME":
    target_path = SOUL_DIR / str(value)
    ...
    g["DEFAULT_SOUL_PATH"] = target_path
```
```python
# settings.py L826-836：SOUL_CONTENT 直接把使用者字串 write_text 到該路徑
if key_str == "SOUL_CONTENT":
    ...
    target_soul.write_text(str(value), encoding="utf-8")
```
```javascript
// app.js L38：管理員 token 自 localStorage 讀取
adminToken: localStorage.getItem('sherry_admin_token') || null,
// app.js L2582：登入成功即寫入 localStorage（任何 XSS 可竊）
localStorage.setItem('sherry_admin_token', data.token);
```
**攻擊鏈**（全鏈已於本次審查實測各環節程式碼）：
1. 攻擊者誘導管理員瀏覽 `GET /api/mod/<mod_key>/view`（未認證可取，含插入腳本之模板/快取污染）→ **XSS 在管理員瀏覽器上執行**；
2. 腳本讀 `localStorage['sherry_admin_token']` → 附帶該 token 呼叫 `/api/settings`（POST）；
3. `update_setting("CURRENT_SOUL_NAME", "../../…")` 設定越界路徑 + `update_setting("SOUL_CONTENT", <payload>)` **對任意既存檔覆寫**（行程權限內所有路徑）；
4. 寫入 `.py` 模組/啟動腳本等「未來會被載入」的檔案 → **任意程式碼執行 / 持久化後門**；或寫壞憑證/設定造成服務癱瘓。
**建議修法**（四點須同批）：
- 靈魂名稱白名單：`^[A-Za-z0-9_\-]+\.(md|txt)$` 且 `resolved_path` 必須位於 `SOUL_DIR` 內（`Path.resolve()` + `is_relative_to`）；
- 擴充 `server.py:1353` 敏感字清單至所有「會造成檔案副作用」的 key（或其白名單反轉：非白名單一律拒）；
- token 改存 `HttpOnly + Secure + SameSite=Lax` cookie（見 §5.3.4），前端不再持有；
- 修 XSS 本體（§5.1.1、§5.4.1）。

### 5.1.3 防禦斷鏈建議（本節為 5.1.1／5.1.2 之修復驗收清單，不另計問題數）：三個獨立斷點，任一即阻斷
| 斷點 | 修復項 | 成本 |
|---|---|---|
| A. 取不到 token | 改 HttpOnly cookie + CSRF token | S（半天） |
| B. 注入不了腳本 | 端點認證化 + 輸出轉義 + CSP | S–M（1–2 天） |
| C. 寫不了檔 | 路徑白名單 + 敏感 key 白名單反轉 | S（半天） |
**建議**：三者於 Phase 1 第一週全部落地並加回歸測試（參照既有 `tests/unit/test_web_server_auth.py`、`test_security_deep_audit.py` 擴充）。

## 5.2 資料刪除與軟刪一致性（GDPR/資料生命週期）

### [P0 - Critical] 5.2.1 `/forget` 刪除不完整＋fire-and-forget：對外宣稱「已清除所有長期記憶」不實
**位置**：`handlers/memory/cog.py:201-208`；`handlers/memory/storage.py:747-778`。

```python
# handlers/memory/cog.py L201-208（節錄語意）
# /forget 僅走 save_user_memory(...) 對 profiles / user_facts 做（軟）刪除，
# 隨即回覆「✅ 已清除所有長期記憶」；未觸及：
#   user_relations（好感度/關係）、chat_messages（對話全文）、
#   JSON 快照（*.json fallback 檔）、archive_legacy_json/（整月原始對話）
```
```python
# handlers/memory/storage.py L773-778（節錄語意）：刪除亦為 fire-and-forget
try:
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _do_sqlite_write)   # 未 await、無 callback
except RuntimeError:
    _do_sqlite_write()
```
**影響**：使用者行使刪除權後，好感度、對話全文、備份與歸檔原樣留存——合規風險直接命中（GDPR 第 17 條、台灣個資法第 11 條）；且「已清除」回覆可能在不同步失敗時成為虛假陳述。與 §2.3.1/§2.3.2 的競態疊加可出現「刪了又復活」。
**建議修法**：`/forget` 改為單一 `await` 的**交易級清除流程**（同一 DB 交易 + 檔案刪除 + 稽核記錄）：
```
1) user_facts / user_profiles：is_deleted=1, deleted_at=now
2) user_relations：匿名化（清除可識別欄位、分數歸零）或刪除列（依政策）
3) chat_messages：UPDATE is_deleted=1（補欄位，見 5.2.4）
4) JSON 快照 / archive / logs：物理刪除或重寫（原子替換）
5) 寫入 admin_audit：who/when/scope/結果
6) 全數成功才回覆「已清除」；部分失敗 → 明確告知範圍與重試途徑
```

### [P1 - High] 5.2.2 全表 `DELETE FROM user_facts` 硬刪：毀滅軟刪軌跡、放大 lost-update
**位置**：`handlers/memory/storage.py:814`（全表）、`:720`（單 user）；`handlers/admin/cog.py:108-111`、`:280-284`。

```python
# storage.py L814（節錄）：保留策略的軟刪設計被「全表重建」直接抹除
cur.execute("DELETE FROM user_facts")          # 之後按 RAM store 全量重插
```
**影響**：`is_deleted/deleted_at` 軌跡歸零、審計不可回溯；且以「較舊 RAM 快照」重插會覆蓋並行寫入（lost update）。`/memory clear`（admin super-only，權限正確）與 `MemoryControlView` 按鈕同走此路徑。
**建議修法**：見 §5.2.9 範例（UPSERT + 差異化軟刪）；清除指令改呼叫專用 `soft_delete_user_facts(user_id)`。

### [P2 - Medium] 5.2.3 已刪記憶復活路徑：JSON fallback 與 roast 回灌
**位置**：`handlers/memory/storage.py:630,666-683`；`handlers/memory/relationship.py:103-112`；`handlers/vibe_manager/storage.py:83-101`；`handlers/roast/manager.py:101-138,127`。

```python
# handlers/memory/storage.py L666-683（節錄語意）：SQLite 為空時回讀舊 JSON 檔補寫，
# 已軟刪的項目若 JSON 快照仍存在 → 復活
```
```python
# handlers/roast/manager.py L127（節錄）：啟動時由 roast_rules.json 回灌，
# 對應列「is_deleted 被重設為 0」（JSON 無刪除欄位可繼承）
```
**影響**：「刪除」不具單調性——只要任一歷史檔案還在，重啟/降級路徑就可讓資料復活；語意上比硬刪更糟（使用者以為刪了）。
**建議修法**：JSON fallback 僅在「首次遷移」使用且一次性封存後停用；回灌掃描改為「僅補缺失、不覆寫刪除標記」（`INSERT ... ON CONFLICT DO NOTHING`）。

### [P0 - Critical] 5.2.4 `chat_messages` 無任何刪除/保留途徑：對話 PII 無界滯留
**位置**：`handlers/memory/chat_logger.py:43-56`（僅寫入）；保留清理器對它零覆蓋（`handlers/memory/retention_worker.py:206-245`）。

```python
# retention_worker.py L206-207：清理面僅涵蓋 temp/cache 與兩個 jsonl
for temp_sub in ["temp", "cache", "chat_log/temp"]:
    sub_path = self.base_data_dir / temp_sub
```
（另注意 `"chat_log/temp"` 為拼寫路徑、`base_data_dir` 預設為**相對路徑** `"data"`（`:71`）、JSONL 清理為**原地重寫**（`:183-185`，非原子），以及 `admin_audit_max_days: int = 90`（`:52`）**定義後從未被套用**——admin 稽核從不清理。）
**影響**：`data/chat_history.db` 16.7MB 並持續增長（另含 12 組手動備份），對話 PII 永久滯留且無使用者可控刪除途徑；`/forget` 亦不覆蓋（§5.2.1）。這是「資料生命週期」最重的缺口。
**建議修法**：a) `chat_messages` 增 `is_deleted/deleted_at` 與保留期設定（預設 180 天），接 `DataRetentionWorker`；b) 補 `admin_audit.jsonl` 清理（實作 `:52` 政策）；c) 路徑改 `settings.DATA_DIR`（絕對）；d) JSONL 輪轉改「寫新檔 + 原子替換」。

### [P1 - High] 5.2.5 `world_memory`：讀取全無 `is_deleted` 過濾 + 硬刪 + 60 秒快取
**位置**：`handlers/memory/world_memory.py:265,270,299,304,393-412,441`（讀取）、`:479,502`（硬刪）、`:318-319,353-356`（TTL 快取）。

**影響**：世界記憶表有軟刪欄位卻在全部查詢點漏過濾——被刪的世界事件仍會注入 prompt（60 秒快取期間尤其）；刪除本身又是硬刪。世界記憶會直接進入人格上下文，屬「已刪資料仍被使用」的合規問題。
**建議修法**：所有 SELECT 統一經 `WHERE is_deleted=0`（封裝查詢函式）；刪除改軟刪；快取失效策略與寫入同步（寫入時清 key）。

### [P2 - Medium] 5.2.6 `schedule_storage` 硬刪且表無 `is_deleted` 欄
**位置**：`handlers/schedule_storage.py:154-169`。
**影響**：排程刪除無軌跡；與 SPARK 的軟刪模型不一致（同一系統兩套刪除語意），審計與除錯皆缺。
**建議修法**：補欄位 + 軟刪 + `db_manager cleanup_soft_deleted`（`tools/db_manager.py:683-720` 已有清理器，目前覆蓋面小）排程化。

### [P2 - Medium] 5.2.7 WebUI 日誌檢視 `SELECT *` 無任何過濾
**位置**：`web_ui/server.py:663,680`。
**影響**：已軟刪內容仍經管理介面展示（與 §5.2.5 同理），且為公開 GET（§5.3.1）。
**建議修法**：查詢加 `is_deleted=0`；日誌端點權限收斂。

### [P2 - Medium] 5.2.8 三軌排程表漂移 + 遷移工具非冪等（REPLACE 復活 / rmtree）
**位置**：三個排程資料表（`triggers` / `schedule_tasks` / `schedules`）分屬不同時期設計；`tools/migrate_json_to_sqlite.py:1066,1076`。
```python
# migrate_json_to_sqlite.py（節錄）：INSERT OR REPLACE 使已軟刪列被復活；
# 另有 shutil.rmtree(...) 破壞性分支（L1066,1076 附近）
```
**影響**：schema 漂移 + 遷移不可重放 + 破壞性分支；升級/回滾毫無保證。（維度二 §3.5.3 同源，此處以資料生命週期角度記錄。）
**建議修法**：版本化 migration + `--dry-run` + 廢除 REPLACE/rmtree。
**核銷（R18）**：`PRAGMA user_version`＋`migrations/` 單檔單步＋`tools/migrate.py` 乾跑／備份／rollback；`schedules` 成為 canonical 讀模型（m002 回填、m003 triggers、`tools/unify_schedules.py` 收斂 SPARK）；`REPLACE`／`rmtree` 已清除。

### 5.2.9 修法設計範例：UPSERT + 差異化軟刪（對應 §2.3.2 引用）
```python
# 建議落實於 handlers/memory/storage.py
def upsert_user_facts(cur: sqlite3.Cursor, user_id: str, facts: list[str]) -> None:
    now = _now_str()
    incoming = set(facts)
    # 1) 本次未出現的舊事實 → 軟刪（保留軌跡，不再全表硬刪）
    cur.execute(
        "UPDATE user_facts SET is_deleted=1, deleted_at=? "
        "WHERE user_id=? AND is_deleted=0",
        (now, user_id),
    )
    for fact in incoming:
        # 2) 已知唯一索引：CREATE UNIQUE INDEX idx_user_fact ON user_facts(user_id, fact)
        cur.execute(
            """
            INSERT INTO user_facts (user_id, fact, created_at, updated_at, is_deleted, deleted_at)
            VALUES (?, ?, ?, ?, 0, NULL)
            ON CONFLICT(user_id, fact) DO UPDATE SET
                updated_at = excluded.updated_at,
                is_deleted = 0,
                deleted_at = NULL
            """,
            (user_id, fact, now, now),
        )
    # 3) 物理清除僅由 retention worker 在保留期後「分批 + 稽核」執行
```
**配套**：`(user_id, fact)` 唯一索引（現況無，重複列會累積，見 §2.4.2）；`/forget` 呼叫 `soft_delete_all(user_id)` 而非重建；清理由 `tools/db_manager.py:683-720 cleanup_soft_deleted` 擴表並排程。

## 5.3 認證邊界與 WebUI 硬化

### [P1 - High] 5.3.1 未認證可讀端點盤點（資訊洩漏面）
**位置（白名單本體）**：`web_ui/server.py:344-366`。

```python
# server.py L344-366（節錄）：未認證 GET 白名單
public_get_prefixes = (
    "/api/stock_trader/", "/api/deep_search/", "/api/feed_sentinel/", "/api/painter/",
    "/api/models/", "/api/services/", "/api/modules",
    "/api/logs",
    "/api/roast/config", "/api/git/",
    "/api/system/dashboard", "/api/system/metrics", "/api/system/components/metrics",
    "/api/system/incidents",
    "/api/system/audit/",
    "/api/system/tasks",
    "/api/self_monitoring/",
    "/api/bot/status",
    "/api/spark/",
    "/handlers/", "/mod/",
)
```
| 端點 | 位置 | 洩漏內容 |
|---|---|---|
| `/api/system/tasks` | `server.py:1863-1885` | 排程任務內容（**prompt 全文**、目標頻道、`user_id`） |
| `/api/bot/status` | `server.py:2004-2086` | 成員名稱/頭像/上線狀態 |
| `/api/system/audit/kpi`、`/api/system/audit/records` | `server.py:1733-1782` | 稽核統計與紀錄 |
| `/api/self_monitoring/*` | `server.py:1733-1782` 區段 | 自我監控數據 |
| `/api/logs`（含 `?file=`） | `server.py:773-886` | 見 §5.3.5 |
| `/api/stock_trader/*`、`/api/spark/*`、`/api/deep_search/*` | 同上白名單 | 交易/排程/研究內容 |
**影響**：未登入即可蒐集「目標人物、prompt、頻道、稽核、個資」；配合 §5.1.1 的 XSS 是攻擊鏈的偵查階段。
**建議修法**：白名單全面檢視；敏感者移入 admin 或加遮罩（prompt 摘要化、成員名去識別）；`/api/logs` 必登入。

### [P1 - High] 5.3.2 模組 `public_routes`：前綴命中即**免認證全方法**放行
**位置**：`web_ui/server.py:306-314`（掃描）、`:338-341`（放行）。

```python
# server.py L306-310（節錄）：MOD_INFO 宣告什麼就放行什麼
declared_routes = info.get("public_routes", [])
for r in declared_routes:
    routes.add(r.strip())
```
```python
# server.py L339-341：method 完全不檢查
for pub_route in _public_mod_routes:
    if path.startswith(pub_route) or path == pub_route:
        return None
```
**受害者清單（節錄）**：`mod/game_center/__init__.py:29`（`/api/games/`）、`mod/livestream/__init__.py:27`。經此設計：**任何人**可 POST 觸發 LLM 撐起的遊戲（成本濫用）、直播控制與聊天偽造（`mod/livestream/web.py` 控制端點、blueprint 由 `mod/livestream/cog.py:424-431` 掛入主站）。
**影響**：與 5.1.1 同級的「未認證寫入面」；LLM 成本、直播內容完整度、審計污染全暴露。
**建議修法**：`public_routes` 參數化 `{path, methods, auth}`；server 端強制；`game_center` 至少加 Turnstile/Discord OAuth 或改成「Bot 內按鈕觸發」；直播控制端點強制 token。

### [P1 - High] 5.3.3 X-Forwarded-For 偽造：繞過登入鎖定與遊戲限流
**位置**：`web_ui/server.py:60-77`（取最左值）；`mod/game_center/web.py:55-64`。

```python
# game_center/web.py L59-61：左值完全由客戶端可控
forwarded = request.headers.get("X-Forwarded-For")
client_ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "127.0.0.1")
```
**影響**：攻擊者每次請求換一個 XFF 值 → 登入暴力破解不再受鎖定限制（`_locked_ips` 永遠剛滿即被新 IP 替換）、遊戲限流形同虛設；且 `_login_attempts/_locked_ips` 無上限（`server.py:56-57`）會被灌爆記憶體（見 §2.3）。
**建議修法**：僅信任「來自已配置代理（`TRUSTED_PROXIES`）的 XFF」，且取**最右**非私有 IP；無代理佈署直接忽略 XFF 用 `remote_addr`。

### [P1 - High] 5.3.9 綁定失敗靜默回退 `0.0.0.0`（外加獨立 Livestream 伺服器 `0.0.0.0:8764` 零認證）
**位置**：`web_ui/server.py:2789-2797`（回退）；`mod/livestream/config.py:32-41`（獨立伺服器）。

```python
# mod/livestream/config.py L32-41
HOST: str = os.getenv("LIVESTREAM_HOST", "0.0.0.0")
PORT: int = int(os.getenv("LIVESTREAM_PORT", "8764"))
HTTPS: bool = os.getenv("LIVESTREAM_HTTPS", "true").lower() in ("true", "1", "yes")
...
RADMIN_IP: str | None = os.getenv("WEB_UI_RADMIN_IP", "26.108.83.196")   # 硬編 VPN IP
```
**影響**：主站若遇埠衝突/權限問題會**回退綁定 0.0.0.0**（超出管理員預期）；livestream 側車伺服器（`mod/livestream/web.py:528-534` 建立）預設對外開放且無認證，影片串流可讀本機 `Video/` 目錄。兩者疊加＝家庭/主機網路暴露面。
**建議修法**：綁定失敗一律 fail-fast（拒絕以 0.0.0.0 降級，除非顯式 `ALLOW_FALLBACK_BIND=1`）；livestream 改 `127.0.0.1` 由主站反向代理並加認證。

### [P2 - Medium] 5.3.4 傳輸/會話硬化缺口
**位置**：`web_ui/server.py:1066-1073`（cookie 無 `Secure`）；`:381-415`（`after_request` 無任何安全標頭：CSP/XFO/nosniff/HSTS/Referrer-Policy）；`settings.py:530`（`WEB_UI_HTTPS` 硬編 `True`）；`server.py:2716`（自簽憑證 `NoEncryption`）。

```python
# server.py L1066-1073（節錄）：僅 HttpOnly + SameSite，缺 Secure
resp.set_cookie("sherry_admin_token", token, httponly=True, samesite="Lax",
                max_age=int(_SESSION_TIMEOUT_SEC))
```
**影響**：同道網路可攔截（無 Secure 的 cookie 可在 http 下落地）；無 CSP 使 XSS 影響最大化（§5.1）；自簽 + 硬編 HTTPS 使佈署僵化。
**建議修法**：補 `secure=True`；統一安全標頭中介層；HTTPS 由環境變數控制、憑證改可注入路徑。

### [P2 - Medium] 5.3.5 `/api/logs?file=` 任意檔讀取（管理員權限下仍是提權面）
**位置**：`web_ui/server.py:773-886`。
**影響**：路徑可讀 `DATA_DIR` 內任意檔（`config_override.json`、`cookies.txt`、DB 檔……）；配合 token 竊取（§5.1）即「讀取任意資料檔」。即使限管理員，也違反最小權限。
**建議修法**：檔名白名單（僅允許 `logs/` 下既知檔）；resolve 後路徑必須在 `LOGS_DIR` 內。

### [P2 - Medium] 5.3.6 例外細節外洩（`str(exc)` 直送 API 回應）
**位置**：`web_ui/server.py:830,876,947,1317,1671,1859,1885,1898,1984,2001,2084,2513`；`mod/spark/web.py:95,132,146,164,185,200`；`mod/livestream/web.py:268`；`handlers/context_menu/cog.py:198`（本次掃描新發現，直接回傳 `{exc}` 給 Discord 使用者）。
**影響**：堆疊訊息/檔案路徑/SQL 片段可輔助偵查（並與 §5.3.1 疊加）。
**建議修法**：回應統一代碼化訊息（`error_id` 對照伺服器日誌）；`debug` 模式才展開。

### [P2 - Medium] 5.3.7 管理員密碼每啟動重生並列印終端
**位置**：`settings.py:711-721`（生成 + 列印）；`server.py:2734-2740`（啟動橫幅再印）；`:810-811`（改密碼重置旗標）。
**影響**：日誌/終端錄影/多工環境即洩漏；且「重生」使密碼無法集中管理（密管系統/保險庫無處接入）。另注意 `update_setting('WEB_UI_PASSWORD', ...)` 為合法路徑，但仍走同一無白名單覆寫器。
**建議修法**：支援 `WEB_UI_PASSWORD_HASH`（argon2/bcrypt）環境注入；預設不再列印明文；重生僅限 `--gen-password` 一次性。

### [P3 - Low] 5.3.8（併記）鎖定表無上限 / 遊戲限流記憶體成長
`server.py:56-57`、`mod/game_center/web.py:44-52`：均為無清掃的行程內字典/列表；配合 XFF 偽造可無限灌入（詳 §2.3 表）。
**建議**：TTL + 上限 LRU。

## 5.4 前端與輸出安全

### [P2 - Medium] 5.4.1 `innerHTML` 注入面與未完善轉義
**位置**：`web_ui/static/app.js:595-605→662,637,649,3314,3357-3381`；`web_ui/static/spark.js:54,66,137`；`escHtml` 缺口 `app.js:1487-1493`（未處理單引號 `'`）。

```javascript
// app.js L1487-1493（節錄）：轉義覆蓋 <>& 與雙引號，獨漏單引號
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
```
**影響**：管理介面本身即 XSS 平台（資料來自使用者/LLM 輸出）；單引號缺口使 inline attribute 拼接場景失效。全站 83 處 `innerHTML`（§2.5）使修補工作量大。
**建議修法**：導入 DOMPurify 或全面改 `textContent`/`createElement`；`escHtml` 補 `'`→`&#39;`；以 ESLint 規則禁新 `innerHTML`。

### [P2 - Medium] 5.4.2 Livestream SSE：無連線上數上限、無 Origin 驗證
**位置**：`mod/livestream/web.py:164-198`（SSE 生成器）；`:268`（`str(exc)` 外洩併見 §5.3.6）。
**影響**：未認證者可開無限 SSE 連線（資源耗盡）；跨站可嵌入訂閱事件流（資訊側漏）。
**建議修法**：連線數上限 + `Origin` 白名單 + 心跳逾時回收；端點移入認證層。

## 5.5 資料保留、備份與升級治理

### [P2 - Medium] 5.5.1 手動備份含大量 PII 且無生命週期
**位置**：`data/backups/manual_db_backup_*`（12 組，每組含 ~12MB `chat_history.db` 全量對話）。
**影響**：即使修好 §5.2.4 的線上保留，備份仍是完整 PII 副本且無到期/加密/存取控管——GDPR 審計時為一級問題；亦占空間（合計 >140MB）。
**建議修法**：備份腳本化 + 加密（age/GPG）+ 保留 N 份自動輪替 + 記錄於稽核；刪除權行使時同步標記備份處置。

### [P2 - Medium] 5.5.2 保留政策覆蓋不足（實測清單）
**位置**：`handlers/memory/retention_worker.py:46-53`（政策）、`:195-254`（實作）。
| 資產 | 政策 | 實際 |
|---|---|---|
| `runtime_log.jsonl` | 14 天 | ✅ 有（原地重寫，非原子） |
| `error_log.jsonl` | 30 天 | ✅ 有（同上） |
| `admin_audit.jsonl` | 90 天（`:52` 定義） | ❌ **從未清理** |
| `temp/cache/chat_log/temp` | 24h | ⚠️ 路徑拼寫 `chat_log/temp`（`:207`）、`base_data_dir` 相對路徑（`:71`） |
| `chat_messages`（DB） | 無 | ❌（見 §5.2.4） |
| `archive_legacy_json/` | 無 | ❌ |
| 備份 | 無 | ❌（見 §5.5.1） |

**建議修法**：政策 → 實作的映射表化（資產清單驅動）；補 admin_audit/chat/archive/備份四項；JSONL 原子輪轉。

### [P3 - Low] 5.5.3（併記）migration/清理工具破壞性語意
`tools/migrate_json_to_sqlite.py:1066,1076`（REPLACE + `rmtree`）、`tools/db_manager.py:683-720`（已有 `cleanup_soft_deleted`，覆蓋面待擴大並排程）。
**建議**：破壞性動詞（rmtree/REPLACE/DELETE）統一要求 `--yes-i-know` 與 dry-run 輸出。

## 5.6 資安正面確認（值得保留）
| 項目 | 位置 | 說明 |
|---|---|---|
| SSRF 防護 | `core/guard.py:is_safe_public_url`，被 `mod/deep_search/repositories/scraper_client.py:16,35`、`handlers/social/tools/search_tools.py:239` 正確前置調用 | 公開 URL 才放行 |
| 登入比對 | `web_ui/server.py`（admin token 恆定時間比對、TTL 600s、逐請求驗證） | 基本會話安全意識在線 |
| 限流機制存在 | `core/guard.py`、`handlers/social/rate_limiter.py`、`game_center` per-IP | 機制俱在（雙套問題見維度五） |
| 稽核日誌 | `admin_audit.jsonl`（547KB）+ `server.py` 稽核寫入點 | 有留痕意識（僅缺保留政策） |
| 敏感 key 過濾雛形 | `server.py:1353` | 方向正確，需擴充/反轉（§5.1.2） |
| 軟刪清理器具 | `tools/db_manager.py:683-720` | 已有 `cleanup_soft_deleted`，缺的是排程與覆蓋面 |
| 密碼不落庫 | `settings.py:711-721` | 無 DB 明文儲存（但列印終端問題見 §5.3.7） |
| CORS | 全庫實測**無任何 CORS/cross-origin 設定** | 同源模型、未開放跨域——屬正確的保守姿態（非「設定過寬」） |

## 5.7 維度四問題匯總表

| ID | 嚴重度 | 主題 | 關鍵位置 |
|---|---|---|---|
| 5.1.1 | **P0** | 公開反射式 XSS（mod view 端點） | `web_ui/server.py:2253-2267,331` |
| 5.1.2 | **P0** | 設定任意檔覆寫 + token 存 localStorage（RCE 鏈） | `settings.py:813-842`、`app.js:38,2582`、`server.py:1353` |
| 5.2.1 | **P0** | `/forget` 刪除不完整 + fire-and-forget | `handlers/memory/cog.py:201-208`、`storage.py:747-778` |
| 5.2.4 | **P0** | `chat_messages` 無任何刪除/保留途徑 | `handlers/memory/chat_logger.py:43-56`、`retention_worker.py:206-245` |
| 5.2.2 | P1 | 全表 `DELETE FROM user_facts` 硬刪 | `handlers/memory/storage.py:720,814`、`admin/cog.py:108-111,280-284` |
| 5.2.5 | P1 | world_memory 讀取無 `is_deleted` 過濾 + 硬刪 + 快取 | `handlers/memory/world_memory.py:265-441,479-502` |
| 5.3.1 | P1 | 未認證端點盤點（prompt/成員/稽核/logs） | `web_ui/server.py:344-366,1863-1885,2004-2086,1733-1782,773-886` |
| 5.3.2 | P1 | `public_routes` 免認證全方法放行 | `web_ui/server.py:306-314,338-341`、`mod/game_center/__init__.py:29` |
| 5.3.3 | P1 | XFF 偽造繞過鎖定/限流 | `web_ui/server.py:60-77`、`mod/game_center/web.py:55-64` |
| 5.3.9 | P1 | 綁定失敗回退 0.0.0.0；livestream:8764 零認證 | `web_ui/server.py:2789-2797`、`mod/livestream/config.py:32-41` |
| 5.2.3 | P2 | JSON fallback 與 roast 回灌「復活」已刪記憶 | `handlers/memory/storage.py:630,666-683`、`roast/manager.py:127` |
| 5.2.6 | P2 | `schedule_storage` 硬刪且無 `is_deleted` 欄 | `handlers/schedule_storage.py:154-169` |
| 5.2.7 | P2 | WebUI logs 檢視 `SELECT *` 無過濾 | `web_ui/server.py:663,680` |
| 5.2.8 | P2 | 三軌排程表漂移 + 遷移工具非冪等（REPLACE/rmtree） | `tools/migrate_json_to_sqlite.py:1066,1076` |
| 5.3.4 | P2 | Cookie 缺 Secure、無安全標頭、硬編 HTTPS/自簽 | `web_ui/server.py:1066-1073,381-415,2716`、`settings.py:530` |
| 5.3.5 | P2 | `/api/logs?file=` 任意檔讀取 | `web_ui/server.py:773-886` |
| 5.3.6 | P2 | `str(exc)` 外洩（含 spark/livestream/context_menu） | `web_ui/server.py:830,876,947` 等 12 處；`mod/spark/web.py:95-200`；`handlers/context_menu/cog.py:198` |
| 5.3.7 | P2 | 管理員密碼每啟動重生並列印終端 | `settings.py:711-721`、`server.py:2734-2740` |
| 5.4.1 | P2 | `innerHTML` 注入面 + `escHtml` 缺單引號轉義 | `app.js:595-605,637,649,3314,3357-3381,1487-1493`、`spark.js:54,66,137` |
| 5.4.2 | P2 | Livestream SSE 無連線上限/無 Origin 驗證 | `mod/livestream/web.py:164-198` |
| 5.5.1 | P2 | 手動備份含大量 PII、無生命週期 | `data/backups/manual_db_backup_*`（12 組） |
| 5.5.2 | P2 | 保留政策覆蓋不足（admin_audit 從未清理等） | `handlers/memory/retention_worker.py:46-53,195-254` |
| 5.3.8 | P3 | 鎖定表無上限（併記，配合 XFF 可灌爆） | `web_ui/server.py:56-57`、`mod/game_center/web.py:44-52` |
| 5.5.3 | P3 | 破壞性工具語意（rmtree/REPLACE 無防護） | `tools/migrate_json_to_sqlite.py:1066,1076` |

# 6. 維度五：企業級可靠性、AI 治理與可觀測性 (Reliability, AI Governance & Observability)

> 維度評分 **5.0 / 10**。Trace ID 基建、稽核日誌、WAL checkpoint 顯示工程意識在線；但**治理機制未接線**（三件套死碼）、SPARK 無熔斷無配額、背景系統零結構化觀測、Prompt Injection 面無隔離、部署面基本盤缺口（root/無 .dockerignore/0.0.0.0 暴露）。
> 本章計 **P0 ×1、P1 ×11、P2 ×8、P3 ×3**（詳表見 §6.7）。

## 6.1 AI 治理與自主性邊界（與 ADR-0002 的落差）

### [P0 - Critical] 6.1.1 治理三件套零接線：`autonomy_guard` / `risk_engine` / `system_state` 全庫零生產呼叫
**位置**：`core/autonomy_guard.py`（`:120` 為其准入/熔斷檢查入口）、`core/risk_engine.py`、`core/system_state.py`；文件：`Docs/adr/ADR-0002-governance-and-ai-evaluation.md`。

```text
# 本次全庫呼叫點掃描（生產程式碼 vs 測試）
core/autonomy_guard.py      ← 定義
core/risk_engine.py         ← 定義
core/system_state.py        ← 定義
tests/unit/test_autonomy_guard.py   ← 唯一「使用」者（＋test_risk_engine / test_system_state）
# app/、handlers/、mod/、web_ui/、main.py 之呼叫次數：0
```
**影響**：ADR-0002 以「強制實施」的語氣描述全域熔斷、預算上限與 AI 評估實驗室；實作卻是**死碼**。這代表：a) 所有自主行為（SPARK 推播、主動動作）沒有本應存在的唯一防線；b) `tests/unit/test_autonomy_guard.py` 等測試全綠是「測到死碼」的偽保證——治理能力從未在生產路徑受驗證。這是本維度最重的單一問題，也是功能二（§4.2）必須先修的原因。
**建議修法**：Phase 1 將 guard 接進兩個 choke point：a) `handlers/spark/cog.py` 每輪行動派發前 `guard.check(...)`；b) BotBrain 任務提交前預算/熔斷檢查。同時把 ADR-0002 改為「Designed（尚未接線）」直至驗證完成（配套見 §7.1.3）。

### [P1 - High] 6.1.2 SPARK 對任何成員開放、最短週期 1 分鐘、無配額與每日上限
**位置**：`handlers/spark/nlp.py:1088`（週期解析下限）；`handlers/spark/cog.py:1461-1491`（建立/執行路徑）；全庫掃描 `max_triggers` 類上限常數 = 0 個。

```python
# 節錄（語意）：NLP 解析接受最小 "1 分鐘" 週期；建立 trigger 無量/無上限檢查
period_minutes = min_floor(...)   # nlp.py:1088 區域：最短 1 分鐘
```
**影響**：單一成員即可建立大量 1 分鐘 trigger → 每分鐘喚醒 checker（外部 API 打點 + LLM 推播），1,440 次/日/使用者起跳且無治理層攔（見 6.1.1）；對成本、對外伺服器、對成員打擾都是三輸。
**建議修法**：per-user/per-guild trigger 數上限；週期下限分級（LLM 型 ≥30 分鐘、通知型 ≥5 分鐘）；每日推播配額；admin 全域暫停。

### [P1 - High] 6.1.3 動作派發 fire-and-forget：無 semaphore、無追蹤、無 timeout（無界累積）
**位置**：`handlers/spark/cog.py:1480-1482`（派發）、`:1293`、`:1411`；`:1480-1491`（semaphore 每輪重建且只包檢查）。

```python
# 節錄（語意）：本輪檢查有併發上限；「動作」沒有任何上限與追蹤
async with self._sem:                     # 僅覆蓋 checker 檢查（且每輪重建）
    ...
asyncio.create_task(self._action_runner.run(trigger))   # 無 semaphore、無集合強引用、無 timeout
```
**影響**：動作（含 LLM 呼叫）並發無上限；失敗無重試與告警；任務可被 GC 靜默消失；與 6.1.2 疊加形成「推播炸彈」。
**建議修法**：動作走獨立長生命週期 semaphore + `self._tasks` 強引用集 + `asyncio.timeout`；失敗進重試佇列並計數告警。

### [P1 - High] 6.1.4 SPARK 單輪無 timeout（可永久停擺）＋ daily 任務無 catch-up ＋「立即測試」破壞狀態
**位置**：`handlers/spark/cog.py:1476`（單輪無 `asyncio.timeout` 包裹）；`handlers/spark/checkers/schedule_checker.py:237`（過期 daily 不補跑）；`handlers/spark/cog.py:887,1778`、`handlers/spark/web.py:176`（「立即測試」改寫 trigger 狀態）。

```python
# 節錄（語意）
await self._check_due_triggers()      # cog.py:1476：任一 checker 卡死（網路無 timeout）→ 整個迴圈停擺
...
# web.py:176 / cog.py:887,1778：「立即測試」將 last_triggered/run_count 寫回、關閉 one-shot 任務
```
**影響**：a) 外部 API 不回應時 SPARK 永久停擺且**無告警**（背景功能靜默死亡）；b) 使用者按「立即測試」即消耗正式任務（one-shot 被關閉）——語意錯誤；c) 跨日後 daily 任務不補跑，行為不可預測。
**建議修法**：單輪 `asyncio.timeout(60)` + checker 級 timeout；「測試」走 dry-run 不落狀態；catch-up 政策明確化（當日補一次並記錄）。

### [P1 - High] 6.1.5 推論優先序語意錯亂：背景工具與使用者回覆搶同一階，天氣永遠墊底
**位置**：`handlers/social/tools/chat_tools.py:263` 等（背景/輔助任務以 `PRIORITY_TAG_RESPONSE` 提交）；weather 任務 `priority=0`；同類硬編亦見 `handlers/social/utils.py:500-535`（`_summarize_context` 硬編 `priority=20`，繞過設定）。

**影響**：優先序不再是「使用者可感知延遲」的排序鍵——背景工具可瞬間插隊、天氣類任務永遠最後；head-of-line 行為不可預測，容量規劃與 SLO 無從談起（與維度一 §2.1.3 的單 worker 疊加）。
**建議修法**：優先級枚舉化（`INTERACTIVE / TOOL_BACKGROUND / MAINTENANCE`）並統一走設定；禁止魔術數字。

## 6.2 韌性與錯誤處理（背景系統）

### [P1 - High] 6.2.1 Worker 崩潰後 0.5 秒無退避重啟、日誌洗版
**位置**：`handlers/bot_brain/cog.py:207-212`。

```python
# 節錄（語意）：推論 worker 的無限迴圈
while True:
    try: ... await task
    except Exception:
        logger.exception(...); await asyncio.sleep(0.5)   # 固定 0.5s，無退避、無失敗上限
```
**影響**：持續性錯誤（磁碟滿、依賴壞）下每 0.5 秒刷一次堆疊——日誌爆量（`bot.log` 已 22.5MB 等級）、CPU 空轉、真故障被噪音淹沒。
**建議修法**：指數退避（0.5s → 60s cap）+ 連續 N 次失敗轉降級模式並告警。

### [P1 - High] 6.2.2 熱路徑 `except: pass` 黑洞
**位置**：`handlers/spark/cog.py:503-509`；`handlers/bot_brain/health_monitor.py:47-48,108-109,129-130`；`handlers/social/observability.py:664-665,734-735`。
**影響**：背景系統失敗靜默化——「正常運作」與「什麼都沒在跑」不可區分（治理/巡檢/觀測自身吞錯，最為諷刺）。
**建議修法**：至少 `logger.debug(exc_info=True)`；關鍵路徑改明確處理 + 失敗計數指標。

### [P1 - High] 6.2.3 `DiscordChannelHandler`：logging handler 內直接 `loop.create_task`（非執行緒安全）＋ markdown fence 注入
**位置**：`handlers/bot_brain/health_monitor.py:19-31`。

```python
# 節錄（語意）：logging.Handler.emit() 於「呼叫端執行緒」執行
loop = getattr(self, '_loop', None)
if loop and not loop.is_closed():
    loop.create_task(self._send(msg))     # ← 非執行緒安全；未經 call_soon_threadsafe
```
**影響**：日誌來源若在 `to_thread`/executor 執行緒（本平台大量使用），`create_task` 屬未定義行為（任務遺失/競態）；且訊息直插 Discord，內容含 ``` fence 時可**逃逸排版**（日誌內容=使用者可控資料時形成注入面）。
**建議修法**：改 `asyncio.run_coroutine_threadsafe` 或 `queue.Queue` + 消費協程；輸出前中和 fence。

### [P2 - Medium] 6.2.4 Trace 斷鏈：`InferenceTask` 缺 `trace_id`（與維度一 §2.1.6 同項之治理視角）
**位置**：`handlers/bot_brain/models.py:6-29`；`handlers/bot_brain/cog.py:475`。
**影響**：LLM→工具→回覆的端到端追溯在推論層斷掉；事故調查退化為時間戳猜謎，SLO 無法歸因。
**建議修法**：`InferenceTask` 必填 `trace_id`（呼叫方注入），推論日誌全量攜帶。

### [P2 - Medium] 6.2.5 雙限流器與 `from_core` 繞道
**位置**：`handlers/social/rate_limiter.py:11-31,66`（自建限流器）vs `core/guard.py`；`handlers/social/router.py:31-37`（`from_core=True` 分支）。
**影響**：同一使用者經不同入口獲得不同限額；治理規則無法單點調整/觀測/報表（與 6.1.1 疊加：無全域熔斷時，限流是唯一防線，卻有兩套）。
**建議修法**：`core/guard.py` 為唯一來源；`from_core` 僅允許放寬 UI 級限制，不得繞過全域熔斷。

### [P2 - Medium] 6.2.6 觀測寫入含完整 PII：`raw_input` / `cot` / `reply` 全量落 JSONL
**位置**：`handlers/social/observability.py:352-361`。
**影響**：思考鏈與完整對話長期滯留（保留清理對觀測類覆蓋不全，見維度四 §5.5.2）；擴大任何讀檔/外洩弱點的破壞半徑；GDPR 資料最小化原則直接衝突。
**建議修法**：PII 自動 redact（密鑰/ID/email 樣式）+ 可配置採樣與保留期；敏感場景只記 hash/摘要。

### [P2 - Medium] 6.2.7 觀測佇列無上限、`flush` 僅測試呼叫（併記，程序結束遺失）
**位置**：`handlers/social/observability.py:151,218-246,272-275`（維度一 §2.3.3 同項；此處記其可靠性語意）。
**建議修法**：有界佇列 + 溢出計數 + 正常關閉流程週期 flush。

## 6.3 Prompt Injection 與模型輸出入安全（AI 治理）

### [P1 - High] 6.3.1 使用者輸入直插 planner prompt（無隔離、無不可信標記）
**位置**：`handlers/social/dispatch/prompts.py:86-127`（`context`/`user_query` 內插）。
**影響**：使用者可用「忽略以上指令」類文字操縱 planner（工具誤觸、越權描述、繞過安全策略）；社群上下文（含他人訊息）同樣未標記為不可信資料——注入面即所有輸入面。
**建議修法**：資料/指令分離（明確 delimiter + 「以下為使用者提供之資料」聲明）；planner 輸出 schema 驗證 + 工具參數白名單。

### [P1 - High] 6.3.2 網頁內容僅 XML 轉義即入 prompt
**位置**：`handlers/social/tools/search_tools.py:70-87`（擷取）→ `handlers/social/tools/chat_tools.py:119-123`（注入）。
**影響**：第三方網頁是最廉價的 injection 載體（SEO 投毒即可定向影響）；XML 轉義只防結構破壞，不防語義注入。
**建議修法**：外部內容包 `<untrusted>` wrapper + 顯式聲明；工具結果預設只用於摘要輸出，不進入 planner 指令段。

### [P1 - High] 6.3.3 Observation 未轉義回填（ReAct 遞歸注入放大）
**位置**：`handlers/social/dispatch/integrated.py:368-383`。
**影響**：工具輸出（可含外部網頁/使用者內容）原樣回填下一步 prompt——一段惡意網頁可在多步迴圈中反覆強化。
**建議修法**：與 6.3.2 同批：observation 統一經 sanitizer + 長度上限 + 不可信標記。

### [P1 - High] 6.3.4 NSFW/圖片安全判定含「他人上下文」且未按頻道分流
**位置**：`handlers/social/tools/chat_tools.py:67-75,100,140-141`；`handlers/social/tools/image_tool.py:450-457`。
**影響**：判定輸入可含非發話者訊息（跨使用者畫像與隱私風險）；未按頻道 NSFW 設定分流，可能在不適頻道生成/描述敏感內容（信任與合規風險）。
**建議修法**：判定輸入最小化（當前訊息 + 當前頻道設定）；輸出前雙重檢查；成人向操作留審計。

### [P2 - Medium] 6.3.5 Mention 未消毒（LLM 輸出可產生 mention）
**位置**：`handlers/social/utils.py:888-899`；`handlers/social/streaming_thinking.py:400`。
**影響**：被操控的輸出可 @提及（打擾/濫用）；意外觸發其他機械人形成迴圈。
**建議修法**：`allowed_mentions` 收斂 + 輸出後處理中和。

## 6.4 觀測與營運就緒

### [P2 - Medium] 6.4.1 背景系統（SPARK/記憶/nightly）零結構化觀測
**位置**：`handlers/spark/cog.py`（迴圈無 trace 產生/傳遞，失敗僅 logger）；`handlers/memory/nightly_cog.py`（無結構化執行報告）。
**影響**：平台最差異化的「主動性」與「記憶整合」恰恰是最不可觀測的部分——無 SLO/SLI 可定義；功能二（§4.2）導入前必須先補。
**建議修法**：每輪產生 trace + 執行摘要（成功/失敗/跳過/耗時/預算用量）落觀測；已存在的 WebUI 面板模式可直接承接。

### [P2 - Medium] 6.4.2 健康巡檢面窄（僅驗 Ollama）＋ 效能告警死碼
**位置**：`handlers/bot_brain/health_monitor.py:146-209`（`record_tok_speed` 全庫零呼叫端）。（維度一 §2.1.6 同源，此處為營運視角。）
**建議修法**：巡檢覆蓋雲端 provider 配額/延遲、磁碟、DB 尺寸、SPARK 心跳、worker 佇列深度；接既有告警面板。

### [P3 - Low] 6.4.3 事故演練與降級路線缺失
**位置**：全域盤點（無還原演練腳本；無「全 provider 失效」安全模式）。
**建議修法**：季度備份還原演練；定義 D1（單供應商故障）/D2（全 LLM 故障→本地模型/Mock）/D3（DB 損毀）手冊。

### [P3 - Low] 6.4.4 文件自評與現實落差未管理
**位置**：`Docs/`（Deep-Dive 系列自評 8.36/10 與本報告發現不符）；`Docs/adr/ADR-0002-...`（「已強制實施」）。
**影響**：以「已交付」語氣撰寫的未驗證設計會誤導維護者與審計者（本報告已發現多處同型問題）。
**建議修法**：文件狀態三段制（Designed / Implemented / Verified）；未經 CI 驗證者一律標注。

## 6.5 部署與供應鏈

### [P2 - Medium] 6.5.1 容器化基本盤缺口：root、`COPY . .` 無 `.dockerignore`、無 HEALTHCHECK、無資源限制、直曝 `0.0.0.0:8763`
**位置**：`Dockerfile:1-32`；`.dockerignore` **實測不存在**；`docker-compose.yml:1-15`。

```dockerfile
# Dockerfile 節錄
ENV WEB_UI_HOST=0.0.0.0 \        # L6：預設即對外
    WEB_UI_PORT=8763
...
COPY . .                          # L26：.env / data / backups / __pycache__ 全數入鏡
CMD ["python", "main.py"]        # L32：無 USER（root）、無 HEALTHCHECK
```
```yaml
# docker-compose.yml 節錄
ports:
  - "8763:8763"                  # 全介面映射；與維度四未認證端點疊加
# 無 healthcheck、無 mem_limit、無唯讀根
```
**影響**：映像含 `.env` 與資料備份（秘密＋PII 固化進 layer）；容器 root 執行（逃逸後果放大）；ML 負載無資源上限可 OOM 拖垮宿主；8763 直曝公網（維度四端點風險即刻兌現）。
**建議修法**：補 `.dockerignore`（`.env`、`data/`、`__pycache__`、`.git`、`Docs/`）；加 `USER app` 與 `HEALTHCHECK`（打 `/api/version`）；compose 加 `mem_limit`、埠綁 `127.0.0.1`（或前置反向代理）。

### [P3 - Low] 6.5.2 依賴治理：寬鬆下限、無 hash 鎖定、重相依常駐
**位置**：`requirements.txt:1-55`（`Pillow>=12.3.0`、`selenium>=4.18.0`、`TTS>=0.22.0` 等）。
**影響**：重現性低（上游任意更新可破壞佈署）；無 hash 驗證之供給鏈盲區；未用模組的重相依仍全程安裝（映像肥大）。
**建議修法**：`pip-compile` 鎖定（含 hash）；可選模組拆 extras；CI 做 `pip-audit`。

## 6.6 維度五正面確認
| 項目 | 位置 | 說明 |
|---|---|---|
| WAL/checkpoint 全局機制 | `core/db.py:51-59` ＋ `web_ui/server.py` checkpoint 端點 | 關機/檢查點流程存在 |
| Trace ID 基建 | `handlers/social/observability.py`（`new_trace_id`） | 觀念正確（斷鏈在推論層，見 6.2.4） |
| 稽核與觀測雛形 | `admin_audit.jsonl`；觀測面板 API | 留痕意識存在（缺保留與遮罩） |
| 測試廣度 | `tests/unit/` 81 檔 | 覆蓋面廣；但多為模組孤立測試，對治理接線保護不足（6.1.1） |
| 維運腳本化意識 | `handlers/memory/nightly_cog.py:752-755` 接 retention worker | 深夜維護班表已自動化 |

## 6.7 維度五問題匯總表

| ID | 嚴重度 | 主題 | 關鍵位置 |
|---|---|---|---|
| 6.1.1 | **P0** | 治理三件套零接線（autonomy_guard/risk_engine/system_state 死碼） | `core/autonomy_guard.py:120`、`core/risk_engine.py`、`core/system_state.py`、`Docs/adr/ADR-0002-...` |
| 6.1.2 | P1 | SPARK 開放全員、最短 1 分鐘、無配額/每日上限 | `handlers/spark/nlp.py:1088`、`spark/cog.py:1461-1491` |
| 6.1.3 | P1 | 動作 fire-and-forget 無界累積 | `handlers/spark/cog.py:1480-1482,1293,1411` |
| 6.1.4 | P1 | SPARK 單輪無 timeout／daily 無 catch-up／立即測試破壞狀態 | `handlers/spark/cog.py:1476,887,1778`、`checkers/schedule_checker.py:237`、`spark/web.py:176` |
| 6.1.5 | P1 | 推論優先序語意錯亂（背景工具搶階、weather=0） | `handlers/social/tools/chat_tools.py:263`、`handlers/social/utils.py:500-535` |
| 6.2.1 | P1 | worker 0.5s 無退避重啟洗版 | `handlers/bot_brain/cog.py:207-212` |
| 6.2.2 | P1 | `except: pass` 黑洞（spark/health/observability） | `handlers/spark/cog.py:503-509`、`health_monitor.py:47-48,108-109,129-130`、`observability.py:664-665,734-735` |
| 6.2.3 | P1 | logging handler `loop.create_task` 非執行緒安全 ＋ fence 注入 | `handlers/bot_brain/health_monitor.py:19-31` |
| 6.3.1 | P1 | 使用者輸入直插 planner prompt | `handlers/social/dispatch/prompts.py:86-127` |
| 6.3.2 | P1 | 網頁內容僅 XML 轉義入 prompt | `handlers/social/tools/search_tools.py:70-87`、`chat_tools.py:119-123` |
| 6.3.3 | P1 | Observation 未轉義回填 | `handlers/social/dispatch/integrated.py:368-383` |
| 6.3.4 | P1 | NSFW 判定含他人上下文、無頻道分流 | `handlers/social/tools/chat_tools.py:67-75,100,140-141`、`image_tool.py:450-457` |
| 6.2.4 | P2 | Trace 斷鏈（InferenceTask 缺 trace_id） | `handlers/bot_brain/models.py:6-29`、`cog.py:475` |
| 6.2.5 | P2 | 雙限流器與 `from_core` 繞道 | `handlers/social/rate_limiter.py:11-31,66`、`router.py:31-37` |
| 6.2.6 | P2 | 觀測寫入含完整 PII（raw_input/cot/reply） | `handlers/social/observability.py:352-361` |
| 6.2.7 | P2 | 觀測佇列無上限、flush 僅測試呼叫 | `handlers/social/observability.py:151,218-246,272-275` |
| 6.3.5 | P2 | Mention 未消毒 | `handlers/social/utils.py:888-899`、`streaming_thinking.py:400` |
| 6.4.1 | P2 | 背景系統零結構化觀測 | `handlers/spark/cog.py`、`handlers/memory/nightly_cog.py` |
| 6.4.2 | P2 | 健康巡檢面窄＋效能告警死碼 | `handlers/bot_brain/health_monitor.py:146-209` |
| 6.5.1 | P2 | 容器化缺口（root/無 .dockerignore/無限制/直曝） | `Dockerfile:1-32`、`docker-compose.yml:1-15` |
| 6.4.3 | P3 | 事故演練與降級路線缺失 | 全域 |
| 6.4.4 | P3 | 文件自評與現實落差未管理 | `Docs/` |
| 6.5.2 | P3 | 依賴無鎖定/hash、重相依常駐 | `requirements.txt:1-55` |

# 7. 分階段修復與導入路線圖 (Roadmap)

> 原則：**Phase 1 封血（資安與軟刪除）→ Phase 2 止漏（效能與韌性）→ Phase 3 增值（架構與新功能）**。每階段均有可驗證的出口標準（Exit Criteria），且所有 P0 必須在 Phase 1 全數核銷——**未核銷前不得對外開放任何介面（WebUI/Livestream）**。

## 7.1 Phase 1（第 1–2 週）：資安與軟刪除止血

### 7.1.1 資安封血（對應維度四 P0/P1）
- [x] A1 管理員 token 改 `HttpOnly + Secure + SameSite` cookie＋CSRF 防護；移除 `localStorage` 存取（`app.js:38,2582`、`server.py:1066-1073`）✅ R1（commit `aa78848`）
- [x] A2 `/api/mod/<mod_key>/view` 收認證＋輸出轉義＋加 CSP/nosniff/HSTS 安全標頭（`server.py:2253-2267,331,381-415`）✅ R1（收認證＋安全標頭；「輸出轉義」子項併入 A9/R4 追蹤）
- [x] A3 靈魂檔名白名單＋`Path.resolve()` 邊界檢查；敏感 key 過濾反轉為白名單（`settings.py:813-842`、`server.py:1353`）✅ R2（commit `b0991f2`）
- [x] A4 未認證端點收斂：`/api/system/tasks`、`/api/bot/status`、`/api/system/audit/*`、`/api/self_monitoring/*`、`/api/logs` 一律登入；`/api/logs?file=` 白名單（`server.py:344-366,773-886,1863-1885,2004-2086`）
- [x] A5 `public_routes` 結構化（method+auth）＋ deny-by-default；`game_center`/`livestream` 端點補驗證（`server.py:306-314,338-341`）
- [x] A6 XFF 僅信任受控代理、取最右值；鎖定表 TTL＋上限（`server.py:60-77,56-57`、`mod/game_center/web.py:55-64`）
- [x] A7 綁定失敗 fail-fast；livestream 綁 `127.0.0.1`＋認證；密碼改 hash 注入不再列印（`server.py:2789-2797`、`mod/livestream/config.py:32-41`、`settings.py:711-721`）✅ R2（commit `b0991f2`）
- [x] A8 錯誤訊息統一化（`str(exc)` 全列表清除；spark/livestream/context_menu 一併）✅ R2（commit `b0991f2`；清單內站點全清；計畫外同類站點 `mod/stock_trader/web.py`、`mod/feed_sentinel/web.py`、`mod/deep_search/web.py` 與 `mod/game_center/web.py` 已於複審 1 全數清理，commit `c4257ec`，新增靜態＋行為回歸 5 項）
- [x] A9 前端 XSS：導入 DOMPurify/`textContent` 重構熱點、`escHtml` 補單引號、禁新 `innerHTML`（ESLint）（R4 ✅ 熱點轉義＋工具統一；R5 ✅ DOMPurify vendored＋`sanitizeHtml` 入口＋預算閘門替代 ESLint＋inline handler 例外清單文件化；事件代理化與 CSP 移除 `unsafe-inline` → 後續回合／複審追蹤）

### 7.1.2 軟刪除一致性（對應維度四 5.2 全節）
- [x] B1 `/forget` 交易化全清除（facts/relations/chat/JSON/archive）＋稽核＋「全成功才聲明」（`handlers/memory/cog.py:201-208`）✅ R6 `5c0a831`（`purge_user_data` 清除管線：facts/profiles 軟刪＋relations 匿名化軟刪＋chat_messages 標記＋JSON 快照/JSONL 歸檔原子重寫＋admin_audit 稽核；逐範圍結果彙總，全成功才回覆「已清除」、部分失敗回報範圍與重試指引；「交易級」跨儲存語意見計畫現實校正 16）
- [x] B2 `chat_messages` 補 `is_deleted/deleted_at`＋保留政策＋接 `DataRetentionWorker`（`chat_logger.py:43-56`）✅ R7 `0360c92`（欄位/讀取過濾 R6 已就緒；R7 於 worker 落保留政策：180 天軟刪＋30 天寬限期後分批物理清運，夜間自動執行、`db_manager cleanup` 手動互補）
- [x] B3 廢全表 `DELETE`，改 UPSERT＋差異化軟刪＋`(user_id, fact)` 唯一索引（`storage.py:720,814`，範例見 §5.2.9）✅ R6 `5c0a831`（`upsert_user_facts` 差異化 UPSERT＋`soft_delete_all`；`idx_user_fact` 唯一索引＋冪等去重遷移；單一增量可復活／全庫快照 `resurrect=False` 不覆寫刪除標記＝nightly 不復活，見現實校正 17）
- [x] B4 `world_memory` 全查詢補 `is_deleted=0`＋軟刪；`schedule_storage` 補欄位；roast/JSON 回灌不得覆寫刪除標記 ✅ R7 `0360c92`（世界記憶查詢面全補過濾＋`soft_delete_event`＋清理改軟刪＋快取寫入同步；schedule_tasks 冪等補四柱＋軟刪；roast 回灌改 `ON CONFLICT DO NOTHING`；user_memory JSON 備援僅首遷可用）
- [x] B5 retention 補 `admin_audit`（政策 `:52` 落地）＋路徑絕對化＋JSONL 原子輪轉（`retention_worker.py`）✅ R7 `0360c92`（admin_audit 90 天實際清理；`base_data_dir` 預設絕對路徑 `settings.DATA_DIR`；`chat_logs/temp` 正名；temp＋`os.replace` 原子替換）
- [x] B6 備份加密＋輪替＋刪除權連動處置（`data/backups/manual_db_backup_*`）✅ R7 `0360c92`（Fernet 加密備份不落明文＋保留 5 份自動輪替＋`restore_backup`/CLI 可還原；「連動處置」＝加密靜態＋殘餘副本隨輪替淘汰，語意見計畫現實校正 19）

### 7.1.3 治理接線（最小可用版，對應維度五 6.1）
- [x] C1 `autonomy_guard` 接入 SPARK 行動派發＋BotBrain 提交（預算/熔斷基本值）（`core/autonomy_guard.py`）✅ R8 `8d9a6af`（`check()` 單一入口實際串接 `RiskPolicyEngine`／`SystemStateNormalizer`；SPARK 派發前 `spark_push`、BotBrain 自主任務 `bot_brain_autonomous`，拒絕即不派發／以空回覆 fail-closed）
- [x] C2 SPARK 配額：trigger 數上限＋週期分級＋每日推送上限（`spark/nlp.py:1088`、`spark/cog.py:1461-1491`）✅ R8 `8d9a6af`（per-user 10 個 trigger、LLM 型 ≥30 分／通知型 ≥5 分、每日 30 次；集中於 `service.create_trigger`＋`spark/quota.py`，涵蓋 NLP／Modal／Web 全入口）
- [x] C3 動作派發 semaphore＋強引用＋timeout（`spark/cog.py:1480-1482`）✅ R8 `8d9a6af`（長生命週期 `Semaphore(SPARK_MAX_CONCURRENT_ACTIONS)`＋`_action_tasks` 強引用＋`wait_for` timeout；`cog_unload` 取消未完成動作）
- [x] C4 ADR-0002 與 Deep-Dive 文件狀態更正（Designed/Implemented/Verified 三段制）✅ R8 `8d9a6af`＋`28ce720`（ADR-0002 Status 改三段制並標記 R8 驗證；本報告 §7.1.3／§7.5 同步核銷）

**Phase 1 出口標準**：P0 = 0；`/forget` 端到端測試（含備份處置與失敗路徑）通過；XSS 回歸測試（模組視圖＋前端熱點）全綠；SPARK 超限拒發測試通過；未認證端點清單複掃為空。

## 7.2 Phase 2（第 3–6 週）：效能與韌性止漏

- [x] D1 串流逾時模型重做：`total=None + sock_read=chunk`＋外層 `asyncio.timeout`；重試指數退避（`handlers/bot_brain/client.py:499-509`、`cog.py:409-438`）
- [x] D2 取消傳播：`InferenceTask` 持有 `asyncio.Task` 強引用，改 `task.cancel()`（`cog.py:231-240`）
- [x] D3 雲端 per-user + 全域雙層 semaphore；本地 worker 重試移出熱路徑（`cog.py:199-206`）
- [x] D4 錯誤分類結構化：`LLMStreamError/AuthError/RateLimitError/ContentFiltered`（`client.py:546-554`、`cog.py:409-427`）
- [x] D5 SPARK：單輪 `asyncio.timeout`＋checker timeout＋catch-up＋「立即測試」dry-run（`spark/cog.py:1532-1662`、`spark/service.py:115-190`、`schedule_checker.py:203-310`）✅ R11 `0522bd9`（checker 逾時 45s／單輪 60s 皆可設定，逾時回傳結構化 `timeout=True` 且不落狀態；daily 補跑窗 120 分鐘可覆寫、同日不重複；三個測試入口一律 dry-run；另附每輪 trace 與執行摘要）
- [x] D6 fire-and-forget 全面回收：記憶寫入 await/回調、插話任務強引用、觀測有界佇列＋關閉 flush（`storage.py:823-828`、`handlers/social/__init__.py:81-91`、`observability.py:151,218-246`）
- [x] D7 專用執行器：`heavy_io_pool`/`db_pool`（music/stock/memory/db 全替換裸 `run_in_executor(None)`；`core/concurrency.py` 新設）
- [x] D8 熱路徑阻塞 I/O 清單逐項 `to_thread`（維度一 §2.2 表 13 項）；`metrics.get_summary` 預熱（`server.py:1698-1699`）✅ R12 前半 `7d3a2b1`（WebUI／sys_inspector 群：gzip、settings 人設 `mtime_ns` 快取、稽核／mod_config 寫檔、shutdown 訊號、checkpoint 與延遲退出 async task、git 子進程背景預熱＋`sw.js` 快取、metrics 預熱與 `to_thread`＋bootstrap 鎖、audit SQLite `to_thread`；事件循環心跳 <50ms 回歸測試 9 項）；R13 後半 `75100a8`（tools／背景群：RSS `loop.getaddrinfo`、search SSRF `to_thread`、trafilatura 專用池＋bounded slot＋逾時丟棄、股市全域併發上限、vibe 批次／單頻道落盤 `to_thread`、heartbeat 每輪任務上限；心跳 <50ms 回歸測試 15 項）
- [x] D9 SPARK 優先序枚舉化＋worker 指數退避＋`except: pass` 清理（`bot_brain/cog.py:207-212`、`spark/cog.py:423,972,1092,1119,1130,1154,1304` 等）✅ R11 `0522bd9`（`settings.Priority`＋`priority_for()` 清除 `priority=20`／`priority=0` 魔術數字；worker 指數退避已於 R10 落地；spark／health_monitor／observability 熱路徑 `except: pass` 全改 `logger.debug(exc_info=True)`）
- [x] D10 記憶檢索上線：FTS5＋Ollama embeddings＋權重融合（功能一；含軟刪過濾回歸）✅ R14 `1ff746c`（FTS5 基礎：`facts_fts` 外部內容表＋trigram／unicode61 降級、同步 triggers 與回填工具、FTS5＋短詞 LIKE 混合召回、硬性事實／Profile 軟刪過濾、bm25／時效／重要性／來源權重骨架、評測集 v1 與 16 項單元測試；recall 1.000、誤召 0.000、已刪召回 0.000、P95 2.0ms）＋R15 `9a37761`（向量融合：Ollama `/api/embeddings` 佇列／指數退避重試、`fact_embeddings` float32 BLOB＋失效 triggers、Python cosine／可選 `sqlite-vec` 降級、bm25＋cos_sim＋recency＋affinity＋importance＋source_priority 融合、affinity 正規化與軟刪排除、Prompt 動態尾段注入且靜態前綴不變、`tools/backfill_embeddings.py`；評測集 v2 8 案例 recall 1.000、誤召 0.000、已刪召回 0.000、P95 2.9ms；單元測試 22 項全綠）

**Phase 2 出口標準**：長生成情境下 tag 回應 P95 不再被單一 180s 任務拖死；SPARK 連續 7 天無停擺且失敗可見（摘要式觀測）；記憶檢索 P95 < 300ms 且不召回已刪資料；高負載演練（併發 tag×10）無失控並發。

## 7.3 Phase 3（第 7–16 週）：架構收斂與新功能增值

- [x] E1 架構護欄：`web_ui` 納入 `.importlinter`；`handlers⇄mod` 解耦契約；豁免清單只減不增（維度二 §3.1）
- [x] E2 雙註冊表合一＋工具新增流程樣板化（≤1 天且不動核心）（§3.2）
- [x] E3 狀態外置介面：`SessionStore`/`RateLimitStore`/`LoginLockout`；SPARK 單 leader DB 租約（§3.4）
- [x] E4 遷移治理：`user_version` 版本化＋dry-run；三軌排程表收斂（§3.5、§5.2.8）✅ R18（`core/migrations.py`、`migrations/m001–m003`、`tools/migrate.py`、`tools/unify_schedules.py`；WAL 安全備份＋rollback 安全快照）
- [x] E5 主動關懷引擎（功能二）：guard 閘門＋opt-in/opt-out＋預算＋面板 ✅ R19–R20（`handlers/care/` 引擎／額度／三軌候選；`/care` 指令、WebUI 面板、伺服器灰度與沉默率回饋循環）
- [ ] E6 動態情感狀態機（功能三）＋語氣防崩壞錨點（功能四）
- [ ] E7 多模態深化 M2/M3（語音管線現代化、低延遲對話）（功能五）
- [ ] E8 觀測體系：背景系統指標化、事故手冊（D1–D3）、季度還原演練、文件三段制
- [ ] E9 容器硬化：`.dockerignore`＋非 root＋HEALTHCHECK＋資源限制＋反代；依賴鎖定（hash）

**Phase 3 出口標準**：新增工具/外掛 ≤1 天且零核心改動（以一個真實新工具驗收）；雙實例佈署無重複推播、無 session 漂移；新功能（1–5）至少三項上線且各有評測集；月度 `eval_runner` 回歸綠燈。

## 7.4 里程碑總覽

| 週次 | 階段 | 主題 | 關鍵交付 |
|---|---|---|---|
| W1–W2 | **Phase 1** | 封血 | 攻擊鏈斷鏈（A1–A9）、軟刪一致（B1–B6）、治理接線（C1–C4） |
| W3–W6 | **Phase 2** | 止漏 | 逾時/取消/並發模型（D1–D4）、SPARK 韌性（D5,D9）、執行器與 I/O（D7,D8）、記憶檢索（D10） |
| W7–W16 | **Phase 3** | 增值 | 架構收斂（E1–E4）、新功能（E5–E7）、觀測與部署（E8–E9） |

## 7.5 P0 核銷追蹤（複審用）

- [x] P0-1｜公開反射式 XSS（mod view）：端點收認證＋轉義＋CSP（`web_ui/server.py:2253-2267,331`）✅ R1+R4（`aa78848` 將 `/api/mod/<mod_key>/view` 納入管理員 session、移除公開放行；R4 `ab81749` 於模組視圖輸出補 `html.escape` 並加非法 mod_key 400 守門；CSP 由 R1 部署、`'unsafe-inline'` 例外清單於 R5 `bb7c979` 文件化並以預算閘門防擴大）
- [x] P0-2｜設定任意檔覆寫＋token localStorage → RCE 鏈：三斷點全修（`settings.py:813-842`、`app.js:38,2582`、`server.py:1353`）✅ R1+R2（token localStorage/app.js＝R1 `aa78848`；靈魂檔邊界＋設定寫入白名單＝R2 `b0991f2`；複審 1 實測攻擊鏈）
- [x] P0-3｜`/forget` 不完整＋fire-and-forget：交易化全清除＋真誠回覆（`handlers/memory/cog.py:201-208`）✅ R6 `5c0a831`（清除管線涵蓋 facts/profiles／relations／chat_messages／JSON 快照與 JSONL 歸檔／稽核；刪除路徑同步化、失敗即回報；備份處置依計畫屬 R7/B6）
- [x] P0-4｜`chat_messages` 無刪除/保留途徑：補欄位＋政策＋接 worker（`chat_logger.py:43-56`、`retention_worker.py`）✅ R7 `0360c92`（180 天軟刪政策＋30 天寬限物理清運，夜間週期執行；查詢面自 R6 起過濾 `is_deleted`）
- [x] P0-5｜治理三件套零接線：`autonomy_guard`/`risk_engine` 接生產 choke point（`core/autonomy_guard.py:120`、`Docs/adr/ADR-0002-...`）✅ R8 `8d9a6af`（`AutonomyGuard.check()` 實際串接 `RiskPolicyEngine`／`SystemStateNormalizer`；SPARK 行動派發與 BotBrain 提交兩個生產 choke point 接線；ADR-0002 三段制見 `28ce720`）
- [x] 複審 1（2026-10-21）：P0/P1 複掃＋攻擊鏈回歸 **166 passed**；A8 計畫外站點清零、`localStorage` token 0 命中、全表 `DELETE FROM user_facts` 0 命中；詳見 `execution_plan.md §5`（修復 `c4257ec`）。
- [x] 複審 2（2026-10-21）：Phase 2 增量審 **160 passed**；v1/v2 評測 deleted_recall 皆 0.000、P95 5.0ms／4.2ms；深度研究取消追蹤缺口已修（`c4257ec`），詳見 `execution_plan.md §5`。

## 7.6 結語與再審機制

本報告在**唯讀**條件下完成（未新增/修改/刪除任何業務程式碼、未執行具副作用指令、未對資料庫做任何寫入），所有結論均可回溯至列出的檔案與行號。建議之複審機制：

1. **Phase 1 後複審**：以相同方法學複掃本報告 P0/P1 清單（§7.5＋各維度匯總表），並實際運行攻擊鏈回歸測試；
2. **Phase 2/3 後增量審**：聚焦新增面（新任務佇列、檢索層、主動引擎）之三類驗證——預算強制、刪除單調性、逾時/取消傳播；
3. **持續機制**：將 `.importlinter` 豁免數、未認證端點數、`str(exc)` 出現數、軟刪一致性測試數納入 CI 儀表板，只准單向收斂。

> 本平台距離「企業級上線」的差距不在功能，而在**邊界（邊界＝攻擊面）與治理（治理＝可信度）**。好消息是：本報告的 P0 全部是「小改動、大收益」——五個核銷項中，四個可在 2 人日內完成、全部可在兩週封血。路徑已明，見上。

*— 報告完（2026-10-19，基於 commit `eefe7fc`）*
