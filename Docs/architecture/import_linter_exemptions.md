# Import-linter 豁免清單（R16 架構護欄）

> 規則：每一條 `ignore_imports` 都必須在此登記「理由、到期日、追蹤回合」。
> CI 測試 `tests/unit/test_architecture_guard_r16.py` 會檢查：
> 1. `.importlinter` 中的豁免總數不得超過預算（40 條）；
> 2. 本文件必須存在且包含所有契約名稱。
> 新增豁免而未同步本文件／未調整預算，測試即紅燈。

## 到期日：2027-03-31

### Contract 1 — mod 禁 I/O 庫（既有 12 條）

| 豁免 | 理由 | 追蹤 |
|---|---|---|
| `mod.clients.*` / `mod.*.clients.*` | 外掛自有 HTTP/SDK 轉接層，屬 adapter | 長期 |
| `mod.*.repositories.*` / `mod.*.repository` | Repository 邊界即 I/O adapter | 長期 |
| `mod.*.cog` / `mod.*.ui` / `mod.*.views` | Discord UI／Cog 本質需 discord | 長期 |
| `mod.*.reports.*` | 報表輸出 adapter | 長期 |
| `mod.dnd.world_sim -> discord` | 世界模擬需 Discord 型別 | 2027-03-31 |
| `mod.market_plugin.utils -> discord` | Embed 建構工具 | 2027-03-31 |
| `mod.feed_sentinel.fetcher -> *` | RSS 抓取 adapter | 2027-03-31 |
| `mod.market_plugin.data_fetcher -> *` | 行情抓取 adapter | 2027-03-31 |

### Contract 4 — 下層不得 import Web UI（3 條）

| 豁免 | 理由 | 追蹤 |
|---|---|---|
| `mod.livestream.cog -> web_ui.server` | 串流模組取 Quart app 掛載 | R17／2027-03-31 |
| `mod.livestream.web -> web_ui.server` | 共用自簽憑證工具 | R17／2027-03-31 |
| `mod.painter.web -> web_ui.server` | 取 bot 實例 | R17／2027-03-31 |

### Contract 5 — handlers 不得 import mod（7 條）

| 豁免 | 理由 | 追蹤 |
|---|---|---|
| `handlers.executor.cog -> mod.dnd.views` | Context Menu 動態 UI 入口 | R16 後續／2027-03-31 |
| `handlers.executor.cog -> mod.livestream.ui` | Context Menu 動態 UI 入口 | R16 後續／2027-03-31 |
| `handlers.executor.cog -> mod.market_plugin.ui` | Context Menu 動態 UI 入口 | R16 後續／2027-03-31 |
| `handlers.social.tools.search_tools -> mod.deep_search.repositories.search_client` | 精選工具共用搜尋 client | 2027-03-31 |
| `handlers.social.tools.draw_tool -> mod.clients` | 共用 GeminiImageClient | 2027-03-31 |
| `handlers.social.tools.image_tool -> mod.clients` | 共用 Booru/Gemini client | 2027-03-31 |
| `handlers.spark.clients.web_monitor_client -> mod.deep_search.repositories` | 監測站台抓取 | 2027-03-31 |

### Contract 6 — mod 僅可使用穩定 handler 契約（12 條）

| 豁免 | 理由 | 追蹤 |
|---|---|---|
| `mod.clients.cloud_inference_client -> handlers.bot_brain.client` | 雲端推論 fallback 取 Ollama client | 2027-03-31 |
| `mod.deep_search.repositories.llm_client -> handlers.bot_brain.client` | 深度研究 LLM client | 2027-03-31 |
| `mod.dnd.cog` / `mod.dnd.views -> handlers.bot_brain` | 外掛推論佇列 DTO | 2027-03-31 |
| `mod.feed_sentinel.cog -> handlers.bot_brain` | 外掛推論佇列 DTO | 2027-03-31 |
| `mod.feed_sentinel.cog -> handlers.social.persona` | 動態人設建構 | 2027-03-31 |
| `mod.game_center.sherry_ai -> handlers.bot_brain.client` | 外掛 LLM client | 2027-03-31 |
| `mod.painter.cog` / `mod.painter.web -> handlers.bot_brain` | 外掛推論佇列 DTO | 2027-03-31 |
| `mod.livestream.cog` / `mod.livestream.web` / `mod.painter.web -> web_ui.server` | 與 Contract 4 同一組反向依賴的間接鏈終止 | 2027-03-31 |

## 預算

- 目前豁免總數：**34**（Contract 1：12、Contract 4：3、Contract 5：7、Contract 6：12）。
- CI 上限：**40**。任何新增都必須先在此登錄理由與到期日。
