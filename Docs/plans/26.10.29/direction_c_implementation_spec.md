# 方向 C 實作規格：正式群 SOLO、沙盒群像

> 上層文件：`Docs/plans/26.10.29/cyber_group_v2_analysis_and_roadmap.md`
> 本文件把「兩個都要，但正式群只放雪莉，沙盒才開多角色」收斂成可施工規格。
> 狀態：規格定稿，尚未動工。程式碼一律以繁體中文註解、commit message 用 Conventional Commits。

---

## 0. 一句話定義

**正式群維持「一個雪莉」：她是一個人，不是一個系統。**
**沙盒頻道是「群像實驗室」：雪莉 + 1~3 個 Webhook NPC，有 scene、有輪流、有煞車。**

兩者共用同一套 Social Core（決策、記憶、狀態、帳本），差別只在 **Speaker adapter** 與 **輪流規則**。

---

## 1. 不可違反的 Invariants（先寫測試，再寫功能）

這 8 條是硬規則。任何一顆 commit 違反其中一條，就不准合。

| # | Invariant | 驗證方式 |
|---|---|---|
| I1 | 非 `SANDBOX_GROUP` 頻道永遠不會建立、修改、呼叫 Discord Webhook | `SpeakerRouter` 單元測試 + monkeypatch sentinel |
| I2 | 非沙盒頻道的發言者永遠是 primary persona（雪莉），不存在第二個 `soul_name` | `resolve()` 回傳 `BotSpeaker` 且 `persona.is_primary=True` |
| I3 | 同一頻道同一時間最多只有一個 `scene` 處於 `open` | DB partial unique index + repository 測試 |
| I4 | 單一 scene 內，同一 speaker 不得連續發言兩次 | `TurnScheduler` 單元測試 |
| I5 | 連續 3 個 NPC turn 後，調度器必須停止並記錄 `SILENT(rule=npc_streak)` | 狀態機測試 |
| I6 | 人類在任何階段發言，會取消所有「已排程未送出」的 NPC turn | 競態測試（fake clock） |
| I7 | 生成失敗、超時、內容審核失敗 → `SILENT`，不得送出任何硬編碼台詞 | grep 不到罐頭字串 + 測試 |
| I8 | `SOCIAL_SANDBOX_ENABLED=0` 時，沙盒相關 API 一律 409，且不建立 webhook | API 測試 |

> 正式群的隔離**不是 UI 隱藏**，是 `SpeakerRouter` 這一層丟 `SoloModeViolation`。UI 只是第二道。

---

## 2. 名詞

| 名詞 | 定義 |
|---|---|
| **Channel Mode** | `SOLO`（預設，正式群）/ `SANDBOX_GROUP`（沙盒群像） |
| **Persona** | 一個可發言身分。雪莉是 `is_primary=1` 的 persona；NPC 是 `is_primary=0` |
| **Speaker** | 實際把文字送到 Discord 的 adapter：`BotSpeaker` / `WebhookSpeaker` |
| **Scene** | 一次有始有終的群聊段落（有主題、有 turn 上限、可被 kill） |
| **Turn** | scene 內的一次發言（人或 AI），含 `role`、`persona_id`、`message_id` |
| **Decision** | 一次「要不要說話」的判斷結果，**包含沉默** |
| **Social Core** | 決策 + 狀態 + 記憶 + 帳本的統稱，不含傳輸層 |

---

## 3. 頻道模式與硬隔離

### 3.1 模式判定

優先序（由高到低）：

1. `social_channel_modes` 有該 `channel_id` 且 `enabled=1` → 使用該 `mode`
2. 該 `channel_id == care_surfer_config.sandbox_channel_id` → `SANDBOX_GROUP`（相容舊設定）
3. 其餘 → `SOLO`

`SOLO` 是預設值，**沒有資料列就代表 SOLO**。這確保「忘記設定」不會意外開多角色。

### 3.2 SpeakerRouter（新增 `handlers/social/autonomy/router.py`）

```python
class SoloModeViolation(RuntimeError):
    """正式群不允許第二個 AI 身分。"""


class SpeakerRouter:
    def resolve(self, *, channel, persona, mode: str) -> Speaker:
        if mode != ChannelMode.SANDBOX_GROUP:
            if not persona.is_primary:
                raise SoloModeViolation(
                    f"channel={channel.id} mode=SOLO 拒絕非 primary persona={persona.key}"
                )
            return BotSpeaker(channel)          # 唯一合法出口
        if persona.is_primary:
            return BotSpeaker(channel)
        if not settings.SOCIAL_SANDBOX_ENABLED:
            raise SoloModeViolation("sandbox 總開關未開啟")
        return WebhookSpeaker(channel, persona)  # 只有這裡能碰到 webhook
```

`WebhookSpeaker` 只在 `resolve()` 內被建構。`webhook` 相關 API 呼叫集中在 `speakers.py` 的兩個方法：

- `ensure_webhook()`：`channel.webhooks()` 找同名 → 沒有才 `channel.create_webhook(name=..., avatar=...)`
- `send()`：`webhook.send(content, username=..., avatar_url=..., wait=True, allowed_mentions=...)`

**審查規則**：PR 中只要 `create_webhook(` / `webhook.send(` 出現在 `speakers.py` 以外的地方，直接 reject。

### 3.3 硬隔離測試（Commit 1 就要有）

```python
def test_solo_channel_never_touches_webhook(monkeypatch):
    boom = Mock(side_effect=AssertionError("webhook called in SOLO"))
    monkeypatch.setattr(discord.Webhook, "send", boom)
    monkeypatch.setattr(discord.TextChannel, "create_webhook", boom)

    router = SpeakerRouter()
    for persona in all_personas():
        if persona.is_primary:
            assert isinstance(router.resolve(channel=solo, persona=persona, mode="SOLO"), BotSpeaker)
        else:
            with pytest.raises(SoloModeViolation):
                router.resolve(channel=solo, persona=persona, mode="SOLO")
```

再補一個「設定髒掉」的測試：`sandbox_channel_id == 正式群 id` 時，`SANDBOX_GROUP` 仍必須被拒絕（防呆）。

### 3.4 沙盒頻道的額外前置條件

要進 `SANDBOX_GROUP` 必須同時滿足：

1. `settings.SOCIAL_SANDBOX_ENABLED=1`
2. `social_channel_modes.enabled=1` 且 `mode='SANDBOX_GROUP'`
3. Bot 在該頻道有 `manage_webhooks`
4. `allow_webhooks=1`
5. 該頻道不是 `care_surfer_config` 指向的正式群

任一不符 → 降級 `SOLO` 並寫一筆 `decision(action=SILENT, reason=sandbox_precondition_failed)`，同時在 WebUI 顯示原因。

---

## 4. 身分與 Roster

### 4.1 Persona 來源

- 雪莉：`settings.CURRENT_SOUL_NAME`（`file/soul/manosaba_sherry_default.md`），`is_primary=1`
- NPC：`file/soul/sandbox/<key>.md`，透過既有 `_load_soul_persona_by_name()` 載入
  - 需要在 `build_dynamic_persona()` 增加 `soul_name: str | None = None` 參數（預設 `None` 代表走現行邏輯）
  - **不得**改動 `CHANNEL_SOUL_MAP` 的既有語意

### 4.2 頭像

- 存 `file/soul/sandbox/avatars/<key>.png`
- 上限 256KB、512x512，上傳時壓縮；檔名只允許 `[a-z0-9_-]+`
- Webhook 頭像用 `avatar_url`（需公開可讀）或 `edit(avatar=bytes)`；**優先用 bytes**，避免外部依賴

### 4.3 Webhook 生命週期

| 事件 | 行為 |
|---|---|
| 啟用 persona | `ensure_webhook()`，把 `webhook_id` 寫回 DB |
| 修改 name/avatar | 以 `webhook_id` 取回後 `edit()`；找不到就重建 |
| 停用 persona | 不刪 webhook（避免歷史訊息 attribution 消失），只標 `enabled=0` |
| kill switch | 停用所有 NPC，嘗試刪除本 Bot 建立的 webhook；失敗只記 log |
| Bot 離開 guild | 不做任何事（webhook 隨 guild 消失） |

DB **只存 `webhook_id`，不存 token**。token 由 `channel.webhooks()` 在執行期取得。

---

## 5. Scene 與輪流規則

### 5.1 狀態機

```
IDLE ──trigger──> OPEN ──turn──> OPEN
                    │              │
                    │              ├─ 達 turn 上限 ──> CLOSED
                    │              ├─ NPC 連續 3 次 ──> CLOSED
                    │              ├─ 人類發言 ──> OPEN（取消待送 NPC turn）
                    │              └─ kill switch ──> KILLED
                    └─ 閒置 > scene_idle_seconds ──> CLOSED
```

### 5.2 輪流規則（`TurnScheduler`）

| 規則 | 值（可設定） | 說明 |
|---|---|---|
| 每輪最多 NPC 發言數 | 2 | 一輪 = 一次觸發到人類下次發言之間 |
| 單一 speaker 連續發言 | 禁止 | 换人才能接 |
| 連續 NPC turn 上限 | 3 | 到頂強制 `SILENT` |
| scene 總 turn 上限 | 12 | 超過就收束 |
| scene 閒置關閉 | 600 秒 | 無新訊息 |
| NPC 每小時上限 | 20 | per channel |
| NPC 每日上限 | 120 | per channel |
| 人類發言後冷卻 | 0 秒 | 人類優先，不冷卻 |
| NPC 對 NPC 回覆延遲 | 2~8 秒隨機 | 避免機器感 |
| 同一 NPC 兩次發言間隔 | ≥ 45 秒 | 防止單一角色洗版 |

**人類優先原則**：`on_message` 收到人類訊息時，先 `scene.cancel_pending()`，再進入決策。已進入 `webhook.send()` 的無法取消，因此調度器必須在送出前再做一次 `scene.is_stale(trigger_seq)` 檢查。

### 5.3 反回音室

1. 每個 NPC 的 prompt 明確標示「你是誰、其他人說了什麼、不要重複上一句的句型」
2. 相鄰兩個 turn 的開頭 8 個字不得相同（送出前檢查，撞到就重生一次，再撞就 `SILENT`）
3. 同一 scene 內，`SILENT` 次數 > 6 就關閉 scene
4. NPC 不得 `@` 人類；`allowed_mentions` 一律 `parse=[]`

---

## 6. 決策帳本（沉默也要記）

新增 `social_decisions`。**每次評估都寫一筆**，不論說不說話。

```sql
CREATE TABLE IF NOT EXISTS social_decisions (
    decision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      TEXT NOT NULL,
    guild_id        TEXT NOT NULL DEFAULT '',
    scene_id        INTEGER,
    mode            TEXT NOT NULL,            -- SOLO / SANDBOX_GROUP
    trigger_type    TEXT NOT NULL,            -- human_message / mind_pulse / surf / timer / npc_turn
    trigger_ref     TEXT NOT NULL DEFAULT '', -- message_id / article_id / scene turn id
    persona_key     TEXT NOT NULL DEFAULT '',
    action          TEXT NOT NULL,            -- SPEAK / REACT / SILENT
    score           REAL NOT NULL DEFAULT 0,
    reasons_json    TEXT NOT NULL DEFAULT '[]',
    gate_json       TEXT NOT NULL DEFAULT '{}',
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    model           TEXT NOT NULL DEFAULT '',
    tokens          INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_decisions_channel
    ON social_decisions(channel_id, created_at DESC);
```

`reasons_json` 範例：

```json
["cooldown_remaining=12s", "room_mood=low", "score=4<6", "rule=interject_threshold"]
```

WebUI 的「為什麼沉默」直接讀這張表，不需要另外推論。

---

## 7. 狀態持久化

### 7.1 `social_rooms`（每個頻道的房間狀態）

```sql
CREATE TABLE IF NOT EXISTS social_rooms (
    channel_id        TEXT PRIMARY KEY,
    guild_id          TEXT NOT NULL DEFAULT '',
    last_human_at     REAL NOT NULL DEFAULT 0,
    last_bot_at       REAL NOT NULL DEFAULT 0,
    last_npc_at       REAL NOT NULL DEFAULT 0,
    last_speaker_key  TEXT NOT NULL DEFAULT '',
    cooldown_until    REAL NOT NULL DEFAULT 0,
    topic             TEXT NOT NULL DEFAULT '',
    updated_at        REAL NOT NULL
);
```

取代目前只在 RAM 的 `_last_channel_chatter_time` / `_last_bot_speak_time`。重啟後行為一致。

### 7.2 `social_mind`（單一語意，per-channel）

```sql
CREATE TABLE IF NOT EXISTS social_mind (
    channel_id   TEXT PRIMARY KEY,
    mood         REAL NOT NULL DEFAULT 0.5,   -- 0 低落 ~ 1 亢奮
    energy       REAL NOT NULL DEFAULT 0.5,   -- 0 累 ~ 1 有電
    boredom      REAL NOT NULL DEFAULT 0.0,   -- 0 不無聊 ~ 1 很無聊
    last_pulse_at REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL
);
```

原本 `get_mind_state()` 的 `active_start/active_end` 排程保留，但**數值本體**搬到 DB。`decay` 以「距上次更新秒數」計算，重啟不會歸零。

### 7.3 `social_scenes` / `social_scene_turns`

```sql
CREATE TABLE IF NOT EXISTS social_scenes (
    scene_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id    TEXT NOT NULL,
    guild_id      TEXT NOT NULL DEFAULT '',
    topic         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open', -- open / closed / killed
    max_turns     INTEGER NOT NULL DEFAULT 12,
    turns_used    INTEGER NOT NULL DEFAULT 0,
    close_reason  TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL,
    closed_at     REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_social_scenes_one_open
    ON social_scenes(channel_id) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS social_scene_turns (
    turn_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id     INTEGER NOT NULL,
    channel_id   TEXT NOT NULL,
    role         TEXT NOT NULL,        -- human / bot / npc
    persona_key  TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    message_id   TEXT NOT NULL DEFAULT '',
    reply_to     TEXT NOT NULL DEFAULT '',
    decision_id  INTEGER,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scene_turns_scene
    ON social_scene_turns(scene_id, turn_id);
```

### 7.4 `social_personas` / `social_channel_modes`

```sql
CREATE TABLE IF NOT EXISTS social_channel_modes (
    channel_id   TEXT PRIMARY KEY,
    guild_id     TEXT NOT NULL DEFAULT '',
    mode         TEXT NOT NULL DEFAULT 'SOLO',
    enabled      INTEGER NOT NULL DEFAULT 0,
    allow_webhooks INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT '',
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS social_personas (
    persona_key   TEXT PRIMARY KEY,
    channel_id    TEXT NOT NULL,
    guild_id      TEXT NOT NULL DEFAULT '',
    soul_name     TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    avatar_path   TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'npc',   -- primary / npc
    is_primary    INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 0,
    webhook_id    TEXT NOT NULL DEFAULT '',
    speech_style  TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
```

**Schema 遷移方式**：沿用 `CareRepository._init_schema()` 的 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` + `ALTER TABLE` 模式，不引入 Alembic。所有新表集中在同一段 `executescript`，新欄位用既有的 `for col_name, col_type in ...` 迴圈補。

---

## 8. 生成失敗一律 SILENT

移除所有硬編碼 fallback（例如「本名偵探」）。統一在 `SocialDirector._emit()`：

```python
if not text or text.strip() in _BANNED_FALLBACKS:
    repo.record_decision(..., action="SILENT", reasons=["generation_failed"])
    return None
```

失敗分類要記進 `reasons_json`：`timeout` / `empty` / `moderation` / `too_short` / `duplicate` / `rate_limited`。

> 使用者體感：一個人不說話是正常的；一個人突然講一句不像他的話才是 bug。

---

## 9. Kill Switch（三層）

| 層級 | 位置 | 效果 | 適用 |
|---|---|---|---|
| L1 全域 | `SOCIAL_SANDBOX_ENABLED=0` | 所有沙盒功能停用，API 回 409 | 出事第一時間 |
| L2 頻道 | `social_channel_modes.enabled=0` 或 `mode='SOLO'` | 該頻道降回 SOLO，關閉 open scene | 單一頻道失控 |
| L3 Persona | `social_personas.enabled=0` | 該 NPC 不再被選中 | 單一角色講錯話 |

WebUI 的紅色按鈕 = L2。按下後：

1. `UPDATE social_channel_modes SET enabled=0, mode='SOLO'`
2. `UPDATE social_scenes SET status='killed' WHERE channel_id=? AND status='open'`
3. `UPDATE social_personas SET enabled=0 WHERE channel_id=?`
4. 取消 `TurnScheduler` 內所有 pending task（用 `scene_seq` 失效）
5. 廣播 SSE `sandbox_killed` 到 Port 8765 實況台
6. 寫 `social_decisions(action='SILENT', reasons=['kill_switch'])`

**不做**：不刪歷史訊息、不刪記憶（避免二次傷害）。

---

## 10. WebUI 規格

### 10.1 分頁（`web_ui/templates/views/care.html`）

| 分頁 | 內容 |
|---|---|
| 雪莉（正式群） | 現有主控台 + **永久保護 banner** |
| 沙盒群像 | roster、scene inspector、kill switch（總開關關閉時整頁鎖住） |
| 決策帳本 | `social_decisions` 時間軸，可 filter `SILENT` |
| 實況台 | 既有 Port 8765 iframe / SSE |

### 10.2 正式群保護 banner（固定顯示，不可關閉）

> 🔒 此頻道為正式群，永久只允許「雪莉」一個 AI 身分。
> Webhook 多角色僅能在沙盒頻道啟用。若需測試群像，請切到「沙盒群像」分頁。

banner 要顯示後端實際判定：`mode=SOLO`、`speakers=BotSpeaker`、`webhook_calls=0`。

### 10.3 API（掛在既有 `care_bp`，沿用 `web_ui/server.py` 的 admin session 保護）

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/care/social/modes` | 列出所有頻道模式與生效原因 |
| POST | `/api/care/social/modes` | 設定某頻道 `mode` / `enabled` / `allow_webhooks` |
| GET | `/api/care/social/roster?channel_id=` | persona 列表 + webhook 狀態 |
| POST | `/api/care/social/roster` | 新增/更新 persona（含頭像 base64） |
| DELETE | `/api/care/social/roster/<key>` | 停用 persona |
| POST | `/api/care/social/roster/<key>/test` | 送一則測試訊息（僅沙盒） |
| GET | `/api/care/social/scenes?channel_id=&status=` | scene 列表 |
| GET | `/api/care/social/scenes/<id>` | scene 明細 + turns + 每 turn 的 decision |
| POST | `/api/care/social/scenes/<id>/close` | 手動關閉 |
| POST | `/api/care/social/kill` | L2 kill switch |
| GET | `/api/care/social/decisions?channel_id=&action=&limit=` | 決策帳本 |

所有寫入 API 都要檢查：目標頻道 `mode=SANDBOX_GROUP` 且 `SOCIAL_SANDBOX_ENABLED=1`，否則 `409 sandbox_disabled`。

### 10.4 Scene Inspector

時間軸呈現：

```
14:02:11  human  阿哲       「今天那個 model 又掛了」
14:02:19  npc    小夜       「掛幾次了？」        ← score 8.2, latency 1.4s
14:02:31  bot    雪莉       「第三次了吧，我剛剛也在看」← score 9.1
14:02:40  npc    小夜       SILENT               ← npc_streak=2, duplicate_open
14:03:02  human  阿哲       「對啊」
```

每一列可展開看 `reasons_json` / `gate_json` / prompt 摘要（不含完整 system prompt）。

---

## 11. 新增設定（`settings.py`）

```python
SOCIAL_SANDBOX_ENABLED: bool = _env_bool("SOCIAL_SANDBOX_ENABLED", False)
SOCIAL_SANDBOX_MAX_NPCS: int = int(os.getenv("SOCIAL_SANDBOX_MAX_NPCS", "3"))
SOCIAL_SCENE_MAX_TURNS: int = int(os.getenv("SOCIAL_SCENE_MAX_TURNS", "12"))
SOCIAL_SCENE_IDLE_SECONDS: int = int(os.getenv("SOCIAL_SCENE_IDLE_SECONDS", "600"))
SOCIAL_NPC_MAX_CONSECUTIVE: int = int(os.getenv("SOCIAL_NPC_MAX_CONSECUTIVE", "3"))
SOCIAL_NPC_MAX_PER_ROUND: int = int(os.getenv("SOCIAL_NPC_MAX_PER_ROUND", "2"))
SOCIAL_NPC_HOURLY_LIMIT: int = int(os.getenv("SOCIAL_NPC_HOURLY_LIMIT", "20"))
SOCIAL_NPC_DAILY_LIMIT: int = int(os.getenv("SOCIAL_NPC_DAILY_LIMIT", "120"))
SOCIAL_NPC_MIN_GAP_SECONDS: int = int(os.getenv("SOCIAL_NPC_MIN_GAP_SECONDS", "45"))
SOCIAL_WEBHOOK_AVATAR_MAX_BYTES: int = int(os.getenv("SOCIAL_WEBHOOK_AVATAR_MAX_BYTES", "262144"))
```

預設值刻意保守：**沙盒預設關閉、NPC 上限 3、連續 3 次就停**。

---

## 12. 分階段 Commit 清單

### Phase 0 — 止血（不動架構，先把錯誤行為關掉）

**C0-1 `fix(care): 尊重 enabled 並移除硬編碼罐頭回覆`**

- 檔案：`handlers/care/surfer.py`（`tick_mind_pulse`、`surf_once`、各 `_on_*_done`）、`handlers/care/cog.py`
- 內容：
  - `tick_mind_pulse()` / `surf_once()` 開頭檢查 `config.enabled` 與 `active_hour`
  - 生成失敗 → return，不送罐頭
  - 刪除 `_BANNED_FALLBACKS`
- 測試：`tests/care/test_surfer_enabled_gate.py`
- 驗收：關閉衝浪後 24 小時內 0 則主動訊息（以 fake clock 模擬）

**C0-2 `fix(care): 讓 interject_cooldown_seconds 真的生效`**

- 檔案：`handlers/social/interject.py`、`handlers/social/__init__.py`
- 內容：`InterjectState` 讀取 `config.interject_cooldown_seconds`；`maybe_interject` 入口檢查
- 測試：連續觸發 5 次，第 2~5 次皆 `SILENT(cooldown)`
- 驗收：`interject_cooldown_seconds` 在 `git grep` 中至少有一個執行處

**C0-3 `feat(care): 新增最小 SpeechGate 與決策記錄介面`**

- 檔案：新增 `handlers/social/autonomy/gate.py`、`handlers/care/repository.py`
- 內容：`SpeechGate.evaluate()` 回傳 `GateResult(allow, reasons)`；四條路徑先全部經過它（行為不變，只記錄）
- 測試：gate 對 `enabled=False` / 冷卻中 / 非活躍時段 都回 `allow=False` 且有 reason
- 驗收：不改使用者體感，但每次評估都有 log

> Phase 0 結束後，正式群不會再無故發文、不會再出現罐頭台詞。這是「先不變爛」。

---

### Phase 1 — 狀態與決策帳本（讓「為什麼」可觀測）

**C1-1 `feat(care): 新增 social_rooms / social_mind 持久化`**

- 檔案：`handlers/care/repository.py`、`handlers/care/surfer.py`
- 內容：新表 + `get_room/upsert_room`、`get_mind/upsert_mind`；RAM 全域改為 cache
- 測試：寫入 → 重建 repository → 讀值一致；`decay` 用 fake clock 驗證
- 驗收：重啟後 `_last_bot_speak_time` 不再歸零（有測試）

**C1-2 `feat(care): 新增 social_decisions 決策帳本`**

- 檔案：`handlers/care/repository.py`、`handlers/social/autonomy/gate.py`、四條發話路徑
- 內容：`record_decision()`，SPEAK / REACT / SILENT 全記
- 測試：每個 action 都落帳；`SILENT` 一定有 reason
- 驗收：WebUI 可查「昨天為什麼沉默」

**C1-3 `feat(web): 決策帳本分頁與沉默原因`**

- 檔案：`handlers/care/web.py`、`web_ui/templates/views/care.html`、`handlers/care/static/care.js`
- 內容：`GET /api/care/social/decisions` + 時間軸 UI
- 測試：API schema 測試（pytest + app test client）
- 驗收：不用看 log 就能回答「她為什麼沒講話」

---

### Phase 2 — Social Core（收斂四條路徑）

**C2-1 `refactor(care): 建立 SocialDirector 骨架與事件介面`**

- 檔案：新增 `handlers/social/autonomy/{__init__,contracts,director}.py`
- 內容：`SocialEvent`（human_message / mind_pulse / surf_article / timer）、`SocialDirector.handle(event)`
- 測試：事件進來 → 一定產生一筆 decision
- 驗收：`CareCog.on_message` 只剩轉發，不再自己判斷

**C2-2 `refactor(care): 把 lurk_chat / daily_vibe 搬進 SocialDirector`**

- 檔案：`handlers/care/surfer.py`、`handlers/social/autonomy/actions/*.py`
- 內容：兩個最常用 action 先遷移，其餘保留 fallback
- 測試：遷移前後對同一輸入產生相同決策（golden test）
- 驗收：`surfer.py` 行數下降；行為不變

**C2-3 `feat(care): 接入 build_user_memory_block 與 live_state`**

- 檔案：`handlers/social/autonomy/prompting.py`、`handlers/social/memory_builder.py`、`handlers/social/live_state.py`
- 內容：主動發話也吃使用者記憶與即時狀態
- 測試：有記憶 vs 無記憶的 prompt 差異
- 驗收：雪莉記得「上週講過的事」

> Phase 2 結束，正式群的「單一雪莉」已經足夠像群友。**此時先觀察 1~2 週 KPI，不急著做 NPC。**

---

### Phase 3 — 沙盒群像（最小可行：雪莉 + 1 個 NPC）

**C3-1 `feat(social): ChannelMode 與 SpeakerRouter 硬隔離`**

- 檔案：`settings.py`、`handlers/care/repository.py`、`handlers/social/autonomy/{router,speakers,modes}.py`
- 內容：`SOLO` / `SANDBOX_GROUP`、`BotSpeaker`、`WebhookSpeaker`、`SoloModeViolation`
- 測試：I1 / I2 / I8 三條 invariant + 髒設定防呆
- 驗收：`SOCIAL_SANDBOX_ENABLED=0` 時，程式碼中不存在任何可達的 `create_webhook`

**C3-2 `feat(social): persona roster 與 webhook 管理`**

- 檔案：`handlers/care/repository.py`、`handlers/social/autonomy/personas.py`、`handlers/social/persona.py`
- 內容：`social_personas` CRUD、`build_dynamic_persona(soul_name=...)`、頭像處理
- 測試：roster CRUD、頭像大小/格式驗證、soul 檔不存在時 graceful
- 驗收：可在沙盒新增 / 停用一個 NPC，不改程式碼

**C3-3 `feat(social): scene 與 TurnScheduler`**

- 檔案：`handlers/social/autonomy/scene.py`、`handlers/social/autonomy/turns.py`
- 內容：scene 狀態機、輪流規則、人類優先取消、反回音檢查
- 測試：I3 / I4 / I5 / I6 四條 invariant + 競態測試
- 驗收：三個 NPC 也不會變成互相洗版；人類一講話，待送 turn 立刻作廢

**C3-4 `feat(web): 沙盒群像分頁、scene inspector、kill switch`**

- 檔案：`handlers/care/web.py`、`web_ui/templates/views/care.html`、`handlers/care/static/care.js`、`handlers/care/surfer_server.py`
- 內容：第 10 節全部 API 與 UI；SSE 新增 `scene_turn` / `sandbox_killed`
- 測試：API 權限、409 行為、kill switch 端到端
- 驗收：按一次紅色按鈕，10 秒內該頻道不再有任何 NPC 訊息

---

## 13. 測試矩陣

| 類型 | 範圍 | 指令 |
|---|---|---|
| 單元 | gate / router / scheduler / persona | `pytest tests/social/autonomy -q` |
| 契約 | repository schema 與遷移 | `pytest tests/care/test_repository_schema.py -q` |
| 整合 | fake Discord（webhook sentinel） | `pytest tests/integration/test_solo_isolation.py -q` |
| 競態 | fake clock + 取消 pending turn | `pytest tests/social/autonomy/test_turns_race.py -q` |
| 回歸 | 既有 care / social 測試全綠 | `pytest tests -q` |

**必須存在的三個「紅線測試」**：

1. `test_solo_channel_never_touches_webhook`
2. `test_generation_failure_never_sends_canned_reply`
3. `test_kill_switch_stops_all_npc_within_10s`

---

## 14. 驗收 KPI

沙盒群像上線後觀察 2 週：

| 指標 | 目標 |
|---|---|
| 人類發言被回覆率 | ≥ 60% |
| NPC 對 NPC 佔比 | ≤ 40%（避免回音室） |
| 連續 3 NPC 觸發次數 | < 5 次 / 日 |
| 罐頭台詞出現次數 | 0 |
| 正式群 webhook 呼叫 | 0（硬性） |
| 使用者「這不像人」回饋 | 每週 ≤ 2 |

---

## 15. 風險與回滾

| 風險 | 對策 | 回滾 |
|---|---|---|
| Webhook 被誤認為真人 | 沙盒限定 + UI 標示 + `allowed_mentions=[]` | L1 關總開關 |
| 多角色洗版 | 每輪 2 則 + 連續 3 停 + 每小時 20 | L2 關頻道 |
| DB schema 壞掉 | 只新增表，不改既有欄位 | 舊版程式可忽略新表 |
| `surfer.py` 重構出錯 | Phase 2 只搬 2 個 action，golden test 保底 | revert 單一 commit |
| API 成本暴增 | 先 gate 再 LLM，NPC 用 LIGHT_MODEL | 調 `SOCIAL_*` 上限 |

**回滾原則**：Phase 0/1 可獨立 revert；Phase 3 只要 `SOCIAL_SANDBOX_ENABLED=0` 就等於沒上線。

---

## 16. 明確不做

1. 不在正式群做「雙雪莉」或「雪莉的分身」。
2. 不讓 NPC 使用工具（搜尋、記憶寫入）——只有雪莉有工具權限。
3. 不讓 NPC 主動 DM 任何人。
4. 不做「AI 自己決定要不要開新 scene」——scene 只能由人類訊息或明確 timer 觸發。
5. 不在 Phase 3 之前做 WebUI 群像頁（沒有 ledger 的 UI 只是裝飾）。

---

## 17. 施工順序一句話

**C0-1 → C0-2 → C0-3 → C1-1 → C1-2 → C1-3 →（觀察）→ C2-1 → C2-2 → C2-3 →（觀察 1~2 週）→ C3-1 → C3-2 → C3-3 → C3-4。**

前三顆（C0-1 ~ C0-3）是「先不變爛」；
中間三顆（C1-*）是「讓沉默可解釋」；
Phase 2 是「讓雪莉真的像一個人」；
Phase 3 才是「沙盒裡多幾個人」。
