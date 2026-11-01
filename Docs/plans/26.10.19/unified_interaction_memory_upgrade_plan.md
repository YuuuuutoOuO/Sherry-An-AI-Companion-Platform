# 雪莉統一互動與記憶升級計畫

## One Brain, Two Lanes：`@雪莉` 與 Cyber 的融合

- 文件版本：1.0
- 日期：2026-10-19（檔期 26.10.19）
- 前置文件：`Docs/plans/26.10.19/cyber_friend_replacement_plan.md`
- 目標專案：`D:\程式\Python\-雪莉-\Sherry-An-AI-Companion-Platform`
- 參考專案：`D:\程式\Python\dafeiyu-astrbot-main`

> 本文件是「融合計畫」，不是第二套引擎的規格。
> `cyber_friend_replacement_plan.md` 負責把舊賽博群友換成事件驅動 worker；本文件負責把該 worker 與現有 `@雪莉`／正常 router／記憶系統接成同一個大腦。
> 撰寫當下尚未修改 production code。

---

## 0. 執行摘要

### 0.1 一句話結論

**不要做「`@雪莉` 一套、Cyber 一套」。要做「同一個雪莉、同一個頻道狀態、同一份記憶、同一個模型入口，兩個 policy lane」。**

| Lane | 入口 | 行為 | 工具 |
|---|---|---|---|
| **Passive Lane** | `@雪莉`、引用雪莉、叫名字 | 必回；被動回答 | 完整工具（STOCK、SEARCH、MUSIC、DRAW…） |
| **Autonomous Lane** | 一般群聊、主動開題、追話 | 由窗口／評分／心智值決定 | 唯讀工具（SEARCH、STOCK 查詢、SUMMARY） |

### 0.2 目前真正的問題

不是「兩個引擎同時回覆」這麼簡單，而是：

1. **入口分裂**：`@雪莉` 走 `SocialChat.on_message` → `SherryCore` → `route_and_execute_tag()`；Cyber 走 `CareCog.on_message` → `CyberRoomWorker`。
2. **狀態分裂**：Cyber 的 `ChannelRuntime` 只看得到自己送出的訊息；`@雪莉` 由正常 router 回覆後，Cyber 不知道窗口已開。
3. **記憶讀取分裂**：`@雪莉` 會注入使用者 facts／好感度／檢索記憶；Cyber 只注入自憶／世界／行動記憶。
4. **工具分裂**：`@雪莉` 有完整 `TOOL_LIST`；Cyber 目前只有 `web_search`／`fetch_url`。
5. **輸出分裂**：`@雪莉` 走 `DispatchAgent` 的長文／`<thought>` 流程；Cyber 走氣泡＋去 AI 味清洗。

### 0.3 本計畫的四個核心元件

1. `TurnGateway`：單一入口，把訊息分類成 `mention / reply / ambient / proactive / followup`。
2. `ChannelActor`：每頻道單一佇列與狀態，沿用 Cyber worker，但接受 `TurnRequest`。
3. `MemoryContextService`：`@雪莉` 與 Cyber 共用的記憶讀取 facade。
4. `OutboundPipeline`：共用的清洗、拆分、發送管線。

### 0.4 分階段交付

| 階段 | 目標 | 預估 |
|---|---|---|
| **P0** | 共用狀態＋共用記憶讀取，先消除「Cyber 不認識這個人」 | 1–2 天 |
| **P1** | 共用輸出管線＋工具權限，統一語氣與工具邊界 | 2–4 天 |
| **P2** | `TurnGateway` 單一入口，`@雪莉` 高優先、Cyber 低優先 | 3–5 天 |
| **P3** | 記憶觀測／provenance／審批，借 dafeiyu 的優點但不換引擎 | 3–5 天 |

---

## 1. 現況盤點

### 1.1 `@雪莉` 路徑

```
Discord Message
  → SocialChat.on_message                 (handlers/social/__init__.py:107)
  → if bot.user in message.mentions       (handlers/social/__init__.py:166)
  → SherryCore.handle_request             (app/orchestrator.py:264)
  → route_and_execute_tag                 (handlers/social/router.py:25)
  → DispatchAgentTool.execute             (handlers/social/dispatch_agent.py:85)
  → build_dynamic_persona                 (handlers/social/persona.py:?)
  → build_user_memory_block               (handlers/social/memory_builder.py:21)
  → InferenceTask / BotBrain              (handlers/social/dispatch_agent.py:151)
```

特性：

- 完整工具：`TOOL_LIST`，包含 `SUMMARY / MUSIC / SCHEDULE / DRAW / PIXIV / STOCK / SYSTEM_INSPECT / SEARCH / CHAT`。
- 完整記憶：使用者 facts、好感度、檢索記憶、世界記憶、自憶、行動記憶。
- 被 Tag 時好感度 `+2`（`handlers/social/router.py:73`）。
- 回覆由 `DispatchAgent` 自己送出，沒有回傳統一的 `SentBatch`。

### 1.2 Cyber 路徑（依前置計畫）

```
Discord Message
  → CareCog.on_message
  → CyberEngine.submit_human
  → CyberRoomWorker
  → PrimaryModelClient → BotBrain → PRIMARY_MODEL
  → send_bubbles
  → commit_send
```

特性：

- 事件驅動、先開窗、buffer 不丟訊息。
- 工具目前只有 `web_search`、`fetch_url`（`CyberToolRegistry`）。
- 記憶只有 `build_identity_block()` 的自憶＋ `build_dynamic_persona()` 的世界／自憶／行動記憶。
- 发後寫入 `record_proactive_action_memory()`（自我行動），不更新使用者關係。

### 1.3 記憶讀寫矩陣

| 記憶種類 | 儲存 | `@雪莉` 讀 | Cyber 讀 | 寫入來源 |
|---|---|---|---|---|
| 使用者 facts | `USER_MEMORY_PATH` + `sherry_memory.db` | ✅ `get_user_memory_snippet()` | ❌ | `MemoryManager.on_message()` 獨立監聽所有人類訊息 |
| 好感度／關係 | `user_relations` | ✅ `get_relationship_snippet()` | ❌ | `MemoryManager`／`router.py` |
| 檢索記憶（FTS＋向量） | `sherry_memory.db` | ✅ `build_retrieved_memory_tail()` | ❌ | `MemoryManager` 背景萃取 |
| 世界記憶 | `world_memory` | ✅ `get_world_memory_snippet()` | ✅ 透過 persona | `nightly_cog`／`log_world_event` |
| 自我記憶 | `self_memory.db` | ✅ `get_self_memory_snippet()` | ✅ 透過 persona＋identity block | `nightly_cog`／手動 |
| 行動記憶 | `self_memory.db` | ✅ `get_action_memory_snippet()` | ✅ 透過 persona | `record_self_action()` |
| 頻道氛圍 | `VibeManager` | ✅ | ❌ | `VibeManager` |

**結論**：使用者 facts 與好感度其實有被 `MemoryManager` 寫入，但 Cyber 回覆時**沒有讀取**。所以不是「資料庫不共享」，而是「讀取管線不共享」。這比重新設計資料庫容易得多。

### 1.4 工具矩陣

| 工具 | `@雪莉` | Cyber 現況 | 建議 Autonomous |
|---|---|---|---|
| SEARCH | ✅ | ✅（自製 web_search） | ✅ |
| STOCK | ✅ | ❌ | ✅（唯讀查詢） |
| SUMMARY | ✅ | ❌ | ✅（唯讀） |
| MUSIC | ✅ | ❌ | ❌（除非明確要求） |
| DRAW | ✅ | ❌ | ❌ |
| SCHEDULE | ✅ | ❌ | ❌（需審批） |
| SYSTEM_INSPECT | ✅ | ❌ | ❌ |
| CHAT | ✅ | ⚠️ 自製 prompt | ✅（改用共用 chat） |

### 1.5 衝突矩陣

| 情境 | `SocialChat` | `CareCog` | 結果 |
|---|---|---|---|
| sandbox 非 @ | `is_sandbox` 跳過 interject | Cyber live 接管 | ✅ 單一回覆 |
| sandbox @ | 正常 router 回覆 | Cyber 若 `owns_mentions=False` 忽略 | ⚠️ 不雙回，但狀態／記憶分裂 |
| sandbox @ + `owns_mentions=True` | 正常 router 仍回覆 | Cyber 也回覆 | ❌ 雙回 |
| 非 sandbox 非 @ | 舊 interject | Cyber 不啟用 | ✅ 維持現狀 |
| 非 sandbox @ | 正常 router | Cyber 不啟用 | ✅ 維持現狀 |
| 雪莉自己的訊息 | 不處理（bot） | `cog.py:389` 只更新舊時間戳 | ⚠️ Cyber 不知道 @ 回覆已開窗 |

---

## 2. 目標架構

### 2.1 設計原則

1. **Single Ingress**：所有人類訊息先經過 `TurnGateway`，由它決定 lane。
2. **Single Actor**：每頻道只有一個 `ChannelActor` 能生成與發送；`@雪莉` 與 Cyber 共用同一個 queue。
3. **Single State**：`ChannelRuntime` 是唯一窗口／參與者／已回覆訊息來源。
4. **Single Memory Read**：所有 prompt 的記憶都由 `MemoryContextService` 產生。
5. **Single Outbound**：所有文字都經 `OutboundPipeline` 清洗與拆分。
6. **Policy, not Engine**：`@雪莉` 與 Cyber 的差別是 `TurnMode` 與 `ToolPolicy`，不是兩個大腦。

### 2.2 元件圖

```
Discord Gateway
   │
   ├─ @雪莉 / 引用雪莉 / 叫名字 ──┐
   ├─ sandbox 一般訊息 ───────────┤
   ├─ 主動 tick ──────────────────┤
   └─ 非 sandbox 一般訊息 ──► 舊 SocialChat interject（暫時保留）
                                  │
                                  ▼
                         TurnGateway
                         ├─ classify()
                         ├─ build TurnRequest
                         └─ submit()
                                  │
                                  ▼
                    ChannelActor（每頻道單一）
                    ├─ asyncio.PriorityQueue
                    ├─ ChannelRuntime
                    ├─ ReplyBuffer
                    └─ TurnExecutor
                         ├─ MentionPolicy
                         ├─ AutonomousPolicy
                         ├─ ToolBroker
                         ├─ MemoryContextService
                         └─ OutboundPipeline
                                  │
                                  ▼
                    PrimaryModelClient → BotBrain → PRIMARY_MODEL
```

### 2.3 Lane 與優先級

| `TurnMode` | Lane | `must_reply` | 工具 | 優先級 | 說明 |
|---|---|---|---|---|---|
| `MENTION` | Passive | ✅ | `FULL_MENTION` | `PRIORITY_TAG_RESPONSE` | `@雪莉`、叫名字 |
| `REPLY` | Passive | ✅ | `FULL_MENTION` | `PRIORITY_TAG_RESPONSE` | 引用／回覆雪莉 |
| `FOLLOWUP` | Autonomous | ❌ | `AUTONOMOUS` | `PRIORITY_INTERJECT` | 窗口內的續聊 |
| `AMBIENT` | Autonomous | ❌ | `AUTONOMOUS` | `PRIORITY_INTERJECT` | 一般群聊 |
| `PROACTIVE` | Autonomous | ❌ | `AUTONOMOUS` | `PRIORITY_INTERJECT` | 雪莉主動開題 |

### 2.4 優先級與搶佔

- `MENTION`／`REPLY` 進 `PriorityQueue` 的高優先級。
- 若 Autonomous lane 正在生成、**尚未送出新訊息**：
  - 取消該回合的 `InferenceTask`（`is_cancelled=True`）。
  - 把該回合的 `ChatEvent` 放回 `ReplyBuffer`。
  - 先處理 Mention。
- 若 Autonomous lane **已經在送氣泡**：
  - 讓它送完目前批次，避免半截訊息。
  - 送完後立刻處理 Mention。
- 不允許 Mention 被 `ReplyPolicy` 的 `lurk_streak` 或 `interject_score` 擋下；只受 `rate_limiter`、安全與管理員 kill switch 限制。

### 2.5 狀態所有權

| 狀態 | 擁有者 | 寫入者 |
|---|---|---|
| 頻道窗口、participants、answered IDs | `ChannelRuntime` | `ChannelActor` |
| 使用者 facts／好感度 | `MemoryManager` | `MemoryManager`（人類訊息） |
| 自憶／世界／行動 | `handlers/memory` | `nightly_cog`／`record_self_action` |
| 主動預算、cooldown | `ReplyBudget` + `AutonomyGuard` | `ChannelActor` |
| 模型選擇 | `settings.PRIMARY_MODEL` | `resolve_primary_model()` 每次現查 |
| 回合事件 | `InteractionEventLog` | `ChannelActor` |

---

## 3. 詳細設計

### 3.1 `TurnRequest`：唯一輸入契約

```python
@dataclass(slots=True)
class TurnRequest:
    channel_id: str
    mode: TurnMode
    events: tuple[ChatEvent, ...] = ()
    guild_id: str = ""
    target_author_id: str = ""
    target_author_name: str = ""
    message: Any = None              # discord.Message，供工具與回覆引用
    reply_to: Any = None
    priority: int = 2
    must_reply: bool = False
    tool_policy: ToolPolicy = NO_TOOL_POLICY
    trace_id: str = ""
    source: str = ""
```

`TurnMode`：

```python
class TurnMode(str, Enum):
    MENTION = "mention"
    REPLY = "reply"
    AMBIENT = "ambient"
    FOLLOWUP = "followup"
    PROACTIVE = "proactive"
```

### 3.2 `TurnGateway`：分類與投遞

分類規則（優先序）：

1. `message.mentions` 包含 bot → `MENTION`。
2. `message.reference` 的 resolved／cached author 是 bot → `REPLY`。
3. 文字含 `@雪莉`、`雪莉`、`sherry` → `MENTION`（可設定是否啟用）。
4. 是 sandbox 頻道 → `AMBIENT`。
5. 其他 → `None`（交回舊 SocialChat interject）。

`must_reply = mode in {MENTION, REPLY}`。

`tool_policy = FULL_MENTION_POLICY if must_reply else AUTONOMOUS_POLICY`。

`priority = PRIORITY_TAG_RESPONSE if must_reply else PRIORITY_INTERJECT`。

### 3.3 `ChannelActor`：沿用 Cyber worker

不新增第二個 worker。把前置計畫的 `CyberRoomWorker` 升級為：

- `submit_turn(request)` 取代 `submit_human(event)`。
- `PriorityQueue` 排序：`(priority, sequence)`。
- `ChannelRuntime.begin_turn()` 對 Mention 也適用，所以 `@雪莉` 也會「先開窗、後生成」。
- `TurnExecutor` 依 `request.mode` 選擇 `MentionPolicy` 或 `AutonomousPolicy`。
- `MentionPolicy` 直接回傳 `speak=True, reason="mention"`，不經 `lurk_streak`。
- `AutonomousPolicy` 使用前置計畫的 `ReplyPolicy`。

### 3.4 `MentionPolicy` 與 `AutonomousPolicy`

```python
class MentionPolicy:
    def decide(self, request: TurnRequest, runtime: ChannelRuntime, config: Any, *, now: float) -> TurnDecision:
        return TurnDecision(
            speak=True,
            reason=request.mode.value,
            target_author_id=request.target_author_id,
            score=10.0,
            addressed=True,
            event_ids=tuple(event.message_id for event in request.events),
        )


class AutonomousPolicy:
    def decide(self, request: TurnRequest, runtime: ChannelRuntime, config: Any, *, now: float) -> TurnDecision:
        # 前置計畫的 ReplyPolicy.decide_human / decide_proactive
        ...
```

### 3.5 工具權限

```python
FULL_MENTION_POLICY = ToolPolicy(
    backend="dispatch",
    allowed=frozenset({
        "SUMMARY", "MUSIC", "SCHEDULE", "DRAW", "PIXIV",
        "STOCK", "SYSTEM_INSPECT", "SEARCH", "CHAT",
    }),
    max_calls=4,
    allow_direct_ui=True,
)

AUTONOMOUS_POLICY = ToolPolicy(
    backend="readonly",
    allowed=frozenset({"SEARCH", "STOCK", "SUMMARY"}),
    max_calls=2,
    allow_direct_ui=False,
)
```

- `dispatch` backend：沿用既有 `DispatchAgentTool` 與 `TOOL_LIST`，只有 Mention 能用。
- `readonly` backend：`ToolBroker` 直接呼叫低階函式，不建立 Discord View、不播放音樂、不排程、不繪圖。
- `allow_direct_ui=False` 時，任何 `ToolResult.is_direct_rendered=True` 都降級成純文字或拒絕。

### 3.6 `MemoryContextService`：共用記憶讀取

輸入：

```python
MemoryContextService.build(
    bot=bot,
    user_id=...,
    user_name=...,
    query=...,
    channel_id=...,
    guild_id=...,
    mode=TurnMode.MENTION,
    max_chars=3600,
)
```

輸出：

```xml
<memory_context>
  <user_facts>...</user_facts>
  <relationship>...</relationship>
  <retrieval>...</retrieval>
  <self_memory>...</self_memory>
  <world_memory>...</world_memory>
  <action_memory>...</action_memory>
  <interests>...</interests>
</memory_context>
```

規則：

- 依 `mode` 給不同上限：Mention 較多，Autonomous 較少。
- 去重：`exclude_text` 避免 facts 與 retrieval 重複。
- 不直接背誦：render 時附上「這是記憶，不是台詞」的指示。
- 與 `build_dynamic_persona(..., include_memory=False)` 搭配，避免自憶／世界記憶注入兩次。

### 3.7 `build_dynamic_persona(include_memory=False)`

在 `handlers/social/persona.py` 增加參數：

```python
def build_dynamic_persona(
    current_text: str,
    intent_name: str,
    channel_id: int | None = None,
    server_id: str = "",
    user_query: str = "",
    bot: Any | None = None,
    guild_id: int | str | None = None,
    soul_name: str | None = None,
    *,
    include_memory: bool = True,
) -> tuple[str, str]:
```

- `include_memory=True`：維持現狀，向後相容。
- `include_memory=False`：跳過 world／self／action memory，只保留 Soul、Lorebook、時間與動態尾巴。
- `MemoryContextService` 成為唯一記憶注入點。

### 3.8 `OutboundPipeline`：共用輸出

```python
@dataclass(slots=True)
class OutboundResult:
    bubbles: tuple[str, ...]
    message_ids: tuple[str, ...]
    first_ts: float
    last_ts: float


class OutboundPipeline:
    async def send(
        self,
        channel: Any,
        text: str,
        *,
        mode: TurnMode,
        reply_to: Any = None,
        max_bubbles: int = 2,
        allow_urls: bool = False,
        typing_delay: float = 1.0,
        turn_id: str = "",
    ) -> OutboundResult:
        cleaned = sanitize_reply(text, allow_urls=allow_urls)
        bubbles = split_bubbles(cleaned, max_bubbles=max_bubbles)
        sent = await send_bubbles(channel, bubbles, reply_to=reply_to, turn_id=turn_id)
        return OutboundResult(
            bubbles=tuple(sent.bubbles),
            message_ids=tuple(sent.message_ids),
            first_ts=sent.first_ts,
            last_ts=sent.last_ts,
        )
```

- Mention：可在 `DispatchAgent` 送出後，對文字做 `sanitize_reply`；P2 再改成回傳文字由 pipeline 發送。
- Autonomous：直接用 pipeline。
- 所有 TTS／語音氣泡都應從 `OutboundResult.bubbles` 取得已清洗文字。

### 3.9 `ScheduleContext`

借 dafeiyu `schedule_awareness` 的概念，但不改排程引擎：

```python
def build_schedule_context(bot, *, channel_id: str, now: float) -> str:
    # 從 HeartbeatCog / SparkService 取得接下來 N 個任務與冷卻
    # 只讀，不執行、不修改
    return "<schedule>\n- 23:30 提醒主人睡覺\n- 07:00 起床提醒\n</schedule>"
```

注入位置：`MemoryContextService` 之後、`transcript` 之前。這樣雪莉不會說「我現在沒事」但其實 10 分鐘後有排程。

### 3.10 觀測

每次回合寫一筆 `InteractionEvent`：

```json
{
  "event": "interaction_turn",
  "trace_id": "chat-...",
  "channel_id": "123",
  "mode": "mention",
  "lane": "passive",
  "must_reply": true,
  "tool_policy": "dispatch",
  "tool_calls": 1,
  "memory_sources": ["user_facts", "relationship", "retrieval"],
  "memory_chars": 812,
  "model": "gemini:...",
  "gen_ms": 2100,
  "preempted": false,
  "outcome": "sent"
}
```

---

## 4. 行為規則

### 4.1 `@雪莉` 的行為

- 一定回覆（除非 rate limit、管理員 kill switch、內容安全擋下）。
- 不經 `lurk_streak`、不經 `interject_score`。
- 使用完整工具。
- 回覆後 `ChannelRuntime.commit_send()` 開窗，讓後續 Cyber 可續聊。
- 若使用者只是「@雪莉」沒有內容，回覆「怎麼了？」之類的短句，不呼叫工具。

### 4.2 Cyber 的行為

- 只在 sandbox 頻道的主動 lane 運作（第一版）。
- 一般訊息先進 buffer，再由 `AutonomousPolicy` 決定是否回。
- 使用唯讀工具；不得主動播放音樂、繪圖、排程、執行系統指令。
- 若人類在 Cyber 生成期間 @雪莉：
  - Mention 進高優先級 queue。
  - 若 Cyber 尚未發送，取消並把事件放回 buffer。
  - 先回 Mention；Cyber 的事件下一個回合再處理。

### 4.3 記憶一致性

- `@雪莉` 與 Cyber 都使用 `MemoryContextService`。
- `MemoryManager.on_message()` 繼續監聽所有人類訊息，寫入 facts／好感度。
- Cyber 發送後：
  - `record_self_action()` 記錄自哔行動。
  - 不重複寫入 user facts（避免與 MemoryManager 重複）。
  - 可選擇 `relations.update_affinity(uid, +0.5)` 作為「有回應」的微加分；需與 `router.py` 的 `+2` 區分，避免雙重計分。

### 4.4 不要做的事

- 不要讓 `@雪莉` 與 Cyber 各自建立 `ChannelRuntime`。
- 不要讓 Cyber 直接呼叫 `DispatchAgentTool` 的完整工具。
- 不要在 `build_dynamic_persona()` 與 `MemoryContextService` 同時注入自憶。
- 不要把 dafeiyu 的 LivingMemory 當成雪莉的 primary memory store。
- 不要在 `CYBER_V2_OWNS_MENTIONS=True` 時仍讓 `SocialChat` 的 mention router 運作。

---

## 5. 檔案變更清單

### 5.1 新增檔案

| 檔案 | 責任 | 階段 |
|---|---|---|
| `handlers/interaction/__init__.py` | export 與 singleton | P0 |
| `handlers/interaction/contracts.py` | `TurnMode`、`TurnRequest`、`TurnResult`、`ToolPolicy` | P0 |
| `handlers/interaction/gateway.py` | 單一入口、分類、投遞、外部發言通知 | P2 |
| `handlers/interaction/tool_policy.py` | `ToolBroker`、唯讀工具 | P1 |
| `handlers/memory/context_service.py` | 共用記憶讀取 facade | P0 |
| `handlers/social/text_style.py` | 清洗／拆分（由 cyber prompts 抽出） | P1 |
| `handlers/social/outbound.py` | 共用輸出管線 | P1 |
| `handlers/memory/observatory.py` | 唯讀記憶投影／provenance | P3 |
| `tests/interaction/test_turn_gateway.py` | 入口分類與雙回防護 | P2 |
| `tests/interaction/test_memory_context.py` | 記憶共享與去重 | P0 |
| `tests/interaction/test_tool_policy.py` | 工具權限 | P1 |
| `tests/interaction/test_preemption.py` | 高優先級搶佔 | P2 |

### 5.2 修改檔案

| 檔案 | 修改 | 階段 |
|---|---|---|
| `settings.py` | 新增 `INTERACTION_GATEWAY_ENABLED`、`INTERACTION_MENTION_SCOPE`、`CYBER_V2_OWNS_MENTIONS`、`MEMORY_CONTEXT_SERVICE_ENABLED`、`INTERACTION_PREEMPT_AMBIENT` | P0 |
| `handlers/social/__init__.py` | 在 interject／mention router 之前呼叫 `TurnGateway.handle_message()` | P2 |
| `handlers/social/router.py` | 發送後呼叫 `gateway.note_external_bot_spoke()`（fallback 路徑） | P0 |
| `handlers/social/dispatch_agent.py` | 改用 `MemoryContextService`；`build_dynamic_persona(include_memory=False)` | P0 |
| `handlers/social/persona.py` | 新增 `include_memory` 參數 | P0 |
| `handlers/care/cog.py` | bot 訊息分支通知 Cyber；live 模式 sandbox 不再走舊熱聊 | P0 |
| `handlers/care/cyber/contracts.py` | 擴充 `TurnMode`／`ToolPolicy`／`TurnRequest`（或改 import `handlers.interaction.contracts`） | P0 |
| `handlers/care/cyber/state.py` | 新增 `note_external_bot_speak()` | P0 |
| `handlers/care/cyber/worker.py` | `submit_turn()`、`TurnRequest`、Mention 分支、工具 policy | P2 |
| `handlers/care/cyber/policy.py` | 新增 `MentionPolicy` 或 `decide_mention()` | P2 |
| `handlers/care/cyber/model.py` | 工具迴圈接受 `ToolPolicy` | P1 |
| `handlers/care/cyber/prompts.py` | 移除 `build_identity_block` 或改為 re-export；新增 `mode` 參數 | P0 |
| `handlers/care/cyber/engine.py` | `submit_turn()`、`note_external_bot_speak()`、`status_payload()` 增加 lane | P0/P2 |
| `handlers/care/web.py` | 新增 `/api/care/cyber/status`、`/api/memory/observatory` | P2/P3 |
| `web_ui/templates/views/care.html` | 顯示 lane／model／memory sources | P2 |
| `handlers/care/static/care.js` | 輪詢與 Pill | P2 |

---

## 6. 分階段實作

### P0：共用狀態與記憶讀取（1–2 天）

**目標**：不改入口，先讓 Cyber 看得見 `@雪莉` 的回覆，並且讀得到使用者記憶。

**任務**：

1. `ChannelRuntime` 新增：

```python
def note_external_bot_speak(
    self,
    *,
    sent_at: float,
    target_author_id: str = "",
    message_id: str = "",
) -> None:
    with self._lock:
        self.last_bot_at = max(self.last_bot_at, float(sent_at))
        self.opened_at = min(self.opened_at or float(sent_at), float(sent_at))
        if target_author_id:
            self.participants = {str(target_author_id)}
        self.humans_since_bot = 0
        self.lurk_streak = 0
        self.phase = RoomPhase.LISTENING
        if message_id:
            self._answered[str(message_id)] = time.time()
        self.updated_at = time.time()
```

2. `CareCog.on_message` 的 bot 分支（`handlers/care/cog.py:389-391`）：

```python
if bot_user and message.author.id == bot_user.id:
    update_bot_speak_timestamp(cid_str, keep_participants=True)
    if getattr(self, "cyber", None) is not None:
        self.cyber.note_external_bot_speak(
            cid_str,
            sent_at=message.created_at.timestamp(),
            message_id=str(message.id),
        )
    return
```

3. `handlers/social/router.py` 在 `dispatch_agent.execute()` 之後（fallback 路徑）：

```python
finally:
    await remove_hourglass_reaction(message, bot.user)
    try:
        from handlers.interaction import get_turn_gateway
        gateway = get_turn_gateway()
        if gateway is not None:
            await gateway.note_external_bot_spoke(
                message.channel.id,
                sent_at=time.time(),
                target_author_id=message.author.id,
            )
    except Exception:
        pass
```

4. 新增 `MemoryContextService`，Cyber worker 的 `_persona`／prompt 改用它；`build_identity_block` 停用，避免自憶重複。

5. `DispatchAgent._generate_final_response` 改用 `MemoryContextService`，並把 `build_dynamic_persona(..., include_memory=False)`。

**驗收**：

- `@雪莉` 回覆後 3 秒內，Cyber 的 `window_open=True`、`participants` 包含該使用者。
- Cyber 回覆的 prompt 中出現該使用者的 facts（測試用 monkeypatch MemoryManager）。
- 同一則訊息不會被重複注入自憶（prompt 中 `<self_memory>` 只出現一次）。

**回滾**：`MEMORY_CONTEXT_SERVICE_ENABLED=false`，回復舊 prompt；`note_external_bot_speak` 無副作用，可保留。

### P1：共用輸出與工具權限（2–4 天）

**目標**：統一語氣、去 AI 味、工具邊界。

**任務**：

1. 把 `sanitize_reply`／`split_bubbles` 從 `handlers/care/cyber/prompts.py` 抽到 `handlers/social/text_style.py`；cyber prompts 只 re-export。
2. 新增 `OutboundPipeline`，Cyber 與 `@雪莉` 最終文字都經過它。
3. 新增 `ToolBroker` 唯讀後端；Cyber 只能使用 `SEARCH`／`STOCK`／`SUMMARY`。
4. Mention 仍使用 `DispatchAgentTool` 的完整工具；但工具結果与最終文字要過 `sanitize_reply`，避免 `<thought>`、`<action>`、Markdown 外洩。
5. TTS 取 `OutboundResult.bubbles`，不要取原始模型輸出。

**驗收**：

- Cyber 嘗試 `DRAW`／`MUSIC`／`SYSTEM_INSPECT` 時回 `tool_denied`，不執行。
- Mention 可正常使用 `STOCK`、`SEARCH`。
- 兩條 lane 的回覆都不含 `<thought>`、`[[TOOL:...]]`、`<action>`、外圍引號。
- 同一段文字在 mention 與 cyber 的氣泡數、句尾風格一致。

### P2：TurnGateway 單一入口（3–5 天）

**目標**：真正的一個 queue、一個狀態、兩個 policy。

**任務**：

1. 新增 `TurnGateway`；`SocialChat.on_message` 在 interject／mention router 之前呼叫。
2. `CareCog.on_message` 在 live 模式 sandbox 直接 return，不再處理訊息；只保留 proactive worker。
3. `TurnGateway` 分類：
   - `@雪莉`／名字 → `MENTION`
   - 引用雪莉 → `REPLY`
   - sandbox 一般 → `AMBIENT`
   - 其他 → `None`（交回舊 interject）
4. `CyberRoomWorker` 改成 `ChannelActor`：
   - `PriorityQueue`，Mention 高優先。
   - Mention 到達時，若 Autonomous 尚未發送 → 取消生成、事件放回 buffer。
   - Mention 不受 `lurk_streak`／`interject_score` 限制。
5. `SocialChat` 的 mention router 在 gateway 接管時不再執行；`CYBER_V2_OWNS_MENTIONS=True` 時必須加 gate，否則雙回。
6. WebUI 顯示 `lane`、`mode`、`preempted`、`memory_sources`。

**驗收**：

- 72 小時內同一 `message_id` 不被兩個 lane 處理。
- `@雪莉` 到達時，若 Cyber 正在生成但尚未發送，Mention 在 2 秒內開始處理，Cyber 事件回到 buffer。
- `@雪莉` 回覆後，下一句一般訊息被判定為 `FOLLOWUP`，不是 `AMBIENT`。
- WebUI 可看到 `mode=mention`、`lane=passive`、`tool_policy=dispatch`。

### P3：記憶觀測與 provenance（3–5 天）

**目標**：借 dafeiyu 的觀測／可信度，不換記憶引擎。

**任務**：

1. 新增 `handlers/memory/observatory.py`：
   - 唯讀投影：使用者 facts、關係、自憶、世界、行動、檢索來源。
   - 每條記憶附 `source`、`confidence`、`pinned`、`half_life_days`、`last_recalled_at`（若無則 `null`）。
2. 新增 sidecar `fact_meta` 表（不破壞既有 `user_facts`）：

```sql
CREATE TABLE IF NOT EXISTS fact_meta (
    fact_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '',
    confidence REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    half_life_days REAL,
    last_recalled_at TEXT,
    FOREIGN KEY (fact_id) REFERENCES user_facts(id) ON DELETE CASCADE
);
```

3. `MemoryManager` 寫入 facts 時，同步寫 `fact_meta`；舊資料不回填，顯示 `null`。
4. WebUI 新增唯讀「記憶觀測」頁：時間軸、衰減、來源、召回理由。
5. 高影響記憶（`category=rule`、`importance>=4`、關係承諾）先產生 proposal，經管理員審批才寫入；預設關閉。

**驗收**：

- 觀測頁只讀，不提供寫入按鈕。
- 每條記憶可追溯來源；沒有來源的顯示「未評估」。
- 高影響記憶未審批前不會進入 prompt。
- dafeiyu 的 schema 沒有被直接引入；雪莉的 `user_facts`／retrieval 仍是唯一 runtime 來源。

---

## 7. 完整程式碼

### 7.1 `handlers/interaction/contracts.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class TurnMode(str, Enum):
    MENTION = "mention"
    REPLY = "reply"
    AMBIENT = "ambient"
    FOLLOWUP = "followup"
    PROACTIVE = "proactive"


class Lane(str, Enum):
    PASSIVE = "passive"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    backend: Literal["dispatch", "readonly", "none"] = "none"
    allowed: frozenset[str] = frozenset()
    max_calls: int = 0
    allow_direct_ui: bool = False


NO_TOOL_POLICY = ToolPolicy()

FULL_MENTION_POLICY = ToolPolicy(
    backend="dispatch",
    allowed=frozenset({
        "SUMMARY", "MUSIC", "SCHEDULE", "DRAW", "PIXIV",
        "STOCK", "SYSTEM_INSPECT", "SEARCH", "CHAT",
    }),
    max_calls=4,
    allow_direct_ui=True,
)

AUTONOMOUS_POLICY = ToolPolicy(
    backend="readonly",
    allowed=frozenset({"SEARCH", "STOCK", "SUMMARY"}),
    max_calls=2,
    allow_direct_ui=False,
)


@dataclass(slots=True)
class TurnRequest:
    channel_id: str
    mode: TurnMode
    events: tuple[Any, ...] = ()
    guild_id: str = ""
    target_author_id: str = ""
    target_author_name: str = ""
    message: Any = None
    reply_to: Any = None
    priority: int = 2
    must_reply: bool = False
    tool_policy: ToolPolicy = NO_TOOL_POLICY
    trace_id: str = ""
    source: str = ""

    @property
    def lane(self) -> Lane:
        return (
            Lane.PASSIVE
            if self.mode in (TurnMode.MENTION, TurnMode.REPLY)
            else Lane.AUTONOMOUS
        )


@dataclass(slots=True)
class TurnResult:
    status: Literal["sent", "silent", "blocked", "failed", "shadow"]
    reason: str = ""
    bubbles: tuple[str, ...] = ()
    message_ids: tuple[str, ...] = ()
    model: str = ""
    latency_ms: int = 0
    tool_calls: int = 0
    memory_sources: tuple[str, ...] = ()
    preempted: bool = False
```

### 7.2 `handlers/interaction/tool_policy.py`

```python
from __future__ import annotations

import json
import logging
from typing import Any

from .contracts import ToolPolicy

logger = logging.getLogger(__name__)


class ToolDenied(RuntimeError):
    pass


class ToolBroker:
    """Autonomous lane 的唯讀工具後端。

    不建立 Discord View、不播放音訊、不排程、不繪圖。
    所有輸出都會被包成不可信資料，回填 prompt。
    """

    def __init__(self, bot: Any, *, timeout: float = 20.0) -> None:
        self.bot = bot
        self.timeout = max(3.0, float(timeout))

    async def run(self, policy: ToolPolicy, name: str, args: dict[str, Any]) -> str:
        canonical = str(name or "").strip().upper()
        if canonical not in policy.allowed:
            raise ToolDenied(f"tool '{name}' is not allowed by policy")
        if policy.backend != "readonly":
            raise ToolDenied(f"tool backend '{policy.backend}' is not readonly")

        import asyncio

        handler = {
            "SEARCH": self._search,
            "STOCK": self._stock,
            "SUMMARY": self._summary,
        }.get(canonical)
        if handler is None:
            raise ToolDenied(f"tool '{name}' is not implemented")
        try:
            return await asyncio.wait_for(handler(args or {}), timeout=self.timeout)
        except asyncio.TimeoutError:
            return f"error: tool '{canonical}' timeout"
        except ToolDenied:
            raise
        except Exception as exc:
            logger.warning("[ToolBroker] %s 失敗: %s", canonical, exc)
            return f"error: tool '{canonical}' failed: {exc}"

    async def _search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query") or args.get("q") or "").strip()
        if not query:
            return "error: missing query"
        from handlers.social.tools.search_tools import fetch_web_and_entity_context

        context, link = await fetch_web_and_entity_context(query)
        text = str(context or "").strip()
        if link:
            text += f"\n\n來源：{link}"
        return text[:4000] or "（沒有找到可用結果）"

    async def _summary(self, args: dict[str, Any]) -> str:
        url = str(args.get("url") or "").strip()
        if not url:
            return "error: missing url"
        from handlers.social.tools.search_tools import fetch_and_extract_url

        title, content, method = await fetch_and_extract_url(url)
        body = f"{title}\n{url}\n{content}\n（萃取方式：{method}）".strip()
        return body[:4000] or "（頁面沒有可讀內容）"

    async def _stock(self, args: dict[str, Any]) -> str:
        query = str(args.get("query") or args.get("ticker") or "").strip()
        if not query:
            return "error: missing ticker"
        from handlers.social.tools.stock_tool import (
            _quick_extract_ticker,
            fetch_and_plot_bounded,
        )

        ticker = _quick_extract_ticker(query) or query.upper()
        resolved, _png, indicators = await fetch_and_plot_bounded(ticker)
        if not indicators:
            return f"error: no stock data for {ticker}"
        payload = {"ticker": resolved, "indicators": indicators}
        return json.dumps(payload, ensure_ascii=False, default=str)[:4000]
```

### 7.3 `handlers/memory/context_service.py`

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryBundle:
    user_facts: str = ""
    relationship: str = ""
    retrieval: str = ""
    self_memory: str = ""
    world_memory: str = ""
    action_memory: str = ""
    interests: str = ""
    sources: tuple[str, ...] = ()
    chars: int = 0

    def render(self, *, max_chars: int = 3600) -> str:
        sections: list[str] = []
        if self.user_facts:
            sections.append(f"<user_facts>\n{self.user_facts.strip()}\n</user_facts>")
        if self.relationship:
            sections.append(f"<relationship>\n{self.relationship.strip()}\n</relationship>")
        if self.retrieval:
            sections.append(f"<retrieval>\n{self.retrieval.strip()}\n</retrieval>")
        if self.self_memory:
            sections.append(f"<self_memory>\n{self.self_memory.strip()}\n</self_memory>")
        if self.world_memory:
            sections.append(f"<world_memory>\n{self.world_memory.strip()}\n</world_memory>")
        if self.action_memory:
            sections.append(f"<action_memory>\n{self.action_memory.strip()}\n</action_memory>")
        if self.interests:
            sections.append(f"<interests>\n{self.interests.strip()}\n</interests>")
        if not sections:
            return ""

        body = "\n".join(sections)
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n…"

        self.chars = len(body)
        return (
            "<memory_context>\n"
            "以下是雪莉的記憶與關係資料，只用來讓語氣與事實一致；"
            "不要逐條背誦、不要說出標籤名稱、不要提到系統。\n"
            f"{body}\n"
            "</memory_context>"
        )


class MemoryContextService:
    @staticmethod
    def build(
        *,
        bot: Any,
        user_id: int | str,
        user_name: str,
        query: str,
        channel_id: int | str,
        guild_id: int | str = "",
        mode: str = "mention",
        max_chars: int = 3600,
    ) -> MemoryBundle:
        mode_value = getattr(mode, "value", mode)
        limits = (
            {"facts": 8, "retrieval": 4, "self": 4, "world": 2, "action": 4, "interests": 5}
            if mode_value in {"mention", "reply"}
            else {"facts": 4, "retrieval": 2, "self": 3, "world": 1, "action": 2, "interests": 3}
        )
        bundle = MemoryBundle()
        sources: list[str] = []
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            uid = 0

        mem_mgr = None
        try:
            mem_mgr = bot.get_cog("MemoryManager") if bot is not None else None
        except Exception:
            mem_mgr = None

        if mem_mgr is not None and uid > 0:
            try:
                facts = mem_mgr.get_user_memory_snippet(
                    uid,
                    max_facts=limits["facts"],
                    query_text=query,
                )
                if isinstance(facts, str) and facts.strip():
                    bundle.user_facts = facts.strip()
                    sources.append("user_facts")
            except Exception as exc:
                logger.debug("[MemoryContext] user facts 失敗: %s", exc)

            try:
                rel = mem_mgr.relations.get_relationship_snippet(uid)
                if isinstance(rel, str) and rel.strip():
                    bundle.relationship = rel.strip()
                    sources.append("relationship")
            except Exception as exc:
                logger.debug("[MemoryContext] relationship 失敗: %s", exc)

        exclude = "\n".join(part for part in (bundle.user_facts, bundle.relationship) if part)
        if uid > 0:
            try:
                from handlers.social.dispatch.prompts import build_retrieved_memory_tail

                retrieval = build_retrieved_memory_tail(
                    query,
                    uid,
                    exclude_text=exclude,
                    limit=limits["retrieval"],
                )
                if retrieval and retrieval.strip():
                    bundle.retrieval = retrieval.strip()
                    sources.append("retrieval")
            except Exception as exc:
                logger.debug("[MemoryContext] retrieval 失敗: %s", exc)

        try:
            from handlers.memory.self_memory import get_self_memory_snippet

            self_text = get_self_memory_snippet(query_text=query, limit=limits["self"])
            if self_text and self_text.strip():
                bundle.self_memory = self_text.strip()
                sources.append("self_memory")
        except Exception as exc:
            logger.debug("[MemoryContext] self memory 失敗: %s", exc)

        try:
            from handlers.memory.world_memory import get_world_memory_snippet

            world = get_world_memory_snippet(
                days=2,
                min_importance=3,
                server_id=str(guild_id or ""),
                limit=limits["world"],
            )
            if world and world.strip():
                bundle.world_memory = world.strip()
                sources.append("world_memory")
        except Exception as exc:
            logger.debug("[MemoryContext] world memory 失敗: %s", exc)

        try:
            from handlers.memory.self_memory import get_action_memory_snippet

            action = get_action_memory_snippet(
                query_text=query,
                channel_id=channel_id,
                limit=limits["action"],
            )
            if action and action.strip():
                bundle.action_memory = action.strip()
                sources.append("action_memory")
        except Exception as exc:
            logger.debug("[MemoryContext] action memory 失敗: %s", exc)

        try:
            from handlers.memory.self_memory import get_active_interests

            interests = get_active_interests(limit=limits["interests"])
            if interests:
                bundle.interests = "、".join(str(item) for item in interests if str(item).strip())
                sources.append("interests")
        except Exception as exc:
            logger.debug("[MemoryContext] interests 失敗: %s", exc)

        bundle.sources = tuple(sources)
        bundle.chars = sum(
            len(part)
            for part in (
                bundle.user_facts,
                bundle.relationship,
                bundle.retrieval,
                bundle.self_memory,
                bundle.world_memory,
                bundle.action_memory,
                bundle.interests,
            )
        )
        return bundle
```

### 7.4 `handlers/social/text_style.py`

```python
"""共用文字清洗與氣泡拆分。

第一階段：直接 re-export 前置計畫的實作，避免兩份邏輯。
第二階段：把 `handlers/care/cyber/prompts.py` 的 sanitize/split 搬到此處，
          cyber 只保留 import，不再擁有自己的副本。
"""

from handlers.care.cyber.prompts import sanitize_reply, split_bubbles

__all__ = ["sanitize_reply", "split_bubbles"]
```

### 7.5 `handlers/social/outbound.py`

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from handlers.care.cyber.send import send_bubbles
from handlers.social.text_style import sanitize_reply, split_bubbles

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OutboundResult:
    bubbles: tuple[str, ...]
    message_ids: tuple[str, ...]
    first_ts: float
    last_ts: float

    @property
    def empty(self) -> bool:
        return not self.message_ids


class OutboundPipeline:
    """所有對外文字的唯一出口。"""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    async def send(
        self,
        channel: Any,
        text: str,
        *,
        mode: str = "ambient",
        reply_to: Any = None,
        max_bubbles: int = 2,
        allow_urls: bool = False,
        typing_delay: float = 1.0,
        turn_id: str = "",
    ) -> OutboundResult:
        cleaned = sanitize_reply(text, allow_urls=allow_urls)
        if not cleaned:
            return OutboundResult((), (), 0.0, 0.0)

        bubbles = split_bubbles(cleaned, max_bubbles=max(1, int(max_bubbles)))
        if not bubbles:
            return OutboundResult((), (), 0.0, 0.0)

        sent = await send_bubbles(
            channel,
            bubbles,
            reply_to=reply_to,
            turn_id=turn_id,
            typing_delay=typing_delay,
            max_bubbles=max_bubbles,
        )
        return OutboundResult(
            bubbles=tuple(sent.bubbles),
            message_ids=tuple(sent.message_ids),
            first_ts=sent.first_ts,
            last_ts=sent.last_ts,
        )
```

### 7.6 `handlers/interaction/gateway.py`

```python
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import discord

import settings
from handlers.care.cyber import event_from_message, get_cyber_engine
from handlers.interaction.contracts import (
    AUTONOMOUS_POLICY,
    FULL_MENTION_POLICY,
    TurnMode,
    TurnRequest,
)

logger = logging.getLogger(__name__)


class TurnGateway:
    """單一入口：把 Discord 訊息分類成 Passive / Autonomous lane。"""

    def __init__(self, bot: Any, *, repository: Any = None) -> None:
        self.bot = bot
        self.repository = repository
        self._last_config: Any = None
        self._last_config_at = 0.0
        self._name_tokens = tuple(
            token.lower()
            for token in (
                "雪莉",
                "sherry",
                str(getattr(settings, "BOT_NAME", "") or ""),
            )
            if token
        )

    async def _config(self) -> Any:
        if self.repository is None:
            try:
                from handlers.care.repository import get_care_repository

                self.repository = get_care_repository()
            except Exception:
                return None
        now = time.time()
        if self._last_config is not None and (now - self._last_config_at) < 5.0:
            return self._last_config
        try:
            self._last_config = await asyncio.to_thread(self.repository.get_surfer_config)
            self._last_config_at = now
        except Exception as exc:
            logger.debug("[TurnGateway] 讀取設定失敗: %s", exc)
            return None
        return self._last_config

    async def handle_message(self, message: discord.Message) -> bool:
        """回傳 True 表示 gateway 已接管，呼叫端不要再走舊 router。"""
        if message.author.bot:
            return False
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return False
        if not bool(getattr(settings, "INTERACTION_GATEWAY_ENABLED", True)):
            return False

        config = await self._config()
        if config is None or not bool(getattr(config, "enabled", False)):
            return False

        engine = get_cyber_engine()
        if engine is None or engine.mode == "off":
            return False

        channel_id = str(message.channel.id)
        sandbox_id = str(getattr(config, "sandbox_channel_id", "0") or "0").strip()
        is_sandbox = sandbox_id not in {"", "0"} and channel_id == sandbox_id

        mentioned = bool(self.bot.user and self.bot.user in message.mentions)
        event = event_from_message(message, self.bot.user, is_mentioned=mentioned)
        name_hit = bool(getattr(settings, "INTERACTION_NAME_MENTION_ENABLED", True)) and self._name_hit(event.content)

        if event.is_mentioned:
            mode = TurnMode.MENTION
        elif event.is_reply_to_bot:
            mode = TurnMode.REPLY
        elif name_hit:
            mode = TurnMode.MENTION
        elif is_sandbox:
            mode = TurnMode.AMBIENT
        else:
            return False

        if mode in (TurnMode.MENTION, TurnMode.REPLY):
            scope = str(getattr(settings, "INTERACTION_MENTION_SCOPE", "sandbox") or "sandbox").lower()
            if scope == "sandbox" and not is_sandbox:
                return False

        must_reply = mode in (TurnMode.MENTION, TurnMode.REPLY)
        priority = (
            int(getattr(settings, "PRIORITY_TAG_RESPONSE", 1))
            if must_reply
            else int(getattr(settings, "PRIORITY_INTERJECT", 2))
        )
        request = TurnRequest(
            channel_id=channel_id,
            mode=mode,
            events=(event,),
            guild_id=event.guild_id,
            target_author_id=event.author_id,
            target_author_name=event.author_name,
            message=message,
            reply_to=message,
            priority=priority,
            must_reply=must_reply,
            tool_policy=FULL_MENTION_POLICY if must_reply else AUTONOMOUS_POLICY,
            source="gateway",
        )
        go = getattr(engine, "submit_turn", None)
        if go is None:
            logger.warning("[TurnGateway] CyberEngine 尚未支援 submit_turn")
            return False
        accepted = await go(request)
        return bool(accepted)

    async def note_external_bot_spoke(
        self,
        channel_id: int | str,
        *,
        sent_at: float | None = None,
        target_author_id: int | str = "",
        message_id: str = "",
    ) -> None:
        engine = get_cyber_engine()
        if engine is None:
            return
        note = getattr(engine, "note_external_bot_speak", None)
        if note is None:
            return
        await note(
            channel_id,
            sent_at=time.time() if sent_at is None else float(sent_at),
            target_author_id=str(target_author_id or ""),
            message_id=message_id,
        )

    def _name_hit(self, content: str) -> bool:
        lowered = str(content or "").lower()
        return any(token in lowered for token in self._name_tokens)


_GATEWAY: TurnGateway | None = None


def get_turn_gateway() -> TurnGateway | None:
    return _GATEWAY


def set_turn_gateway(gateway: TurnGateway | None) -> None:
    global _GATEWAY
    _GATEWAY = gateway
```

### 7.7 `handlers/interaction/__init__.py`

```python
from __future__ import annotations

from .contracts import (
    AUTONOMOUS_POLICY,
    FULL_MENTION_POLICY,
    NO_TOOL_POLICY,
    Lane,
    ToolPolicy,
    TurnMode,
    TurnRequest,
    TurnResult,
)
from .gateway import TurnGateway, get_turn_gateway, set_turn_gateway
from .tool_policy import ToolBroker, ToolDenied

__all__ = [
    "AUTONOMOUS_POLICY",
    "FULL_MENTION_POLICY",
    "NO_TOOL_POLICY",
    "Lane",
    "ToolPolicy",
    "TurnMode",
    "TurnRequest",
    "TurnResult",
    "TurnGateway",
    "get_turn_gateway",
    "set_turn_gateway",
    "ToolBroker",
    "ToolDenied",
]
```

### 7.8 `handlers/care/cyber/state.py` 增量

```python
def note_external_bot_speak(
    self,
    *,
    sent_at: float,
    target_author_id: str = "",
    message_id: str = "",
) -> None:
    """讓 Cyber 知道 @雪莉／正常 router 已經發言，避免狀態分裂。"""
    with self._lock:
        ts = float(sent_at)
        self.last_bot_at = max(self.last_bot_at, ts)
        self.opened_at = ts
        if target_author_id:
            self.participants = {str(target_author_id)}
        self.humans_since_bot = 0
        self.lurk_streak = 0
        self.phase = RoomPhase.LISTENING
        if message_id:
            self._answered[str(message_id)] = time.time()
        self.updated_at = time.time()
```

### 7.9 `handlers/care/cyber/engine.py` 增量

```python
async def submit_turn(self, request: Any) -> bool:
    if self.mode == "off":
        return False
    if not self._started:
        await self.start()
    if self._worker is None:
        return False
    return await self._worker.submit_turn(request)

async def note_external_bot_speak(
    self,
    channel_id: int | str,
    *,
    sent_at: float,
    target_author_id: str = "",
    message_id: str = "",
) -> None:
    if self._worker is None:
        return
    if str(channel_id) != self._worker.channel_id:
        return
    self._worker.runtime.note_external_bot_speak(
        sent_at=sent_at,
        target_author_id=target_author_id,
        message_id=message_id,
    )
```

### 7.10 `handlers/care/cyber/worker.py` 增量

```python
async def submit_turn(self, request: Any) -> bool:
    if self._stopping:
        return False

    # Mention 優先：若目前有 Autonomous 回合尚未送出，取消它並把事件放回 buffer。
    if request.must_reply and self._active_request is not None and not self._active_request.must_reply:
        if not self.runtime.pending_ticket or not self.runtime.pending_ticket.committed_at:
            self._cancel_active_generation("mention_preempt")

    batch = tuple(request.events)
    for event in batch:
        self.runtime.note_human(event)
    item = (int(request.priority), self._next_seq(), request)
    await self._turn_queue.put(item)
    self._wake()
    return True
```

> `_active_request`、`_cancel_active_generation()`、`_turn_queue`、`_next_seq()` 為 worker 內部新增欄位；實際落地時把前置計畫的 `_wake_q` 改成 `asyncio.PriorityQueue`。
> 保留 `submit_human(event)` 作為 `submit_turn(TurnRequest(mode=AMBIENT, events=(event,)))` 的 wrapper，避免既有呼叫端一次全壞。

### 7.11 `handlers/care/cyber/prompts.py` 增量

```python
def build_system_prompt(
    *,
    persona: str,
    memory_block: str = "",
    dynamic_block: str = "",
    tools_enabled: bool = False,
    mode: str = "ambient",
) -> str:
    mode_rule = (
        "- 這一回合是被動回覆：直接回答對方，不要主動轉移話題。\n"
        if mode in {"mention", "reply"}
        else "- 這一回合是自主接話：只在真的有話想說時開口，不要硬接。\n"
    )
    return (
        f"{persona.strip()}\n\n"
        f"{memory_block.strip()}\n\n"
        f"{dynamic_block.strip()}\n\n"
        "<rules>\n"
        "- 你現在是在 Discord 群組裡打字，不是在寫文章。\n"
        "- 只輸出要對群友說的話；不要輸出思考過程、標籤、JSON、Markdown 清單或程式碼區塊。\n"
        "- 每次 1 到 3 句，口語、短句、可以有情緒。\n"
        "- 不要自稱 AI、語言模型或助手。\n"
        f"{mode_rule}"
        "</rules>"
    )
```

### 7.12 `handlers/social/__init__.py` 接線

在 `SocialChat.on_message` 的 vibe 更新之後、`# --- 主動插話評估 ---` 之前插入：

```python
# 單一入口：Mention / sandbox ambient 由 TurnGateway 接管。
try:
    from handlers.interaction import get_turn_gateway

    gateway = get_turn_gateway()
    if gateway is not None and await gateway.handle_message(message):
        return
except Exception as exc:
    logger.debug("TurnGateway 略過訊息: %s", exc)
```

`SocialChat.cog_load()` 初始化 gateway：

```python
from handlers.interaction import TurnGateway, set_turn_gateway

try:
    from handlers.care.repository import get_care_repository

    gateway = TurnGateway(self.bot, repository=get_care_repository())
    set_turn_gateway(gateway)
except Exception as exc:
    logger.warning("TurnGateway 初始化失敗: %s", exc)
```

`cog_unload()`：

```python
from handlers.interaction import set_turn_gateway

set_turn_gateway(None)
```

### 7.13 `handlers/care/cog.py` 接線

bot 訊息分支：

```python
if bot_user and message.author.id == bot_user.id:
    update_bot_speak_timestamp(cid_str, keep_participants=True)
    if getattr(self, "cyber", None) is not None:
        self.cyber.note_external_bot_speak(
            cid_str,
            sent_at=message.created_at.timestamp(),
            message_id=str(message.id),
        )
    return
```

sandbox 檢查之後（`cog.py:413-415`）加 live gate：

```python
if self.cyber.mode == "live" and bool(getattr(settings, "INTERACTION_GATEWAY_ENABLED", False)):
    # 單一入口已由 SocialChat → TurnGateway 接管；CareCog 只負責主動 tick。
    return
```

若 `INTERACTION_GATEWAY_ENABLED=0`，這行不會 return，舊 CareCog 熱聊仍可運作，避免「gateway 關了、Cyber 卻把訊息吃掉的空窗」。`settings` 需在 `cog.py` 頂端已 import。

### 7.14 `handlers/social/router.py` 接線

在 `dispatch_agent.execute()` 的 `finally` 之後（或 `finally` 內）通知 gateway。`router.py` 需新增 `import time`：

```python
try:
    from handlers.interaction import get_turn_gateway

    gateway = get_turn_gateway()
    if gateway is not None:
        await gateway.note_external_bot_spoke(
            message.channel.id,
            sent_at=time.time(),
            target_author_id=message.author.id,
        )
except Exception:
    pass
```

### 7.15 `handlers/social/dispatch_agent.py` 接線

把：

```python
head_persona, tail_lore = build_dynamic_persona(
    recent_text,
    "CHAT",
    channel_id=channel_id,
    server_id=server_id,
    user_query=clean_query,
    bot=bot,
    guild_id=server_id or None,
)
memory_block = build_user_memory_block(bot, user_id, display_name, query_text=clean_query)
```

改成：

```python
head_persona, tail_lore = build_dynamic_persona(
    recent_text,
    "CHAT",
    channel_id=channel_id,
    server_id=server_id,
    user_query=clean_query,
    bot=bot,
    guild_id=server_id or None,
    include_memory=False,
)

from handlers.memory.context_service import MemoryContextService

bundle = MemoryContextService.build(
    bot=bot,
    user_id=user_id,
    user_name=display_name,
    query=clean_query,
    channel_id=channel_id or "",
    guild_id=server_id,
    mode="mention",
)
memory_block = bundle.render()
```

### 7.16 `handlers/social/persona.py` 接線

在 `build_dynamic_persona()` 的記憶注入段外加：

```python
if include_memory:
    # 既有的 world_memory / self_memory / action_memory 注入
    ...
```

簽章新增：

```python
def build_dynamic_persona(
    ...
    *,
    include_memory: bool = True,
) -> tuple[str, str]:
```

### 7.17 `handlers/care/cyber/worker.py` 的 `_persona` 接線

把 `_persona()` 內的 `build_dynamic_persona(...)` 改成：

```python
head, tail = build_dynamic_persona(
    transcript,
    "CHAT",
    channel_id=channel_id_int,
    user_query=...,
    bot=self.bot,
    guild_id=guild_id,
    include_memory=False,
)
```

並在 `_execute_turn` 內用：

```python
bundle = await asyncio.to_thread(
    MemoryContextService.build,
    bot=self.bot,
    user_id=decision.target_author_id,
    user_name=batch[-1].author_name,
    query=batch[-1].content,
    channel_id=self.channel_id,
    guild_id=batch[-1].guild_id,
    mode=request.mode.value,
)
memory_block = bundle.render()
```

### 7.18 WebUI 狀態

`GET /api/care/cyber/status` 增加：

```json
{
  "lane": "passive",
  "mode": "mention",
  "must_reply": true,
  "tool_policy": "dispatch",
  "memory_sources": ["user_facts", "relationship", "retrieval"],
  "memory_chars": 812,
  "preempted": false
}
```

前端 Pill：

```javascript
const lane = cyber.lane === 'passive' ? '📣 被動回覆' : '🌙 自主行動';
const mode = cyber.mode || '—';
const mem = (cyber.memory_sources || []).length;
```

---

## 8. dafeiyu 記憶系統的借鑒範圍

### 8.1 不直接替換

dafeiyu 的記憶主體是上游 **LivingMemory**；repo 內的 `deploy/memory_observatory` 是**唯讀投影**。雪莉已經有：

- `handlers/memory/storage.py`：`user_facts`、FTS5、向量、去重、別名清洗。
- `handlers/memory/retrieval.py`：BM25＋向量＋recency＋importance＋affinity 混合檢索。
- `handlers/memory/relationship.py`：好感度與關係衰減。
- `handlers/memory/world_memory.py`：世界事件與每日壓縮。
- `handlers/memory/self_memory.py`：自憶、行動、興趣。
- `handlers/memory/nightly_cog.py`：深夜整併。
- `handlers/memory/retention_worker.py`：保留與清除政策。

直接換成 dafeiyu 的 schema 會失去上述整合，且多一套 DB 同步。**不建議。**

### 8.2 值得借的四個概念

| 概念 | dafeiyu 參考 | 雪莉落地 |
|---|---|---|
| **Provenance** | `memory_observatory/model.py` 的來源、`source`、`confidence` | `fact_meta` sidecar；不改 `user_facts` 主表 |
| **Retention curve** | `retention()`、`cue_score()`、`project()` | 在 observatory 顯示衰減；retrieval 可選加入 half-life，不強制 |
| **Read-only observatory** | `LIVINGMEMORY_V02.md`、`server.py` | 雪莉 WebUI 唯讀頁；不寫回 runtime |
| **Proposal / approval** | dafeiyu 的審批與 `UNVERIFIED` 文化 | 高影響記憶先 proposal，管理員審批才寫入 |

### 8.3 不要借的部分

- 不要引入 LivingMemory 的 DB/schema 作為 primary store。
- 不要把 observatory 的 demo snapshot 當成真實記憶。
- 不要讓模型輸出直接覆寫人格或關係。
- 不要用 `control.json` 取代雪莉的 WebUI 設定。

---

## 9. 測試計畫

### 9.1 單元測試

```python
# tests/interaction/test_turn_gateway.py
from handlers.interaction.contracts import Lane, TurnMode, TurnRequest


def test_lane_mapping():
    mention = TurnRequest(channel_id="1", mode=TurnMode.MENTION, must_reply=True)
    ambient = TurnRequest(channel_id="1", mode=TurnMode.AMBIENT)
    assert mention.lane == Lane.PASSIVE
    assert ambient.lane == Lane.AUTONOMOUS
    assert mention.must_reply is True
```

實際測試建議使用 fake message／fake config：

| 測試 | 內容 | 預期 |
|---|---|---|
| `test_mention_mode` | `message.mentions` 包含 bot | `mode=MENTION`、`must_reply=True`、`tool_policy.backend="dispatch"` |
| `test_reply_mode` | `reference.resolved.author` 是 bot | `mode=REPLY`、`must_reply=True` |
| `test_ambient_sandbox` | sandbox 一般訊息 | `mode=AMBIENT`、`must_reply=False`、`tool_policy.backend="readonly"` |
| `test_non_sandbox_ambient_ignored` | 非 sandbox 一般訊息 | `handle_message=False` |
| `test_name_hit` | 文字含「雪莉」但沒有 @ | 依 `INTERACTION_NAME_MENTION_ENABLED` 決定 |

### 9.2 記憶測試

```python
# tests/interaction/test_memory_context.py
from handlers.memory.context_service import MemoryContextService


class FakeMemoryManager:
    def get_user_memory_snippet(self, user_id, max_facts=8, query_text=""):
        return "- 喜歡喝黑咖啡"

    class relations:
        @staticmethod
        def get_relationship_snippet(user_id):
            return "好感度：信任"


class FakeBot:
    def get_cog(self, name):
        return FakeMemoryManager()


def test_memory_context_includes_user_facts():
    bundle = MemoryContextService.build(
        bot=FakeBot(),
        user_id=123,
        user_name="A",
        query="今天喝什麼",
        channel_id=1,
        guild_id=1,
        mode="mention",
    )
    text = bundle.render()
    assert "黑咖啡" in text
    assert "<user_facts>" in text
    assert "user_facts" in bundle.sources
```

| 測試 | 預期 |
|---|---|
| `test_memory_context_includes_user_facts` | Cyber prompt 含使用者 facts |
| `test_memory_context_no_duplicate_self` | `build_dynamic_persona(include_memory=False)` 後，`<self_memory>` 只出現一次 |
| `test_memory_context_budget` | 超過 `max_chars` 會截斷，不爆 prompt |
| `test_memory_context_proactive_empty_user` | `user_id=""` 不拋錯，只跳過 user facts |

### 9.3 工具測試

```python
# tests/interaction/test_tool_policy.py
import asyncio

import pytest

from handlers.interaction.contracts import AUTONOMOUS_POLICY, FULL_MENTION_POLICY
from handlers.interaction.tool_policy import ToolBroker, ToolDenied


@pytest.mark.asyncio
async def test_autonomous_denies_draw():
    broker = ToolBroker(bot=None)
    with pytest.raises(ToolDenied):
        await broker.run(AUTONOMOUS_POLICY, "DRAW", {"prompt": "cat"})


@pytest.mark.asyncio
async def test_autonomous_allows_search():
    broker = ToolBroker(bot=None)
    # 以 monkeypatch 替換 _search，避免真的連網
    broker._search = lambda args: asyncio.sleep(0, result="ok")
    assert await broker.run(AUTONOMOUS_POLICY, "SEARCH", {"query": "x"}) == "ok"


def test_mention_policy_allows_stock():
    assert "STOCK" in FULL_MENTION_POLICY.allowed
```

### 9.4 狀態／搶佔測試

| 測試 | 內容 | 預期 |
|---|---|---|
| `test_external_bot_speak_opens_window` | `note_external_bot_speak()` 後 | `window_open=True`、`participants` 正確 |
| `test_mention_preempts_ambient` | Ambient 生成中收到 Mention | Ambient 取消、事件回 buffer、Mention 先處理 |
| `test_no_double_reply` | Gateway 接管 mention 時 | 舊 router 不被呼叫 |
| `test_same_minute_reply` | 沿用前置計畫測試 | 同分鐘訊息不丟 |

### 9.5 驗收命令

```bash
python -m pytest tests/interaction tests/care/test_cyber_engine.py -q
```

若環境沒有 Python（目前工作區的 `python` 是 Windows Store stub），先在有 venv 的機器或 CI 執行；不要用「人工看過」代替測試。

---

## 10. 指標與驗收

### 10.1 必要指標

| 指標 | 意義 | 目標 |
|---|---|---|
| `mode` 分布 | mention／reply／ambient／proactive 比例 | mention 不應被誤判成 ambient |
| `must_reply` 比例 | 被動 vs 自主 | mention 100% |
| `preempted` | Mention 搶佔次數 | 有，但不頻繁 |
| `memory_sources` | 實際注入的記憶種類 | Cyber 應出現 `user_facts`／`relationship` |
| `memory_chars` | 記憶 token 預算 | P95 < 4000 chars |
| `tool_denied` | 自主 lane 被拒工具 | 應為 0（正常不該嘗試） |
| `double_reply_guard` | 雙回防護觸發 | 0 |
| `mention_to_first_bubble_ms` | 被動延遲 | P95 < 8s |
| `ambient_to_first_bubble_ms` | 自主延遲 | P95 < 15s |

### 10.2 上線門檻

- P0：72 小時 shadow，`memory_sources` 出現 `user_facts`，無 exception。
- P1：工具權限測試全綠，無 `<thought>`／`<action>` 外洩。
- P2：72 小時 `double_reply_guard=0`，同分鐘測試通過，Mention 搶佔正常。
- P3：觀測頁唯讀、provenance 可追溯、不影響回覆延遲。

---

## 11. 部署與回滾

### 11.1 Feature flags

```python
# settings.py
INTERACTION_GATEWAY_ENABLED: bool = os.getenv("INTERACTION_GATEWAY_ENABLED", "0").lower() in {"1", "true", "yes"}
INTERACTION_MENTION_SCOPE: str = os.getenv("INTERACTION_MENTION_SCOPE", "sandbox").lower()  # sandbox | all
INTERACTION_NAME_MENTION_ENABLED: bool = os.getenv("INTERACTION_NAME_MENTION_ENABLED", "1").lower() in {"1", "true", "yes"}
INTERACTION_PREEMPT_AMBIENT: bool = os.getenv("INTERACTION_PREEMPT_AMBIENT", "1").lower() in {"1", "true", "yes"}
MEMORY_CONTEXT_SERVICE_ENABLED: bool = os.getenv("MEMORY_CONTEXT_SERVICE_ENABLED", "1").lower() in {"1", "true", "yes"}
MEMORY_CONTEXT_OWNS_PERSONA_MEMORY: bool = os.getenv("MEMORY_CONTEXT_OWNS_PERSONA_MEMORY", "1").lower() in {"1", "true", "yes"}
MEMORY_OBSERVATORY_ENABLED: bool = os.getenv("MEMORY_OBSERVATORY_ENABLED", "0").lower() in {"1", "true", "yes"}
CYBER_ENGINE_MODE: str = os.getenv("CYBER_ENGINE_MODE", "shadow").lower()
CYBER_V2_OWNS_MENTIONS: bool = os.getenv("CYBER_V2_OWNS_MENTIONS", "0").lower() in {"1", "true", "yes"}
```

### 11.2 上線順序

1. P0：`CYBER_ENGINE_MODE=shadow`，`MEMORY_CONTEXT_SERVICE_ENABLED=1`，觀察 memory sources。
2. P1：`INTERACTION_GATEWAY_ENABLED=0`，先只開 shared outbound／tool policy。
3. P2：`INTERACTION_GATEWAY_ENABLED=1`，`INTERACTION_MENTION_SCOPE=sandbox`，`CYBER_ENGINE_MODE=live`。
4. 確認 72 小時無雙回後，才考慮 `INTERACTION_MENTION_SCOPE=all`。
5. P3：`MEMORY_OBSERVATORY_ENABLED=1`，只開唯讀頁。

### 11.3 回滾

| 問題 | 回滾 |
|---|---|
| 雙回覆 | `INTERACTION_GATEWAY_ENABLED=0`；`CYBER_V2_OWNS_MENTIONS=0`；重啟 SocialChat |
| 記憶重複／爆 token | `MEMORY_CONTEXT_OWNS_PERSONA_MEMORY=0`，恢復 persona 記憶 |
| 工具誤觸 | `AUTONOMOUS_POLICY` 只留 `SEARCH` |
| Mention 延遲 | `INTERACTION_PREEMPT_AMBIENT=0`，先讓 Ambient 送完 |
| 記憶觀測異常 | `MEMORY_OBSERVATORY_ENABLED=0`，不影響 runtime |

---

## 12. 風險與限制

| 風險 | 影響 | 對策 |
|---|---|---|
| `SocialChat` 與 `CareCog` 同時監聽 | 雙回 | Gateway 先接管；CareCog live 直接 return |
| `DispatchAgent` 自己發送 | 無法共用 OutboundPipeline | P0 只通知狀態；P2 再重構為回傳文字 |
| 記憶重複注入 | Token 浪費、語氣重複 | `include_memory=False`＋`MemoryContextService` 單一來源 |
| Mention 名字誤判 | 一般聊天被當成叫名字 | `INTERACTION_NAME_MENTION_ENABLED` 可關；預設只在 sandbox 開 |
| 優先級搶佔 | 半截訊息 | 只在尚未送出時取消；送出中不中斷 |
| 工具權限 | 自主 lane 誤觸 | 唯讀後端＋allowlist＋`allow_direct_ui=False` |
| 記憶 schema 變更 | 舊資料不相容 | 用 sidecar `fact_meta`，不動 `user_facts` |
| Prefix cache | prompt 變動導致 TTFT 上升 | 記憶放動態尾巴；static prefix 不變 |

---

## 13. 附錄：符號與行號索引

| 符號 | 檔案:行號 |
|---|---|
| `SocialChat.on_message` | `handlers/social/__init__.py:107` |
| `@mention` 分支 | `handlers/social/__init__.py:166` |
| `SherryCore` chat_message | `app/orchestrator.py:264` |
| `route_and_execute_tag` | `handlers/social/router.py:25` |
| 好感度 +2 | `handlers/social/router.py:73` |
| `DispatchAgentTool` | `handlers/social/dispatch_agent.py:85` |
| `_generate_final_response` | `handlers/social/dispatch_agent.py:376` |
| `build_user_memory_block` | `handlers/social/memory_builder.py:21` |
| `build_dynamic_persona` | `handlers/social/persona.py`（簽章約 119 行） |
| 世界／自憶／行動注入 | `handlers/social/persona.py:240-268` |
| `MemoryManager.on_message` | `handlers/memory/cog.py:230` |
| `get_user_memory_snippet` | `handlers/memory/cog.py:104` |
| `RelationshipManager` | `handlers/memory/relationship.py:21` |
| `build_retrieved_memory_tail` | `handlers/social/dispatch/prompts.py` |
| `get_world_memory_snippet` | `handlers/memory/world_memory.py:329` |
| `get_action_memory_snippet` | `handlers/memory/self_memory.py:604` |
| `get_self_memory_snippet` | `handlers/memory/self_memory.py:717` |
| `retrieval.search_facts_for_user` | `handlers/memory/retrieval.py:542` |
| `CareCog.on_message` | `handlers/care/cog.py:370` |
| bot 訊息分支 | `handlers/care/cog.py:389-391` |
| `CyberRoomWorker` | `handlers/care/cyber/worker.py`（前置計畫） |
| `ChannelRuntime` | `handlers/care/cyber/state.py`（前置計畫） |
| `PrimaryModelClient` | `handlers/care/cyber/model.py`（前置計畫） |
| `TOOL_LIST` | `handlers/social/registry.py` |
| `ToolSpec` 清單 | `handlers/social/tool_specs.py` |
| dafeiyu 動態 context | `dafeiyu-astrbot-main/deploy/patches/proactive_chat/llm_service.py:34-60` |
| dafeiyu 工具迴圈 | 同上 `:174-297` |
| dafeiyu 身份標籤 | `dafeiyu-astrbot-main/plugin/astrbot_plugin_face_convert/main.py:688-721` |
| dafeiyu 排程感知 | `dafeiyu-astrbot-main/plugin/astrbot_plugin_schedule_awareness/main.py:130` |
| dafeiyu 記憶投影 | `dafeiyu-astrbot-main/deploy/memory_observatory/model.py:181-240` |
| dafeiyu contact ledger | `dafeiyu-astrbot-main/plugin/astrbot_plugin_bili_activity/contact.py:44-168` |
| dafeiyu budget | `dafeiyu-astrbot-main/plugin/astrbot_plugin_bili_activity/budget.py:61-213` |

---

## 14. 最終建議

1. **先做 P0，不要直接開 gateway。** 先讓 Cyber 看見 `@雪莉`、讀得到使用者記憶，這一步風險最低、體感最明顯。
2. **P1 再統一輸出。** 不要讓 `@雪莉` 與 Cyber 繼續用兩套語氣。
3. **P2 才切單一入口。** 切之前先把 `CareCog` 的舊熱聊關掉，否則會雙回。
4. **P3 只做觀測，不改記憶引擎。** dafeiyu 的價值在 provenance／observatory／審批，不在取代雪莉的記憶。
5. **任何時候都保留 `INTERACTION_GATEWAY_ENABLED=0` 的回滾路徑。**

一句話：

> **`@雪莉` 是 must-reply lane，Cyber 是 autonomous lane；同一個大腦、同一份記憶、同一個模型，只是 policy 與工具權限不同。**

---

## 15. 實作狀態（2026-10-31）

已完成：

- `handlers/care/cyber/`：事件驅動 worker、先開窗、buffer、狀態機、`PRIMARY_MODEL` 動態轉接、工具迴圈、記憶整併、發送。
- `handlers/interaction/`：`TurnGateway`、`TurnMode`、`ToolPolicy`、唯讀 `ToolBroker`。
- `handlers/memory/context_service.py`：共用記憶讀取。
- `handlers/social/text_style.py`、`outbound.py`：共用清洗與輸出。
- `handlers/social/persona.py`：`include_memory` 開關。
- `handlers/social/dispatch_agent.py`：改用 `MemoryContextService`。
- `handlers/social/__init__.py`：TurnGateway 接入。
- `handlers/social/router.py`、`handlers/care/cog.py`：外部發言同步到 Cyber 窗口。
- `handlers/care/followup_scanner.py`：live 模式停止舊 ladder。
- `handlers/care/web.py` 與 care 前端：Cyber 狀態／模型／queue Pill。
- `handlers/care/cyber/ui_config.py`：`data/cyber_ui.json`，提供 engine_mode、gateway、mention scope、工具 allowlist、動態 context、身份標籤、記憶 context、contact budget 等 v2 設定。
- `GET/POST /api/care/cyber/config`：WebUI 可直接切換 off/shadow/live 並即時重建 worker。
- care 頁面改為「One Brain, Two Lanes」面板；舊版定時巡航／漸進巡檢參數收進回退區。
- `ChannelRuntime` 新增「追話寬限窗」：熱聊窗口關閉後，只要對方是在回雪莉上一句，仍在 `followup_window_seconds`（預設 900 秒）內就直接續聊，不再被 `lurk_streak`／插話分數卡住；同時保留 `max_humans_since_bot`、冷卻與每小時／每日額度防洗版。
- 主動發言沒有指定對象時清空 `last_bot_target_id`，寬限期內第一位回話者即視為續聊；被動回覆則只鎖定原本的對象。
- `CareCog` 的 live/gateway 判斷改讀 WebUI v2 設定，不再只看 `.env`，避免 UI 已開 gateway 但舊路徑仍重複接管。
- `handlers/care/cyber/ledger.py`：`ContactLedger` 把每頻道實際發送時間寫入 `data/cyber_ledger.json`，`ReplyBudget` 重啟後不再歸零；不同頻道各自計額。
- `CyberRoomWorker`：連續訊息 debounce（預設 10 秒，@ 為 2 秒），同一段連續打字會合併成單一回合；最多等 30 秒避免被洗版無限延後。
- `ReplyPolicy`：移除「必須先潛水 N 句」的 `lurk_wait` 硬門檻，改用 0~10 分內容分數（預設門檻 6）；問句、多句、引用雪莉、附件會加分，對別人說話的插話會降分。
- `CyberEngine._proactive_loop`：除了心智值，新增「頻道安靜 `idle_proactive_seconds` 秒就主動開話題」的觸發條件。
- WebUI v2 面板新增：連續訊息等待、@ 等待、啟用接話評分、接話門檻、安靜開話題秒數。
- 評分只作用在「非 addressed、非 followup」的 ambient 分支；關閉評分後一般訊息一律接話，仍受活躍時段、冷卻與每小時／每日額度限制。
- 工具協議 `[[TOOL:...]]`：容忍 `] ]` 結尾與常見別名（`web_search`→`SEARCH`、`stock_price`→`STOCK`、`url_summary`→`SUMMARY`）；prompt 改列 canonical 名稱；sanitizer 會移除未閉合或空白格式的工具殘留，避免協議漏到 Discord。
- `TurnGateway`：沙盒內「只叫名字、沒有真的 Tag」的訊息改走 v2 ambient，不再進舊 router；因此可與前後句一起 debounce，也不會被加上舊 router 的 🔄📌🗑️ 互動圖示。真正的 @Tag／引用回覆仍走被動 lane。
- 被動 lane 接手時會呼叫 `CyberEngine.cancel_pending()`，丟棄同頻道尚未生成的 ambient 批次，避免「一問兩答」。
- 生成速度：`build_system_prompt` 改為 **完全靜態**（SOUL 頭部 + 規則 + 工具），時間／記憶／世界書／作者備註全部移到 `build_user_prompt` 尾段。前綴快取（Gemini 隱式快取、llama.cpp KV reuse）只要開頭 token 相同就能重用，等於整段人設不用每回合重新 prefill。
- `InferenceTask` 新增 `reasoning_effort`；v2 短對話預設 `low`（WebUI 可切 medium/high）。Gemini 系後端在 medium 會先花 8192 個思考 Token，改成 low 只剩 1024，首字延遲明顯下降。
- 頻道歷史（Discord API）與記憶整理改為平行處理；歷史另加 4 秒 TTL 快取，避免每回合都打一次 API。
- ambient 回合的記憶 context 預算降到 1400 字（被動 lane 2600 字），縮短 prefill。
- 工具迴圈第一輪（只決定要不要呼叫工具）可用 `decision_max_tokens` 壓低輸出量；system prompt 也補上「一般閒聊不要呼叫工具」。
- `cyber_turn` 事件新增 `prep_ms` 與 `reasoning_effort`，可直接比較「前置處理」與「生成」各花多久。
- 測試：`tests/interaction/` 42 項；既有 care／social 相關 121 項通過。

目前預設值（保守上線）：

- `CYBER_ENGINE_MODE=shadow`：只觀察，不發送。
- `INTERACTION_GATEWAY_ENABLED=0`：不改變既有 `@雪莉` 路由。
- `CYBER_V2_OWNS_MENTIONS=0`：Mention 仍由正常 router 處理。
- `MEMORY_CONTEXT_SERVICE_ENABLED=1`：`@雪莉` 與 Cyber 先共用記憶讀取。

尚未啟用／後續：

- 真正把 Mention 放進同一個 `PriorityQueue`（目前 Passive lane 先委派既有 `route_and_execute_tag`，保留完整工具）。
- `MEMORY_OBSERVATORY_ENABLED` 的 WebUI 唯讀頁。
- `ReplyBudget` 持久化與跨模組 contact ledger。

### 26.10.31.027 回覆截斷、氣泡上限與工具半截訊息修正

- 工具迴圈第一輪的 `decision_max_tokens=256` 會讓「不需要工具、直接回答」的回覆在 1~2 句後被切斷（中文在 Gemma tokenizer 約 2 token/字）。v2 改為不額外限制第一輪，回覆上限提高到 addressed 900／ambient 800；舊 care lane 的 250／280／300 也一併提高到 800~900。
- `split_bubbles` 不再用 `chunk[:60]` 截斷；改為「只切不丟」，超過氣泡上限時併回最後一顆，所有原文都會保留。單顆氣泡軟上限 70 字。
- `sanitize_reply` 會移除被 token 上限切斷、沒有收尾的引號片段（例如「1%bakery），避免送出半截店名。
- 氣泡上限從硬鎖 3 放寬到 1~8；WebUI 儲存後立即回填伺服器實際套用值，不再重整才發現被改成 3。舊 lane 也改讀同一份設定，不再固定 2 顆。
- 工具解析器容忍 `[[TOOL: SEARCH {...}]]`（冒號後空白）。這種寫法以前會解析失敗，工具不執行，sanitizer 再把協議洗掉，只剩「我幫你搜尋一下」卻沒有下文。模型若只說要查，工具迴圈會多要一輪真正的工具呼叫或完整答案。
- 測試：`tests/interaction/` 48 項通過；新增尾端內容保留、截斷引號、空白工具協議、氣泡上限 5、工具承諾補救等回歸測試。

### 26.10.31.028 恢復 v2 主動上網找話題

- 原因：One Brain, Two Lanes 上線時，`CareCog._surfer_loop` 在 `cyber.mode == "live"` 直接 return，避免第二條發送路徑；但 CyberEngine 的主動排程只發 generic 開話題，沒有接回 RSS／興趣檢索，造成「雪莉自己上網找話題」實際消失。
- `CyberEngine` 新增 `surfer` 注入與 `_fetch_web_topic()`：主動開話題前先呼叫既有 `CareSurfer.fetch_candidates()`（RSS＋WebMonitor 興趣檢索＋已讀去重），最多等 20 秒；抓不到就照原本方式開話題，不影響發言。
- 抓到的情報以 `submit_proactive(web_topic=...)` 帶進 Cyber worker，由 `build_web_topic_block()` 併入 prompt；仍然只有 Cyber 這條發送路徑，不會重複發言。
- 發送成功後以 `record_reading_item()` 寫入閱讀紀錄，同一篇文章不會被反覆拿來開話題。
- 是否上網由原本 WebUI 的「啟用定時衝浪主動開話題」（`CareSurferConfig.enabled`）控制；關閉時只做一般主動開話題。
- 測試：`tests/interaction`＋`test_cyber_friend_engine`＋`test_social_brain` 共 86 項通過；新增上網題材進 prompt、閱讀紀錄、關閉時不抓取等回歸測試。


