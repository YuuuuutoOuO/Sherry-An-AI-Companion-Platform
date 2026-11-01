# 賽博群友大腦重構：從「罐頭接話」到「有上下文的對話」

> 適用範圍：正式群 SOLO。沙盒群像（SANDBOX_GROUP）本次不動。
> 前置文件：`Docs/plans/26.10.30/solo_trigger_timing_design.md`（決定「什麼時候開口」）
> 本文處理「開口之後，大腦看到什麼、用什麼意圖說話」。

## 1. 舊版到底哪裡壞了

### 1.1 所有情境共用同一段 prompt

`handlers/care/cog.py` 的熱聊路徑硬編碼：

```text
妳剛剛在群組裡開口說話，群友【X】此時回覆了：『...』
```

這句話只在「她先說話、對方回她」時成立。但實際上同一條路徑還會處理：

- 對方第一次叫她（她根本還沒開口）
- 她正在跟同一個人一來一往（不是「剛剛」一句，是一串）
- 群友在聊，她只是被插話評分選中

結果是模型被餵了錯誤的時序，回覆會突然冒出「我剛剛不是說了嗎」這種不存在的記憶。

### 1.2 上下文只是一段沒有關係的文字

`fetch_recent_script()` 只輸出「誰說了什麼」，沒有：

- 這句是不是在回她
- 她最近說過哪幾句
- 現在是別人在聊，還是有人在跟她講話

模型只能靠猜，於是每次接話都像重新認識一個人。

### 1.3 評分與生成各自一套 prompt

`evaluate_channel_lurk()` 自己組一段 prompt、自己解析 JSON、自己判斷門檻。
熱聊路徑又另一套。兩邊的輸出格式、沉默規則、失敗處理都不一樣，改一邊很容易漏另一邊。

## 2. 新架構：四層

```text
L0 觸發       DialogueTracker / 發言額度 / 冷卻        ← 已有的「什麼時候」
L1 意圖       REPLY / JOIN / INITIATE / MONOLOGUE      ← 這次新增
L2 上下文     逐字稿 + 回覆關係 + 她最近說過的話        ← 這次新增
L3 生成       統一 prompt + 統一 JSON 契約 + 統一沉默   ← 這次新增
```

L0 已經在 `handlers/social/autonomy/dialogue.py` 完成。這次補 L1～L3，
新增 `handlers/social/brain/`。

## 3. 意圖（L1）

| 意圖 | 什麼時候 | 誰決定 | 能不能沉默 |
|---|---|---|---|
| `REPLY` | 有人叫她、回覆她、延續她剛才那串對話 | `DialogueTracker` | 只有生成失敗才沉默 |
| `JOIN` | 別人在聊，插話評分選中 | 評分 + 門檻 | 可以，分數不到就沉默 |
| `INITIATE` | 頻道冷場，她自己開話題 | 心智脈動 / 衝浪 | 可以，想不到就沉默 |
| `MONOLOGUE` | 只寫日記，不外發 | 心智脈動 | 不適用 |

關鍵差別：**REPLY 不會被模型的「不想講話」騙掉**。既然有人在跟她說話，
只要生成出內容就回；真的生成失敗才 `SILENT`。

## 4. 上下文（L2）

`handlers/social/brain/context.py` 把訊息轉成結構化的 `ContextMessage`：

```python
ContextMessage(
    message_id="...",
    author_id="...",
    author_name="阿明",
    content="可是我還想聊天",
    is_self=False,
    reply_to_self=True,      # 這句在回雪莉
    reply_to_name="雪莉",
    mentioned_self=False,
)
```

`ConversationContext` 提供：

| 方法 | 用途 |
|---|---|
| `render_transcript()` | 逐字稿；每則訊息固定一行，適合 append-only |
| `render_focus_block()` | 描述「現在這句」與她的關係 |
| `render_recent_self()` | 她最近說過的話，避免重複與失憶 |
| `render_participants()` | 最近誰在講話 |
| `render_silence()` | 冷場幾分鐘 |

逐字稿長這樣：

```text
群友 [阿明]：「今天好累」
妳自己（雪莉）：「累就早點睡啊」
群友 [阿明]（回覆妳）：「可是我還想聊天」
```

`build_conversation_context()` 會盡量讀 Discord 歷史；讀不到時（權限、
測試替身、頻道太新）自動退回 `fetch_recent_script()`，不讓生成整段失敗。

## 5. 生成（L3）

### 5.1 system / user 分離

```text
system : SOUL + tail lore + 意圖規則 + JSON 契約     ← 同一人格下可重用
user   : 逐字稿 + 現在要回應的訊息 + 她最近說過的話   ← 每輪變動
```

舊版把對象與台詞塞進 system prompt，等於每次都在改前綴。
新版的動態資訊全部放 user prompt，對 Prefix / KV cache 友善。

### 5.2 統一輸出契約

```json
{
  "decision": "speak",
  "score": 0,
  "thought": "內心 OS",
  "reaction": "",
  "reply": "說出口的話"
}
```

- `decision`：`speak` / `silent`
- `score`：只有 JOIN 會用，1～10
- `reaction`：JOIN 可以順手按的 Emoji
- `thought`：不送出，只進日誌與行為記憶
- `reply`：要送出的口語短句

`think()` 會把四種意圖都收斂成 `BrainResult`：

```python
result = await think(bot, intent=BrainIntent.REPLY, context=ctx, head_persona=head)
if result.should_speak:
    await send_cyber_bubbles(channel, split_into_bubbles(result.reply))
```

失敗、空回覆、分數不足一律得到 `result.decision == "silent"` 且 `reply == ""`。

## 6. 接線位置

| 路徑 | 檔案 | 意圖 |
|---|---|---|
| 熱聊／未 tag 接話 | `handlers/care/cog.py::on_message` | `REPLY` |
| 窺屏插話 | `handlers/care/surfer.py::evaluate_channel_lurk` | `JOIN` |
| 主動冒泡／上網找話題 | `surfer.surf_once` | 維持原 prompt（本次不動） |
| 心智日記 | `surfer` 的 `mind_monologue` | 維持原 prompt（本次不動） |

`REPLY` 的觸發條件仍然是 L0 的對話鏈：

- `ADDRESSED`：tag、引用回覆她、文中出現「雪莉 / sherry」
- `CONTINUATION`：她剛才在跟這個人一來一往，中間最多一句別人插話
- `AMBIENT`：別人在聊 → 不自動回，只累積 JOIN 評分

## 7. 不變量

1. 正式群永遠只有雪莉一個 AI 身分；大腦層不碰 Speaker。
2. 生成失敗一律 `SILENT`，不得補任何罐頭台詞。
3. 沙盒群像（SANDBOX_GROUP）不套用發言額度，也不改 prompt。
4. `REPLY` 不因模型客氣而閉嘴；`JOIN` / `INITIATE` 可以自己決定沉默。
5. 動態上下文不進 system prompt，保護 KV cache。
6. 所有沉默都要能在 `social_decisions` 查到原因。

## 8. 驗收

- `tests/unit/test_social_brain.py`：17 條
  - 回覆關係、她最近說過的話、逐字稿渲染
  - 四種意圖的 prompt 內容
  - `REPLY` 不再出現「妳剛剛開口說話」
  - 動態內容不在 system prompt
  - 空生成、分數不足、純文字回退、引號清理
  - 窺屏插話真的走 `JOIN`
- 既有 care / social / cyber_friend 測試全過。

## 9. 後續可以做的事

1. 把 `surf_gossip`、`daily_vibe` 也接上 `INITIATE`，讓主動開話題共用同一份輸出契約。
2. `MONOLOGUE` 接上 `mind_monologue`，讓日記與公開發言分開記錄。
3. 心智狀態池整理完後，把 `boredom` / `curiosity` 餵進 `INITIATE` 的 `stats`。
4. 用決策帳本的 `latency_ms` / `score` 回頭調 `interject_threshold`。

## 10. 第二階段：INITIATE 接線與心智持久化

### 10.1 `daily_vibe` → `INITIATE`

原本 `DailyVibeAction` 只有一段被壓平的 `context_block`，模型看不到誰在跟誰講話。
現在改走 `CareSurfer._brain_initiate`：

- 用 `build_conversation_context` 讀頻道歷史，取得回覆關係、她最近說過的話、參與者。
- `silence_minutes` 以心智脈動的即時統計覆寫，避免 Discord 歷史落後。
- 模型說 `silent` 就 `SILENT`，並推進 `last_run_at`，不會每分鐘重問一次。

### 10.2 `surf_gossip` → 兩段式

| 階段 | 做的事 | 對應意圖 |
|---|---|---|
| 1 | 挑文章、寫私心觀點、衍生興趣（進自我記憶庫） | 私人閱讀，不外發 |
| 2 | 看這個頻道現在的氣氛，決定要不要提起這篇、怎麼提起 | `INITIATE` |

第一階段維持原 prompt（`selected_index` / `sherry_opinion` / `new_interest` 仍要寫進記憶庫），
第二階段才由大腦決定發言；模型選 silent 時記 `brain_declined`，文章照樣沉澱。

### 10.3 心智狀態持久化

- 位置：`data/mind_state.json`（`settings.DATA_DIR`）。
- 欄位：`social_satiety`、`curiosity_satiety`、`boredom_level`、`curiosity_level`、`last_thought`、`saved_at`。
- 寫入：`accumulate_mind_state` / `replenish_mind_state` / `update_chatter_timestamp` /
  `update_bot_speak_timestamp` / `tick_mind_pulse`，10 秒節流 + 原子替換。
- 載入：`CareCog.cog_load` 呼叫 `load_mind_state()`；離線期間最多補算 10 分鐘
  （normal 節奏 1.8 無聊／分、2.0 好奇／分，不套冷場加速），避免重啟後歸零或瞬間爆表。
- 真相來源統一為飽和度，`boredom_level` / `curiosity_level` 一律由它鏡射。

### 10.4 新增測試

- `tests/unit/test_mind_state_persistence.py`：重啟還原、離線補算上限、無存檔時保留預設。
- `tests/unit/test_care_context_awareness.py`：`daily_vibe` 走 `INITIATE` 且看得到頻道對話；
  `surf_gossip` 第一段保留文章挑選、第二段才走 `INITIATE`。

