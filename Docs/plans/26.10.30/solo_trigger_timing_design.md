# 正式群（SOLO）觸發時機設計

> 適用範圍：正式群 SOLO 模式。沙盒群像（SANDBOX_GROUP）本次不變。
> 目標：內容生成維持現狀，只重新設計「什麼時候該開口」。

## 1. 為什麼要改

舊邏輯的觸發條件是「距離雪莉上次發言 < `active_chat_window_seconds`」。
問題是她每次發言都會把這個計時器歸零，於是形成正回饋：

```
她講一句 → 窗口重置 → A 回一句 → 她又講 → 窗口再重置 → ...
```

而且 `on_message` 的熱聊分支傳了 `ignore_cooldown=True`，等於整條路徑沒有冷卻。
只要被 `@` 一次，接下來五分鐘內幾乎每則人類訊息都會換來一段完整生成。

另外 `mind_pulse` 用 `random.choice` 挑主動冒泡模式，會出現「頻道正熱卻自說自話」、
「冷場半小時卻想插話」的時機錯配。

## 2. 三層架構

```
L0 硬閘門    時段 / 頻道 / enabled / 冷卻 / 發言額度   ← 不花 token
L1 觸發資格  是否被點名 / 是否延續對話 / 是否達評估間隔 ← 規則判定
L2 意願裁決  interest / 話題缺口                       ← 既有 LLM 評分
```

- L0 由 `SpeechGate.evaluate` 負責，任何路徑都必須先過。
- L1 由 `DialogueTracker.classify` 負責（新增）。
- L2 沿用 `evaluate_channel_lurk` 的 1~10 分評分，只是門檻與頻率改了。

## 3. 對話鏈（DialogueTracker）

`handlers/social/autonomy/dialogue.py`

| 判定 | 條件 | 行為 |
|---|---|---|
| `ADDRESSED` | `@她`、引用回覆她的訊息、文中出現「雪莉 / sherry」 | 直接生成 |
| `CONTINUATION` | 她在 `active_chat_window_seconds` 內發言過，且這則訊息緊接其後（中間最多 1 句人類訊息），且發話者是剛才被她回覆的人 | 直接生成 |
| `AMBIENT` | 其他所有情況 | **不自動接話**，只累積插話評分計數 |

關鍵不變量：

1. 她發言後，只有「接著她這句話回」的人類訊息才算延續；別人插隊後對話鏈即中斷。
2. 計時器只是上限，不是開關。`active_chat_window_seconds=180` 表示最多延續三分鐘，
   而不是「三分鐘內誰講話都回」。
3. 主動冒泡（`surf_once`）也會開啟對話鏈，但沒有特定對象，所以只有引用回覆她的訊息
   才會被判為 `ADDRESSED`。

`update_bot_speak_timestamp()` 是唯一掛鉤點：所有發言路徑（熱聊、插話、衝浪、router 回覆）
都經過它，因此對話鏈不會漏記。

## 4. 發言額度

`care_surfer_config` 新增三個欄位：

| 欄位 | 預設 | 說明 |
|---|---|---|
| `max_replies_per_hour` | 8 | 每小時 SPEAK 次數上限（0 = 不限） |
| `max_replies_per_day` | 60 | 每 24 小時上限（0 = 不限） |
| `proactive_cooldown_seconds` | 120 | 主動插話／冒泡後的最短間隔 |

額度直接從 `social_decisions` 帳本計算（`CareRepository.count_speaks`），
不需要額外的計數器，重啟也不會歸零。
被擋下時會寫一筆 SILENT，reason 為 `hourly_budget=8/8` 或 `daily_budget=60/60`。

冷卻有兩種：

- 被動回話（有人在跟她說話）→ `interject_cooldown_seconds`，預設 45 秒。
- 主動插話／冒泡 → `proactive_cooldown_seconds`，預設 120 秒。

## 5. 主動冒泡的時機

`CareSurfer.choose_social_bubble_mode(stats, config)`：

| 頻道狀態 | 模式 |
|---|---|
| 3 分鐘內有人講話 | `None`（不冒泡，專心接話） |
| 冷場 ≥ 30 分鐘 | `surf_gossip`（上網找話題） |
| 冷場 10~30 分鐘且有回訪話題 | `care_poke` |
| 其他冷場 | `daily_vibe` |

`surf_gossip` 若抓不到候選文章，會自動退回 `daily_vibe`，不浪費一次脈動。
好奇心觸發同理：安靜時 `surf_gossip`，有人在聊時 `mind_monologue`（只寫日記不外發）。

## 6. 建議參數（閒聊型）

| 參數 | 舊值 | 新值 |
|---|---|---|
| `interject_threshold` | 3 | 6 |
| `interject_score_threshold` | 6 | 7 |
| `interject_cooldown_seconds` | 20 | 45 |
| `active_chat_window_seconds` | 300 | 180 |
| `max_replies_per_hour` | — | 8 |
| `max_replies_per_day` | — | 60 |
| `proactive_cooldown_seconds` | — | 120 |

> 既有資料庫不會自動改寫已儲存的值，需在 WebUI「賽博群友設定」調整一次。

## 7. 驗收

- `tests/unit/test_social_dialogue_trigger.py`：14 條，涵蓋對話鏈、額度、冷卻、冒泡時機。
- 決策帳本可查每筆 SILENT 的原因，用來回頭調參數。
