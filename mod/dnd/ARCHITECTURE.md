# 🎲 AI D&D 世界引擎 — 架構設計書

> 基於橘雪莉 Discord AI Agent (v1.3.0) 的動態 D&D 插件模組。
> 模組位置：`./mod/dnd/`，遵循現有插件系統自動發現與載入機制。

---

## 目錄

1. [設計哲學](#1-設計哲學)
2. [系統架構總覽](#2-系統架構總覽)
3. [世界持續模擬引擎](#3-世界持續模擬引擎)
4. [世界觀定義策略](#4-世界觀定義策略)
5. [玩家互動流程](#5-玩家互動流程)
6. [子系統說明](#6-子系統說明)
7. [與現有系統的整合](#7-與現有系統的整合)
8. [檔案結構](#8-檔案結構)
9. [資料儲存](#9-資料儲存)
10. [實作階段](#10-實作階段)

---

## 1. 設計哲學

### 核心原則

- **Persistent World**：世界在無人上線時仍持續運作，NPC 過自己的生活、隨機事件持續發生
- **LLM-Driven Narrative**：所有故事描述、NPC 對話、事件生成由大型語言模型即時驅動，無固定劇本
- **Hybrid World Setting**：世界骨架由你定義（種族、職業、魔法等級），血肉由 LLM 填充
- **Modular Plugin**：完全獨立於雪莉主程式，透過 `./mod/` 自動發現載入，失敗不影響主程式

### 與一般 D&D Bot 的差異

| 傳統 D&D Bot | 本系統 |
|---|---|
| 固定冒險劇本 | LLM 即時生成故事 |
| 玩家離線 = 世界暫停 | 24/7 世界持續 tick |
| NPC 只有對話 | NPC 有日程、記憶、獨立人格 |
| 世界靜態不變 | 世界動態演化（城市興衰、陣營消長） |

---

## 2. 系統架構總覽

```
玩家 Discord 訊息
       │
       ▼
┌──────────────────────────────────────────┐
│  mod/dnd/cog.py                          │
│  入口 Cog：slash commands + 事件監聽      │
│  /dnd_join, /dnd_action, /dnd_status ... │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  BotBrain (現有系統)                      │
│  enqueue_task() → LLM 推論 → callback    │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  D&D 子系統                               │
│                                          │
│  character.py  ← 角色屬性/等級/經驗      │
│  dice.py       ← 擲骰引擎 (D4~D20)       │
│  combat.py     ← 回合制戰鬥               │
│  quest_gen.py  ← 任務生成與追蹤           │
│  npc_gen.py    ← NPC 人格/記憶/日程       │
│  inventory.py  ← 物品/裝備/金錢           │
│  world_sim.py  ← 世界 tick 引擎           │
│  models.py     ← 所有資料類別             │
│  storage.py    ← JSON 持久化              │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  data/dnd_*.json                         │
│  JSON 檔案持久化 (atomic write)           │
└──────────────────────────────────────────┘
```

---

## 3. 世界持續模擬引擎

### 核心概念：World Tick

世界以固定間隔的心跳（例如每 15~30 分鐘）前進。每次心跳就是一個 **World Tick**。

```
Tick N                          Tick N+1                     Tick N+2
  │                               │                            │
  ├─ 遊戲時間 +1 小時             ├─ 遊戲時間 +1 小時          ├─ 遊戲時間 +1 小時
  ├─ NPC 依照日程移動             ├─ 商店完成補貨              ├─ 隨機事件觸發！
  ├─ 檢查任務時限                 ├─ 陣營關係微調              ├─ 野外怪物重生
  └─ 寫入世界日誌                 └─ 寫入世界日誌              └─ 寫入世界日誌
```

### Tick 驅動方式

沿用現有的 `HeartbeatCog` 模式（`tasks.loop`），在 `world_sim.py` 中：

```python
@tasks.loop(minutes=15)
async def _world_tick(self):
    # 1. 推進遊戲內時間
    # 2. 執行 NPC 日程移動
    # 3. 商店補貨
    # 4. 觸發隨機事件
    # 5. 檢查任務時限
    # 6. 寫入世界日誌
```

### 時間流動

- 預設：**現實 30 分鐘 = 遊戲內 1 小時**
- 一整天週期 = 24 ticks（12 小時現實）
- 可設定加速倍率
- Bot 重啟後：計算上次 tick 到現在經過的現實時間，一次補齊錯過的 ticks

### 隨機事件系統

每個 tick 有機率（如 15%）生成事件，事件種類：

| 類別 | 範例 |
|------|------|
| 🌤️ 環境 | 天氣變化、季節轉換、自然災害 |
| 🗣️ 社交 | NPC 爭執、節慶開始、商隊抵達 |
| ⚔️ 危險 | 怪物襲擊、盜賊出沒、神秘現象 |
| 💎 機會 | 寶藏消息、任務委託、稀有商品到貨 |

事件產生後寫入 `data/dnd_history.jsonl`，可選擇推播至 Discord 頻道。

### 世界狀態資料結構

定義於 `models.py`，儲存於 `data/dnd_world.json`：

```json
{
  "game_time": {
    "year": 1247,
    "month": 3,
    "day": 17,
    "hour": 14,
    "minute": 30
  },
  "locations": {
    "start_village": {
      "name": "晨風村",
      "type": "village",
      "description": "待探索",
      "npcs_present": ["老鐵匠", "旅店老闆娘"],
      "shops": [
        { "name": "鐵匠舖", "inventory": {...}, "last_restock_tick": 142 }
      ]
    },
    "whisper_forest": {
      "name": "低語森林",
      "type": "wilderness",
      "description": "待探索",
      "encounter_table": [...]
    }
  },
  "factions": {
    "冒險者公會": { "reputation": 0, "influence": 50 },
    "暗影兄弟會": { "reputation": 0, "influence": 30 }
  },
  "npc_schedules": {
    "老鐵匠": {
      "06:00": "開店",
      "12:00": "午餐",
      "18:00": "收店",
      "21:00": "回家睡覺"
    }
  }
}
```

---

## 4. 世界觀定義策略

採用 **Hybrid 混合模式**：

### 底層骨架（你手寫，LLM 不可變更）

建檔於 `mod/dnd/world_setting.json`：

```json
{
  "world_name": "艾瑟蘭大陸",
  "era": "裂隙紀元 1247 年",
  "magic_level": "中魔",
  "starting_area": {
    "name": "晨風村",
    "type": "邊境村落"
  },
  "available_races": ["人類", "精靈", "矮人", "半身人"],
  "available_classes": ["戰士", "法師", "遊俠", "盜賊", "牧師"],
  "dm_core_directive": "你是艾瑟蘭大陸的 DM。世界是中魔設定、科技約等同中世紀歐洲。嚴禁引入現代科技或其它作品的角色。保持一致的奇幻基調。"
}
```

### 細節填充（LLM 即時生成，永久固化）

| 玩家做了... | LLM 生成... | 寫入存檔？ |
|---|---|---|
| 走進酒館 | 酒館外觀、老闆名字、在場客人 | ✅ 寫入 world.json |
| 問 NPC 名字 | NPC 名稱 + 粗略背景 | ✅ 寫入 NPC 資料 |
| 探索未知區域 | 地理環境、遇到的生物 | ✅ 寫入地圖 |
| 觸發事件 | 事件內容、獎勵、後續影響 | ✅ 寫入歷史 |

### 一致性保證

每次 LLM 生成新內容後，**同時寫入世界存檔**，確保：
- NPC 名稱不會變來變去
- 探索過的地點描述一致
- 世界有「逐步被探索擴展」的真實感

### DM Prompt 固定注入規則

每當需要 LLM 生成敘事時，系統性在 prompt 中注入：

> 你是「{world_name}」的 DM。世界觀：{magic_level}、{era}。
> 嚴禁引入現代科技或跨作品角色。保持一致的奇幻基調。
>
> 【當前世界狀態】
> - 遊戲時間：{game_time}
> - 玩家位置：{location}
> - 在場 NPC：{npcs_present}
> - 角色狀態：{character_summary}

---

## 5. 玩家互動流程

### 創建角色

```
/dnd_join [種族] [職業] [角色名]
  → character.py 創角（骰屬性、算 HP、給起始裝備）
  → 儲存至 data/dnd_characters.json
  → 回傳角色卡 Embed
```

### 採取行動（核心迴圈）

```
/dnd_action 我要去鐵匠舖看看
  │
  ├─ 1. 讀取 data/dnd_world.json（鐵匠舖狀態 + 老鐵匠行程）
  ├─ 2. 讀取 data/dnd_characters.json（玩家屬性）
  ├─ 3. 組裝 LLM Prompt（世界狀態 + NPC + 角色 + 動作）
  ├─ 4. 送 BotBrain.enqueue_task() → LLM 回傳敘事
  ├─ 5. 自動處理擲骰判定（dice.py）
  ├─ 6. 更新世界狀態（world.py）
  └─ 7. 回覆 Discord Embed 給玩家
```

### 擲骰

```
/dnd_roll d20 [優勢/劣勢]
  → dice.py 處理
  → 顯示擲骰結果 + 總計
```

### 戰鬥

```
/dnd_encounter [敵人名稱]
  → combat.py 建立 CombatSession
  → 輪到玩家時：/dnd_action [攻擊/防禦/施法]
  → 輪到敵人時：LLM 決定敵方行動
  → HP、狀態、Buff/Debuff 即時更新
```

### 其他指令

| 指令 | 功能 |
|---|---|
| `/dnd_status` | 查看角色卡（屬性、等級、HP、裝備） |
| `/dnd_inventory` | 查看背包 |
| `/dnd_quests` | 查看進行中的任務 |
| `/dnd_map` | 查看已探索的地圖 |
| `/dnd_recent` | 查看錯過的世界事件（離線期間） |
| `/dnd_world` | 查看世界總覽（時間、勢力、地區） |

---

## 6. 子系統說明

### `dice.py` — 骰子引擎

- 支援 D4 / D6 / D8 / D10 / D12 / D20
- 優勢（取高） / 劣勢（取低）機制
- 技能判定、豁免檢定、攻擊骰、傷害骰
- 暴擊（20） / 大失敗（1）自動偵測

### `character.py` — 角色系統

- 種族、職業、能力值（STR/DEX/CON/INT/WIS/CHA）
- 升級曲線（經驗值 → 等級）
- HP / AC / 熟練加值
- 技能與專長
- 法術列表（若職業為施法者）

### `combat.py` — 戰鬥系統

- 先攻順序（initiative）
- 回合制行動序列
- 敵方 AI（由 LLM 驅動決策）
- 自動計算傷害 / 減傷
- Buff / Debuff 持續時間追蹤
- Boss 戰特殊機制

### `quest_gen.py` — 任務系統

- 主線 / 支線 / 限時事件 / 隨機遭遇
- LLM 自動生成任務描述與獎勵
- 進度追蹤與完成判定
- 時限任務倒數（由 world tick 驅動）

### `inventory.py` — 物品系統

- 金錢管理（金幣 / 銀幣 / 銅幣）
- 武器 / 防具 / 消耗品 / 材料 / 任務物品
- 裝備與卸裝（影響角色 AC 與攻擊）
- 商店交易（買入 / 賣出）
- 稀有度分級

### `npc_gen.py` — NPC 系統

- NPC 由 LLM 生成名稱、外觀、人格、背景故事
- 每位 NPC 有獨立記憶（沿用 `UserFact` 模式）
- NPC 對不同玩家的好感度追蹤（沿用 `RelationshipManager` 模式）
- NPC 每日行程表（由 world tick 驅動移動）

### `world_sim.py` — 世界引擎

- 基於 `tasks.loop` 的定時 tick
- 遊戲時間推進（日夜 / 季節 / 年份）
- NPC 日程執行
- 商店經濟模擬（補貨、價格波動）
- 隨機事件生成與觸發
- 任務時限檢查

---

## 7. 與現有系統的整合

| 現有系統 | 整合方式 |
|----------|----------|
| `BotBrain.enqueue_task()` | 所有 D&D LLM 呼叫（敘事、NPC 對話、戰鬥、任務生成）都走這個佇列 |
| `handlers/memory/world_memory.py` | D&D 世界事件寫入 `log_world_event()`，prompt 注入 `get_world_memory_snippet()` |
| `handlers/memory/relationship.py` | NPC 對玩家的好感度直接使用 `RelationshipManager` 模式 |
| `handlers/memory/storage.py` | NPC 長期記憶沿用 `UserFact` / `make_fact` / `sanitize_and_merge_facts` |
| `handlers/social/persona.py` | D&D 模式注入額外的「DM 鐵則」persona 層 |
| `handlers/heartbeat/` (tasks.loop 模式) | `world_sim.py` 直接複用相同模式 |
| `handlers/social/utils.py` (simulate_typing_and_send) | 傳送 D&D 長篇敘事訊息 |
| Discord Embed | 角色卡 / 物品 / 任務使用 Embed 呈現 |

### LLM 呼叫量分析

| 情境 | 使用模型 | 頻率 |
|------|----------|------|
| `/dnd_action` 敘事 | PRIMARY_MODEL | 每次玩家動作 |
| NPC 對話 | PRIMARY_MODEL | 每次玩家與 NPC 互動 |
| 隨機事件內容 | PRIMARY_MODEL | 每次 world tick 觸發事件時 |
| 戰鬥敵方 AI | PRIMARY_MODEL | 每回合敵人行動時 |
| 任務生成 | PRIMARY_MODEL | 任務觸發時 |
| NPC 記憶萃取 | CLASSIFIER_MODEL | NPC 互動累積後背景執行 |

---

## 8. 檔案結構

```
mod/dnd/
├── __init__.py           # 空的套件標記
├── cog.py                # 主 Cog：slash commands + 事件監聽
├── models.py             # 所有資料類別 (dataclasses / TypedDict)
├── storage.py            # JSON 讀寫 (atomic write 模式)
├── dice.py               # 骰子引擎 (D4~D20)
├── world_sim.py          # 世界 tick 迴圈 (tasks.loop)
├── world_setting.json    # 你手寫的世界骨架設定
├── character.py          # 角色創建與成長
├── combat.py             # 回合制戰鬥管理器
├── quest_gen.py          # 任務生成與追蹤
├── inventory.py          # 物品 / 裝備 / 金錢管理
├── npc_gen.py            # NPC 生成、記憶、日程
└── ARCHITECTURE.md       # 本文件
```

---

## 9. 資料儲存

所有檔案位於 `data/` 目錄，沿用現有的 JSON / JSONL 模式。

| 檔案 | 內容 | 格式 |
|------|------|------|
| `data/dnd_world.json` | 世界狀態（時間、地點、NPC 位置、陣營） | JSON |
| `data/dnd_world_setting.json` | 世界骨架（種族、職業、魔法等級） | JSON |
| `data/dnd_characters.json` | 所有玩家角色資料 | JSON |
| `data/dnd_quests.json` | 進行中的任務 | JSON |
| `data/dnd_combat.json` | 戰鬥中的 session | JSON |
| `data/dnd_history.jsonl` | 世界事件歷史 log | JSONL |

### 存取模式

- **讀取**：每次玩家動作時讀取，組裝進 LLM prompt
- **寫入**：LLM 回傳後解析，更新對應 JSON，使用 atomic write（先寫 `.tmp` 再 `replace`）
- **歷史**：JSONL append-only，定期壓縮清理

---

## 10. 實作階段

### Phase 1 — 基礎建設

- `models.py` — 所有資料類別
- `storage.py` — JSON 讀寫
- `dice.py` — 骰子引擎
- `cog.py` — `/dnd_join` 創角 + `/dnd_roll` 擲骰
- `world_setting.json` — 世界骨架

### Phase 2 — 世界引擎

- `world_sim.py` — World tick 迴圈
- 遊戲時間推進
- NPC 日程移動
- 隨機事件生成

### Phase 3 — 核心玩法

- `character.py` — 等級成長、屬性檢定
- `inventory.py` — 物品管理、商店交易
- `npc_gen.py` — NPC 生成與記憶
- `quest_gen.py` — 任務生成與追蹤

### Phase 4 — 戰鬥系統

- `combat.py` — 先攻、回合制、敵方 AI、Buff/Debuff

---

> 📌 此架構文件對應於雪莉插件系統 `./mod/dnd/`。
> 載入方式：檔案就緒後重啟 bot，`main.py` 會自動發現並載入，失敗不影響主程式。
