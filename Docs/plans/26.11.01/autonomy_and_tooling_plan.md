# 雪莉工具可用性與 L2 自主性更新計畫

> 基準 commit：`825b700`（`26.11.01.001-控制台UI深色質感升級與核心頁重構`）
> 狀態：**已實作（2026-11-01）**，Phase 1～5 全部落地
> 適用範圍：cyber v2 lane（沙盒頻道）
> 不包含：`/mod`、說話長度與公式調整（另案）、30 分鐘主動頻率調整

---

## 0. 摘要

這份計畫處理兩件事：

1. **工具真的能用、她真的知道自己能用什麼。**
   - 修好 proactive 回合的工具 runner（目前名稱接錯，呼叫會失敗）。
   - 從 `TOOL_SPECS` 自動產生能力提示，不再手寫。
   - 把「這一輪為什麼開口」寫進 context。

2. **把「要不要開口」的決定權還給她（L2 自主）。**
   - 程式只決定「什麼時候可以考慮」。
   - 雪莉自己決定「說 / 安靜 / 晚點說」。
   - 仍然限定在指定頻道，所有硬護欄不變。
   - WebUI 看得到她的決定、動機與內心戲（Phase 5B／5D）。

五個 phase 可以分開上線，不必一次全部做完。

---

## 1. 現況與問題

### 1.1 工具

目前專案有兩套工具路徑：

| Lane | 工具 | 狀態 |
|---|---|---|
| 被動回覆（被 tag） | `SUMMARY`、`MUSIC`、`SCHEDULE`、`DRAW`、`PIXIV`、`STOCK`、`SYSTEM_INSPECT`、`SEARCH`、`CHAT` | 正常，走 SocialChat dispatch |
| 自主 / ambient | `SEARCH`、`STOCK`、`SUMMARY`（唯讀） | 正常，走 `ToolBroker` |
| **proactive** | prompt 說有 `SEARCH` / `STOCK` / `SUMMARY` | **實際接錯** |

問題在 `handlers/care/cyber/worker.py`：

```python
if tools_enabled and backend == "readonly":
    runner = _BrokerAdapter(ToolBroker(self.bot), tool_policy)
elif tools_enabled:
    runner = self.model.tool_registry   # CyberToolRegistry
```

`proactive` 的 `tool_policy=None`，所以掉進第二條。但 `CyberToolRegistry.run()` 只認得：

- `web_search`
- `fetch_url`

prompt 卻教模型輸出 `SEARCH` / `STOCK` / `SUMMARY`，於是實際呼叫會得到：

```
error: unknown tool 'SEARCH'
```

也就是說：**被動與 ambient 的工具是通的，只有 proactive 那條線是啞的。**

### 1.2 自我能力認知

`build_system_prompt()` 只在工具開啟時塞三行協議：

```
- 查網路／新聞：[[TOOL:SEARCH ...]]
- 查股票／行情：[[TOOL:STOCK ...]]
- 總結網址：[[TOOL:SUMMARY ...]]
```

沒有「這一輪哪些工具可用、哪些不可用、不確定該怎麼回答」。所以她被問「你能做什麼」時，是靠人設即興回答，而不是系統事實。

### 1.3 主動話題是程式驅動的

`handlers/care/cyber/engine.py` 的 `_proactive_loop()`：

```python
if ready and (now - last_fire) >= interval:
    web_topic = await self._fetch_web_topic()
    await self._worker.submit_proactive(reason="mind_pulse", web_topic=web_topic)
    last_fire = now
```

只要 `ready`（無聊值／社交值／安靜 30 分鐘）成立，就一定會生成並發送。她只有「怎麼講」的自由，沒有「不講」的自由。

`build_dynamic_context()` 也只給她時間、待回訊息數、上次發言、安靜分鐘數，沒有「這一輪為什麼是你主動」的動機。

---

## 2. 全局約束（地端 prompt 預算）

### 2.1 目前 cyber 每回合的 prompt 組成

| 區塊 | 字數 | 特性 |
|---|---:|---|
| SOUL 人設 | 2,462 | 靜態、可快取 |
| 通用規則 | 1,096 | 靜態、可快取 |
| CHAT 狀態設定 | ~140 | 靜態 |
| `<rules>` ＋工具協議 | ~320 | 靜態 |
| 記憶區塊 | 1,400 | 每回合變動 |
| 頻道最近 12 則 | ~1,400 | 每回合變動 |
| 動態時間／情緒／尾註 | ~300 | 每回合變動 |
| **合計** | **~7,100 字** | **≈ 14k tokens**（Gemma 級 tokenizer） |

### 2.2 本計畫遵守的規則

1. 能力提示**只放工具名稱**，不放 `llm_description`。
   （8 個工具的完整描述約 500 字，會多 ≈1,000 tokens。）
2. 能力提示放 **system prompt**，保持靜態、可被前綴快取重用。
3. 名稱順序**固定依 `TOOL_SPECS`**，不從 `set` 迭代，避免每次前綴不同導致 KV cache 全滅。
4. 動機與自主狀態只加在 **proactive** 回合；一般接話、被 tag 的回合 0 成本。
5. proactive 工具結果上限 **1,200 字**，避免一次塞 4,000 字（≈8k tokens）。
6. 不新增額外 LLM 呼叫（L2 自主採單次生成內決策）。

### 2.3 預估成本

| 項目 | 靜態 | 每回合 |
|---|---|---|
| Phase 1 runner | 0 | 0（只有真的呼叫工具時才有結果） |
| Phase 2 能力提示 | +約 60 字（≈120 tokens），之後快取 | ≈0 |
| Phase 3 動機 | 0 | proactive +約 25 字（≈50 tokens） |
| Phase 4 spec 統一 | 0 | 0 |
| Phase 5 自主 | +約 120 字（≈240 tokens），proactive prefix | proactive +約 80 字（≈160 tokens） |

---

## 3. Phase 1：修 proactive 的 tool runner

**目標**：`tool_policy=None` 時不再掉進 `CyberToolRegistry`，改用和 ambient 一致的唯讀 `ToolBroker`。

### 3.1 設計

新增兩個 helper（純函式，方便測試）：

```python
def _resolve_tool_policy(tool_policy, ui):
    if tool_policy is not None:
        if getattr(tool_policy, "backend", "none") == "none":
            return None
        return tool_policy
    # 沒有指定 policy（proactive / submit_human）→ 預設唯讀
    allowed = normalize_tools(getattr(ui, "allowed_tools", None)) or AUTONOMOUS_POLICY.allowed
    return ToolPolicy(
        backend="readonly",
        allowed=frozenset(allowed),
        max_calls=max(1, min(3, int(getattr(ui, "tool_max_rounds", 2) or 2))),
        allow_direct_ui=False,
    )

def _resolve_tool_runner(policy, bot):
    if policy is None:
        return None
    if getattr(policy, "backend", "none") == "readonly":
        return _BrokerAdapter(ToolBroker(bot), policy)
    logger.warning("[CyberV2] cyber lane 不支援 backend=%s，降級為唯讀", policy.backend)
    return _BrokerAdapter(ToolBroker(bot), _readonly_version(policy))
```

要點：

- 明確的 `backend="none"` 仍然代表「這回合不給工具」。
- `tool_policy=None` 代表「沒有指定」，不是「禁止工具」。
- UI 的 `allowed_tools` 優先；空清單才退回 `AUTONOMOUS_POLICY.allowed`。
- cyber lane 只支援唯讀工具；收到 `dispatch` 時降級並記 warning，不靜默升級權限。

### 3.2 `worker.py` 的改動

`_execute_turn()` 內：

```python
policy = _resolve_tool_policy(getattr(request, "tool_policy", None), ui)
runner = _resolve_tool_runner(policy, self.bot) if tools_enabled else None
```

並把 `tool_policy=policy` 傳進 `run_tool_loop()`。

刪掉現在「`backend == "readonly"` 才用 broker，其餘用 `self.model.tool_registry`」的分支。

### 3.3 proactive 工具結果上限

`handlers/care/cyber/model.py`：

- `run_tool_loop()` 新增 `tool_result_max_chars: int = 0`。
- 預設 `0` = 維持原行為。
- 大於 0 時，每個 `<tool_result>` 內容截到上限並加 `…（已截斷）`。

`worker.py`：

```python
tool_result_max_chars=1200 if mode == "proactive" else 0
```

### 3.4 測試

- `test_proactive_none_policy_uses_readonly_broker`
- `test_explicit_no_tool_policy_disables_tools`
- `test_proactive_tool_result_truncated`
- `test_ambient_tool_result_not_truncated`

### 3.5 驗收

- log 不再出現 `error: unknown tool 'SEARCH'`。
- 主動回合的 `cyber_turn` 事件有 `tool_rounds >= 1`，且後續有正常回覆。

**Prompt 影響：0。**

---

## 4. Phase 2：能力區塊（single source of truth）

**目標**：讓她明確知道「這一輪能用什麼、不能用什麼」，清單由 spec 自動產生。

### 4.1 `ToolSpec` 增加中文標籤

`handlers/social/tool_specs.py`：

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    aliases: tuple[str, ...] = ()
    label: str = ""
```

補上：

| name | label |
|---|---|
| `SUMMARY` | 網址摘要 |
| `MUSIC` | 音樂 |
| `SCHEDULE` | 鬧鐘／提醒 |
| `DRAW` | 畫圖 |
| `PIXIV` | Pixiv |
| `STOCK` | 股票 |
| `SYSTEM_INSPECT` | 系統檢查 |
| `SEARCH` | 搜尋 |
| `CHAT` | 閒聊 |

### 4.2 新增格式化函式

```python
def format_capability_hint(allowed, *, exclude={"CHAT"}) -> str:
    """依 TOOL_SPECS 順序產生兩行短提示；只回名稱，不回 llm_description。"""
```

規則：

- 用 `resolve_tool_name()` 正規化別名。
- 依 `TOOL_SPECS` 宣告順序輸出。
- 排除 `CHAT`（它不是使用者會直接要求的工具）。
- 長度上限 80 字。

### 4.3 `build_system_prompt()` 的改動

```python
def build_system_prompt(
    *,
    persona: str,
    tools_enabled: bool = False,
    mode: str = "ambient",
    static_context: str = "",
    allowed_tools: Iterable[str] | None = None,
) -> str:
```

- `allowed_tools is None` → 輸出與現在完全相同（向後相容）。
- `tools_enabled and allowed_tools is not None` → 把提示併進現有 `<rules>`，不新增區塊。

實際會長這樣（約 60 字）：

```
- 本回合可用：搜尋、股票、網址摘要。
- 其他工具（音樂、鬧鐘、畫圖、Pixiv、系統檢查）不可用；不確定就說做不到，不要先答應。
```

> 第二行是「全部 spec − allowed」自動算出來的，不是手寫。

### 4.4 測試

- `test_capability_hint_lists_only_allowed`
- `test_capability_hint_is_deterministic`（連跑 10 次字串相同）
- `test_capability_hint_absent_when_tools_disabled`
- `test_capability_hint_length_budget`（≤80 字）
- `test_capability_hint_normalizes_aliases`（`web_search` → `SEARCH`）

**Prompt 影響：+約 60 字，靜態、可快取。**

---

## 5. Phase 3：把「動機」寫進 dynamic context

**目標**：讓她知道這一輪是「無聊／想聊天／安靜太久／手動觸發」才開口。

### 5.1 engine 產生動機字串

`_proactive_loop()` 裡本來就算好了 `boredom`、`social`、`idle_ready`：

| 條件 | 動機字串 |
|---|---|
| `boredom >= boredom_th` | `無聊累積 82/80` |
| `social <= social_th` | `想聊天（社交滿足 25）` |
| `idle_ready` | `頻道安靜 32 分鐘` |
| 其他 fallback | `排程時間到` |

呼叫改成：

```python
await self._worker.submit_proactive(
    reason="mind_pulse",
    web_topic=web_topic,
    motivation=motivation,
)
```

### 5.2 傳遞鏈

```
engine._proactive_loop
  → worker.submit_proactive(motivation=...)
  → worker._pending_motivation
  → worker._handle_proactive 放進 request
  → worker._execute_turn
  → build_dynamic_context(trigger="proactive", motivation=...)
```

### 5.3 `build_dynamic_context()` 的改動

```python
def build_dynamic_context(
    *,
    now: float,
    unanswered: int,
    last_bot_at: float,
    last_human_at: float,
    phase: str,
    silence_seconds: float,
    trigger: str = "",
    motivation: str = "",
) -> str:
```

只有 `trigger == "proactive"` 時多一行：

```
- 這一輪：你自己想開話題（無聊累積 82/80）
```

一般接話／被 tag：完全不加。

### 5.4 測試

- proactive 的 dynamic block 含「你自己想開話題」與原因。
- ambient／reply 不含這一行。
- engine → worker 的 motivation 有正確傳遞。
- 沒有 motivation 時只顯示「你自己想開話題」，不顯示空括號。

**Prompt 影響：proactive 每回合 +約 25 字。**

---

## 6. Phase 4：被動 lane 工具描述統一

### 6.1 先說結論

被動 lane **現在已經是**從 `TOOL_SPECS` 產生：

- `dispatch_agent._build_tool_descriptions()` 走的是 spec 對應的工具實例與 `llm_description`。

手寫三行的是 **cyber 的 `prompts.py`**，不是被動 lane。

### 6.2 要做的兩件事

1. `handlers/social/dispatch_agent.py`
   - `_build_tool_descriptions()` 改成明確迭代 `TOOL_SPECS`（而非 `self._tools.items()`）。
   - 保留原本排除內部 agent 與 `CHAT` 的規則。
   - 輸出順序與內容不變，屬於行為中性的重構。

2. 回歸測試
   - 每個 AI-callable spec（排除 `CHAT`）都要出現在描述裡，且帶自己的 `llm_description`。
   - 內部 agent（`PARALLEL_AGENT` 等）不得出現。
   - 連跑兩次輸出相同。

**Prompt 影響：0。**

---

## 7. Phase 5：L2 單次生成自主

> Phase 5 依賴 Phase 3（動機）。Phase 3 先做，Phase 5 才有狀態可以判斷。

### 7.1 設計決定

| 項目 | 決定 |
|---|---|
| 誰決定「要不要說」 | 雪莉（模型輸出 `SILENT` / `WAIT` / 正常文字） |
| 誰決定「什麼時候可以考慮」 | 程式（機會窗：安靜、時段、冷卻、額度） |
| 機會頻率 | 維持 30 分鐘最小間隔，不增加呼叫次數 |
| `SILENT` | 不送、不消耗 proactive 額度、下次機會照正常間隔 |
| `WAIT:n` | 不送、下次機會延到 n 分鐘後（限制 30～90） |
| 生效範圍 | 只有 `mode="proactive"` |
| 頻道 | 只允許 `sandbox_channel_id`，engine 與 worker 各擋一次 |
| 關閉開關 | `autonomy_enabled=false` 時完全回到現在行為 |
| 額外 LLM 呼叫 | 0 次 |

### 7.2 Prompt：靜態區塊

放在 `<rules>` 之後，只在 `mode="proactive"` 且 `autonomy_enabled=true` 時出現：

```
<autonomy>
現在頻道很安靜，這是你自己的時間。
你可以：
- 想說就說：直接講你真正想說的，1～2 句。
- 不想說：只輸出 [[SILENT]]
- 想晚點說：只輸出 [[WAIT:45]]（30～90 分鐘）
沒有想說的就安靜，不用填滿沉默。
</autonomy>
```

### 7.3 Prompt：動態狀態

只在 proactive 回合：

```
- 這一輪：你自己的時間（無聊累積 82/80）
- 你現在：無聊 82/100、社交滿足 25/100
- 距離上次你開口：35 分鐘
- 今天你主動開話題：3 次
- 上次你選擇安靜：25 分鐘前
```

- 由 Phase 3 的 `motivation` 擴充。
- 約 80 字。
- 「上次你選擇安靜」讓她有連續感，不會每次重新決定。

### 7.4 輸出協議

新增 `parse_autonomy_reply(text) -> (act, minutes)`：

| 輸入 | 結果 |
|---|---|
| 整則只有 `[[SILENT]]`（容忍空白、大小寫） | `("silent", 0)` |
| 整則只有 `[[WAIT:45]]` | `("wait", 45)`，clamp 30～90 |
| 其他 | `("speak", 0)` |

**只認整則完全符合。** 若協議混在句子裡，視為說話，並由 `sanitize_reply()` 清掉殘留。

### 7.5 `worker.py` 的狀態與流程

新增狀態：

```python
self._autonomy_next_at = 0.0          # 下次可以再考慮的時間
self._pending_motivation = ""          # 由 engine 帶入
self._pending_mood = ""                # 由 engine 帶入
self._last_autonomy = None             # {"act","at","wait","reason"}
self.proactive_budget = ReplyBudget(   # 只計算主動發言
    channel_id=self.channel_id,
    path=data_dir / "cyber_autonomy_ledger.json",
)
```

流程：

1. `submit_proactive()` 新增 `motivation`、`mood` 參數並暫存。
2. `_handle_proactive()`：
   - 檢查 `autonomy_enabled`。
   - 檢查 `proactive_budget.allow(autonomy_hourly, autonomy_daily)`。
   - 把 `motivation`、`mood` 放進 request。
3. `_execute_turn()`：
   - **頻道護欄**：proactive 時 `channel.id != sandbox_channel_id` → abort，不生成。
   - 生成成功後、`sanitize_reply()` 之前：
     ```python
     if mode == "proactive" and autonomy_enabled:
         act, wait = parse_autonomy_reply(result.text)
         if act in {"silent", "wait"}:
             self._finish_autonomy(ticket, act, wait, decision)
             return
     ```
4. 新增 `_finish_autonomy()`：
   - `runtime.abort_turn(ticket)`（會還原 `last_bot_at`，不會留下幽靈窗口）
   - `_autonomy_next_at = now + (wait * 60 if wait else interval)`
   - `_record_event({"event": "autonomy_decision", ...})`
   - 不呼叫 `_record_web_topic()`（她沒有發文，文章不標記已讀）
   - 不消耗 proactive 額度
5. `_after_send()`：
   - proactive 發送時 `proactive_budget.record(sent.first_ts)`。
6. 新增 property `autonomy_next_at` 給 engine 讀。

### 7.6 `engine.py` 的排程

原本：

```python
if ready and (now - last_fire) >= interval:
    ...
```

改成：

```python
next_allowed = max(last_fire + interval, worker.autonomy_next_at)
if ready and now >= next_allowed:
    motivation, mood = self._describe_mind(...)
    await worker.submit_proactive(
        reason="mind_pulse",
        web_topic=web_topic,
        motivation=motivation,
        mood=mood,
    )
    last_fire = now
```

- `_describe_mind()`：把已算好的 `boredom`／`social`／`idle_ready` 轉成短句。
- `submit_proactive()` 對外也加 `motivation`、`mood`（WebUI 手動觸發用）。
- `autonomy_enabled=false` 時 `worker.autonomy_next_at` 永遠 0，行為與現在完全相同。

### 7.7 UI 設定

`handlers/care/cyber/ui_config.py` 新增：

```python
autonomy_enabled: bool = True
autonomy_hourly: int = 2
autonomy_daily: int = 12
autonomy_wait_max_minutes: int = 90
autonomy_thought_enabled: bool = True   # 是否要求 [[THINK: ...]]
```

正規化：

| 欄位 | 範圍 |
|---|---|
| `autonomy_hourly` | 0～12 |
| `autonomy_daily` | 0～48 |
| `autonomy_wait_max_minutes` | 30～180 |

WebUI：

| 檔案 | 改動 |
|---|---|
| `web_ui/templates/views/care.html` | 加「啟用自主判斷」「顯示內心戲」checkbox、每小時上限、每日上限、最長等待分鐘 |
| `handlers/care/static/care.js` | `loadCyberV2Config` / `saveCyberV2Config` 加這四個欄位 |
| `handlers/care/web.py` | 沿用現有 config POST；確認 `from_dict` 有吃新欄位 |

### 7.8 觀測

新增事件：

```json
{
  "event": "autonomy_decision",
  "act": "silent",
  "wait_minutes": 0,
  "reason": "mind_pulse",
  "next_at": 1760000000.0,
  "mood": "無聊 82/100、社交滿足 25/100"
}
```

`cyber_turn` 的 `outcome` 增加 `silent` / `wait`，這樣 log 裡能分辨「她不想講」和「壞掉沒送出去」。WebUI 的呈現方式見 7.12。

### 7.9 硬護欄（不可被模型覆蓋）

依序檢查，任何一項失敗就不生成或不送：

1. `channel.id == sandbox_channel_id`（engine + worker）
2. `autonomy_enabled`
3. 活躍時段
4. proactive 冷卻（`contact_cooldown`）
5. `proactive_budget` 每小時／每日上限
6. 既有 `decide_proactive` 的窗口、最近活動、總額度
7. 送出前既有的長度、氣泡、URL、`@everyone` 規則

模型只能在這七道門之後決定「說／不說／晚點說」。

### 7.10 測試

| 測試 | 內容 |
|---|---|
| `test_parse_autonomy_silent` | 空白、大小寫、整則匹配 |
| `test_parse_autonomy_wait_clamped` | `WAIT:5` → 30、`WAIT:300` → 90 |
| `test_parse_autonomy_embedded_is_speech` | 「我想想 [[SILENT]]」→ speak，sanitize 清掉協議 |
| `test_proactive_silent_does_not_send` | 不呼叫 `send_bubbles`、不記 budget、`next_at` 正確 |
| `test_proactive_wait_delays_next_opportunity` | engine 讀到 `autonomy_next_at` 不再 30 分鐘觸發 |
| `test_autonomy_disabled_keeps_old_behavior` | `[[SILENT]]` 不會被當成決策；prompt 不含 autonomy 區塊 |
| `test_proactive_channel_guard` | 非 sandbox 頻道 → abort、不生成 |
| `test_autonomy_hourly_cap` | 達上限後不再生成 |
| `test_mention_never_silent` | mention 回覆含 `[[SILENT]]` 也不會被吞掉 |
| `test_autonomy_prompt_only_proactive` | ambient／reply 的 system prompt 不含 `<autonomy>` |

### 7.11 預設值

| 欄位 | 建議值 | 理由 |
|---|---|---|
| `autonomy_enabled` | `true` | 你就是要這個；隨時可關 |
| `autonomy_hourly` | `2` | 配合 30 分鐘間隔 |
| `autonomy_daily` | `12` | 比現在最多 48 次少很多 |
| `autonomy_wait_max_minutes` | `90` | 太長她會忘記自己想做什麼 |
| `autonomy_thought_enabled` | `true` | 想看到內心戲就開；關掉可省 output tokens |
| 最小間隔 | 維持 30 分鐘 | 先不加機會，只加「拒絕」的權利 |

### 7.12 WebUI 自主觀察面板（紀錄與顯示）

**目標**：在「賽博群友」頁看得到：

- 她現在想不想說話
- 說話動機
- 內心戲（她自己的一句話）
- 上次決定、下次考慮時間、今日主動次數
- 最近 20 次自主決定
- 被程式擋下的原因（時段／冷卻／額度／熱聊中）

#### 現況

- `/api/care/cyber/events` 端點已存在，但前端目前沒有接。
- 頁面上的「最新自言自語」來自舊 surfer 的 `mind_state.last_thought`，v2 live 模式下可能是過期資料。
- `engine._events` 只在記憶體，重啟就消失，不能當「紀錄」。

#### 三種狀態要分清楚

| 狀態 | 來源 | 顯示 |
|---|---|---|
| 她選擇說話 | `autonomy_decision.act="speak"` | 🗣️ 想說話 |
| 她選擇安靜／晚點 | `act="silent"` / `act="wait"` | 🤫 想安靜／⏳ 晚點再說 |
| 程式沒給她機會 | `decision.reason`（時段、窗口、冷卻、額度） | ⛔ 這一輪沒問她（原因） |

第三種很重要：不然你會分不清「她不想講」和「程式根本沒讓她講」。

#### 內心戲來源

| 方案 | 做法 | Token | 評價 |
|---|---|---|---|
| A（推薦） | 模型輸出 `[[THINK: ...]]`，解析後只存在 WebUI，不送出 | +20～60 output tokens／次 | 最真實 |
| B | 用動機字串＋回覆內容推導 | 0 | 不是真的內心戲 |
| C | 另外呼叫一次小模型 | +1 次呼叫 | 不建議 |

方案 A 的協議：

```
[[THINK: 大家好像都睡了，現在丟新聞有點怪。]]
[[SILENT]]
```

```
[[THINK: 這個新聞太蠢了，想吐槽。]]
欸，你們看到那個…
```

規則：

- `[[THINK: ...]]` 只取第一行、上限 60 字。
- 解析後從回覆移除，**絕對不能**送進 Discord。
- 若移除後只剩 `[[SILENT]]`／`[[WAIT:n]]` → 照自主協議處理。
- 若移除後什麼都不剩 → 視為 silent。
- `autonomy_thought_enabled=false` 時不要求、也不解析 THINK。

#### 持久化

新增 `data/cyber_autonomy.jsonl`（append-only）：

```json
{"ts":1760000000.0,"act":"silent","motivation":"無聊累積 82/80","thought":"大家好像都睡了…","wait_minutes":0,"next_at":1760001800.0,"reason":"mind_pulse","bubbles":0}
```

- 每次 proactive 決定寫一筆。
- 超過 500 筆輪替（保留最新 500）。
- 重啟後仍看得到歷史。
- 不寫進 `self_memory`，避免把內心戲變成永久記憶噪音。

#### API

`GET /api/care/cyber/status` 增加：

```json
"autonomy": {
  "enabled": true,
  "act": "silent",
  "motivation": "無聊累積 82/80",
  "thought": "大家好像都睡了…",
  "decided_at": 1760000000.0,
  "next_at": 1760001800.0,
  "today_speaks": 3,
  "hourly_limit": 2,
  "daily_limit": 12,
  "blocked_reason": ""
}
```

新增 `GET /api/care/cyber/autonomy?limit=20` 回傳最近紀錄。

`/api/care/cyber/events` 保留給除錯，不變。

#### UI

在 v2 設定面板上方加一張「🧠 雪莉的此刻」卡片：

- 大狀態 pill：🗣️ 想說話／🤫 想安靜／⏳ 晚點再說／⛔ 沒問她
- 動機：`無聊累積 82/80`
- 內心戲：`大家好像都睡了…`
- 下次考慮：`14:32`／今天主動 `3/12`
- 下方摺疊表格：最近 20 次（時間／決定／動機／內心戲／下次）

更新方式：

- 頁面載入時抓一次。
- 分頁可見時每 15 秒更新。
- 保留手動「更新」按鈕。

舊的「最新自言自語」欄位標記為 legacy，或直接改接 v2 的 `thought`。

#### 測試

- `test_parse_thought_and_silent`
- `test_parse_thought_and_speak_strips_thought`
- `test_only_thought_becomes_silent`
- `test_autonomy_history_append_and_rotate`
- `test_status_payload_includes_autonomy`
- `test_autonomy_history_endpoint_limit`

#### Token

| 項目 | 成本 |
|---|---|
| THINK 協議 prompt | +約 20 字靜態，proactive prefix，可快取 |
| THINK 輸出 | +20～60 output tokens／次 |
| `autonomy_thought_enabled=false` | 0 |
| WebUI／API | 0 |

---

## 8. 依賴與實作順序

```
Phase 1 runner ─┐
Phase 2 能力提示 ├─ 可獨立
Phase 3 動機 ───┴─→ Phase 5 L2 自主
Phase 4 spec 統一 ── 可獨立
```

| 順序 | Commit | 內容 | Prompt 影響 |
|---|---|---|---|
| 1 | `26.11.01.002-fix-proactive-tool-runner` | Phase 1 ＋測試 | 0 |
| 2 | `26.11.01.003-tool-capability-hint` | Phase 2 ＋ `ToolSpec.label` | +60 字靜態 |
| 3 | `26.11.01.004-proactive-motivation` | Phase 3 ＋測試 | proactive +25 字 |
| 4 | `26.11.01.005-tool-spec-single-source` | Phase 4 重構＋回歸測試 | 0 |
| 5 | `26.11.01.006-autonomy-l2-core` | Phase 5A：prompt、解析、worker、engine、護欄 | proactive +120 字靜態、+80 字動態 |
| 6 | `26.11.01.007-autonomy-l2-ui` | Phase 5B：WebUI 設定欄位、狀態卡片、事件顯示 | 0 |
| 7 | `26.11.01.008-autonomy-l2-history` | Phase 5D：`cyber_autonomy.jsonl`、歷史 API、內心戲 THINK 協議 | proactive +20 字靜態、+20～60 output |
| 8 | `26.11.01.009-autonomy-l2-nudges` | Phase 5C：連續 speak／silent 的溫和 nudge（可選） | proactive +20 字 |

每個 phase：跑測試 → commit → push `origin/dev`。

Phase 1 可以最先上，因為它跟 prompt 完全無關，也不會影響現在的回覆品質。

---

## 9. 測試與驗收

### 9.1 自動測試

```powershell
python -m pytest tests/interaction tests/unit/test_cyber_friend_engine.py tests/unit/test_social_brain.py tests/unit/test_architecture_guard_r16.py -q
```

- 現有 86 個相關測試必須全過。
- 新增 Phase 1～5 的測試約 20～25 個。

### 9.2 實機驗收清單

- [ ] WebUI 引擎設 `live`、工具開啟。
- [ ] 手動觸發一次主動話題：dynamic context 含動機。
- [ ] 若模型呼叫 `SEARCH`：log 沒有 `unknown tool`，`tool_rounds >= 1`。
- [ ] ambient 問「0050 現在多少」：回覆不是「我幫你查一下」就結束。
- [ ] proactive 工具結果有截斷標記。
- [ ] 她輸出 `[[SILENT]]`：Discord 沒有新訊息、runtime 回到 idle／listening、事件記 `silent`。
- [ ] 她輸出 `[[WAIT:60]]`：60 分鐘內 engine 不再觸發；WebUI 看得到下次時間。
- [ ] 非 sandbox 頻道：永遠不會有 proactive 發言。
- [ ] `autonomy_enabled=false`：行為與現在 100% 相同。
- [ ] 被 tag 時：即使模型輸出 `[[SILENT]]`，也不會被吞掉。

---

## 10. 風險總表

| 風險 | 對策 |
|---|---|
| 改 system prompt 造成一次 cache 失效 | 只失效一次；名稱順序固定 |
| 能力提示讓她太愛呼叫工具 | 保留「一般閒聊直接回答」規則；proactive 結果上限 1,200 字 |
| `ui.allowed_tools` 有未知名稱 | `resolve_tool_name()` 正規化後與 spec 取交集，未知的忽略並 log |
| cyber 收到 `dispatch` policy | 降級成 readonly ＋ warning，不靜默升級權限 |
| 她永遠選說話 | `autonomy_hourly`＝2、`autonomy_daily`＝12；連續 3 次 speak 加溫和 nudge |
| 她永遠選安靜 | 連續 4 次 silent 加「你已經安靜很久，真的有話就說」；不強制發言 |
| `[[SILENT]]` 洩漏到訊息 | 只認整則協議；`sanitize_reply` 再清一次 |
| `WAIT` 與 30 分鐘間隔打架 | clamp 30～90；engine 取 `max(interval, autonomy_next_at)` |
| abort 後留下幽靈窗口 | 用 `runtime.abort_turn()`，會還原 `last_bot_at` 與 participants |
| 她選安靜但文章被標記已讀 | 安靜／等待時不呼叫 `_record_web_topic()` |
| 重啟後 `autonomy_next_at` 遺失 | 可接受；最壞就是重啟後 30 分鐘再考慮一次 |
| 模型把「安靜」寫成一句話而不是協議 | prompt 明確要求「只輸出 `[[SILENT]]`，不要加其他字」；測試覆蓋 |

---

## 11. 這份計畫刻意不做的事

- 不改主動頻率（30 分鐘）。
- 不改回覆長度、不改「欸／剛才看到／一拳」的公式（另案）。
- 不改 `max_bubbles` 與 WebUI 既有欄位。
- 不改記憶寫入 schema。
- 不動 `/mod` 與管理指令。
- 不新增額外 LLM 呼叫（L2 是單次生成內決策）。

---

## 12. 關鍵檔案索引

| 檔案 | 涉及的 Phase |
|---|---|
| `handlers/care/cyber/worker.py` | 1、2、3、5 |
| `handlers/care/cyber/engine.py` | 3、5 |
| `handlers/care/cyber/prompts.py` | 2、3、5 |
| `handlers/care/cyber/model.py` | 1 |
| `handlers/care/cyber/ui_config.py` | 5 |
| `handlers/care/cyber/policy.py` | 5（沿用 `ReplyBudget`） |
| `handlers/social/tool_specs.py` | 2、4 |
| `handlers/social/dispatch_agent.py` | 4 |
| `handlers/interaction/contracts.py` | 1（`AUTONOMOUS_POLICY`） |
| `handlers/interaction/tool_policy.py` | 1（`ToolBroker`） |
| `web_ui/templates/views/care.html` | 5 |
| `handlers/care/static/care.js` | 5 |
| `handlers/care/web.py` | 3、5 |
| `data/cyber_autonomy.jsonl` | 5（新增，自主歷史） |
| `tests/interaction/test_cyber_core.py` | 1～5 |
| `tests/interaction/test_cyber_worker_flow.py` | 1、3、5 |

---

## 13. 實作進度（2026-11-01）

| Phase | Commit | 狀態 |
|---|---|---|
| 1. proactive tool runner ＋結果上限 | `34d5df2`（`26.11.01.002`） | ✅ |
| 2. 工具能力提示 | `c20fdfa`（`26.11.01.003`） | ✅ |
| 3. 主動動機 | `52a5023`（`26.11.01.004`） | ✅ |
| 4. 被動 lane 工具說明統一 | `fafac33`（`26.11.01.005`） | ✅ |
| 5A. L2 自主核心 | `eed84e4`（`26.11.01.006`） | ✅ |
| 5B. WebUI 自主狀態卡＋設定 | `0760b4a`（`26.11.01.007`） | ✅ |
| 5D. 內心戲＋JSONL 歷史 | `6782ec4`（`26.11.01.008`） | ✅ |
| 5C. 連續說話／安靜提醒 | `0e5f0c5`（`26.11.01.009`） | ✅ |

測試：`tests/interaction` ＋ `test_cyber_friend_engine` ＋ `test_social_brain` ＋ `test_architecture_guard_r16` 共 **126 passed**。

---

## 14. 待確認事項

1. Phase 5 的 `autonomy_enabled` 預設要開還是關？
   - 建議：**開**，但 WebUI 可隨時關。
2. 連續 speak／silent 的 nudge 要不要一起做（Phase 5C）？
   - 建議：先做 5A/5B，觀察一週再決定。
3. 是否要把 Phase 1 單獨先上線？
   - 建議：是。它不碰 prompt、不改行為，只是讓 proactive 的工具真的能執行。
4. 內心戲（`[[THINK: ...]]`）要不要預設開？
   - 建議：開，但 UI 可關；每次只多 20～60 output tokens。
5. 自主歷史保留 500 筆夠不夠？
   - 建議：先 500 筆；真的需要長期分析再改 SQLite。
