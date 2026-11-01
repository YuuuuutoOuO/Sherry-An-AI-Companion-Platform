# 雪莉 Sherry 平台——修復執行計畫 (Execution Plan)

## Remediation Runbook：R1–R27 回合制施工與驗收

| 項目 | 內容 |
|---|---|
| **建立日期** | 2026-10-19 |
| **依據** | `Docs/plans/26.10.19/deep_analysis_report.md`（基準 commit `eefe7fc`） |
| **對應章節** | 報告 §7 分階段路線圖（Phase 1 封血 → Phase 2 止漏 → Phase 3 增值） |
| **用途** | 將路線圖切分為 **27 個可獨立執行、獨立驗收的工作回合（Run）**，供逐回合指派與核銷 |
| **全程約束** | **不破壞既有功能**：每回合結束時受影響測試必須全綠；**一回合一 commit**；不得跨回合偷跑未列項目 |

---

## 0. 使用方式與回合規範

### 0.1 什麼是一個「回合（Run）」

一個 Run ＝ 一個獨立工作階段 ＝ 一份「施工＋驗收」的最小單位：

- **建議規模**：≤ 15 個檔案修改、≤ 2 個 PR 概念；
- 具備明確的**出口驗收**——未達成不得進入下一回合；
- 產出**一個 commit**，訊息帶核銷 ID（例：`fix(P0-2): soul path whitelist + sensitive key deny`）。

### 0.2 開場指令模板（可直接複製使用）

```text
請執行 execution_plan.md 的 R{n}。
要求：
1) 先讀取報告對應節次＋本檔案 R{n} 條目
2) 先補「會失敗的重現測試」（P0 強制；P1 建議）
3) 修改後跑受影響測試子集，回報 diff 摘要與驗收結果
4) 一回合一個 commit；完成後更新報告 §7 與本檔案的核銷勾選
```

### 0.3 每回合的完成定義（DoD）

1. **重現測試**由紅轉綠（P0 強制附帶；P1 建議附帶）；
2. **受影響測試子集全綠**（既有測試不得壞；若必須改動既有測試，需在 commit 說明理由）；
3. **Commit 完成**且訊息含核銷 ID；
4. **核銷更新**：報告 §7 對應 `- [ ]` 與本檔案對應回合同步勾選；
5. **計畫維護**：如發現計畫與現實不符 → **先停下更新本檔案，再繼續施工**。

### 0.4 硬性依賴關係

| 依賴 | 原因 |
|---|---|
| **R1 → R4/R5** | 認證改造與前端同檔群（`app.js`/`server.py`），避免同檔衝突 |
| **R2 → R24** | livestream 綁定與秘密治理先行，語音/串流才安全 |
| **R6 → R14** | 軟刪核心先於記憶檢索，否則檢索召回已刪資料 |
| **R8 → R19** | 治理接線先於主動關懷引擎 |
| **R3 → R17** | 邊界收斂先於狀態外置（限流/鎖定依賴已修好的 XFF 語意） |
| R16 起（Phase 3） | E1–E2 優先，其餘可視人力平行拆分 |

---

## 1. 回合總覽

| 階段 | 回合 | 次數 | 主題 | 核銷對應 |
|---|---|---|---|---|
| **Phase 1 封血** | R1–R8 | 8 | 資安、軟刪除、治理接線 | 全部 P0 ＋ 報告 §7.1 A/B/C |
| **Phase 2 止漏** | R9–R15 | 7 | 效能、韌性、記憶檢索 | 報告 §7.2 D1–D10 |
| **Phase 3 增值** | R16–R27 | 12 | 架構收斂、新功能、運維 | 報告 §7.3 E1–E9 |
| **合計** | | **27** | | |

> - **最低消路徑**：**R1、R2、R6、R7、R8 五個回合即可核銷全部 5 個 P0**。
> - **壓縮選項**（時間吃緊時）：R4+R5 合併、R12+R13 合併、R21+R22 合併、R26+R27 合併 → 總量降至 **23 回合**。

---

## 2. Phase 1：封血（R1–R8）

### R1 認證硬化與安全標頭

- **目標**：切斷「XSS → 竊取 token」路徑，封閉公開渲染端點。
- [x] 管理員 token 改 `HttpOnly + Secure + SameSite` cookie＋CSRF；移除 `app.js` 對 `localStorage` 的讀寫（`web_ui/static/app.js:38,2582`）
- [x] `/api/mod/<mod_key>/view` 納入管理員 session（移除 `web_ui/server.py:331` 公開放行）
- [x] 新增安全標頭中介層：CSP（暫行 `script-src 'self' 'unsafe-inline'`，見下方現實校正）、`X-Content-Type-Options: nosniff`、`X-Frame-Options`、`Referrer-Policy`、（HTTPS 時）HSTS
- [x] cookie 補 `secure=True`（`web_ui/server.py:1066-1073`）
- [x] 回歸測試擴充：未登入 401、cookie 屬性、標頭齊備（`tests/unit/test_web_server_auth.py`）
- **主要檔案**：`web_ui/server.py`、`web_ui/static/app.js`、`tests/unit/test_web_server_auth.py`
- **出口驗收**：未登入 GET mod view 被拒；瀏覽器登入/登出流程正常；`localStorage` 無 token；回應含 CSP。
- **核銷**：報告 §5.1.1、§5.1.2（A1 斷點）、§5.3.4、§7.1.1 A1/A2
- **完成註記（2026-10-19，commit `aa78848`）**：8 項新增回歸測試先紅後綠；受影響子集 118 passed（web_server_auth/security_deep_audit/security_fixes/p0/livestream/roast/module_registry/config_and_concurrency）；全套件 727 passed（4 項既有失敗經 stash 驗證為施工前既有問題，與本回合無關）。

> **現實校正（施工前查核，2026-10-19）**
> 1. **CSP 暫行條款**：模板現有 **148 處 inline 事件處理器**（`onclick` 等，11 個檔案）＋`web_ui/templates/index.html:57-65` inline `<script>`，若直接下 `script-src 'self'` 將全面癱瘓 UI。R1 先部署**暫行 CSP**：`script-src 'self' 'unsafe-inline'`、`style-src 'self' 'unsafe-inline'`（大量 inline `style=`），同時以 `default-src 'self'`、`object-src 'none'`、`base-uri 'self'`、`frame-ancestors 'none'` 先行收緊；移除 `'unsafe-inline'` 依原計畫由 **R5** 完成（其出口驗收已載明「CSP 不含 'unsafe-inline'」）。
> 2. **登入回應保留 `token` 欄位**：既有測試（`test_web_server_auth.py::test_login_success`）與非瀏覽器 Bearer 客戶端依賴 JSON `token`，故不移除；但前端不再寫入 `localStorage`、不再發送 `Authorization`，瀏覽器唯一憑證路徑改為「HttpOnly＋Secure Cookie（`admin_session`）＋CSRF（`sherry_csrf`）」。XSS 面殘餘風險（腳本可代發請求）由 R4/R5 的 XSS 修復消滅。
> 3. **CSRF 適用範圍**：僅對「Cookie 鑑權」之變更型請求（POST/PUT/DELETE/PATCH）強制 `X-CSRF-Token`（double-submit cookie）；`Bearer`／`X-Admin-Token` 標頭鑑權（CSRF 本質免疫）與 `login/logout/check` 端點豁免。`SameSite` 維持 `Strict`（較報告建議之 `Lax` 更嚴）。

### R2 檔案寫入、秘密與外洩面

- **目標**：封閉「任意檔覆寫」與資訊外洩。
- [x] 靈魂名稱白名單（`^[A-Za-z0-9_\-]+\.(md|txt)$`）＋`Path.resolve()` 邊界檢查（`settings.py:813-842`）
- [x] 敏感 key 過濾改「白名單反轉」；`update_setting` 僅允許 schema 標記 `runtime_safe` 的鍵（`server.py:1353`、`settings.py:667-707`）
- [x] `str(exc)` 全清理：`server.py` 12 處、`mod/spark/web.py:95-200`、`mod/livestream/web.py:268`、`handlers/context_menu/cog.py:198` → 統一代碼化錯誤＋`error_id`
- [x] 密碼策略：支援 `WEB_UI_PASSWORD_HASH` 注入、預設不列印明文、`--gen-password` 一次性（`settings.py:711-721`、`server.py:2734-2740`）
- [x] 綁定失敗 **fail-fast**（不降級 `0.0.0.0`）（`server.py:2789-2797`）
- [x] livestream 伺服器改綁 `127.0.0.1`＋簡易認證（`mod/livestream/config.py:32-41`、`web.py:528-534`）
- [x] 攻擊鏈回歸測試（三個斷點各一組）
- **主要檔案**：`settings.py`、`web_ui/server.py`、`mod/livestream/config.py`、`mod/livestream/web.py`、`handlers/context_menu/cog.py`、`mod/spark/web.py`
- **出口驗收**：路徑穿越樣本被拒；非白名單 key 寫入被拒；錯誤回應不含路徑/堆疊；livestream 預設不監聽外部介面。
- **核銷**：§5.1.2（A3 斷點）、§5.3.6、§5.3.7、§5.3.9、§7.1.1 A3/A7/A8
- **完成註記（2026-10-19，commit `b0991f2`）**：20 項新增回歸測試全數轉綠（16 項先紅後綠＋4 項守門用例；`test_security_fixes`／`test_livestream`／`test_spark_web` 三方檔合計 56 passed）；受影響子集 91 passed（web_server_auth/config_and_concurrency/kv_cache_prompt_structure/ai_personality_generic/security_deep_audit/p0_security_and_perf_fixes）；全套件 747 passed（4 項既有失敗與施工前基線一致）；另調整 1 項既有測試斷言（見現實校正 6）。

> **現實校正（施工前查核，2026-10-19）**
> 1. **`str(exc)` 清掃範圍**：計畫列舉之清單（`web_ui/server.py` 12 處、`handlers/spark/web.py` 6 處、`mod/livestream/web.py:268`、`handlers/context_menu/cog.py:198`）全數在案；另掃得**計畫外**同類站點（`mod/stock_trader/web.py`、`mod/feed_sentinel/web.py`、`mod/deep_search/web.py`）。為維持單回合規模，**計畫外站點本回合不動**，於本註記備查、納入複審（§5 複審 1）優先清單。
> 2. **白名單反轉的落點**：報告指向 `server.py:1353`，實況為「Web POST 設定」與 `settings.update_setting`（供 Cog/CLI 呼叫）兩條路徑共用同一覆寫器；本回合將白名單判定下沉至 `web_ui/settings_schema.py`（`is_runtime_safe_key`＝schema 成員＋`runtime_safe`；`is_web_editable_key`＝schema 成員），`POST /api/settings` 與 `update_setting` 分別接線，並補齊 **14 個目前可寫但未登錄 `SETTINGS_META` 的鍵**（避免本回合造成既有編輯面回歸）。
> 3. **密碼策略語意（fail-closed）**：`WEB_UI_PASSWORD_HASH`（`pbkdf2_sha256$…`）優先於明文；**不再自動生成與列印**——兩者皆未配置時啟動顯示設定警告、登入一律 403（fail-closed）；`--gen-password` CLI 為唯一一次性生成路徑（不寫檔、只列印一次）。
> 4. **livestream 認證實作**：預設綁 `127.0.0.1`；`LIVESTREAM_ACCESS_TOKEN` 有值時，`/api/*` 與頁面路由需 `X-Livestream-Token` 或 `?token=`，通過即核發簽名 HttpOnly cookie（避免逐一改動頁面 fetch/EventSource），靜態資源豁免；未設 token 時僅依賴 loopback 邊界（與報告「由主站反代＋認證」同向）。
> 5. **綁定 fail-fast**：回退 `0.0.0.0` 僅在顯式 `ALLOW_FALLBACK_BIND` 真值時允許，否則記錄錯誤並**重新拋出**（fail-fast）。
> 6. **既有測試期望值調整（必要）**：`tests/unit/test_perf_optimizations_top1_to_5.py::test_services_status_probe_exception_fallback` 原斷言「探測例外字串須出現在 API `details`」（正是本回合要移除的外洩面）；已改為斷言「`details` 不得含例外字串、須含錯誤代碼」，測試原意（5 服務卡片完整、單一探測例外時該服務回 fallback offline）不變。此為本回合唯一因行為變更而調整斷言的既有測試（其餘既有測試全數維持不動且通過）。

### R3 端點與傳輸邊界收斂

- **目標**：消滅未認證面與邊界繞過。
- [x] 未認證端點逐一收斂：`/api/system/tasks`、`/api/bot/status`、`/api/system/audit/*`、`/api/self_monitoring/*`、`/api/logs`（必登入）（`server.py:344-366,1863-1885,2004-2086,1733-1782,773-886`）
- [x] `/api/logs?file=` 檔名白名單＋`resolve` 邊界檢查
- [x] `public_routes` 結構化 `{path, methods, auth}`＋default deny；`game_center`/`livestream` 宣告更新（`server.py:306-314,338-341`）
- [x] XFF：僅信任 `TRUSTED_PROXIES`、取最右值；`mod/game_center/web.py:55-64` 同步
- [x] 鎖定表 TTL＋上限（`server.py:56-57`）
- [x] 測試：未認證端點矩陣掃描＋偽造 XFF 限流/鎖定測試
- **主要檔案**：`web_ui/server.py`、`mod/game_center/web.py`、`mod/game_center/__init__.py`、`mod/livestream/__init__.py`
- **出口驗收**：未認證端點複掃為空；偽造 XFF 不再影響限流/鎖定。
- **核銷**：§5.3.1–§5.3.3、§5.3.8、§7.1.1 A4/A5/A6
- **完成註記（2026-10-19，commit `35f9182`）**：10 項新增回歸測試（8 項先紅後綠＋2 項守門用例：公開展示端點維持可讀、非受控來源本就忽略 XFF）；受影響子集 185 passed（security_fixes/security_deep_audit/p0/web_server_auth/self_monitoring/observability/perf/livestream/spark_web/feed_sentinel_web/painter_web/deep_search/module_registry/config_modules）；全套件 757 passed（4 項既有失敗與施工前基線完全一致，非本回合造成）。連帶調整 7 處既有測試期望值（見現實校正 7–9）。**12 檔**（+474/−110），符合單回合上限。

> **現實校正（R3 施工查核，2026-10-19）**
> 7. **收斂範圍擴張（依出口驗收「未認證端點複掃為空」）**：計畫列舉 5 組端點（tasks/bot status/audit/self_monitoring/logs），惟報告 §5.3.1 表格同列 `/api/stock_trader/*`、`/api/deep_search/*`、`/api/spark/*`（交易／研究／推播內容）；三者一併收斂為需管理員 Session。收斂後公開面僅餘無機敏內容之展示端點（`_PUBLIC_GET_PREFIXES`：feed_sentinel、painter、models、services、modules、roast/config、git、system/dashboard、system/metrics、components/metrics、incidents（端點自帶 401）、handlers//mod/ 靜態資源）。
> 8. **既有測試期望值調整（必要，共 7 處）**：XFF 鏈測試原斷言「取最左值」（正是 §5.3.3 缺陷語意）→ 更名並改斷言最右值；`test_web_ui_public_vs_admin_auth_guards` 之 `/api/system/audit/kpi` 200→401；`test_web_ui_guest_logs_filter` 更名 `test_web_ui_guest_logs_blocked`、403→401（before_request 先攔）；`test_self_monitoring` 藍圖測試 kpi 公開段改「未登入 401、登入後 200」；日誌端點相關 3 測試（web_server_auth／observability／perf）以管理員身分執行（原為訪客模式，行為面已收斂）。
> 9. **連帶前端修正（收斂之必要配套，非新增範圍）**：`games.js` 之 16 個 POST 補 `X-CSRF-Token`（`/api/games/*` 收斂後管理員 Cookie 請求需通過 CSRF，否則對弈功能 403——deep_search.js／stock_trader.js 已自帶，無須改）；`app.js` 重啟輪詢由 `/api/logs/files` 改用公開 `/api/version`（重啟後舊 Session 必然失效，原探測會永遠逾時）。另補齊 `web_ui/logs` SQLite 例外之錯誤代碼化（R2/A8 同類遺漏點，本次一併清掃，`str(exc)` 於 `web_ui/server.py` 歸零）。

### R4 前端 XSS 修復 A（轉義層＋熱點）

- **目標**：修復最高風險的注入熱點。
- [x] `escHtml` 補單引號轉義（`app.js:1487-1493`）；統一轉義工具函式（escAttr／escapeHtml 委派 escHtml）
- [x] 熱點重構改 `textContent`/DOMPurify：`app.js:595-605→662,637,649,3314,3357-3381`；`spark.js:54,66,137`（R4 以轉義層於輸出點處置；DOMPurify 導入依原計畫於 R5）
- [x] 建立 XSS payload 清單與人工/瀏覽器驗證程序（`xss_payload_checklist.md`）；伺服器端同步補強必要轉義（`/api/mod/<mod_key>/view`）
- **主要檔案**：`web_ui/static/app.js`、`web_ui/static/spark.js`、`web_ui/server.py`（必要的輸出端）
- **出口驗收**：熱點清單歸零；payload 注入無腳本執行。
- **核銷**：§5.4.1（前半）、§7.1.1 A9（前半）
- **完成註記（2026-10-19，commit `ab81749`）**：7 項新增 R4 靜態守門測試先紅後綠（escHtml 單引號、工具委派統一、跨檔轉義函式、日誌／模組／Spark 熱點轉義、非法 mod_key 400）；受影響子集 173 passed；全套件 764 passed（4 項既有失敗與施工前基線一致）。熱點清單逐條歸零；另補齊 R1/A2 遺留之模組視圖輸出轉義（白名單驗證＋`html.escape`）。payload 清單與人工程序見 `Docs/plans/26.10.19/xss_payload_checklist.md`（**6 檔**，+154/−35）。

> **現實校正（R4 施工查核，2026-10-19）**
> 10. **`spark.js` 路徑校正**：計畫與報告寫 `web_ui/static/spark.js`，實況為 `handlers/spark/static/spark.js`（改動落點以此為準，行號 `54,66,137` 對應載入失敗訊息、例外訊息與 `renderTriggers` 輸出）。
> 11. **熱點處置方式**：R4 以「轉義層（sink 轉義）」先行處置全部列舉熱點；DOMPurify／`textContent` 全面重構屬 R5 既定範圍（R5 第一條），故 R4 未引入新依賴。本環境（agent-tf）無 Node.js，無法於 pytest 執行 JS；驗證採「原始碼靜態守門測試＋人工 payload 程序」雙軌（限制與程序已文件化於清單檔）。
> 12. **連帶修補（同缺陷類，隨手歸零）**：`games.js` `_escHtml` 與 `deep_search.js` `_escapeHtml` 同具單引號缺口（與 §5.4.1 所述 `escHtml` 缺口同類），一併補齊；`stock_trader.js` 無轉義函式且多處 `innerHTML` 拼接（不在 R4 熱點清單），已登錄至 R5／複審 1 優先清單。

### R5 前端 XSS 修復 B（全面置換＋CSP 回歸）

- **目標**：把 XSS 面從「已修熱點」降到「系統性防護」。
- [x] 全站 `innerHTML` 清點（約 83 處）分流：必要 HTML → `DOMPurify.sanitize`（6 站）；其餘為靜態模板或全內插已轉義（逐站理由見清點文件）→ 維持
- [x] 引入 DOMPurify（vendored 3.1.6，離線可用；`sanitizeHtml()` 統一入口＋降級過濾）
- [x] 消除 inline `onclick`（主頁 inline `<script>` 已外置化；inline handler 共 232 處列入例外清單並文件化，預算閘門防擴大；事件代理化列入後續回合）
- [x] 提交檢查：新增 `innerHTML` 需附審查理由（以 pytest 預算閘門實作——`_R5_INNERHTML_BUDGET`／`_R5_INTERNAL_HANDLER_BUDGET` 僅可下降）
- **主要檔案**：`web_ui/static/app.js`、`web_ui/static/*.js`、`web_ui/static/vendor/`（新增）
- **出口驗收**：`innerHTML` 掃描僅剩白名單（含 sanitize）；CSP 不含 `'unsafe-inline'`（或例外清單已文件化）。
- **核銷**：§5.4.1（後半）、§2.5、§7.1.1 A9（後半）
- **完成註記（2026-10-19，commit `bb7c979`）**：DOMPurify 3.1.6 vendored＋`sanitizeHtml()` 入口（SherryUI 匯出；6 站套用）；補齊導航／聊天氣泡／stock_trader 轉義缺口；index.html inline script（SW 註冊）外置化；5 項新增測試（4 先紅後綠＋1 治理閘門）。受影響子集 190 passed；全套件 769 passed（4 項既有失敗與施工前基線一致）。CSP 採「例外清單已文件化」路徑（`innerhtml_inventory.md`：innerHTML 清點＋inline handler 232 處清單＋事件代理化路線）。（**8 檔**，+193/−32）

> **現實校正（R5 施工查核，2026-10-19）**
> 13. **inline handler 全消除之可行性**：web_ui 模板 148 處＋mod 模板 87 處＋JS 生成 45 處（不含第三方 1 處），牽動 24 檔且無瀏覽器自動化測試護航；依計畫條文「無法消除者列入例外清單並文件化」處置——主頁 inline `<script>` 面歸零（SW 註冊外置），handler 例外清單＋「僅可下降」預算閘門進版控，事件代理化列後續回合。CSP 維持 `'unsafe-inline'`（僅為 handler 服務）並更新註記。
> 14. **提交檢查機制替代**：本環境無 Node.js／ESLint，計畫所述 ESLint 規則以 **pytest 預算閘門**等效實作（新增站點必使測試紅燈，迫使更新基線並附理由）。
> 15. **清點數字校正**：報告「83 處」指 `web_ui/static`（app.js 82＋live2d 2 之指派，含註解誤差）；本回合另將 mod／handlers 靜態 JS 45 處（deep_search 9、feed_sentinel 2、games 6、painter 9、stock_trader 10、spark 8 等，另 livestream 1）納入同一基線治理。

### R6 軟刪核心（記憶寫入模型）

- **目標**：讓刪除「真實、完整、可追溯」。
- [x] `/forget` 交易化：facts/profiles 軟刪＋relations 政策處置＋`chat_messages` 標記＋JSON/archive 檔案清除＋稽核；**全成功才回覆「已清除」**（`handlers/memory/cog.py:201-208`）
- [x] `upsert_user_facts`（差異化 UPSERT）取代全表 `DELETE`；新增 `soft_delete_all(user_id)`（`storage.py:720,814`，範例見報告 §5.2.9）
- [x] unique index `(user_id, fact)`＋去重遷移腳本
- [x] `handlers/admin/cog.py:108-111,280-284` 改走軟刪 API
- [x] 刪除路徑同步化（移除 fire-and-forget；`storage.py:773-778`）
- **主要檔案**：`handlers/memory/storage.py`、`handlers/memory/cog.py`、`handlers/admin/cog.py`、`tools/db_manager.py`
- **出口驗收**：forget 端到端測試（成功/部分失敗語意）；全庫無 `DELETE FROM user_facts` 全表語句；索引存在；nightly 不復活。
- **核銷**：§5.2.1、§5.2.2、§5.2.9、§7.1.2 B1/B3、P0-3
- **完成註記（2026-10-19，commit `5c0a831`）**：11 項新增測試先紅後綠（新檔 `tests/unit/test_soft_delete_and_forget.py`）：forget 端到端全範圍（成功語意）、部分失敗語意、UPSERT 不復活＋唯一索引、去重遷移、同步刪除、admin/指令接線靜態守門、relations 與 chat_messages 子範圍。`/forget` 走 `purge_user_data` 清除管線（facts/profiles 軟刪＋relations 匿名化軟刪＋chat_messages 標記＋JSON 快照/JSONL 歸檔原子重寫＋admin_audit 稽核；逐範圍結果彙總，全成功才回覆「已清除」）；`upsert_user_facts` 差異化 UPSERT 取代全表 `DELETE`；`idx_user_fact` 唯一索引＋冪等去重遷移；admin 兩處清除改走同一軟刪 API；刪除路徑同步化（`delete_user_memory` 保留為相容委派層）。受影響子集 135 passed；全套件 780 passed（4 項既有失敗與施工前基線一致，非本回合造成）。`tools/db_manager.py` 未動（清運擴表依計畫屬 R7）。**7 檔**（+835/−89），符合單回合上限。

> **現實校正（R6 施工查核，2026-10-19）**
> 16. **「交易級」之跨儲存實作語意**：報告 §5.2.1 步驟 1-6 之「同一 DB 交易」在跨儲存（`sherry_memory.db`／`chat_history.db`／JSON 檔案／稽核檔）下無法以單一 SQL 交易涵蓋——實作改採「各子範圍於所屬儲存之交易中執行＋逐範圍結果獨立記錄」，以彙總 `ok` 旗標落實出口條件「全成功才回覆已清除」；部分失敗明確回報範圍與重試指引（重試為冪等操作）。
> 17. **快照路徑不復活（較報告範例之強化）**：§5.2.9 範例 UPSERT 於衝突時一律 `is_deleted=0`（復活）；實作區分兩條路徑——單一使用者增量（實際重新學到之內容）允許復活，全庫快照（nightly／消毒）以 `resurrect=False` **不覆寫刪除標記**，將出口條件「nightly 不復活」直接固化於寫入模型（profile 快照同採不重置 `is_deleted`）。
> 18. **R7 前置吸收與範圍註記**：`chat_messages` 之 `is_deleted/deleted_at` 欄位與讀取過濾（`read_chat_log` 早已 `WHERE is_deleted=0`）已存在，R6 直接以 `soft_delete_chat_messages` 標記，保留政策／`DataRetentionWorker`／`db_manager` 清運仍依計畫留待 R7；relations 之處置新增於 `handlers/memory/relationship.py`（計畫主要檔案未列，屬 §5.2.1 步驟 2 必要配套）；稽核以 `admin_audit.jsonl` 直接追加 `USER_DATA_PURGE` 記錄（UID 雜湊遮罩；避免 handlers→mod 依賴 `mod/audit_manager.py`）。

### R7 軟刪擴散與保留治理

- **目標**：把一致性擴到所有儲存面。
- [x] `chat_messages` 補 `is_deleted/deleted_at`＋保留政策＋接 `DataRetentionWorker`（`chat_logger.py:43-56`）
- [x] `world_memory` 全查詢補 `is_deleted=0`＋軟刪＋快取失效（`world_memory.py:265-441,479-502,318-319`）
- [x] `schedule_storage` 補欄位＋軟刪（`schedule_storage.py:154-169`）
- [x] roast/JSON 回灌改 `ON CONFLICT DO NOTHING`（不覆寫刪除標記）；JSON fallback 僅首遷可用（`roast/manager.py:101-138`、`storage.py:630,666-683`）
- [x] retention worker：`admin_audit` 生效（`:52`）、路徑絕對化（`:71,207`）、JSONL 原子輪轉（`:183-185`）
- [x] 備份加密＋輪替＋刪除權連動處置（`data/backups/manual_db_backup_*`）
- [x] `db_manager cleanup_soft_deleted` 擴表並排程（`tools/db_manager.py:683-720`）
- **主要檔案**：`handlers/memory/*`、`handlers/schedule_storage.py`、`handlers/roast/manager.py`、`handlers/vibe_manager/storage.py`、`handlers/memory/retention_worker.py`、`tools/db_manager.py`
- **出口驗收**：保留政策表全勾；測試：世界記憶刪後不可檢索、重啟不復活；備份腳本產物加密可還原。
- **核銷**：§5.2.3–§5.2.8、§5.5.1、§5.5.2、§7.1.2 B2/B4/B5/B6、P0-4
- **完成註記（2026-10-19，commit `0360c92`）**：14 項新增測試先紅後綠（新檔 `tests/unit/test_soft_delete_r7.py`）；遷移工具另以臨時環境實跑煙霧測試（二次遷移零重複、歸檔不破壞）。世界記憶／排程／對話／吐槽全線軟刪一致；保留政策落地：runtime 14d ✅、error 30d ✅、admin_audit 90d ✅（新生效）、temp 24h ✅（路徑修正）、chat_messages 180d ✅（新）、軟刪寬限 30d 後分批物理清運 ✅（新）、備份加密＋輪替 5 份 ✅（新）。受影響子集 65 passed；全套件 794 passed（4 項既有失敗與施工前基線一致，非本回合造成）。**11 檔**（+1243/−85），符合單回合上限。

> **現實校正（R7 施工查核，2026-10-19）**
> 19. **「刪除權連動處置」的實作語意**：備份為整庫快照之加密靜態檔（Fernet；金鑰經 `SHERRY_BACKUP_KEY` 或 `data/backups/backup.key` 0600）；刪除權行使後之殘餘副本以「輪替上限（保留 5 份）自然淘汰＋`restore` CLI 可還原（測試護航）」為處置——未對歷史備份做逐使用者手術（避免破壞其他使用者資料完整性）；purge 稽核軌跡見 R6。
> 20. **核銷範圍校正（§5.2.3／§5.2.8）**：`vibe_manager/storage.py` JSON 備援未動——該域無刪除功能、無軟刪欄位，復活語意不成立，其備援一致性併入複審追蹤；§5.2.8 的「版本化 migration＋`--dry-run`」不在 R7 條文（屬 R18 遷移治理），本回合完成其可執行子項：15 處 `INSERT OR REPLACE` 全數轉 `ON CONFLICT DO NOTHING`（`topic_memory` 改 `WHERE NOT EXISTS`）＋歸檔兩處 `rmtree` 廢除（改非破壞性合併搬移）。
> 21. **R6 前置吸收之對照**：`chat_logger.py` 欄位與 `read_chat_log` 過濾已於 R6 就緒，R7 未改動該檔；保留政策落在 retention worker（180 天軟刪＋30 天寬限＋分批物理清運），`db_manager cleanup` 為手動全標準表清運、worker 為夜間自動清運（雙軌互補）。
> 22. **實作細節**：(a) 清運比較以 `REPLACE(SUBSTR(deleted_at,1,19),'T',' ')` 正規化，同時兼容 R6 之 UTC ISO8601 與本地時戳兩種歷史格式；(b) 既有測試期望值調整 2 處（`test_world_memory` 寫入即清快取、`test_data_retention_worker` 改 `chat_logs/temp` 正確路徑）。

### R8 治理接線與 SPARK 預算

- **目標**：讓 ADR-0002 從文件變事實。
- [x] `autonomy_guard` 接入兩個 choke point：SPARK 行動派發前＋BotBrain 提交前（預算/熔斷基本值）（`core/autonomy_guard.py:120`）
- [x] `risk_engine`/`system_state` 被 guard 實際使用（或明確標記為設計狀態）
- [x] SPARK 配額：trigger 數上限＋週期分級（LLM 型 ≥30 分鐘）＋每日推送上限（`spark/nlp.py:1088`、`spark/cog.py:1461-1491`）
- [x] 動作派發 semaphore＋強引用集合＋timeout（`spark/cog.py:1480-1482`）
- [x] ADR-0002 狀態更正；整合測試斷言 SPARK 路徑經過 guard
- **主要檔案**：`core/autonomy_guard.py`、`handlers/spark/cog.py`、`handlers/bot_brain/cog.py`、`Docs/adr/ADR-0002-*.md`
- **出口驗收**：超限拒發測試綠；並發上限測試綠；ADR 與實作一致。
- **核銷**：§6.1.1–§6.1.3、§7.1.3 C1–C4、P0-5
- **完成註記（2026-10-20，commit `8d9a6af`；ADR 補提交 `28ce720`）**：新增 `tests/unit/test_governance_wiring_r8.py` 15 項全綠（guard 4、BotBrain 3、SPARK 派發 4、配額 3、ADR 1）；受影響子集 155 passed（1 項既有基線失敗 `test_spark_nlp`）；全套件 809 passed（4 項既有失敗與施工前基線一致，非本回合造成）。**11 檔**（+427/−29，不含新檔統計之 untracked 計畫文件），符合單回合上限。

> **現實校正（R8 施工查核，2026-10-20）**
> 23. **週期下限的集中落點**：計畫指向 `spark/nlp.py:1088`，但 NLP 僅是入口之一（另有 Modal／Web API／確認卡片）。實作改於 `handlers/spark/service.py:create_trigger` 統一呼叫 `handlers/spark/quota.py:clamp_interval_minutes`，讓所有入口共享同一政策；`nlp.py` 解析器未動（避免只保護單一入口）。
> 24. **guard 判定順序與降級語意**：`check()` 依序 HIGH 拒（需人工審批）→ INCIDENT 拒 MEDIUM/HIGH、放行 LOW → WARNING 拒 MEDIUM、放行 LOW → 熔斷拒全部 → MEDIUM 消耗時間窗預算（LOW 不消耗）。INCIDENT 放行 LOW 為 R8 測試明定行為（唯讀診斷不應被事件阻斷）。
> 25. **BotBrain 自主任務判定**：以 `priority >= settings.PRIORITY_INTERJECT`（預設 10；Tag 回應=1、DRAW=5 不受影響）為主，`InferenceTask` 新增 `is_autonomous: bool | None` 供呼叫端顯式覆寫；拒絕時 `await task.callback("")` 後不入列（fail-closed，呼叫端不懸置）。
> 26. **動作並發模型**：`SparkCog` 採 instance-level 長生命週期 semaphore（`SPARK_MAX_CONCURRENT_ACTIONS` 預設 4）＋`_action_tasks` 強引用集合＋`asyncio.wait_for` timeout（`SPARK_ACTION_TIMEOUT_SECONDS` 預設 300s）；checker 評估仍用每輪局部 `Semaphore(8)`，兩者職責分離。`cog_unload` 會取消未完成動作。
> 27. **配額與錯誤面**：`SparkQuotaExceeded` 於 6 處 Discord 建立路徑（4 個 Modal＋確認卡片＋貪睡）以 ephemeral 訊息回報，Web API 回 429（非 500）；`SparkPushQuota` 為 per-user 每日計數、跨日重置之記憶體政策物件。
> 28. **測試 fixture 校正（R8 新檔自身）**：SPARK 派發測試的 mock service 補 `consume_push_quota→(True, ...)` 契約；並發測試放寬 guard 預算（4 個 trigger 會超過 fixture 的 3/hr，屬測試聚焦設定，非產品語意）。

---

## 3. Phase 2：止漏（R9–R15）

### R9 LLM 鏈路時序模型（timeout／cancel／退避／錯誤分類）

- [x] timeout 解耦：`ClientTimeout(total=None, sock_read=chunk)`＋外層 `asyncio.timeout(total)`（`client.py:499-509`）
- [x] 取消傳播：`InferenceTask` 持有 `asyncio.Task` 強引用，改 `task.cancel()`（`bot_brain/cog.py:231-240`、`dispatch_agent.py:190-200`）
- [x] 重試：指數退避＋抖動；timeout 重試上限獨立（`bot_brain/cog.py:409-438`）
- [x] 錯誤分類結構化：`LLMStreamError/AuthError/RateLimitError/ContentFiltered`（`client.py:546-554`）
- [x] 測試：假 SSE server（逾時、取消、4xx、error frame）
- **主要檔案**：`handlers/bot_brain/client.py`、`handlers/bot_brain/cog.py`、`handlers/social/dispatch_agent.py`
- **出口驗收**：逾時/取消測試綠；長生成不再被硬切；取消後上游請求確實中止。
- **核銷**：§2.1.1–§2.1.5、§7.2 D1–D4
- **完成註記（2026-10-20，commit `0cc5fe8`）**：新增 `tests/unit/test_llm_timing_r9.py` 14 項全綠（假 SSE server 涵蓋 chunk 逾時、整體 deadline、取消斷線、401/429/500 分類、error frame、退避公式、逾時重試上限、串流取消收尾）。`_deadline_post` 以 `asyncio.timeout` 掌握整體 deadline，`ClientTimeout(total=None, sock_read=chunk)`；串流請求改由獨立 Task 承載，`InferenceTask.cancel()` 直接 cancel 該 Task；`LLMStreamError/LLMAuthError/LLMRateLimitError/LLMContentFiltered` 取代字串關鍵字判定；退避改 `min(base*2^(n-1), cap)*uniform(0.8,1.2)`，逾時重試上限 `BOT_BRAIN_MAX_TIMEOUT_RETRIES=1` 獨立。受影響子集 145 passed；全套件 823 passed（3 個模組因環境缺 `yfinance`/`yt_dlp` 無法收集，另有 1 項既有 `test_spark_nlp` 失敗與施工前基線一致）。

> **現實校正（R9 施工查核，2026-10-20）**
> 29. **整體 deadline 的落點**：`asyncio.timeout` 以 `_deadline_post` async context manager 包住 `session.post`＋SSE 讀取，避免在 `_stream_openai_compatible` 內大規模重排縮排；timeout 觸發時 CancelledError 會傳入 aiohttp 請求並轉為 `TimeoutError`，由 `_process_task` 既有的逾時分支接住。Ollama 原生 `/api/chat` 串流同步套用同一模型。
> 30. **取消傳播的承載點**：`_process_task` 將實際請求放進獨立 `chat_task` 並註冊到 `InferenceTask._execution_task`；`cancel()` 同時設旗標與 `chat_task.cancel()`。本地序列 worker 不會被取消，雲端背景任務亦同，只有上游 HTTP 請求立即中止。
> 31. **錯誤分類相容性**：`LLMError` 繼承 `aiohttp.ClientError`，既有依賴 `aiohttp.ClientError` 的呼叫端與測試維持相容；HTTP 401/403→`LLMAuthError`（不重試）、429→`LLMRateLimitError`（退避重試）、5xx/連線→`LLMStreamError(retryable=True)`、內容政策關鍵字→`LLMContentFiltered`（不重試）。`finish_reason=content_filter` 的舊式空回應仍由 fallback 判定。
> 32. **環境註記**：本輪全套件以 `--ignore` 排除 3 個因環境缺套件（`yfinance`、`yt_dlp`）而無法收集的模組；其餘 823 passed，4 failed 皆為施工前基線（3 項同為缺 `yfinance`、1 項 `test_spark_nlp` 已於 R8 記錄）。

### R10 任務治理與併發隔離

- [x] 雲端 per-user＋全域雙層 semaphore（`bot_brain/cog.py:199-206`）
- [x] 本地 worker 重試移出熱路徑（排程回佇列）
- [x] worker 指數退避＋失敗上限（`bot_brain/cog.py:207-212`）
- [x] fire-and-forget 回收：觀測佇列有界＋關閉 flush、插話任務強引用、記憶寫入殘餘（`observability.py:151,218-246`、`handlers/social/__init__.py:81-91`）
- [x] 專用執行器：新增 `core/concurrency.py`（`heavy_io_pool`/`db_pool`），替換裸 `run_in_executor(None)`（music/stock/memory）
- **主要檔案**：`handlers/bot_brain/cog.py`、`handlers/social/observability.py`、`core/concurrency.py`（新增）、`handlers/music/cog.py`、`handlers/social/tools/stock_tool.py`
- **出口驗收**：高負載演練（併發 tag×10）無失控並發；executor 掃描無裸 `None`（白名單除外）。
- **核銷**：§2.1.3、§2.3.1–§2.3.3、§7.2 D3/D6/D7
- **完成註記（2026-10-20，commit `dc52672`）**：新增 `tests/unit/test_task_governance_r10.py` 13 項全綠（雲端全域／per-user 併發、本地重試回佇列與 attempt 續跑、worker 退避／降級、觀測佇列有界與關機 flush、插話強引用、專用執行器掃描）。雲端任務新增全域＋per-user 雙層 semaphore（`BOT_BRAIN_CLOUD_MAX_CONCURRENCY=4`、`BOT_BRAIN_CLOUD_PER_USER_CONCURRENCY=1`）；本地失敗重試改由 `_requeue_after` 排程回 `PriorityQueue`，worker 不再 sleep 佔住 GPU；worker 連續失敗改指數退避（cap `BOT_BRAIN_WORKER_BACKOFF_CAP=60s`）並於 `BOT_BRAIN_WORKER_MAX_FAILURES=5` 標記降級；觀測佇列有界（`OBSERVABILITY_WRITE_QUEUE_MAXSIZE=10000`）＋溢出計數＋`shutdown_observability_logs()` 關機 flush；插話任務改存 `_background_tasks` 強引用並於 `cog_unload` 取消；新增 `heavy_io_pool`/`db_pool` 並清除所有生產路徑裸 `run_in_executor(None)`。受影響子集 90 passed；全套件 836 passed（4 項既有失敗與施工前基線一致，非本回合造成）。

> **現實校正（R10 施工查核，2026-10-20）**
> 33. **重試旗標承載點**：`requeue_retries` 放在 `InferenceTask` 而非 `_process_task` 參數，避免既有測試／外部以 3 參數 mock `_process_task` 的呼叫端因新增關鍵字而斷裂；worker 取到本地任務時設定旗標，`_process_task` 以 `getattr` 讀取。
> 34. **attempt 續跑**：重試回佇列時保留 `attempt`、`timeout_retry_count`、`retry_model`，避免 attempt 歸零造成重試次數重置或備援模型輪替跳號。
> 35. **觀測佇列滿載語意**：改為「有界丟棄＋累計告警」而非同步兜底，確保熱路徑絕不因落盤阻塞；`_writer_worker` 修正哨兵未計入 `task_done()` 導致 `flush/join` 永掛的問題。
> 36. **關機順序**：`main._run_with_shutdown` 先 `shutdown_observability_logs()` 排空事件，再 `shutdown_executors(wait=False)`；executor 的 `atexit` 註冊保留作最後防線。`asyncio.to_thread` 仍屬 R12/R13（D8）範圍，本回合僅替換任務治理涉及的 executor 呼叫。

### R11 SPARK 韌性與背景可觀測

- [x] 單輪 `asyncio.timeout(60)`＋checker 級 timeout（`spark/cog.py:1532-1662`、`spark/service.py:115-190`）
- [x] daily catch-up 政策（`schedule_checker.py:203-310`）
- [x] 「立即測試」dry-run（不改狀態、不消耗 one-shot）（`spark/cog.py:923,2048`、`spark/web.py:181`）
- [x] `except: pass` 清理（`spark/cog.py:423,972,1092,1119,1130,1154,1304`、`health_monitor.py:48,109,131`、`observability.py:539,552,614,763,833`）
- [x] 優先序枚舉化＋清除魔術數字（`settings.py:289-323`；`chat_tools.py:263`、`utils.py:522`、`weather_tool.py:137`）
- [x] SPARK 執行摘要（trace＋成功/失敗/跳過/耗時/預算）落觀測（`spark/cog.py:1632-1662`、`spark/service.py:359-378`）
- **主要檔案**：`handlers/spark/cog.py`、`handlers/spark/checkers/schedule_checker.py`、`handlers/spark/web.py`、`handlers/bot_brain/health_monitor.py`
- **出口驗收**：模擬卡死 checker 不拖垮迴圈；摘要可在 WebUI/日誌查得。
- **核銷**：§6.1.4、§6.1.5、§6.2.1、§6.2.2、§6.4.1、§7.2 D5/D9
- **完成註記（2026-10-20，commit `0522bd9`）**：新增 `tests/unit/test_spark_resilience_r11.py` 14 項全綠（checker 逾時不落狀態、dry-run 不消耗 one-shot、每日補跑窗／過窗／同日不重複／可關閉、卡死 checker 不拖垮整輪、摘要落觀測與 `/api/spark/stats`、`except: pass` 掃描、優先序枚舉與魔術數字掃描）。`SparkService.check_trigger` 以 `asyncio.timeout(SPARK_CHECKER_TIMEOUT_SECONDS=45)` 包住 checker，逾時回傳 `detail_data={"timeout": True}` 且不寫回狀態；`SparkCog._run_check_cycle` 以 `asyncio.timeout(SPARK_CHECK_CYCLE_TIMEOUT_SECONDS=60)` 包住整輪 gather，逾時取消剩餘檢查並續行下一輪。`ScheduleChecker._check_daily_time` 新增當日補跑窗（`SPARK_DAILY_CATCHUP_MINUTES=120`，可用 `condition.catchup_minutes` 覆寫、0 關閉；同日已觸發不重複、不跨日回溯）。「立即測試」三個入口（面板按鈕／`!spark test`／Web API）一律 `dry_run=True`，不寫回 `last_triggered_at`／`run_count`、不關閉 one-shot；Web API 回應補 `dry_run: true`。每輪巡檢產生 `spark-*` trace 與執行摘要（觸發／派發／未達／冷卻跳過／checker 逾時／失敗／治理拒絕／配額拒絕／本輪逾時／耗時），寫入 `SparkService.last_cycle`（`/api/spark/stats` 可見）並落 `spark_cycle` runtime 觀測事件；動作任務繼承同 trace。`settings.Priority` 語意枚舉（`INTERACTIVE`／`DRAW`／`INTERJECT`／`TOOL_BACKGROUND`／`MAINTENANCE`／`IDLE`）＋`priority_for()` 映射，清除 `utils.py:522 priority=20` 與 `weather_tool.py:137 priority=0` 魔術數字。熱路徑 `except: pass` 全數改 `logger.debug(..., exc_info=True)`。受影響子集 145 passed；全套件 850 passed（4 項既有失敗與施工前基線一致：3 項缺 `yfinance`、1 項既有 `test_spark_nlp`）。

> **現實校正（R11 施工查核，2026-10-20）**
> 37. **dry-run 的承載點**：`dry_run` 是 `SparkService.check_trigger` 的關鍵字參數（預設 False），達標後在寫回狀態前提前返回 `(result, None)`；既有呼叫端與 3 參數 mock 不受影響，`_run_check_cycle` 的正式巡檢維持原語意。
> 38. **checker 逾時的表現形式**：逾時不拋例外，而是回傳 `should_fire=False`＋`detail_data={"timeout": True}`，讓巡檢摘要能區分「未達標」與「檢查器卡死」；`_eval_trigger` 據此計數 `checker_timeouts`。
> 39. **daily 補跑邊界**：補跑窗只覆蓋「目標分鐘已過、同一當地日曆日、且當日尚未觸發」；跨午夜不回溯補跑（避免與隔日排程重疊），`catchup_minutes=0` 即完全回到精確分鐘語意。
> 40. **摘要落點與優先序位置**：摘要同時寫入 `SparkService` 記憶體狀態（`/api/spark/stats` 現成可查）與 `runtime_log.jsonl`／`runtime_events`（WebUI 系統日誌檢視器可查）；`Priority` 置於 `settings.py`（不新增 core→settings 依賴），數值仍由既有 `PRIORITY_*` 設定驅動，`priority_for()` 保證 config_override 熱改後映射同步。`SPARK_MAX_CONCURRENT_CHECKS` 為本輪新增的檢查器併發上限，預設 8。

### R12 阻塞 I/O 清單 A（WebUI／sys_inspector 群）

- [x] `after_request` gzip → `to_thread`／ASGI 中介層（`server.py:574-611`）
- [x] 稽核/設定寫檔、checkpoint、shutdown 訊號 → `to_thread`（`server.py:1620-1883`：`_apply_setting_updates_sync`／`_write_shutdown_signal_sync`／`_launch_exit_script_sync`／`_checkpoint_before_exit`）
- [x] `metrics.get_summary` 啟動預熱＋`to_thread`（`server.py:2022-2027,3083-3124`；`core/sys_inspector/metrics.py:458-465` 雙重檢查鎖）
- [x] `audit_service` 同步 SQLite 寫入 → `to_thread`（`core/sys_inspector/audit_service.py:631-637`）
- [x] `GET /api/settings` 人設檔讀取快取/`to_thread`（`web_ui/settings_schema.py:1605-1633`；`server.py:1644-1646`）
- [x] 啟動期 git `subprocess` 延後；`sw.js` 讀檔快取（`server.py:223-240,626-662,3083-3124`）
- **主要檔案**：`web_ui/server.py`、`core/sys_inspector/*`、`web_ui/settings_schema.py`
- **出口驗收**：事件循環阻塞計時器測試（<50ms 閾值）。
- **核銷**：§2.2（web_ui 群）、§7.2 D8（前半）
- **完成註記（2026-10-20，commit `7d3a2b1`）**：新增 `tests/unit/test_webui_blocking_io_r12.py` 9 項全綠（每項以 5ms 心跳、200ms 慢速同步工作模擬，最大事件循環間隔 <50ms；另含 `/sw.js` 快取、git 預熱、延遲關閉與 `/api/stop` 行為）。`after_request` 的 `gzip.compress` 改 `await asyncio.to_thread(..., compresslevel=6)`；`/sw.js` 改由 `_read_service_worker_source_sync` 於工作執行緒讀取並依 `st_mtime_ns` 快取。`GET /api/settings` 整段 `settings.get_editable_settings` 移入工作執行緒，人設檔內容由 `_read_soul_content_cached` 以 `st_mtime_ns` 快取；`POST /api/settings` 先完成白名單／型別清洗，再以單次 `to_thread` 批次套用所有 `update_setting` 寫檔，審計 `record_action` 亦 `to_thread`。登入／登出／IP 鎖定等稽核事件、模組啟停的 `mod_config` 讀寫與審計紀錄全數移出事件迴圈。啟動期 `_STARTUP_GIT_INFO` 不再於 import 執行同步 git 子進程，改由 `before_serving` 的 `_run_startup_warmup` 背景並行預熱 git 資訊與 `component_metrics.get_summary`（保留 Task 強引用）；`metrics.get_summary` 加入雙重檢查 bootstrap 鎖，避免預熱與請求並行造成歷史重複計數。`AuditService.diagnose_incident` 的 SQLite 寫入改 `to_thread`。重啟／停止／更新端點由 `loop.call_later(0.8, 同步閉包)` 改為 async task（`asyncio.sleep(0.8)`＋`to_thread` 寫 shutdown 訊號、啟動外部腳本、`checkpoint_all_dbs`、flush logging），Task 以 `_PROCESS_ACTION_TASKS` 保留強引用。受影響子集 198 passed（另 1 項既有 `yfinance` 缺失失敗）；全套件 859 passed（4 項既有失敗與施工前基線一致：3 項缺 `yfinance`、1 項既有 `test_spark_nlp`）。

### R13 阻塞 I/O 清單 B（tools／背景群）

- [x] RSS 同步 DNS → `loop.getaddrinfo`（`spark/clients/rss_client.py:92,158`）
- [x] `search_tools` 同步 DNS → 非同步（`search_tools.py:257,292,304,312,427,433`）
- [x] trafilatura 擷取專用池＋結果丟棄策略（`search_tools.py:315-410`；`core/concurrency.py:40,50`）
- [x] yfinance/matplotlib 併發上限＋Agg 後端（`stock_tool.py:629-662,738`；`settings.py:685`；Agg 既有）
- [x] vibe 更新/decay 批次化（`vibe_manager/storage.py:115-151`、`cog.py:25-64,138,174`）
- [x] heartbeat 每輪任務數上限（`heartbeat/cog.py:66-75`；`settings.py:657`）
- **主要檔案**：`handlers/spark/clients/rss_client.py`、`handlers/social/tools/search_tools.py`、`handlers/social/tools/stock_tool.py`、`handlers/vibe_manager/*`
- **出口驗收**：同上（計時器測試＋壓力抽測）。
- **核銷**：§2.2（tools 群）、§7.2 D8（後半）
- **完成註記（2026-10-20，commit `75100a8`）**：新增 `tests/unit/test_blocking_io_r13.py` 15 項全綠（RSS／search 慢速 DNS、12 路 SSRF 併發壓力抽測、trafilatura 逾時丟棄與 slot 釋放、股市抓取併發上限、vibe 批次落盤、heartbeat 任務上限；每項事件循環心跳最大間隔 <50ms）。RSS 新增 `is_safe_feed_url_async` 以 `loop.getaddrinfo` 非同步解析 DNS，IP 安全判定與同步版共用；`fetch_latest_items` 改走非同步檢查，同步 `is_safe_feed_url` 保留給 CLI／測試。`search_tools` 新增 `is_safe_public_url_async`（`asyncio.to_thread` 包裝、保留 `patch` 接縫），`_check_redirect_safety` 與 `fetch_and_extract_url` 的所有 SSRF 檢查改為非同步。trafilatura 擷取移入新設 `core/concurrency.web_extract_pool`（`WEB_EXTRACT_POOL_WORKERS=2`），以 bounded semaphore 限制在途工作數；逾時後 `asyncio.shield` 不取消底層 Future、結果丟棄，slot 待執行緒真正結束才釋放，避免滯留執行緒重複排隊。股市抓取統一收斂至 `stock_tool.fetch_and_plot_bounded`（`STOCK_MAX_CONCURRENT_FETCHES=3` 全域 per-loop semaphore；`matplotlib.use("Agg")` 既有），`StockTool` 與 `search_tools` 皆改用此入口。vibe 落盤 `save_vibe_store` 改單交易 `executemany`、新增 `save_vibe_channel` 單頻道寫入；`VibeManager` 初始載入延後至 `cog_load` 並 `to_thread`，更新／decay／卸載改以快照與 `asyncio.Lock` 序列化後 `to_thread` 落盤。heartbeat 每輪新增 `HEARTBEAT_MAX_TASKS_PER_ROUND=10` 任務數上限。新增 `HEARTBEAT_MAX_TASKS_PER_ROUND`、`STOCK_MAX_CONCURRENT_FETCHES` 兩個 WebUI 可調設定與 `SETTINGS_META` 條目。受影響子集 201 passed（另 2 項既有 `yfinance` 缺失失敗）；全套件 874 passed（4 項既有失敗與施工前基線一致：3 項缺 `yfinance`、1 項既有 `test_spark_nlp`）。

### R14 記憶檢索 Part 1（FTS5 基礎）

- [x] schema：`facts_fts`（外部內容表）＋回填工具 `tools/backfill_fts.py`
- [x] `handlers/memory/retrieval.py` 基礎：FTS 查詢＋權重骨架＋**硬性軟刪過濾**
- [x] 檢索評測集 v1（`tests/evaluation/`）
- **主要檔案**：`handlers/memory/storage.py`、`handlers/memory/retrieval.py`（新增）、`tools/backfill_fts.py`（新增）
- **出口驗收**：關鍵字召回測試綠；已刪資料 0 召回。
- **核銷**：§4.1（前半）、§7.2 D10（前半）
- **完成註記（2026-10-20，commit `1ff746c`）**：`storage.py` 新增 `facts_fts` 外部內容表（`content='user_facts'`、`content_rowid='id'`），優先 `trigram` tokenizer（支援 CJK 子字串），舊版 SQLite 不可用時自動降級 `unicode61`。同步 triggers 於 R6 去重遷移之後建立，避免 DELETE trigger 對尚未回填的索引操作；首次建表或補建 triggers 時自動執行 `rebuild`，既有資料庫升級即完成回填。`ensure_memory_db` 支援指定路徑與 `force` 重檢；`rebuild_facts_fts` 以 `INSERT INTO facts_fts(facts_fts) VALUES('rebuild')` 重建並回傳 total／active 筆數。`retrieval.py` 以「FTS5（≥3 字元）＋ LIKE（1–2 字元短詞）」雙路召回；因 SQLite 的 `bm25()` 不能在 CTE／外層聚合展平後使用，候選 id 與分數改為分開查詢、Python 合併排序。查詢一律硬性過濾 `user_facts.is_deleted = 0 AND user_profiles.is_deleted = 0`；分數骨架含 bm25／recency／importance／source_priority，`cos_sim`／`affinity` 於 R15 前維持 0 佔位，權重自動正規化。`asearch_facts` 以 `asyncio.to_thread` 執行，避免阻塞事件循環。`tools/backfill_fts.py` 支援 `--db`／`--dry-run`，可安全重複執行。評測集 v1（`tests/evaluation/memory_retrieval_v1.json`＋`memory_retrieval_eval.py`）13 案例涵蓋 CJK trigram、1–2 字 LIKE、多詞 OR、使用者隔離、事實／Profile 軟刪與排序；實測 recall 1.000、誤召率 0.000、已刪資料召回 0.000、P95 2.0ms（門檻 <300ms）。新增 `tests/unit/test_memory_retrieval_r14.py` 16 項全綠（雙路召回、軟刪 0 召回、隔離／上限、三維排序、triggers 同步、rebuild、backfill CLI、async 包裝、評測集門檻）。受影響子集 75 passed；全套件 890 passed（4 項既有失敗與施工前基線一致：3 項缺 `yfinance`、1 項既有 `test_spark_nlp`）。新增 6 個融合權重、時效半衰期與關鍵字上限共 8 個 WebUI 可調設定。

### R15 記憶檢索 Part 2（Embedding 融合＋評測）

- [x] Ollama `/api/embeddings` 佇列＋失敗重試；`sqlite-vec` 可選降級
- [x] 融合排序：`bm25 + cos_sim + recency + affinity + source_priority`
- [x] prompt 動態尾段注入（維持 Prompt Cache 友善）
- [x] 評測：召回率/誤召率/P95 < 300ms
- **主要檔案**：`handlers/memory/retrieval.py`、`handlers/social/dispatch/prompts.py`、`settings.py`
- **出口驗收**：指標達標；KV cache 結構測試仍綠。
- **核銷**：§4.1（後半）、§7.2 D10（後半）
- **完成註記（2026-10-21，commit `9a37761`）**：新增 `handlers/memory/embeddings.py`：Ollama `/api/embeddings` 客戶端具 timeout（8s）與指數退避重試（0.25s／0.5s，預設 2 次），批次併發上限 2；`EmbeddingService` 以 per-event-loop asyncio 佇列去重、每批 8 筆補算，失敗僅記錄並保留缺失，下一次刷新（`cog_load`、每 60 秒 `_flush_loop`、`reload_store`、檢索後）自動重試，不阻塞對話熱路徑。向量正規化為 float32 BLOB 存於新 `fact_embeddings` 表；`user_facts` UPDATE 文字或 DELETE 時由 triggers 同步失效舊向量。預設以 Python cosine 掃描（單使用者上限 25 筆，成本極低），`sqlite_vec_available()` 偵測選用 `sqlite-vec`，未安裝時自動降級；查詢與事實維度不符（模型切換）直接忽略，避免截斷比較造成假高相似度。`retrieval.py` 新增向量候選（最低 cosine 0.25）與 `cos_sim`／`affinity` 維度，融合權重預設 bm25 0.40、cos_sim 0.25、recency 0.10、affinity 0.10、importance 0.10、source_priority 0.05；affinity 讀取 `user_relations.affinity/100` 並排除軟刪關係；FTS／LIKE／向量三路候選一律硬性過濾事實與 Profile 軟刪。`prompts.py` 新增 `build_retrieved_memory_tail`，只將檢索結果接在既有 snippet 之後的動態尾段，排除已在 snippet 中的事實；`memory_builder.py` 對發話者與被提及者（上限 1 條）注入，`chat_tools.py` 傳入當前 `user_query`，靜態系統前綴不變，KV cache 結構測試維持全綠。`tools/backfill_embeddings.py` 支援 `--db`／`--user`／`--limit`／`--batch-size`／`--timeout`／`--dry-run`，可重複執行。評測集 v2（8 案例：純語意、關鍵字＋向量融合、使用者隔離、事實／Profile 軟刪、低相似度）實測 recall 1.000、誤召率 0.000、已刪召回 0.000、P95 2.9ms；v1 維持 recall 1.000、誤召 0.000。修正歷程：v2 初版誤召 0.143 源於已刪地址與紅茶共用同一向量維度，已改為 5 維正交向量並加入維度不符防護。新增 `tests/unit/test_memory_retrieval_r15.py` 22 項全綠（client 重試、佇列／批次／查詢快取、語意召回、融合、軟刪、維度防護、sqlite-vec 降級、affinity、Prompt 尾段與前綴穩定、backfill CLI、v2 評測）。受影響子集 104 passed；全套件 912 passed（4 項既有失敗與施工前基線一致：3 項缺 `yfinance`、1 項既有 `test_spark_nlp`）。新增 8 個 embedding 設定與 1 個尾段條數設定（共 9 個 WebUI 可調項目）。

---

## 4. Phase 3：增值（R16–R27）

### R16 架構護欄與註冊表合一

- [x] `web_ui` 納入 `.importlinter`＋新契約（禁止反向依賴）；（`.importlinter:1-7`）
- [x] `handlers⇄mod` 解耦處置（7＋13 處盤點：能改 `core` DTO 的改；暫留者加註到期）；豁免清單 CI 上限
- [x] 雙註冊表合一：`TOOL_LIST` 改由 `CapabilityRegistry` 衍生；補一致性測試
- [x] `ToolSpec` 單一聲明（別名/prompt 生成）；清除 `draw_tool` 自我反查
- **出口驗收**：新增一個工具 ≤1 天且零核心改動（以真實案例驗收）。
- **核銷**：§3.1、§3.2、§7.3 E1/E2
- **完成註記（2026-10-21，commit `673245d`）**：`.importlinter` 納入 `web_ui` 並新增 3 條禁止反向依賴契約（下層→WebUI、handlers→mod、mod→handlers），`lint-imports` 6/6 KEPT；豁免集中登錄於 `Docs/architecture/import_linter_exemptions.md`（34 條、到期日 2027-03-31、CI 上限 40 條）。新增 `handlers/social/tool_specs.py` 為別名／工具宣告單一來源，`TOOL_LIST` 改由 `ToolCatalog`（`CapabilityRegistry` 衍生視圖）產生，`draw_tool` 兩處自我反查改 `get_tool("DRAW")`。新增 6 項回歸測試（`test_architecture_guard_r16.py`）；受影響子集 34 passed；全套件 925 passed（4 項既有失敗＋3 個缺套件收集錯誤與施工前基線一致）。**7 檔**（+約 400／−60）。

> **現實校正（R16 施工查核，2026-10-21）**
> 41. **`handlers⇄mod` 未全面改寫**：7＋13 處皆為既有 adapter／UI／DTO 接口，全面下沉 `core` DTO 屬跨回合重構；本回合以「契約＋逐條到期登記＋CI 上限」凍結現況，避免豁免隱形膨脹，實際解耦留待 2027-03-31 前逐條清理。
> 42. **WebUI 反向依賴 3 處**：`mod/livestream`（Quart app、憑證工具）與 `mod/painter`（bot 實例）直接取 `web_ui.server`；已列入同一豁免清單，Contract 6 同步終止間接鏈。
> 43. **`.importlinter` 僅限 ASCII**：import-linter 以系統 locale（cp950）讀取設定，中文註解會導致解析失敗；理由與到期日改記於 Markdown 豁免清單。

### R17 狀態外置與排程 Leader

- [x] `SessionStore`/`RateLimitStore`/`LoginLockout` 介面＋SQLite 實作；`core/guard.py` 與 `social/rate_limiter.py` 合一
- [x] SPARK 改 DB 租約 leader 模型（防雙實例重複推播）
- **出口驗收**：雙實例佈署測試：無重複推播、無 session 漂移。
- **核銷**：§3.4、§6.2.5、§7.3 E3
- **完成註記（2026-10-21，commit `980c1ad`）**：新增 `core/state_store.py`（`SessionStore`／`RateLimitStore`／`LoginLockout`；SQLite `state_kv` JSON mapping，可換 Redis）與 `core/rate_limit.py`（`core.guard` 與 `handlers.social.rate_limiter` 共用的滑動窗口演算法）。WebUI 的 `_admin_sessions`／`_login_attempts`／`_locked_ips` 改為 SQLite-backed mapping，登入鎖定政策收斂至 `LoginLockout`；SPARK 新增 `core/leader_lease.py`（`BEGIN IMMEDIATE`＋TTL 租約），`SparkCog` 於 `cog_load` 取得租約、心跳續約、非 leader 跳過巡檢，`cog_unload` 釋放。新增 6 項回歸測試（`test_state_externalization_r17.py`）；受影響子集 174 passed；全套件於 R18 複跑 940 passed（4 項既有失敗與基線一致）。**8 檔**（+約 500／−40）。

> **現實校正（R17 施工查核，2026-10-21）**
> 44. **SQLite mapping 相容既有測試**：`_admin_sessions` 等 globals 仍以 dict 介面暴露（`MutableMapping`），既有測試的 `clear`／`pop`／`dict(...)`／直接賦值不需改寫；`LoginLockout` 與 mapping 共用同一狀態。
> 45. **時間基準分離**：WebUI／RateLimiter 沿用 `time.time()`；CoreGuard 於 R18 修正為 `time.time()`（原 monotonic 不適合持久化，見校正 50），共用演算法接受 `now` 參數。
> 46. **Leader 租約的單行程相容**：未經 `cog_load`（單元測試／CLI）時 `_is_leader()` 回傳 True；`SPARK_LEADER_LEASE_ENABLED=0` 可退回單實例模式。租約資料庫為 `data/runtime_state.db`（`SHERRY_RUNTIME_STATE_DB` 可覆寫）。

### R18 遷移治理

- [x] `PRAGMA user_version` 版本化＋`migrations/` 一檔一步＋`--dry-run`
- [x] 三軌排程表（triggers/schedule_tasks/schedules）收斂
- [x] `migrate_json_to_sqlite.py` 廢 `REPLACE`/`rmtree`（`:1066,1076`）
- **出口驗收**：升級/回滾乾跑演練通過。
- **核銷**：§3.5.3、§5.2.8、§7.3 E4
- **完成註記（2026-10-21，commit c184b4c）**：新增 `core/migrations.py`（`Migration`／`MigrationRunner`：`PRAGMA user_version`、單檔單步、`BEGIN IMMEDIATE` 單一交易、失敗不前進版本）與 `migrations/m001–m003`（runtime state 表、canonical `schedules` 回填、`schedule_tasks` 同步 triggers）。`tools/migrate.py` 提供 `--status`／`--dry-run`／`--backup`／`--rollback`；備份改用 SQLite backup API（WAL 安全）＋`integrity_check`＋保留 10 份輪替，rollback 先建 `pre_rollback` 安全快照再還原。新增 `tools/unify_schedules.py`，將 SPARK `triggers`（condition mode → schedule_type／cron／epoch）與 `schedule_tasks` 投影至 `schedules`：預設乾跑、`--apply` 冪等 upsert、不刪除任何來源。`tools/migrate_json_to_sqlite.py` 新增 `--dry-run`（零備份／零建庫／零寫入），並確認已無 `INSERT OR REPLACE`／`rmtree`。另修正 R17 外置限流的時間基準（見校正 50）。新增 9 項回歸測試（`test_migration_governance_r18.py`）；受影響子集 43 passed；全套件 940 passed（4 項既有失敗與基線一致）。出口演練：`--dry-run` 不建立 DB；apply→backup→資料變更→restore 可還原；migration 拋錯後 `user_version` 與 schema 均 rollback。**12 檔程式／測試**（+約 800／−30），另更新計畫與報告。

> **現實校正（R18 施工查核，2026-10-21）**
> 47. **UPSERT 接在 SELECT 後需 WHERE**：SQLite 要求 `INSERT ... SELECT ... ON CONFLICT` 以 `WHERE` 消歧；m002 的 `schedule_tasks` 回填加 `WHERE 1`，否則 `near "DO": syntax error`。
> 48. **m003 不使用 `executescript()`**：Python sqlite3 的 `executescript()` 會先隱式 commit，導致 migration 失敗時無法整體 rollback；改為逐條 `execute`，維持單一交易語意。
> 49. **備份不可直接 `copy2`**：WAL 未 checkpoint 時複製主檔會遺漏資料；改用 SQLite backup API＋`PRAGMA integrity_check`，rollback 先建立 `pre_rollback` 快照，若 `-wal` 被占用則明確提示先停服務。
> 50. **CoreGuard 時間基準改回 wall clock**：R17 將 `time.monotonic()` 寫入 SQLite，跨行程／重啟後時間原點不同，會使限流狀態失真；改回 `time.time()`。共享限流狀態也使兩個既有測試以同一 user id 互相污染，已於測試開頭 `clear_user_history` 顯式隔離，未放寬任何斷言。

### R19 主動關懷引擎 Part 1（引擎）

- [x] `handlers/care/`（engine.py/repository.py）：額度表、opt-in/opt-out、觸發來源（關係分數/SPARK/深夜整合）
- [x] 擴充 `autonomy_guard` 檢查點；所有主動動作留稽核
- **出口驗收**：預算強制測試、opt-out 100% 生效測試。
- **核銷**：§4.2（前半）、§7.3 E5
- **完成註記（2026-10-21，commit 9e1d7fc）**：新增 `handlers/care/`（`repository.py`／`engine.py`／`__init__.py`）。`CareRepository` 以 `data/care.db` 保存四類狀態：`care_preferences`（預設 opt-out、guild 專屬優先並回退全域、`frequency=off` 立即失效）、`care_budget`（每人每週＋全域每日滾動窗口，`BEGIN IMMEDIATE` 原子檢查與扣減，任一不足即整筆 rollback）、`care_actions`（核准與拒絕都留 `action_id`／原因／時間）、`care_topics`（深夜待回訪佇列，`(user,guild,topic,source)` 去重）。`CareEngine` 合併三軌候選（關係分數＋14 天未互動、SPARK `care` 排程 trigger、深夜整合話題），以 `(user, guild, topic)` 去重取高分，依序通過同意閘門→全域治理→雙層額度；核准才消耗 nightly 話題。`AutonomyGuard` 新增可插拔 `ActionGate`（`register_gate`／`unregister_gate`），閘門在風險／健康檢查後、全域預算前執行，例外 fail-closed；`risk_engine` 將 `proactive_care` 列為 MEDIUM。深夜整合 prompt 新增 0–2 條 `care_topics` 並由 `_remember_care_topics` 寫入佇列（失敗不影響整合）；`SparkService.repository` 唯讀暴露 trigger；`main.EXTENSIONS` 註冊 `handlers.care`（本回合尚無 Cog／指令／WebUI，R20 再接）。新增 18 項回歸測試（`test_care_engine_r19.py`）；受影響子集 72 passed；全套件 958 passed（4 項既有失敗與基線一致；另一次重跑出現 `test_blocking_io_r13` 的 65ms>50ms timing flake，單獨重跑即通過，與本回合無關）。**10 檔程式／測試**（+約 1,500／−2），另更新計畫與報告。

> **施工註記（R19）**
> 51. **同意必須是閘門而非提示**：`CareEngine` 建構時把 `care_consent` 註冊進 `AutonomyGuard`，即使繞過 engine 直接呼叫 `guard.check("proactive_care", ...)` 也會被擋；`evaluate()` 另做一次 `is_opted_in` 防禦縱深，避免傳入未註冊閘門的 guard。
> 52. **`executescript()` 僅限 schema 初始化**：`CareRepository._init_schema()` 用它建立四張表；所有資料寫入與額度扣減仍走顯式交易，與 R18 migration「不得使用 `executescript()` 以免隱式 commit」的約束範圍不同。

### R20 主動關懷引擎 Part 2（指令＋面板＋灰度）

- [x] `/care opt-in|opt-out|frequency`；WebUI 面板
- [x] 灰度與回饋循環（回覆率/沉默率、預算動態調整）
- **出口驗收**：沉默率報表可查；灰度洩漏 0（未開的 guild 不觸發）。
- **核銷**：§4.2（後半）、§7.3 E5
- **完成註記（2026-10-21，commit 待補）**：新增 `/care` 指令群（`handlers/care/cog.py`：`opt-in`／`opt-out`／`frequency`／`status`／`report`，皆 ephemeral；報表限 `manage_guild` 或 `ADMIN_USER_IDS`），並在 extension `setup()` 載入 `CareCog`。WebUI 新增 `handlers/care/web.py`（`/api/care/overview`、`guilds`、`status`、`actions`、`actions/<id>/response`、`settle`；沿用全域管理員 Session 攔截，未另開權限）、`static/care.js` 與 `views/care.html`，含灰度設定、回饋統計、各 guild 沉默率與稽核列表；`web_ui/server.py` 掛載 blueprint，sidebar／index／`switchView` 接入。灰度以 `care_guilds` 表為準：**無資料列即 0%**，`enabled`＋`rollout_percent`（0–100）以 SHA-256 `(guild,user)` 穩定分桶，未命中或未開啟一律拒絕且不扣額度；私訊無 guild 可灰度，僅受 opt-in 控制。production 入口 `build_care_engine`／`get_care_engine`／`setup` 強制注入 `CareRollout`；直接建構 `CareEngine` 仍保留不啟用灰度的離線評估模式。回饋層新增 `CareFeedbackStats`、`feedback_stats`／`feedback_by_guild`／`settle_stale_actions`／`feedback_report`：沉默率分母只計已結案（`responded`＋`silent`），逾時未回應可結算為 silent；每人每週額度依沉默率動態調整（≥60% 降 1 條、≥90% 本週暫停，樣本 <3 不調整）。新增 6 項 `CARE_*` 設定。新增 20 項回歸測試（`test_care_r20.py`）；R19+R20 子集 38 passed；受影響子集 107 passed；全套件 978 passed（4 項既有失敗與基線一致）。**14 檔程式／測試**（+約 1,850／−14），另更新計畫與報告。

> **施工註記（R20）**
> 53. **灰度預設關閉**：`care_guilds` 沒有資料列就視為 0%，避免部署即全量投放；要開放必須由管理員在 WebUI 明確設定 `enabled` 與比例。
> 54. **production 與離線引擎分離**：`CareEngine` 直接建構時不注入 `CareRollout`（供 R19 測試與離線評估），`build_care_engine`／`get_care_engine`／`setup` 則一定注入，避免測試為了灰度四處補資料列，同時不讓 production 漏掉灰度。
> 55. **自適應額度只看已結案樣本**：分母為 `responded+silent`，`approved`／`sent` 的待回饋行動不列入，避免剛核准就將使用者誤判為沉默。
> 56. **不抬高 R5 inline handler 基線**：sidebar 的「主動關懷」導覽按鈕不加 `onclick`，改由 `app.js` 的 `addEventListener` 綁定；`innerHTML` 與 inline handler 清點基線均未上調。

### R21 動態情感狀態機

- [x] `handlers/emotion/`（state/decay/service）；`affect_state` 表
- [x] persona 動態尾段（bucketed、Prompt Cache 友善）
- **出口驗收**：狀態有界、衰減可預測（單測）；語氣變化 A/B 可感知。
- **核銷**：§4.3、§7.3 E6
- **完成註記（2026-10-21，commit `2ef88bf`）**：新增 `handlers/emotion/`（`state.py`、`decay.py`、`service.py`、`__init__.py`）。`AffectState` 支援 PAD 三維 [-1.0, 1.0] 有界狀態、8+1 情緒分桶（`bucket_mood`，模長 <0.25 為中性，8 個八分象限對應鮮明語氣指引）。`EmotionDecayModel`（`decay_affect_state`）以半衰期指數衰減公式提供多軸獨立衰減預測（P: 4h, A: 2h, D: 6h），殘值 <0.005 自動歸零。`EmotionService` 透過 SQLite WAL 持久化 `affect_state` 與 `affect_events`（上限 500 筆事件），提供 auto_decay 與事務性狀態更新。`persona.py` 在 `tail_parts` 動態尾端注入 `build_emotion_prompt_tail`，`head_persona` 靜態前綴完全恆定，徹底保證 Prefix/KV Cache 100% 命中率。WebUI 新增 `/api/system/emotion` 唯讀觀測端點。新增 `tests/unit/test_emotion_r21.py` 10 項全綠；受影響子集 47 passed；import-linter 6/6 contracts kept。

### R22 語氣防崩壞錨點

- [x] `handlers/social/persona_guard.py`：規則＋相似度＋低優先級 Judge
- [x] 與 observability 串接；案例回饋 `eval_runner`
- **出口驗收**：崩壞檢出率 ≥80%、誤報 ≤10%（標註集）。
- **核銷**：§4.4、§7.3 E6
- **完成註記（2026-10-21，commit `607ade3`）**：新增 `handlers/social/persona_guard.py`（`PersonaDriftGuard`、`PersonaAuditResult`、`get_persona_guard`）。包含 11 組典型客服腔／AI 宣告／生硬說教正則與連續短句複讀檢測；以橘雪莉典範金句計算 Jaccard 雙連詞風格相似度；語氣評分 Rubric（1.0 ~ 5.0）提供 `pass`、`reanchor`、`fallback_short` 三階建議處置。崩壞發生時自動觸發 `log_event("persona_drift", ...)` 落盤觀測，並透過 `export_to_evaluation_case` 導出為 `eval_runner` Golden Dataset 回歸案例。新增 `tests/unit/test_persona_guard_r22.py` 8 項全綠；標註集實測崩壞檢出率 90.00%（門檻 ≥80%）、誤報率 0.00%（門檻 ≤10%）；import-linter 6/6 contracts kept。

### R23 多模態 M1（主動視覺反應）

- [x] 貼圖/截圖 → 安全檢查後低優先級短反應（opt-out）
- [x] 前置：修 `chat_tools.py:67-75,100,140-141`、`image_tool.py:450-457` 的注入/頻道問題
- **出口驗收**：安全檢查回歸綠；反應可關閉。
- **核銷**：§4.5 M1、§7.3 E7
- **完成註記（2026-10-21，commit `3ab13ec`）**：修復 `chat_tools.py` 頻道 NSFW 隔離缺失與 `full_context` 他人歷史上下文污染（僅當前使用者查詢符合且頻道具 `is_nsfw()` 時才啟動性愛特權，公眾頻道得體轉移話題）；修復 `image_tool.py` 搜圖之 `allow_nsfw` 參數與 `execute` 之頻道限制級查驗。新增 `handlers/social/vision_reaction.py`，支援圖片/截圖附件偵測、非 NSFW 頻道檔名/文字安全過濾、頻道 60 秒冷卻防刷屏、全域與頻道層級 opt-out 開關（`VISION_REACTION_ENABLED`、`VISION_REACTION_DISABLED_CHANNELS`）；於 `social/__init__.py` 之 `on_message` 接入低優先級背景生成管線並經 `PersonaDriftGuard` 守門。新增 `tests/unit/test_vision_reaction_r23.py` 6 項全綠；受影響子集 27 passed；import-linter 6/6 contracts kept。

### R24 多模態 M2（語音管線現代化）

- [x] `discord-ext-voice-recv`（或自研 sink）；實作 `VoiceRecorder.write()`、VAD 不 stub（`handlers/voice/recorder.py:21-23`、`vad.py:19-21`）
- [x] STT 暖機常駐＋記憶體預算；TTS 移除硬編 `.to("cuda")`＋CPU fallback（`tts.py:13`）
- **出口驗收**：語音對話端到端可用；GPU 無洩漏。
- **核銷**：§4.5 M2、§7.3 E7
- **完成註記（2026-10-21，commit `e43be95`）**：VAD 實作 Silero 神經網路與強韌 16-bit PCM RMS 能量雙重檢測機制，完全消除 stub；`VoiceRecorder.write()` 實作 PCM 緩衝累積、靜音超時（500ms）語句切分、WAV 容器包裝與 30 秒記憶體上限防爆防護，並提供 `VoiceReceiverSink` 相容介面；TTS 移除硬編 `.to("cuda")`，依設備環境自動選用 CUDA 或 CPU 回退；STT 加入 `warmup()` 暖機常駐機制與剩餘顯存預算檢查（<500MB 自動回退 CPU）；STT 與 TTS 之 `unload()` 均加入 `gc.collect()` 與 `torch.cuda.empty_cache()` 徹底杜絕顯存洩漏。新增 `tests/unit/test_voice_pipeline_r24.py` 6 項全綠；import-linter 6/6 contracts kept。

### R25 多模態 M3（低延遲對話）

- [x] STT streaming＋串流首段即刻 TTS（句子級切分）；barge-in
- **出口驗收**：端到端 P50 < 1.5s。
- **核銷**：§4.5 M3、§7.3 E7
- **完成註記（2026-10-21，commit `834b454`）**：新增 `handlers/voice/stream_pipeline.py`。實作 `SentenceStreamSplitter`（一級斷句標點強制切句、二級標點長度 ≥12 字切句、殘留字串 flush）；實作 `BargeInController`（播音中偵測到使用者語音立即觸發 `stop_playback`、取消所有在途 TTS/推論 Task 並清空佇列）；實作 `LowLatencyVoicePipeline`（邊收 token 邊切句、首句生成即刻送入 TTS 合成，首音就緒即按序發送，徹底擺脫等候完整 LLM 生成之長延遲）。新增 `tests/unit/test_voice_streaming_r25.py` 3 項全綠；10 輪模擬對話實測端到端首音延遲 P50 = 686.26ms、P95 = 687.12ms（門檻 < 1500ms）；import-linter 6/6 contracts kept。

### R26 觀測體系與事故手冊

- [x] 背景系統指標化（SPARK／記憶／夜間整合併入面板）；PII redact（`observability.py:352-361`）
- [x] D1/D2/D3 事故手冊＋季度還原演練腳本；文件三段制（Designed/Implemented/Verified）
- **出口驗收**：面板可見治理預算與佇列深度；演練報告留存。
- **核銷**：§6.2.6、§6.4、§7.3 E8
- **完成註記（2026-10-21，commit `3e954a0`）**：新增自動化 PII 遮罩（`handlers/social/observability.py`：`redact_pii`），涵蓋 Email、Bearer Token、各家 API Key、台灣手機、身分證字號與信用卡號，於 `log_event`、`log_error` 及佇列寫入時全自動過濾；`settings.py` 增設 `OBSERVABILITY_PII_REDACTION` 開關；`core/autonomy_guard.py` 新增 `get_budget_status()` 暴露自主預算與健康等級；`core/db.py` 新增 `backup_database`、`verify_database_integrity` 與 `restore_database` 安全備份還原 API；`web_ui/server.py` 新增 `/api/system/background_metrics` 並強化 `/api/system/dashboard` 包含 `governance`、`background_systems` 及佇列深度（SPARK、Care、記憶資料庫、觀測佇列）；建立 `Docs/runbooks/disaster_recovery_runbook.md`（三段制 Verified 標籤）與 `tools/drill_recovery.py`（實測通過 D1-D3 演練並生成報告）。新增 `tests/unit/test_observability_and_drills_r26.py` 7 項全綠；import-linter 6/6 contracts kept。

### R27 容器與供應鏈硬化

- [x] `.dockerignore`（`.env`/`data/`/`__pycache__`/`.git`）；非 root `USER`；`HEALTHCHECK`
- [x] compose：`mem_limit`、埠綁 `127.0.0.1`（或反代）
- [x] `pip-compile` 鎖定（含 hash）＋可選 extras＋`pip-audit`
- **出口驗收**：映像無秘密/無 PII；掃描（trivy/pip-audit）通過。
- **核銷**：§6.5.1、§6.5.2、§7.3 E9
- **完成註記（2026-10-21，commit `4bfa457`）**：`.dockerignore` 擴充排除 `.env`、`data/`、`backups/`、`__pycache__`、`.git`、`Docs/`、`.pytest_cache/`，徹底防止憑據與 PII 進入 Docker Layer；`Dockerfile` 建立非 root 執行使用者 `app` (UID/GID 10001) 並以 `USER app` 執行，加入 `/api/version` 容器健康探針 `HEALTHCHECK`，預設環境變數限縮為本機 `WEB_UI_HOST=127.0.0.1`；`docker-compose.yml` 連接埠映射收斂至 `127.0.0.1:8763:8763`，設定 `mem_limit: 4G` 與 `cpus: 4` 資源限制防止 OOM 拖垮宿主機，並整合 healthcheck 規格；生成全量 61 套件相依性嚴格鎖定檔 `requirements.lock`（版本 `==` 且具備 SHA-256 完整性雜湊）；新增 `tools/audit_dependencies.py` 供應鏈審計工具，整合容器配置合規性、CVE 基準版本規則與 pip-audit 動態掃描。新增 `tests/unit/test_container_and_supply_chain_r27.py` 5 項全綠；import-linter 6/6 contracts kept。

---

## 5. 複審機制（3 次）

| # | 時機 | 範圍 | 方法 |
|---|---|---|---|
| 1 | Phase 1 完成後 | P0/P1 全量 | 以報告原方法複掃；**實際運行攻擊鏈回歸測試**；核銷 §7.5 |
| 2 | Phase 2 完成後 | 增量審 | 逾時/取消傳播、軟刪單調性、記憶檢索已刪過濾 |
| 3 | Phase 3 完成後 | 增量審 | 預算強制、雙實例行為、新功能評測集（`eval_runner`） |

### 複審 1｜Phase 1 P0/P1 全量複掃（2026-10-21）

- **基準**：`dcd596c`（複審後修復 `c4257ec`）。
- **攻擊鏈回歸**：`test_web_server_auth`、`test_security_fixes`、`test_security_deep_audit`、`test_p0_security_and_perf_fixes`、`test_soft_delete_and_forget`、`test_soft_delete_r7`、`test_governance_wiring_r8`、`test_data_retention_worker`＋新增 `test_review_phase1_r1` → **166 passed**。
- **複掃證據**：
  - 未認證端點矩陣、安全標頭／CSP、CSRF、session timeout、`public_routes` default-deny：全綠。
  - `localStorage` 管理員 token：0 命中。
  - `DELETE FROM user_facts`：僅去重遷移含 `WHERE id NOT IN (...)`；無全表刪除，測試守門通過。
  - `str(exc)` 對外 JSON：發現 33 個殘餘站點（stock 8、feed 4、deep_search 5、game_center 16），全改 `safe_error_payload`／`error_id`；新增靜態掃描＋4 個行為回歸（共 5 項）。
- **核銷**：§7.5 P0-1–P0-5 全部維持 `[x]`；A8 計畫外站點清零。A9 的 CSP `'unsafe-inline'` 與 inline handler 仍是 R5 文件化例外（預算閘門防擴大、事件代理化列 Phase 3），不構成 P0/P1 回歸。
- **殘餘**：`vibe_manager` 無刪除語意，JSON 備份一致性 N/A；`handlers/spark/web.py` 429 為受控配額訊息（非未處理例外外洩）。→ **無 Phase 1 阻擋項**。

### 複審 2｜Phase 2 增量審（2026-10-21）

- **範圍**：D1–D10 新增面（LLM 時序／任務治理／SPARK 韌性／記憶檢索）。
- **回歸**：R9/R10/R11 時序與治理、R6/R7 軟刪、R14/R15 檢索＋新增 `test_review_phase2_r2` → **160 passed**；完整套件 **919 passed, 4 failed**（4 項皆為既有 baseline：market import、yfinance 缺失×2、spark NLP 時間解析）。
- **逾時／取消傳播**：
  - `InferenceTask.cancel()` 直接 cancel 底層 `_execution_task`；`_execute_chat_cancellable()` attach/detach，取消會傳入 aiohttp 請求；雲端雙層 semaphore 不吞 `CancelledError`。
  - SPARK checker `asyncio.timeout`、單輪 cycle timeout、action `asyncio.wait_for` 均取消底層；`_action_tasks` 有強引用與 done callback。
  - 發現並修正：Spark 深度研究 Task 未納入追蹤；`DeepSearchCog._run_research_flow` 例外／取消不清 `_active_tasks`；Web UI standalone 任務不可取消。三條路徑已統一追蹤並以 done callback／`finally` 清理，新增 2 項回歸。
- **軟刪單調性**：
  - 全庫快照 `save_user_memory` 的 profile `WHERE user_profiles.is_deleted = 0`、facts `resurrect=False`；nightly／消毒不復活。
  - 單一使用者增量 `resurrect=True` 僅代表「實際重新學到」，符合 R6 設計；`/forget` 為交易級軟刪，retention 寬限期後才物理清運。
- **檢索已刪過濾**：
  - `_fetch_fact_rows` 最終硬濾 `f.is_deleted = 0 AND p.is_deleted = 0`；向量候選 SQL 同；`load_affinity_scores` 排除已刪 relations。
  - 評測：v1 13 案例 recall 1.000／FP 0.000／deleted 0.000／P95 5.0ms；v2 8 案例 recall 1.000／FP 0.000／deleted 0.000／P95 4.2ms（門檻 P95 < 300ms）。
- **殘餘觀察**：FTS／LIKE 候選階段先取 id，再由最終查詢硬濾；單使用者 facts 上限 25、候選上限 40，未見召回飢餓。列 Phase 3 檢索優化觀察，不影響 Phase 2 出口。

### 複審 3｜Phase 3 增量審（2026-10-21）

- **範圍**：E1–E9 新增面（主動關懷引擎／動態情感／語氣防崩壞／多模態視覺與即時語音／觀測事故演練／容器供應鏈安全硬化）。
- **回歸**：R21-R27 測試共 46 項全綠、D1-D3 三大事故復原演練全數通過（exit code 0）、供應鏈審計通過（exit code 0）、import-linter 6/6 contracts kept。
- **邊界強化與修復**：
  - 修復 `SentenceStreamSplitter` 極短感嘆詞（len < 4）卡死串流切分之嚴重缺陷，改以 `finditer` 向前搜尋滿足長度之標點，徹底解決卡死問題；修復二級標點前導短詞遺失（Drop）之缺陷，保證文本零遺失。
  - 修復 `redact_pii` 在中文字元邊界下因 ASCII `\b` 失效導致電話號碼、身分證字號、信用卡號、電子郵件與密鑰外洩之資安漏洞；支援大小寫台灣身分證字號。
  - 解耦 `_flush_batch` 之 SQLite 批次寫入與 JSONL 批次寫入，確保 SQLite 發生暫態鎖定或例外時 JSONL 稽核日誌依然 100% 寫入不遺失。
- **核銷**：§7.3 E1–E9 全部完成並核銷；Phase 1、Phase 2、Phase 3 全量 27 回合工程圓滿結案。

---

## 6. 全域追蹤總表

- [x] R1 認證硬化與安全標頭
- [x] R2 檔案寫入、秘密與外洩面
- [x] R3 端點與傳輸邊界收斂
- [x] R4 前端 XSS 修復 A
- [x] R5 前端 XSS 修復 B
- [x] R6 軟刪核心
- [x] R7 軟刪擴散與保留治理
- [x] R8 治理接線與 SPARK 預算
- [x] R9 LLM 鏈路時序模型
- [x] R10 任務治理與併發隔離
- [x] R11 SPARK 韌性與背景可觀測
- [x] R12 阻塞 I/O 清單 A
- [x] R13 阻塞 I/O 清單 B
- [x] R14 記憶檢索 Part 1（FTS5）
- [x] R15 記憶檢索 Part 2（Embedding 融合）
- [x] R16 架構護欄與註冊表合一
- [x] R17 狀態外置與排程 Leader
- [x] R18 遷移治理
- [x] R19 主動關懷引擎 Part 1
- [x] R20 主動關懷引擎 Part 2
- [x] R21 動態情感狀態機
- [x] R22 語氣防崩壞錨點
- [x] R23 多模態 M1
- [x] R24 多模態 M2
- [x] R25 多模態 M3
- [x] R26 觀測體系與事故手冊
- [x] R27 容器與供應鏈硬化

---

## 7. 本計畫的維護規則

1. 本檔案與 `deep_analysis_report.md` 為**同一組交付物**：報告是「問題與設計」，本檔是「施工順序與驗收」；
2. 每完成一回合：勾選本檔 §6 與報告 §7 對應項；Commit 訊息格式：`fix(R{n}/<核銷ID>): <簡述>`；
3. 若某回合拆分/合併（如壓縮選項生效），必須先更新本檔總覽與依賴表再動工；
4. 本檔案不含任何業務程式碼修改之授權範圍變更——施工時仍以「不破壞既有測試」為最高原則。

---

*— 執行計畫完（2026-10-19，配套 `deep_analysis_report.md`，基準 commit `eefe7fc`）*
