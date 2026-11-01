# 賽博群友 V2：現況分析與可擴展路線圖

| 項目 | 內容 |
|---|---|
| 建立日期 | 2026-10-29 |
| 分析基準 | `dev` @ `4896b07`（起點 `ab62a63`，之後 6 個 commit） |
| 分析範圍 | `handlers/care/*`、`handlers/social/*`、`handlers/memory/self_memory.py`、`web_ui/templates/views/care.html`、相關測試 |
| 目標 | 讓雪莉從「會發文的 Bot」變成「群裡自然存在的一個群友」，並保留可擴充成多角色群聊的空間 |
| 前提 | 不改動目前 `file/soul/manosaba_sherry_default.md` 的本地修改；先以行為層與架構層改善為主 |

---

## 0. 一句話結論

> **現在缺的不是第 6 種「模式」，而是一個統一的「社交導演（Social Director）」：由它決定要不要開口、對誰開口、用什麼身分開口、說完之後怎麼接，並且把「房間狀態」持久化。**

`ab62a63` 把「主動關懷」重構成大肥魚風格的賽博群友引擎，方向是對的；但後續 6 個 commit 主要是在同一個「5 種模式 + 多條發話管線」的地基上補上下文、補心智、補節奏。**越補越像一個勤奮的內容排程器，而不是一個有生活的群友。**

---

## 1. `ab62a63` 實際建立了什麼

### 1.1 主要元件

| 元件 | 位置 | 職責 |
|---|---|---|
| `CareSurfer` | `handlers/care/surfer.py` | 衝浪、抓 RSS、興趣匹配、5 種行為模式、發文、寫自我記憶 |
| 心智狀態池 | `handlers/care/surfer.py:55` | `boredom / curiosity / energy / satiety`，module-level 全域 dict |
| `SurferBroadcaster` | `handlers/care/surfer.py` | Port 8765 SSE 實況台事件流 |
| `CareCog` | `handlers/care/cog.py` | 每分鐘 `_surfer_loop`、`on_message` 熱聊接話、窺屏評估 |
| 行為記憶 | `handlers/social/action_recorder.py` + `self_memory.self_actions` | 工具執行後寫入「我剛剛做了什麼」 |
| 即時狀態 | `handlers/social/live_state.py` | 把正在播放的音樂注入 prompt 尾端 |
| 動態人格 | `handlers/social/persona.py` | Soul + Lorebook + 自我記憶 + 世界記憶 + 情緒 + 行為記憶 + 即時狀態 |
| 設定/控制台 | `handlers/care/repository.py`、`web.py`、`care.html` | 沙盒頻道、行為模式、接話門檻、活躍時段、心智節奏 |

### 1.2 目前的 5 種行為模式

`surf_once()` 內部以 `if eff_mode == ...` 串接：

| 模式 | 行數約 | 行為 |
|---|---|---|
| `mind_monologue` | `surfer.py:~1077` | 自發思考、寫日記、偶爾分享 |
| `lurk_chat` | `surfer.py:~1248` | 讀近期對話，直接生成插話 |
| `daily_vibe` | `surfer.py:~1368` | 摸魚、吃白飯、碎碎念 |
| `care_poke` | `surfer.py:~1493` | 從待回訪話題找人傲嬌關心 |
| `surf_gossip` | `surfer.py:~1642` | 抓 RSS/搜尋、挑文章、吃瓜分享 |

### 1.3 目前有「四條」會讓雪莉開口的路徑

```text
A. CareCog.on_message 的熱聊窗口        （cog.py:391）  ← 回覆最後一則人類訊息
B. CareCog.on_message 的窺屏評估        （cog.py:484）  ← 每 N 則訊息評估一次
C. _surfer_loop → tick_mind_pulse       （cog.py:264）  ← 每分鐘推進心智，爆表觸發
D. _surfer_loop → 固定 interval 衝浪    （cog.py:283）  ← 每 interval_minutes 發一次
```

另外，`SocialChat` 的一般插話只在「非沙盒頻道」才會跑（`handlers/social/__init__.py:133`），所以沙盒內看似統一，實際上是把 **A/B/C/D 四條路徑疊在一起**，而不是收斂成一條。

---

## 2. 為什麼「跟想像的群不太一樣」

先區分三種可能的使用者期待，對照目前程式碼的症狀：

| 你期待的「群」 | 目前程式碼的實際狀況 | 結論 |
|---|---|---|
| **A. 群裡只有雪莉一個 AI，但她像一般群友自然插話** | 四條發話路徑、五套 prompt、罐頭 fallback、仍偏向「一對一陪聊」的 Soul | 有雛型，但「人格一致性」與「房間感」不足 |
| **B. 群裡有多個 AI 群友，會互相吐槽、接話** | 完全沒有 NPC roster、沒有 webhook 多身分輸出、沒有輪流發言機制 | 目前不可能出現，這是缺失的功能 |
| **C. 雪莉有真正的自主生活，會累積、會記得、會改變** | 心智狀態存在 RAM、重啟歸零；白飯數字硬編碼；沒有長期關係/話題弧線 | 目前是「演出生活感」，不是「累積生活感」 |

以下 10 個問題，是造成違和感的主要來源。

### 2.1 四條發話路徑沒有共同仲裁者

- A/B/C/D 各自有 prompt、各自的記憶注入、各自的節奏，卻沒有共用的：
  - 全域/頻道 cooldown
  - 每小時訊息上限
  - 「剛剛才講過」去重
  - 「這則是否已被其他路徑處理」鎖
- `interject_cooldown_seconds` 目前只被儲存與傳遞（`repository.py:144`、`web.py:373`），**沒有任何執行處引用**。
- 結果：可能同一分鐘內先被 A 回一句、又被 B 插一句、C 再冒泡一句；人格聽起來像三個不同的 Bot。

**影響：** 這是最直接造成「不像群、像 Bot 洗版」的原因。

### 2.2 `enabled = False` 仍然可能發文

- `_surfer_loop` 會先跑 `tick_mind_pulse()`（`cog.py:264`），而 `tick_mind_pulse()` 與 `surf_once()` **都沒有檢查 `config.enabled`**。
- 只有在 `_surfer_loop` 的固定 interval 分支（`cog.py:270`）有檢查。
- 結果：WebUI 關掉「衝浪」後，心智脈動仍可能在活躍時段觸發 `surf_once` 發文。

**影響：** 控制台給人的「關閉」是假的，會嚴重破壞信任感。

### 2.3 人格是「1 對 1 面對面」的 Soul，臨時貼上「大肥魚風格」

- `file/soul/common_rules.md` 明寫「面對面（Face-to-Face）」「兩人就在彼此眼前」「嚴禁大段小說式描寫」。
- `surfer.py` 的多數 prompt 又開頭寫「妳是橘雪莉……大肥魚風格」，要求群聊吐槽。
- 兩者疊加後，模型會同時收到「一對一深情陪伴」與「群組吃瓜吐槽」兩種場景指令。
- 另外，Soul 禁止對話中使用引號，`clean_cyber_bubble()` 又會把 `「」` 全剝掉；遇到正常引用時會破壞語意。

**影響：** 語氣會飄，時而傲嬌偵探、時而客服、時而硬要「大肥魚」。

### 2.4 五個模式各自複製貼上，新增行為成本極高

`surf_once()` 約 2,000 行，5 個 mode 各自重複：

1. 組 persona/prompt
2. 建 `InferenceTask`
3. `wait_for` + JSON 解析 + 容錯
4. 組 `bubbles`
5. 判斷活躍時段 / silent
6. 發送 + `add_token_consumption`
7. 寫 `record_self_action` / `record_action`
8. 組一個 ~30 欄位的 result payload
9. 廣播 SSE

**影響：** 想加「回表情」「轉貼迷因」「約人一起玩」「吐槽昨天的事」等新行為，都要再複製一份，品質與測試無法收斂。

### 2.5 大量硬編碼 fallback，會直接發出罐頭台詞

代表性地點：

- `surfer.py:1110`：`thought = "夜深了...回顧最近的記憶..."`、`post_text = "突然有點睡不著..."`
- `surfer.py:1271`：`post_text = "笑死 你們居然在聊這個👀 我剛剛也剛好在看這個耶！"`
- `surfer.py:1382`：`post_text = "救命…今天已經燒了 ... 碗賽博白飯..."`
- `surfer.py:1525`：`post_text = "欸 ... 你上次說的事情進展得如何了👀 哼…本名偵探只是剛好滑過去..."`
- `cog.py:426`：熱聊失敗時直接回 `"幹嘛啦！本名偵探好心說話..."`

**影響：** 只要 LLM 逾時、JSON 壞掉、模型不聽話，群裡就會出現一模一樣的台詞。這是「像 Bot」最明顯的破口。**生成失敗時，正確行為應該是「這次不說話」，而不是發備援罐頭。**

### 2.6 沒有真正的「房間狀態」

目前每次都是用 `fetch_recent_script(channel, 8)` 讀最近 8 句，然後重新推論。沒有保存：

- 誰在跟誰說話（reply graph）
- 現在的主題是什麼、已經聊多久
- 誰剛剛被無視、誰剛被吐槽
- 雪莉上次對誰說了什麼、對方有沒有回
- 這個群的內部梗、禁忌、常客關係

**影響：** 每次開口都像「第一次加入這個群」，沒有連貫性，也不會有「你們剛剛不是在聊 X 嗎」這種真正的群感。

### 2.7 發話前沒有注入「對人的記憶」

- `match_user_interests()` 只用來決定「要 Tag 誰」，且是 2~4 字 n-gram 比對（`surfer.py:709`），容易誤命中。
- 真正完整的 `build_user_memory_block()`（好感度、關係、Top-K 記憶）只用在一般對話/工具流程，**賽博群友的 5 個模式都沒有用**。
- 結果：雪莉對「老朋友」和「陌生人」說話差別不大，關懷也容易變成對著記憶摘要念稿。

### 2.8 心智狀態是 RAM 全域 dict，重啟就歸零

- `_mind_state` 在 `surfer.py:55`，process 重啟即消失。
- `_last_channel_chatter_time` / `_last_bot_speak_time` 同樣只在 RAM（`surfer.py:51-52`）。
- 重啟後 `silence_minutes` 預設用 `time.time() - 3600`，所以一開機就顯示「已冷場 60 分鐘」。
- 同一份狀態跨頻道、跨伺服器共用。

**影響：** 「生活感」無法累積；重啟後行為會突變；多頻道會互相污染。

### 2.9 心智模型的語意有雙軌，難調參

- `boredom_level` 與 `social_satiety` 是同一件事的兩種表示，`get_mind_state()` 每次讀取都會用 `social_satiety` 反推 `boredom_level`（`surfer.py:87-95`）。
- `boredom_threshold` / `social_threshold` 又用「>50 / <=50」判斷是哪一種語意（`surfer.py:2056-2062`）。
- `replenish_mind_state()` 與 `update_bot_speak_timestamp()` 會直接把 satiety 設到 100。
- `tick_mind_pulse` 的觸發條件與 `config.mind_pace` 的說明（10~15 / 25~35 / 60~90 分）沒有直接對應，實際數值是靠 `1.8/min + 冷場加速` 推出來的。

**影響：** WebUI 上的「無聊度」與實際發文頻率對不起來，調了也無法預期。

### 2.10 沒有「決策可觀測性」與品質閉環

目前只知道「有沒有發出去」，不知道：

- 這次為什麼決定說話 / 不說話？
- 是 A/B/C/D 哪條路徑觸發的？
- 用了哪個模型、花了多少 token、延遲多少？
- 人類有沒有接話？按了表情？還是直接無視？
- 同一句開場白最近用過幾次？

**影響：** 只能靠感覺調 prompt，無法判斷哪個改動真的有效。

---

## 3. 目標架構：從「模式機器」改成「社交導演」

### 3.1 核心設計

把目前的多條路徑收斂成 **每頻道一個 `SocialDirector`**，用事件 + 定時 tick 驅動，流程固定為：

```mermaid
flowchart TD
    Msg["人類訊息 / 定時 tick / 系統事件"] --> Debounce["Debounce 2~6 秒<br/>收集連續訊息"]
    Debounce --> Sense["Sense：組裝 RoomContext<br/>參與者 / 回覆關係 / 話題 / 冷場 / 雪莉狀態"]
    Sense --> Gate{"Gate 0：硬規則<br/>enabled / 活躍時段 / 冷卻 / 每小時上限 / 去重"}
    Gate -->|不通過| Silent["SILENT<br/>寫入 decision ledger"]
    Gate -->|通過| Score["Gate 1：輕量評分<br/>值不值得開口？對誰？"}
    Score -->|不值得| Silent
    Score -->|值得| Act["Gate 2：生成 Action Plan<br/>action / target / lines / reaction / delay"]
    Act --> Exec["Executor<br/>氣泡節奏 / Typing / 表情 / 回覆"]
    Exec --> Ledger["Decision Ledger + Metrics"]
    Ledger --> Learn["回饋：人類是否接話 / 被無視 / 重複率"]
```

### 3.2 建議的 Action 類型（取代 5 個 mode）

| Action | 說明 | 輸出 |
|---|---|---|
| `SILENT` | 這次不說話，只記錄 | 無 |
| `REACT` | 只按表情，不發言 | Emoji |
| `REPLY` | 回覆某一則訊息 | 1~2 氣泡 |
| `BANTER` | 延續熱聊，對多人接話 | 1~3 氣泡 |
| `INITIATE` | 主動開話題 / 分享情報 | 1~2 氣泡 + 可選連結 |
| `MONOLOGUE` | 私下寫日記 / 自我沉澱 | 寫入記憶，不一定發 |
| `FOLLOW_UP` | 幾分鐘後補一句（雙擊訊息感） | 1 氣泡，延遲發送 |

> 重點：`lurk_chat / daily_vibe / care_poke / surf_gossip / mind_monologue` 不再是互斥的「模式」，而是 **Action 的素材來源**。要不要用、用哪個，由 Director 依當下房間狀態決定。

### 3.3 建議模組結構

```text
handlers/social/autonomy/
├── __init__.py
├── state.py          # RoomState / Participant / MindState dataclass
├── store.py          # SQLite 持久化（social_rooms / social_mind / social_decisions / social_bonds）
├── context.py        # RoomContextBuilder：訊息、回覆圖、參與者、話題、冷場、雪莉狀態
├── policy.py         # Gate 0 硬規則 + Gate 1 輕量評分（可規則、可 LLM）
├── director.py       # SocialDirector：debounce、lock、tick、action 選擇
├── actions.py        # Action dataclass 與各 action 的 execute()
├── compose.py        # 統一 prompt composer + JSON schema + 容錯
├── output.py         # 氣泡、typing cadence、表情、webhook 多身分
├── telemetry.py      # decision ledger、metrics、why-silent trace
└── roster.py         # （Phase 4）多角色 NPC roster
```

`handlers/care/cog.py` 只保留：

- 啟動/停止 Director
- Discord 事件轉發（`on_message` → `director.notify_message`）
- `/care` 指令與報表

`handlers/care/surfer.py` 則降級為「內容提供者」：

- `fetch_candidates()`
- `match_user_interests()`
- `build_gossip_material()`
- `build_poke_topic()`

不再負責「決定要不要說話」與「發送」。

### 3.4 關鍵：一個頻道只有一把「發話鎖」

```python
# 概念示意
async with director.channel_lock(channel_id):
    decision = await director.decide(ctx)
    if decision.action is SILENT:
        await ledger.record(decision)
        return
    await director.execute(decision)
```

同時把以下狀態放進 lock 內檢查：

- `last_bot_message_at`
- `last_human_message_at`
- `last_action_type`
- `consecutive_bot_messages`
- `messages_this_hour`
- `recent_openers`（最近 10 次開場白）
- `last_reacted_message_id`

---

## 4. 持久化：讓「生活感」跨重啟累積

建議新增到 `care.db`（或獨立的 `social.db`，但要與 `care.db` 同一交易邊界）：

### 4.1 `social_rooms`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `channel_id` | TEXT PK | 頻道 |
| `guild_id` | TEXT | 伺服器 |
| `enabled` | INTEGER | 此頻道是否啟用 |
| `mode` | TEXT | `sandbox` / `live` |
| `last_human_at` | REAL | 最後人類訊息 |
| `last_bot_at` | REAL | 最後雪莉訊息 |
| `last_action` | TEXT | 上次 action |
| `current_topic` | TEXT | 當前話題摘要 |
| `topic_started_at` | REAL | 話題開始時間 |
| `vibe` | TEXT | 當下氣氛（熱聊/冷場/吵架/深夜） |
| `messages_this_hour` | INTEGER | 速率限制 |
| `updated_at` | REAL | |

### 4.2 `social_mind`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `channel_id` | TEXT PK | 每個頻道一份 |
| `boredom` | REAL | 0~100，單一語意 |
| `curiosity` | REAL | 0~100 |
| `energy` | REAL | 0~100 |
| `mood` | TEXT | `happy / bored / annoyed / sleepy / excited` |
| `last_thought` | TEXT | 上次內心 OS |
| `last_tick_at` | REAL | 上次推進時間 |
| `updated_at` | REAL | |

> 只保留 **boredom / curiosity / energy 三個 0~100 的累積值**，廢除 satiety 雙軌。UI 顯示 `boredom`，內部也只判斷 `boredom`。

### 4.3 `social_decisions`（決策帳本）

| 欄位 | 說明 |
|---|---|
| `decision_id` | PK |
| `channel_id` / `guild_id` | |
| `trigger` | `human_message / tick / interval / manual` |
| `action` | `SILENT / REPLY / REACT / ...` |
| `target_message_id` | 對誰 |
| `score` | 0~10 |
| `reason` | 為什麼 |
| `path` | 哪條 gate 通過 |
| `model` / `tokens` / `latency_ms` | 成本 |
| `created_at` | |

這張表是 WebUI「為什麼雪莉這次不說話？」面板的資料來源，也是調參的唯一依據。

### 4.4 `social_bonds`（可選，Phase 3）

| 欄位 | 說明 |
|---|---|
| `channel_id` / `user_id` | |
| `affinity` | -100~100 |
| `last_interaction_at` | |
| `last_topic` | |
| `inside_jokes` | JSON |
| `notes` | 雪莉對這個人的主觀印象 |

---

## 5. Prompt 與人格策略

### 5.1 拆成三層，不要每次都重寫人格

| 層 | 內容 | 特性 |
|---|---|---|
| **Static Prefix** | Soul 核心 + 群聊模式規則 + 輸出格式 + few-shot | 100% 固定，命中 KV Cache |
| **Room Context** | 最近訊息、回覆圖、參與者、話題、冷場 | 每次變動，放中段 |
| **Dynamic Tail** | 當下時間、雪莉心智、最近行動、對目標的記憶、音樂 | 緊貼生成點 |

> 目前 `surfer.py` 把「妳是橘雪莉……大肥魚風格」直接塞在每個 prompt 開頭，等於每次都在破壞前綴快取，也讓人格被任務指令污染。應該改成一份共用的 `GROUP_CHAT_RULES`，只組一次。

### 5.2 群聊模式的具體語感規則

建議寫成可測試的規則，而不是形容詞：

- 預設 **1 則氣泡、5~30 字**；只有連續吐槽時才 2 則。
- **不要每句都問問題**；每 5 則最多 1 個問句。
- **不要總結對話**、不要「大家」開頭、不要「我剛剛也剛好在看這個」。
- **優先回覆具體的人**，用 `@` 或直接叫名字；不要對空氣說話。
- **允許只按表情**、允許已讀不回、允許只回「?」「笑死」「真假」。
- **不要每次都用同一種開場白**；由 `recent_openers` 做去重。
- **不要自稱「本名偵探」**；口頭禪應該是低頻、由情境自然觸發。
- **不要用客服腔**：「有什麼可以幫你」「希望對你有幫助」「總而言之」。
- 生成失敗時 **SILENT**，不要發 fallback 罐頭。

### 5.3 「大肥魚感」應該來自狀態，不是來自形容詞

大肥魚之所以有生活感，是因為它會：

- 吃到 token / 白飯 → 有飽足/貪吃狀態
- 想偷懶 → 有 energy / 摸魚狀態
- 被叫大肥魚會嘴硬 → 有自尊/被戳的反應
- 做自己的事（寫 Wordle、玩小遊戲）→ 有自主行動紀錄
- 說「我去吃飯，測完告訴你」→ 有擺爛/拖延的選擇

建議把這些做成 **可累積的狀態與行為**，而不是在 prompt 裡寫「妳是大肥魚風格」：

```text
energy 低 → 回覆更短、更常已讀不回、偶爾說想睡
boredom 高 → 主動開話題、翻舊帳、戳人
curiosity 高 → 去衝浪、分享文章、寫日記
被叫大肥魚/被誇 → mood 變化，下一句語氣不同
```

---

## 6. 可擴展方向：A / B / C

### 方向 A：深化「單一雪莉」（建議先做）

**目標：** 群裡只有雪莉一個 AI，但她像一個真正的群友。

- 優點：成本最低、風險最低、與現有 Soul/記憶/工具完全相容。
- 缺點：不會出現「多個 AI 互相吐槽」的群像感。
- 關鍵：Social Director + 房間狀態 + 決策帳本 + 語感規則。

**適合：** 你想要的其實是「雪莉更自然、更像人」。

### 方向 B：賽博群友 NPC 團（Webhook 多身分）

**目標：** 群裡有 3~5 個 AI 角色，會互相接話、吐槽、組隊。

技術做法：

- 用 Discord **Webhook** 發送不同 `username` / `avatar_url`，不需要多個 Bot 帳號。
- `channel.webhooks()` 取得或建立一個 webhook；`webhook.send(username=..., avatar_url=..., content=...)`。
- 雪莉本人仍用 Bot 身分；其他角色用 Webhook。
- 需要一個 `GroupDirector`：
  - 決定這一輪誰說話（避免同時多個角色搶話）
  - 決定對誰說（回覆圖）
  - 限制每輪最多 1~2 個角色
  - 角色之間不要無限互相接話（AI 回音室）
- 建議只在沙盒頻道開啟，正式群仍只放雪莉。

**風險：**

- 成本 × N（每個角色都要推論）
- 容易變成「AI 自己聊給自己看」，人類插不進去
- Webhook 身分需要透明標示，避免誤導
- Discord rate limit（每個 webhook 約 30 msg/min）
- 人格一致性難維持

**適合：** 你想要的是一個「虛擬群組」的表演感，而不是單一陪伴。

### 方向 C：混合（推薦的長期路線）

- 正式群：只有雪莉（方向 A）。
- 沙盒/實況台：雪莉 + 1~2 個 NPC（方向 B），作為「群像實驗場」。
- 用同一個 Social Director，只是 `roster` 不同。
- 先在沙盒驗證「多角色輪流發言」的節奏，再決定要不要放進正式群。

| 維度 | A 單人深化 | B NPC 群像 | C 混合 |
|---|---|---|---|
| 實作成本 | 低 | 高 | 中高 |
| 真實群感 | 中高 | 最高 | 高 |
| 可控性 | 高 | 低 | 中 |
| API 成本 | 低 | 高 | 中 |
| 建議 | **先做** | 沙盒實驗 | **長期目標** |

---

## 7. 分階段路線圖

> 依照專案慣例：每個 Phase 拆成可獨立驗收的 Run，一個 commit 一個主題，先補測試再重構。

### Phase 0：止血（0.5~1 天）

**目標：** 先讓「控制台說關閉就真的關閉」、「不要在群裡連發罐頭」。

| # | 任務 | 檔案 | 驗收 |
|---|---|---|---|
| 0-1 | `tick_mind_pulse()` / `surf_once()` 檢查 `config.enabled` | `handlers/care/surfer.py` | 關閉後不再發文；單元測試 |
| 0-2 | 建立單一 `SpeechGate`，所有發話路徑先過 gate | 新 `handlers/social/autonomy/policy.py` | 同一頻道 60 秒內最多 1 次主動發話 |
| 0-3 | 真正啟用 `interject_cooldown_seconds` | `policy.py` | 冷卻內回傳 SILENT |
| 0-4 | 移除所有硬編碼 fallback；失敗改為 SILENT | `surfer.py`、`cog.py` | 不再出現「本名偵探」罐頭 |
| 0-5 | 加入「最近 10 則訊息去重」 | `policy.py` | 重複開場白率 0 |
| 0-6 | 設定 `SOCIAL_MODEL` / `SOCIAL_TEMPERATURE` | `settings.py` | 可獨立於 PRIMARY_MODEL 調群聊風格 |

**完成定義：** 關閉功能後 24 小時內零主動訊息；同一頻道不會連兩句都由不同路徑發出。

### Phase 1：房間狀態與決策帳本（2~4 天）

| # | 任務 | 檔案 | 驗收 |
|---|---|---|---|
| 1-1 | `social_rooms` / `social_mind` / `social_decisions` schema + migration | `handlers/care/repository.py` 或新 `store.py` | 重啟後 state 保留 |
| 1-2 | 心智狀態改為 per-channel、單一 0~100 語意 | `surfer.py` → `state.py` | 不再有 satiety/boredom 雙軌 |
| 1-3 | `last_human_at` / `last_bot_at` 持久化 | `store.py` | 重啟後冷場時間正確 |
| 1-4 | 每次 decision 寫入 ledger | `telemetry.py` | WebUI 可查「為什麼不說話」 |
| 1-5 | WebUI 新增「決策時間軸 / 為什麼沉默」面板 | `web_ui/templates/views/care.html`、`care.js`、`web.py` | 可看到每分鐘 tick 的決策 |

**完成定義：** 重啟後心智/冷場/上次發言不重置；任何一次「沒說話」都有原因可查。

### Phase 2：Social Director 重構（約 1 週）

| # | 任務 | 檔案 | 驗收 |
|---|---|---|---|
| 2-1 | `RoomContextBuilder`：訊息、回覆圖、參與者、話題、冷場 | `context.py` | golden test：固定 20 則訊息 → 固定 context |
| 2-2 | `Action` dataclass + `execute()` | `actions.py` | 每個 action 可獨立測試 |
| 2-3 | `SocialDirector`：debounce + channel lock + tick | `director.py` | 同一頻道同時只跑一個 decision |
| 2-4 | 把 5 個 mode 降級為素材 provider | `surfer.py` | `surf_once()` 不再直接發送 |
| 2-5 | 統一 prompt composer + JSON schema | `compose.py` | 所有 action 共用同一份規則 |
| 2-6 | 把 `build_user_memory_block()` 接入主動發話 | `context.py` | 對老朋友/陌生人語氣不同 |
| 2-7 | 重構 `cog.py` 只留下事件轉發與啟動 | `care/cog.py` | 行數大幅下降；既有測試全綠 |

**完成定義：** 新增一種 action 不需要複製 200 行；所有發話都經過同一個 gate；5 個舊模式仍可運作（相容）。

### Phase 3：品質閉環（約 1 週）

| # | 任務 | 驗收 |
|---|---|---|
| 3-1 | 50 組「群聊劇本」golden tests：預期 action / 是否 silence | 可回歸 |
| 3-2 | 人工評分表：像人 / 像 AI / 重複 / 太長 / 太愛問 | 每週抽樣 30 則 |
| 3-3 | Prompt A/B：舊 vs 新群聊規則 | 人類接話率提升 |
| 3-4 | 重複台詞偵測與自動降權 | 重複率 < 5% |
| 3-5 | 「已讀不回 / 只按表情 / 雙擊訊息」節奏 | 更像真人 |
| 3-6 | 成本與延遲 dashboard | p95 延遲、每日 token |

**完成定義：** 有一份可重複的評估流程，不再靠感覺調 prompt。

### Phase 4：NPC 群像（可選，1~2 週）

| # | 任務 | 驗收 |
|---|---|---|
| 4-1 | `roster.py`：2~3 個角色（名字、Soul、聲音、話題偏好） | 可在沙盒現身 |
| 4-2 | Webhook 輸出層（username/avatar） | 不同角色有不同頭像 |
| 4-3 | `GroupDirector`：輪流發言、禁止 AI 回音室 | 每輪最多 2 個角色 |
| 4-4 | 角色關係與互相吐槽記憶 | 會提到「上次你說…」 |
| 4-5 | 沙盒開關與正式群隔離 | 正式群仍只有雪莉 |
| 4-6 | 透明標示與 rate limit | 不誤導、不洗版 |

**完成定義：** 沙盒內可看到 2~3 個角色自然接話，且人類仍能插話；正式群不受影響。

---

## 8. KPI：怎麼知道「有變好」

| 指標 | 定義 | 目標 |
|---|---|---|
| 人類接話率 | 雪莉發話後 5 分鐘內有人回覆 / 雪莉主動訊息數 | > 35% |
| 被無視率 | 無人回覆且無表情 / 主動訊息數 | < 50% |
| 重複開場白率 | 最近 10 次 opener 重複 / 10 | < 5% |
| 平均字數 | 每則氣泡字數 | 8~30 字 |
| 氣泡數 | 每次發話的訊息數 | 1~2 |
| 延遲 p95 | 觸發到第一則氣泡 | < 8 秒 |
| 每日主動訊息 | 沙盒每頻道 | 20~40 則（可調） |
| 成本 | 每日 token / 美元 | 有上限、可觀測 |
| 人工評分 | 「像群友」1~5 分 | > 3.5 |

> 注意：不要用「發文數」當 KPI。發得越多不等於越像群；**被接話率**才是關鍵。

---

## 9. 測試策略

### 9.1 單元測試

- `SpeechGate`：enabled / 活躍時段 / cooldown / 每小時上限 / 去重，table-driven。
- `MindState`：累積、回補、重啟持久化、per-channel 隔離。
- `RoomContextBuilder`：固定訊息序列 → 固定 context（snapshot）。
- `Action.execute`：mock channel，驗證氣泡數、對象、表情。
- `DecisionLedger`：每次 decision 都有紀錄。

### 9.2 整合測試

- 模擬 20 則人類訊息，assert 只產生 1 次回覆、對象正確。
- 模擬 `enabled=False`，assert 24 小時零發文。
- 模擬重啟，assert 心智/冷場/上次發言延續。
- 模擬 LLM timeout / JSON 壞掉，assert SILENT（不是罐頭）。

### 9.3 評估腳本

建立 `tests/evaluation/group_chat_scenarios.jsonl`：

```json
{"channel": "sandbox", "messages": ["..."], "expected_action": "REPLY|SILENT|REACT", "expected_target": "user_id", "notes": "..."}
```

每次 prompt 改動都跑一次，人工抽樣評分。

---

## 10. 風險與注意事項

| 風險 | 說明 | 對策 |
|---|---|---|
| 過度重構 | `surfer.py` 已 2,000 行，一次翻掉風險高 | 先加 gate/ledger，再逐步搬 action，保持相容 |
| API 成本 | 每則訊息都呼叫 LLM 會爆 | debounce + 輕量 gate + 只有要說話才呼叫 primary |
| AI 回音室 | 多 NPC 互相接話，人類插不進去 | GroupDirector 限制每輪角色數與總訊息數 |
| Webhook 誤導 | 多身分可能被當成真人 | 只在沙盒、明確標示、可關閉 |
| Prompt injection | 群友訊息可能誘導雪莉 | 系統規則優先、輸出長度/格式限制、敏感動作需確認 |
| 記憶污染 | 錯誤記憶被寫入長期庫 | 重要度/來源標記、可撤回、定期清理 |
| Discord rate limit | 多氣泡 + 多角色容易 429 | 每個頻道 token bucket、退避 |
| 隱私 | 記憶含個資 | opt-out、軟刪除、保留期限 |

---

## 11. 建議的下一步（前三顆 commit）

### Commit 1：`fix(care): respect enabled + single speech gate + cooldown`

- `tick_mind_pulse` / `surf_once` 檢查 `enabled`
- 新增最小 `SpeechGate`（先不動架構）
- 啟用 `interject_cooldown_seconds`
- 移除硬編碼 fallback
- 補測試

### Commit 2：`feat(care): persist room & mind state + decision ledger`

- 新增 `social_rooms` / `social_mind` / `social_decisions`
- 心智改單一語意、per-channel
- 冷場時間持久化
- WebUI 顯示「為什麼沉默」

### Commit 3：`refactor(care): introduce SocialDirector skeleton`

- 建 `handlers/social/autonomy/` 骨架
- `CareCog.on_message` 只轉發事件
- 把 `lurk_chat` / `daily_vibe` 兩個最常用的 action 先搬過去
- 其餘模式保留 fallback，逐步遷移

> 先做這三顆，再決定要不要往 NPC 群像走。**在沒有決策帳本與接話率數據之前，不建議直接加多角色。**
> 後續已定案為方向 C，完整施工順序見 [`direction_c_implementation_spec.md`](./direction_c_implementation_spec.md)。

---

## 12. 不建議做的事

1. **不要再加第 6 種 mode。** 先把「要不要說話」的決策收斂。
2. **不要用更長的 prompt 硬寫「大肥魚風格」。** 形容詞不會產生生活感，狀態與行為才會。
3. **不要讓每則訊息都呼叫 LLM。** 先 debounce + 規則 gate，再輕量評分，最後才 primary。
4. **不要在沒有 ledger 的情況下調參。** 你會分不清是 prompt 變好還是隨機性好。
5. **不要一開始就做 5 個 NPC。** 先 1 個雪莉 + 1 個 NPC，在沙盒驗證輪流發言。
6. **不要讓生成失敗時發罐頭。** 沉默比「本名偵探」更像人。

---

## 13. 方向確認（已定案）

- **A. 我只要雪莉更像群友** → 走 Phase 0 → 1 → 2 → 3。
- **B. 我想要多個 AI 群友互相聊** → 先做 Phase 0/1，再直接進 Phase 4（Webhook roster）。
- **C. 兩個都要，但正式群只放雪莉** → A 做完後，把 B 當沙盒實驗。✅ **已採用**

### 13.1 定案：C

- **正式群永久 SOLO**：只能有雪莉一個 AI 身分，`SpeakerRouter` 層級硬阻擋 Webhook。
- **沙盒頻道才開 `SANDBOX_GROUP`**：雪莉 + 1~3 個 Webhook NPC，需 `manage_webhooks`、明確標示、可 kill switch。
- **共用 Social Core**：決策、狀態、記憶、帳本同一套，差別只在 Speaker adapter 與輪流規則。

完整可施工規格（schema、API、輪流規則、commit 順序、驗收條件）：

> 📄 [`direction_c_implementation_spec.md`](./direction_c_implementation_spec.md)

### 13.2 施工順序（與本文件 Phase 的對應）

| 本文件 Phase | 規格文件 commit | 目的 |
|---|---|---|
| Phase 0 止血 | C0-1 ~ C0-3 | 不再無故發文、不再發罐頭 |
| Phase 1 帳本 | C1-1 ~ C1-3 | 沉默可解釋、重啟不失憶 |
| Phase 2 Social Core | C2-1 ~ C2-3 | 四條路徑收斂成一個 director |
| Phase 3 沙盒群像 | C3-1 ~ C3-4 | 先 1 個 NPC + 雪莉，再談擴充 |

> Phase 2 結束後請先觀察 1~2 週 KPI，再決定是否進 Phase 3。**不要在沒有決策帳本的情況下開多角色。**
