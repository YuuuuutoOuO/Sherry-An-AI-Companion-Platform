"""主動關懷引擎的持久化層（R19）。

``data/care.db`` 保存五類狀態：

- ``care_preferences``：使用者 opt-in／opt-out 與頻率（預設 opt-out）。
- ``care_budget``：每人每週、全域每日的滾動窗口額度。
- ``care_actions``：每次主動關懷的稽核紀錄（允許與拒絕都留痕）。
- ``care_topics``：深夜整合待回訪話題佇列。
- ``care_guilds``：伺服器灰度（無資料列＝未開啟／0%）。

所有寫入皆走 SQLite 交易；額度扣減使用 ``BEGIN IMMEDIATE``，避免多實例
同時通過預算檢查。
"""

from __future__ import annotations

import json
import logging
import hashlib
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import settings
from core.db import get_db_connection

logger = logging.getLogger(__name__)

WEEK_SECONDS = 7 * 24 * 60 * 60
DAY_SECONDS = 24 * 60 * 60
VALID_FREQUENCIES = ("daily", "weekly", "off")


@dataclass(frozen=True)
class CarePreference:
    """單一使用者在特定 guild 的關懷偏好。"""

    user_id: str
    guild_id: str = ""
    opt_in: bool = False
    frequency: str = "weekly"
    updated_at: float = 0.0


@dataclass(frozen=True)
class BudgetResult:
    """關懷額度檢查結果。"""

    allowed: bool
    reason: str
    user_used: int = 0
    user_limit: int = 0
    global_used: int = 0
    global_limit: int = 0


@dataclass(frozen=True)
class GuildRollout:
    """單一伺服器的灰度設定；無資料列代表未開啟（0%）。"""

    guild_id: str
    enabled: bool = False
    rollout_percent: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "enabled": self.enabled,
            "rollout_percent": self.rollout_percent,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class CareFeedbackStats:
    """主動關懷的回饋統計（核准、回應、沉默）。"""

    approved: int = 0
    rejected: int = 0
    responded: int = 0
    silent: int = 0
    pending: int = 0

    @property
    def resolved(self) -> int:
        return self.responded + self.silent

    @property
    def response_rate(self) -> float:
        return self.responded / self.resolved if self.resolved else 0.0

    @property
    def silence_rate(self) -> float:
        return self.silent / self.resolved if self.resolved else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "rejected": self.rejected,
            "responded": self.responded,
            "silent": self.silent,
            "pending": self.pending,
            "resolved": self.resolved,
            "response_rate": round(self.response_rate, 4),
            "silence_rate": round(self.silence_rate, 4),
        }


DEFAULT_SURFER_RSS_FEEDS: list[dict[str, str]] = [
    {"name": "巴哈姆特 GNN", "url": "https://gnn.gamer.com.tw/rss.xml"},
    {"name": "科技新報", "url": "https://technews.tw/feed/"},
    {"name": "泛科學", "url": "https://pansci.asia/feed"},
]

DEFAULT_SURFER_KEYWORDS: list[str] = [
    "AI 新技術與開源模型",
    "ACG 動漫遊戲新品發布",
    "科技新知與有趣發明",
]


@dataclass(frozen=True)
class CareSurferConfig:
    """雪莉網活仔與賽博群友衝浪設定（單一測試頻道沙盒）。"""

    sandbox_channel_id: str = "0"
    enabled: bool = False
    interval_minutes: int = 120
    tag_mode: str = "interest_match"  # interest_match | active_random | none
    rss_feeds: list[dict[str, str]] = field(default_factory=lambda: list(DEFAULT_SURFER_RSS_FEEDS))
    search_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_SURFER_KEYWORDS))
    last_run_at: float = 0.0
    updated_at: float = 0.0
    # === 大肥魚賽博群友擴充欄位 ===
    behavior_mode: str = "balanced"  # balanced | lurk_chat | daily_vibe | care_poke | surf_gossip
    chat_reply_enabled: bool = True  # 是否開啟沙盒頻道雙向陪聊與窺屏話題接話
    interject_threshold: int = 6  # 窺屏評估門檻（每 N 句評估一次）
    interject_score_threshold: int = 7  # 插話意願門檻 (1~10)
    interject_cooldown_seconds: int = 45  # 接話冷卻秒數
    active_chat_window_seconds: int = 180  # 對話延續上限（秒）
    multi_message_enabled: bool = True  # 分段短句連發模式
    emoji_reaction_enabled: bool = True  # 自動按 Emoji 表情互動
    include_url_mode: str = "never"  # never | rare | always
    # === 允許主動講話時段（防半夜擾民） ===
    active_hour_start: int = 9  # 預設早上 9 點開始允許主動講話
    active_hour_end: int = 23  # 預設晚上 23 點後進入深夜休眠靜音
    mind_pace: str = "normal"  # fast (10~15分) | normal (25~35分) | chill (60~90分)
    # === 發言額度（防止話癆；0 表示不限） ===
    max_replies_per_hour: int = 8  # 每小時最多發言次數
    max_replies_per_day: int = 60  # 每 24 小時最多發言次數
    proactive_cooldown_seconds: int = 120  # 主動插話／冒泡後的最短間隔
    # === 自主回覆後漸進退避掃描追蹤 (Follow-up Scanner) ===
    followup_scan_enabled: bool = True  # 是否開啟自主發言後漸進巡視頻道接話
    followup_scan_intervals: list[int] = field(default_factory=lambda: [1, 3, 5, 7, 10])  # 掃描梯級（分鐘）

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_channel_id": self.sandbox_channel_id,
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "tag_mode": self.tag_mode,
            "rss_feeds": self.rss_feeds,
            "search_keywords": self.search_keywords,
            "last_run_at": self.last_run_at,
            "updated_at": self.updated_at,
            "behavior_mode": self.behavior_mode,
            "chat_reply_enabled": self.chat_reply_enabled,
            "interject_threshold": self.interject_threshold,
            "interject_score_threshold": self.interject_score_threshold,
            "interject_cooldown_seconds": self.interject_cooldown_seconds,
            "active_chat_window_seconds": self.active_chat_window_seconds,
            "multi_message_enabled": self.multi_message_enabled,
            "emoji_reaction_enabled": self.emoji_reaction_enabled,
            "include_url_mode": self.include_url_mode,
            "active_hour_start": self.active_hour_start,
            "active_hour_end": self.active_hour_end,
            "mind_pace": self.mind_pace,
            "max_replies_per_hour": self.max_replies_per_hour,
            "max_replies_per_day": self.max_replies_per_day,
            "proactive_cooldown_seconds": self.proactive_cooldown_seconds,
            "followup_scan_enabled": self.followup_scan_enabled,
            "followup_scan_intervals": list(self.followup_scan_intervals),
        }


@dataclass(frozen=True)
class SocialRoom:
    """單一頻道的房間狀態（持久化，重啟不歸零）。"""

    channel_id: str
    guild_id: str = ""
    last_human_at: float = 0.0
    last_bot_at: float = 0.0
    last_npc_at: float = 0.0
    last_speaker_key: str = ""
    cooldown_until: float = 0.0
    topic: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "last_human_at": self.last_human_at,
            "last_bot_at": self.last_bot_at,
            "last_npc_at": self.last_npc_at,
            "last_speaker_key": self.last_speaker_key,
            "cooldown_until": self.cooldown_until,
            "topic": self.topic,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SocialMind:
    """單一頻道的心智狀態（0~1 正規化語意）。"""

    channel_id: str
    mood: float = 0.5
    energy: float = 0.5
    boredom: float = 0.0
    last_pulse_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "mood": self.mood,
            "energy": self.energy,
            "boredom": self.boredom,
            "last_pulse_at": self.last_pulse_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SocialChannelMode:
    """頻道模式設定；無資料列代表正式群 SOLO。"""

    channel_id: str
    guild_id: str = ""
    mode: str = "SOLO"
    enabled: bool = False
    allow_webhooks: bool = False
    note: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "mode": self.mode,
            "enabled": self.enabled,
            "allow_webhooks": self.allow_webhooks,
            "note": self.note,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SocialPersonaRecord:
    """可發言身分。雪莉是 is_primary=1 的 persona。"""

    persona_key: str
    channel_id: str
    soul_name: str
    display_name: str
    guild_id: str = ""
    avatar_path: str = ""
    role: str = "npc"
    is_primary: bool = False
    enabled: bool = False
    webhook_id: str = ""
    speech_style: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_key": self.persona_key,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "soul_name": self.soul_name,
            "display_name": self.display_name,
            "avatar_path": self.avatar_path,
            "role": self.role,
            "is_primary": self.is_primary,
            "enabled": self.enabled,
            "webhook_id": self.webhook_id,
            "speech_style": self.speech_style,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SocialSceneRecord:
    """一次有始有終的群聊段落。"""

    scene_id: int
    channel_id: str
    guild_id: str = ""
    topic: str = ""
    status: str = "open"
    max_turns: int = 12
    turns_used: int = 0
    close_reason: str = ""
    started_at: float = 0.0
    closed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "topic": self.topic,
            "status": self.status,
            "max_turns": self.max_turns,
            "turns_used": self.turns_used,
            "close_reason": self.close_reason,
            "started_at": self.started_at,
            "closed_at": self.closed_at,
        }


@dataclass(frozen=True)
class SocialSceneTurn:
    """scene 內的一次發言。"""

    turn_id: int
    scene_id: int
    channel_id: str
    role: str
    persona_key: str = ""
    content: str = ""
    message_id: str = ""
    reply_to: str = ""
    decision_id: int | None = None
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "scene_id": self.scene_id,
            "channel_id": self.channel_id,
            "role": self.role,
            "persona_key": self.persona_key,
            "content": self.content,
            "message_id": self.message_id,
            "reply_to": self.reply_to,
            "decision_id": self.decision_id,
            "created_at": self.created_at,
        }


def is_within_active_hours(hour: int, start: int, end: int) -> bool:
    """檢查當前小時 (0-23) 是否在允許活躍時段內。

    支援一般區間 (例 9 ~ 23) 與跨日區間 (例 22 ~ 2)。
    若 start == end，視為全天允許 (24小時)。
    """
    h = int(hour) % 24
    s = int(start) % 24
    e = int(end) % 24
    if s == e:
        return True
    if s < e:
        return s <= h < e
    else:
        return h >= s or h < e


DEFAULT_FOLLOWUP_INTERVALS: list[int] = [1, 3, 5, 7, 10]
_MAX_FOLLOWUP_INTERVALS = 8


def normalize_followup_intervals(values: Any) -> list[int]:
    """把階梯輸入正規化為遞增、去重、有上限的正整數清單。

    非數字項目會被忽略；超出 1~120 分鐘的值會被夾住；最多保留 8 階，
    避免使用者貼上超長清單造成無限巡檢。
    """
    if not isinstance(values, (list, tuple)):
        return list(DEFAULT_FOLLOWUP_INTERVALS)
    cleaned: set[int] = set()
    for raw in values:
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            continue
        cleaned.add(max(1, min(120, minutes)))
    ordered = sorted(cleaned)[:_MAX_FOLLOWUP_INTERVALS]
    return ordered or list(DEFAULT_FOLLOWUP_INTERVALS)


def rollout_bucket(guild_id: int | str, user_id: int | str) -> int:
    """穩定灰度分桶：同一 (guild, user) 永遠落在同一桶（0–99）。"""
    digest = hashlib.sha256(f"{guild_id}:{user_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100



def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


class CareRepository:
    """``care.db`` 的資料存取物件（每次操作使用獨立連線）。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(settings.CARE_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return get_db_connection(self.db_path)

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS care_preferences (
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL DEFAULT '',
                    opt_in INTEGER NOT NULL DEFAULT 0,
                    frequency TEXT NOT NULL DEFAULT 'weekly',
                    opted_in_at REAL,
                    opted_out_at REAL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, guild_id)
                );

                CREATE TABLE IF NOT EXISTS care_budget (
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    window_start REAL NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    limit_value INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, scope_id, period)
                );

                CREATE TABLE IF NOT EXISTS care_actions (
                    action_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL DEFAULT '',
                    channel_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    source_ref TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_care_actions_user
                    ON care_actions(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS care_topics (
                    topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL DEFAULT '',
                    channel_id TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'nightly',
                    source_ref TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    consumed_at REAL,
                    UNIQUE(user_id, guild_id, topic, source)
                );
                CREATE INDEX IF NOT EXISTS idx_care_topics_pending
                    ON care_topics(consumed_at, created_at);

                CREATE TABLE IF NOT EXISTS care_guilds (
                    guild_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    rollout_percent INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS care_surfer_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    sandbox_channel_id TEXT NOT NULL DEFAULT '0',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    interval_minutes INTEGER NOT NULL DEFAULT 120,
                    tag_mode TEXT NOT NULL DEFAULT 'interest_match',
                    rss_feeds_json TEXT NOT NULL DEFAULT '[]',
                    search_keywords_json TEXT NOT NULL DEFAULT '[]',
                    last_run_at REAL NOT NULL DEFAULT 0.0,
                    updated_at REAL NOT NULL DEFAULT 0.0
                );

                CREATE TABLE IF NOT EXISTS social_rooms (
                    channel_id TEXT PRIMARY KEY,
                    guild_id TEXT NOT NULL DEFAULT '',
                    last_human_at REAL NOT NULL DEFAULT 0,
                    last_bot_at REAL NOT NULL DEFAULT 0,
                    last_npc_at REAL NOT NULL DEFAULT 0,
                    last_speaker_key TEXT NOT NULL DEFAULT '',
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    topic TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS social_mind (
                    channel_id TEXT PRIMARY KEY,
                    mood REAL NOT NULL DEFAULT 0.5,
                    energy REAL NOT NULL DEFAULT 0.5,
                    boredom REAL NOT NULL DEFAULT 0.0,
                    last_pulse_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS social_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL DEFAULT '',
                    scene_id INTEGER,
                    mode TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    trigger_ref TEXT NOT NULL DEFAULT '',
                    persona_key TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    gate_json TEXT NOT NULL DEFAULT '{}',
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    model TEXT NOT NULL DEFAULT '',
                    tokens INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_social_decisions_channel
                    ON social_decisions(channel_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS social_channel_modes (
                    channel_id TEXT PRIMARY KEY,
                    guild_id TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'SOLO',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    allow_webhooks INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS social_personas (
                    persona_key TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL DEFAULT '',
                    soul_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    avatar_path TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'npc',
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    webhook_id TEXT NOT NULL DEFAULT '',
                    speech_style TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS social_scenes (
                    scene_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    max_turns INTEGER NOT NULL DEFAULT 12,
                    turns_used INTEGER NOT NULL DEFAULT 0,
                    close_reason TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    closed_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_social_scenes_one_open
                    ON social_scenes(channel_id) WHERE status = 'open';

                CREATE TABLE IF NOT EXISTS social_scene_turns (
                    turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scene_id INTEGER NOT NULL,
                    channel_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    persona_key TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT '',
                    reply_to TEXT NOT NULL DEFAULT '',
                    decision_id INTEGER,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scene_turns_scene
                    ON social_scene_turns(scene_id, turn_id);
                """
            )
            # 確保 id=1 預設設定存在
            conn.execute(
                """
                INSERT OR IGNORE INTO care_surfer_config (
                    id, sandbox_channel_id, enabled, interval_minutes, tag_mode,
                    rss_feeds_json, search_keywords_json, last_run_at, updated_at
                ) VALUES (
                    1, ?, ?, ?, 'interest_match', ?, ?, 0.0, ?
                )
                """,
                (
                    str(getattr(settings, "CARE_SURFER_CHANNEL_ID", 0)),
                    1 if getattr(settings, "CARE_SURFER_ENABLED", False) else 0,
                    int(getattr(settings, "CARE_SURFER_INTERVAL_MINUTES", 120)),
                    json.dumps(DEFAULT_SURFER_RSS_FEEDS, ensure_ascii=False),
                    json.dumps(DEFAULT_SURFER_KEYWORDS, ensure_ascii=False),
                    time.time(),
                ),
            )
            # 自動遷移：檢測並補齊大肥魚賽博群友設定欄位
            existing_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(care_surfer_config)").fetchall()
            }
            surfer_col_defs = [
                ("behavior_mode", "TEXT NOT NULL DEFAULT 'balanced'"),
                ("chat_reply_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("interject_threshold", "INTEGER NOT NULL DEFAULT 6"),
                ("interject_score_threshold", "INTEGER NOT NULL DEFAULT 7"),
                ("interject_cooldown_seconds", "INTEGER NOT NULL DEFAULT 45"),
                ("active_chat_window_seconds", "INTEGER NOT NULL DEFAULT 180"),
                ("multi_message_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("emoji_reaction_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("include_url_mode", "TEXT NOT NULL DEFAULT 'never'"),
                ("active_hour_start", "INTEGER NOT NULL DEFAULT 9"),
                ("active_hour_end", "INTEGER NOT NULL DEFAULT 23"),
                ("mind_pace", "TEXT NOT NULL DEFAULT 'normal'"),
                ("max_replies_per_hour", "INTEGER NOT NULL DEFAULT 8"),
                ("max_replies_per_day", "INTEGER NOT NULL DEFAULT 60"),
                ("proactive_cooldown_seconds", "INTEGER NOT NULL DEFAULT 120"),
                ("followup_scan_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("followup_scan_intervals_json", "TEXT NOT NULL DEFAULT '[1, 3, 5, 7, 10]'"),
            ]
            for col_name, col_type in surfer_col_defs:
                if col_name not in existing_cols:
                    try:
                        conn.execute(f"ALTER TABLE care_surfer_config ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 偏好 / 同意管理
    # ------------------------------------------------------------------
    def get_preference(self, user_id: int | str, guild_id: int | str = "") -> CarePreference:
        """取得偏好；guild 專屬設定優先，其次全域（``guild_id=''``）。"""
        uid = _as_str(user_id)
        gid = _as_str(guild_id)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM care_preferences WHERE user_id = ? AND guild_id = ?",
                (uid, gid),
            ).fetchone()
            if row is None and gid:
                row = conn.execute(
                    "SELECT * FROM care_preferences WHERE user_id = ? AND guild_id = ''",
                    (uid,),
                ).fetchone()
        finally:
            conn.close()
        if row is None:
            return CarePreference(user_id=uid, guild_id=gid, opt_in=False)
        return CarePreference(
            user_id=str(row["user_id"]),
            guild_id=str(row["guild_id"]),
            opt_in=bool(row["opt_in"]),
            frequency=str(row["frequency"]),
            updated_at=float(row["updated_at"]),
        )

    def is_opted_in(self, user_id: int | str, guild_id: int | str = "") -> bool:
        return self.get_preference(user_id, guild_id).opt_in

    def get_opt_out_user_ids(self) -> set[str]:
        """批量取得所有明確設定為 opt-out (frequency == 'off') 的使用者 ID 集合。
        
        供批次任務或衝浪雷達以 O(1) 內存集合快速過濾，避免逐一查詢產生 N+1 資料庫 I/O。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM care_preferences WHERE frequency = 'off'"
            ).fetchall()
            return {str(r["user_id"]) for r in rows}
        finally:
            conn.close()

    def set_opt_in(
        self,
        user_id: int | str,
        guild_id: int | str = "",
        *,
        opt_in: bool,
        frequency: str | None = None,
        now: float | None = None,
    ) -> CarePreference:
        """寫入 opt-in／opt-out；opt-out 立即生效且不會被 UI 繞過。"""
        uid = _as_str(user_id)
        gid = _as_str(guild_id)
        selected = (frequency or "weekly").strip().lower()
        if selected not in VALID_FREQUENCIES:
            raise ValueError(f"不支援的關懷頻率：{frequency}")
        if selected == "off":
            opt_in = False
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO care_preferences(
                    user_id, guild_id, opt_in, frequency,
                    opted_in_at, opted_out_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET
                    opt_in = excluded.opt_in,
                    frequency = excluded.frequency,
                    opted_in_at = excluded.opted_in_at,
                    opted_out_at = excluded.opted_out_at,
                    updated_at = excluded.updated_at
                """,
                (
                    uid,
                    gid,
                    1 if opt_in else 0,
                    selected,
                    ts if opt_in else None,
                    None if opt_in else ts,
                    ts,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_preference(uid, gid)

    def set_frequency(
        self,
        user_id: int | str,
        guild_id: int | str = "",
        frequency: str = "weekly",
        *,
        now: float | None = None,
    ) -> CarePreference:
        current = self.get_preference(user_id, guild_id)
        return self.set_opt_in(
            user_id,
            guild_id,
            opt_in=(frequency != "off" and current.opt_in),
            frequency=frequency,
            now=now,
        )

    def count_opted_in(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM care_preferences WHERE opt_in = 1"
            ).fetchone()
        finally:
            conn.close()
        return int(row["total"]) if row else 0

    # ------------------------------------------------------------------
    # 灰度（伺服器層）
    # ------------------------------------------------------------------
    def get_guild_rollout(self, guild_id: int | str) -> GuildRollout:
        gid = _as_str(guild_id)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM care_guilds WHERE guild_id = ?", (gid,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return GuildRollout(guild_id=gid)
        return GuildRollout(
            guild_id=str(row["guild_id"]),
            enabled=bool(row["enabled"]),
            rollout_percent=int(row["rollout_percent"]),
            updated_at=float(row["updated_at"]),
        )

    def set_guild_rollout(
        self,
        guild_id: int | str,
        *,
        enabled: bool = True,
        rollout_percent: int = 100,
        now: float | None = None,
    ) -> GuildRollout:
        gid = _as_str(guild_id)
        if not gid:
            raise ValueError("guild_id 不可為空")
        percent = max(0, min(100, int(rollout_percent)))
        if not enabled:
            percent = 0
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO care_guilds(guild_id, enabled, rollout_percent, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    rollout_percent = excluded.rollout_percent,
                    updated_at = excluded.updated_at
                """,
                (gid, 1 if enabled else 0, percent, ts),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_guild_rollout(gid)

    def list_guild_rollouts(self) -> list[GuildRollout]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM care_guilds ORDER BY updated_at DESC, guild_id ASC"
            ).fetchall()
        finally:
            conn.close()
        return [
            GuildRollout(
                guild_id=str(row["guild_id"]),
                enabled=bool(row["enabled"]),
                rollout_percent=int(row["rollout_percent"]),
                updated_at=float(row["updated_at"]),
            )
            for row in rows
        ]

    def is_guild_enabled(
        self, guild_id: int | str, user_id: int | str = 0
    ) -> bool:
        """伺服器是否開啟；私訊（guild_id 空）不受灰度限制。"""
        gid = _as_str(guild_id)
        if not gid:
            return True
        record = self.get_guild_rollout(gid)
        if not record.enabled or record.rollout_percent <= 0:
            return False
        if record.rollout_percent >= 100:
            return True
        return rollout_bucket(gid, user_id) < record.rollout_percent


    # ------------------------------------------------------------------
    # 額度
    # ------------------------------------------------------------------
    def consume_budget(
        self,
        user_id: int | str,
        *,
        weekly_limit: int,
        daily_limit: int,
        now: float | None = None,
    ) -> BudgetResult:
        """原子檢查並扣減「每人每週」與「全域每日」額度。

        任一額度不足時不扣減任何一格；時間窗為滾動窗口（首次使用起算）。
        """
        uid = _as_str(user_id)
        ts = time.time() if now is None else float(now)
        weekly_limit = max(0, int(weekly_limit))
        daily_limit = max(0, int(daily_limit))
        if weekly_limit == 0:
            return BudgetResult(False, "每人每週關懷額度為 0", 0, 0, 0, daily_limit)
        if daily_limit == 0:
            return BudgetResult(False, "全域每日關懷額度為 0", 0, weekly_limit, 0, 0)

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            user_used, user_start = self._window_state(
                conn, "user", uid, "week", WEEK_SECONDS, ts
            )
            global_used, global_start = self._window_state(
                conn, "global", "*", "day", DAY_SECONDS, ts
            )
            if user_used >= weekly_limit:
                conn.rollback()
                return BudgetResult(
                    False,
                    f"已達每人每週關懷上限 ({user_used}/{weekly_limit})",
                    user_used,
                    weekly_limit,
                    global_used,
                    daily_limit,
                )
            if global_used >= daily_limit:
                conn.rollback()
                return BudgetResult(
                    False,
                    f"已達全域每日關懷上限 ({global_used}/{daily_limit})",
                    user_used,
                    weekly_limit,
                    global_used,
                    daily_limit,
                )

            self._increment(conn, "user", uid, "week", user_start, user_used, weekly_limit, ts)
            self._increment(conn, "global", "*", "day", global_start, global_used, daily_limit, ts)
            conn.commit()
            return BudgetResult(
                True,
                "關懷額度核准",
                user_used + 1,
                weekly_limit,
                global_used + 1,
                daily_limit,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _window_state(
        conn: sqlite3.Connection,
        scope: str,
        scope_id: str,
        period: str,
        window_seconds: int,
        now: float,
    ) -> tuple[int, float]:
        row = conn.execute(
            "SELECT used, window_start FROM care_budget "
            "WHERE scope = ? AND scope_id = ? AND period = ?",
            (scope, scope_id, period),
        ).fetchone()
        if row is None or now - float(row["window_start"]) >= window_seconds:
            return 0, now
        return int(row["used"]), float(row["window_start"])

    @staticmethod
    def _increment(
        conn: sqlite3.Connection,
        scope: str,
        scope_id: str,
        period: str,
        window_start: float,
        used: int,
        limit: int,
        now: float,
    ) -> None:
        conn.execute(
            """
            INSERT INTO care_budget(
                scope, scope_id, period, window_start, used, limit_value, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, scope_id, period) DO UPDATE SET
                window_start = excluded.window_start,
                used = excluded.used,
                limit_value = excluded.limit_value,
                updated_at = excluded.updated_at
            """,
            (scope, scope_id, period, window_start, used + 1, limit, now),
        )

    def budget_snapshot(self, *, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM care_budget ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]


    # ------------------------------------------------------------------
    # 稽核
    # ------------------------------------------------------------------
    def record_action(
        self,
        *,
        user_id: int | str,
        guild_id: int | str = "",
        channel_id: int | str = "",
        source: str = "",
        source_ref: str = "",
        topic: str = "",
        status: str,
        reason: str = "",
        now: float | None = None,
    ) -> str:
        action_id = uuid.uuid4().hex
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO care_actions(
                    action_id, user_id, guild_id, channel_id, source,
                    source_ref, topic, status, reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    _as_str(user_id),
                    _as_str(guild_id),
                    _as_str(channel_id),
                    str(source),
                    str(source_ref),
                    str(topic),
                    str(status),
                    str(reason),
                    ts,
                    ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return action_id

    def update_action(
        self,
        action_id: str,
        *,
        status: str,
        reason: str = "",
        now: float | None = None,
    ) -> bool:
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE care_actions SET status = ?, reason = ?, updated_at = ? WHERE action_id = ?",
                (str(status), str(reason), ts, action_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def list_actions(
        self,
        user_id: int | str | None = None,
        *,
        limit: int = 100,
        statuses: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(_as_str(user_id))
        selected = [str(status) for status in (statuses or [])]
        if selected:
            clauses.append("status IN (" + ",".join("?" for _ in selected) + ")")
            params.extend(selected)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM care_actions {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def last_action_at(self, user_id: int | str, *, statuses: tuple[str, ...] = ("approved", "sent")) -> float | None:
        if not statuses:
            return None
        placeholders = ",".join("?" for _ in statuses)
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT MAX(created_at) AS ts FROM care_actions "
                f"WHERE user_id = ? AND status IN ({placeholders})",
                (_as_str(user_id), *statuses),
            ).fetchone()
        finally:
            conn.close()
        return float(row["ts"]) if row and row["ts"] is not None else None

    @staticmethod
    def _feedback_from_row(row: sqlite3.Row | None) -> CareFeedbackStats:
        if row is None:
            return CareFeedbackStats()
        return CareFeedbackStats(
            approved=int(row["approved"] or 0),
            rejected=int(row["rejected"] or 0),
            responded=int(row["responded"] or 0),
            silent=int(row["silent"] or 0),
            pending=int(row["pending"] or 0),
        )

    @staticmethod
    def _feedback_clauses(
        *,
        user_id: int | str | None = None,
        guild_id: int | str | None = None,
        since: float | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(_as_str(user_id))
        if guild_id is not None:
            clauses.append("guild_id = ?")
            params.append(_as_str(guild_id))
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(float(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    _FEEDBACK_COLUMNS = """
        SUM(CASE WHEN status IN ('approved','sent','responded','silent') THEN 1 ELSE 0 END) AS approved,
        SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
        SUM(CASE WHEN status = 'responded' THEN 1 ELSE 0 END) AS responded,
        SUM(CASE WHEN status = 'silent' THEN 1 ELSE 0 END) AS silent,
        SUM(CASE WHEN status IN ('approved','sent') THEN 1 ELSE 0 END) AS pending
    """

    def feedback_stats(
        self,
        *,
        user_id: int | str | None = None,
        guild_id: int | str | None = None,
        since: float | None = None,
    ) -> CareFeedbackStats:
        """回饋統計；沉默率分母只計已結案（responded＋silent）。"""
        where, params = self._feedback_clauses(
            user_id=user_id, guild_id=guild_id, since=since
        )
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT {self._FEEDBACK_COLUMNS} FROM care_actions {where}",
                params,
            ).fetchone()
        finally:
            conn.close()
        return self._feedback_from_row(row)

    def feedback_by_guild(self, *, since: float | None = None) -> list[dict[str, Any]]:
        where, params = self._feedback_clauses(since=since)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT guild_id, {self._FEEDBACK_COLUMNS} FROM care_actions "
                f"{where} GROUP BY guild_id ORDER BY guild_id ASC",
                params,
            ).fetchall()
        finally:
            conn.close()
        report: list[dict[str, Any]] = []
        for row in rows:
            stats = self._feedback_from_row(row)
            report.append({"guild_id": str(row["guild_id"]), **stats.to_dict()})
        return report

    def settle_stale_actions(
        self,
        *,
        before: float,
        now: float | None = None,
        reason: str = "超過回饋期限未回應，標記為沉默",
    ) -> int:
        """把逾時仍未回應的核准行動標記為 silent（回饋循環用）。"""
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE care_actions SET status = 'silent', reason = ?, updated_at = ? "
                "WHERE status IN ('approved','sent') AND created_at <= ?",
                (str(reason), ts, float(before)),
            )
            conn.commit()
            return int(cursor.rowcount)
        finally:
            conn.close()


    # ------------------------------------------------------------------
    # 深夜整合話題佇列
    # ------------------------------------------------------------------
    def add_topic(
        self,
        *,
        user_id: int | str,
        topic: str,
        guild_id: int | str = "",
        channel_id: int | str = "",
        source: str = "nightly",
        source_ref: str = "",
        score: float = 0.0,
        now: float | None = None,
    ) -> int | None:
        text = str(topic or "").strip()
        if not text:
            return None
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO care_topics(
                    user_id, guild_id, channel_id, topic, source, source_ref, score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, topic, source) DO NOTHING
                """,
                (
                    _as_str(user_id),
                    _as_str(guild_id),
                    _as_str(channel_id),
                    text,
                    str(source),
                    str(source_ref),
                    float(score),
                    ts,
                ),
            )
            conn.commit()
            if cursor.rowcount and cursor.lastrowid is not None:
                return int(cursor.lastrowid)
            return None
        finally:
            conn.close()

    def list_pending_topics(self, *, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM care_topics
                WHERE consumed_at IS NULL
                ORDER BY score DESC, created_at ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def count_pending_topics(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM care_topics WHERE consumed_at IS NULL"
            ).fetchone()
        finally:
            conn.close()
        return int(row["total"]) if row else 0


    def mark_topic_consumed(self, topic_id: int, *, now: float | None = None) -> bool:
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE care_topics SET consumed_at = ? WHERE topic_id = ? AND consumed_at IS NULL",
                (ts, int(topic_id)),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    consume_topic = mark_topic_consumed

    # ------------------------------------------------------------------
    # 隱私
    # ------------------------------------------------------------------
    def forget_user(self, user_id: int | str) -> int:
        """刪除該使用者的偏好、稽核與待回訪話題（供 /forget 擴充）。"""
        uid = _as_str(user_id)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            removed = 0
            for table in ("care_preferences", "care_topics", "care_actions"):
                cursor = conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
                removed += cursor.rowcount
            conn.execute(
                "DELETE FROM care_budget WHERE scope = 'user' AND scope_id = ?", (uid,)
            )
            conn.commit()
            return removed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 網活仔衝浪設定 (CareSurferConfig)
    # ------------------------------------------------------------------
    def get_surfer_config(self) -> CareSurferConfig:
        """讀取網活仔衝浪設定。若尚無設定則初始化並回傳預設值。"""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM care_surfer_config WHERE id = 1").fetchone()
            if row is None:
                return CareSurferConfig()
            try:
                rss_feeds = json.loads(row["rss_feeds_json"])
            except Exception:
                rss_feeds = list(DEFAULT_SURFER_RSS_FEEDS)
            try:
                search_keywords = json.loads(row["search_keywords_json"])
            except Exception:
                search_keywords = list(DEFAULT_SURFER_KEYWORDS)

            def _get_val(key: str, default: Any) -> Any:
                try:
                    return row[key] if key in row.keys() and row[key] is not None else default
                except Exception:
                    return default

            raw_cid = str(row["sandbox_channel_id"] or "").strip()
            if raw_cid in ("0", ""):
                env_cid = str(getattr(settings, "CARE_SURFER_CHANNEL_ID", 0)).strip()
                if env_cid not in ("0", ""):
                    raw_cid = env_cid

            # 階梯設定可能是 JSON 字串或舊版直接存 list；壞資料一律回退預設。
            raw_intervals = _get_val("followup_scan_intervals_json", None)
            if raw_intervals is None:
                parsed_intervals: Any = DEFAULT_FOLLOWUP_INTERVALS
            elif isinstance(raw_intervals, str):
                try:
                    parsed_intervals = json.loads(raw_intervals)
                except (TypeError, ValueError):
                    parsed_intervals = DEFAULT_FOLLOWUP_INTERVALS
            else:
                parsed_intervals = raw_intervals

            return CareSurferConfig(
                sandbox_channel_id=raw_cid,
                enabled=bool(row["enabled"]),
                interval_minutes=int(row["interval_minutes"]),
                tag_mode=str(row["tag_mode"]),
                rss_feeds=rss_feeds if isinstance(rss_feeds, list) else list(DEFAULT_SURFER_RSS_FEEDS),
                search_keywords=search_keywords if isinstance(search_keywords, list) else list(DEFAULT_SURFER_KEYWORDS),
                last_run_at=float(row["last_run_at"]),
                updated_at=float(row["updated_at"]),
                behavior_mode=str(_get_val("behavior_mode", "balanced")),
                chat_reply_enabled=bool(_get_val("chat_reply_enabled", 1)),
                interject_threshold=max(1, int(_get_val("interject_threshold", 6))),
                interject_score_threshold=max(1, min(10, int(_get_val("interject_score_threshold", 7)))),
                interject_cooldown_seconds=max(1, int(_get_val("interject_cooldown_seconds", 45))),
                active_chat_window_seconds=max(0, int(_get_val("active_chat_window_seconds", 180))),
                multi_message_enabled=bool(_get_val("multi_message_enabled", 1)),
                emoji_reaction_enabled=bool(_get_val("emoji_reaction_enabled", 1)),
                include_url_mode=str(_get_val("include_url_mode", "never")),
                active_hour_start=max(0, min(23, int(_get_val("active_hour_start", 9)))),
                active_hour_end=max(0, min(23, int(_get_val("active_hour_end", 23)))),
                mind_pace=str(_get_val("mind_pace", "normal")),
                max_replies_per_hour=max(0, int(_get_val("max_replies_per_hour", 8))),
                max_replies_per_day=max(0, int(_get_val("max_replies_per_day", 60))),
                proactive_cooldown_seconds=max(0, int(_get_val("proactive_cooldown_seconds", 120))),
                followup_scan_enabled=bool(_get_val("followup_scan_enabled", 1)),
                followup_scan_intervals=normalize_followup_intervals(parsed_intervals),
            )
        finally:
            conn.close()

    def update_surfer_config(
        self,
        *,
        sandbox_channel_id: str | int | None = None,
        enabled: bool | None = None,
        interval_minutes: int | None = None,
        tag_mode: str | None = None,
        rss_feeds: list[dict[str, str]] | None = None,
        search_keywords: list[str] | None = None,
        behavior_mode: str | None = None,
        chat_reply_enabled: bool | None = None,
        interject_threshold: int | None = None,
        interject_score_threshold: int | None = None,
        interject_cooldown_seconds: int | None = None,
        active_chat_window_seconds: int | None = None,
        multi_message_enabled: bool | None = None,
        emoji_reaction_enabled: bool | None = None,
        include_url_mode: str | None = None,
        active_hour_start: int | None = None,
        active_hour_end: int | None = None,
        mind_pace: str | None = None,
        max_replies_per_hour: int | None = None,
        max_replies_per_day: int | None = None,
        proactive_cooldown_seconds: int | None = None,
        followup_scan_enabled: bool | None = None,
        followup_scan_intervals: list[int] | None = None,
        now: float | None = None,
    ) -> CareSurferConfig:
        """更新網活仔衝浪與賽博群友設定。"""
        current = self.get_surfer_config()
        new_channel = str(sandbox_channel_id) if sandbox_channel_id is not None else current.sandbox_channel_id
        new_enabled = bool(enabled) if enabled is not None else current.enabled
        new_interval = max(5, int(interval_minutes)) if interval_minutes is not None else current.interval_minutes
        new_tag_mode = str(tag_mode) if tag_mode is not None else current.tag_mode
        new_feeds = rss_feeds if rss_feeds is not None else current.rss_feeds
        new_keywords = search_keywords if search_keywords is not None else current.search_keywords
        new_behavior_mode = str(behavior_mode) if behavior_mode is not None else current.behavior_mode
        new_chat_reply = bool(chat_reply_enabled) if chat_reply_enabled is not None else current.chat_reply_enabled
        new_interject_th = max(1, int(interject_threshold)) if interject_threshold is not None else current.interject_threshold
        new_interject_score = max(1, min(10, int(interject_score_threshold))) if interject_score_threshold is not None else current.interject_score_threshold
        new_cooldown = max(1, int(interject_cooldown_seconds)) if interject_cooldown_seconds is not None else current.interject_cooldown_seconds
        new_active_window = max(0, int(active_chat_window_seconds)) if active_chat_window_seconds is not None else current.active_chat_window_seconds
        new_multi_msg = bool(multi_message_enabled) if multi_message_enabled is not None else current.multi_message_enabled
        new_emoji = bool(emoji_reaction_enabled) if emoji_reaction_enabled is not None else current.emoji_reaction_enabled
        new_include_url = str(include_url_mode) if include_url_mode is not None else current.include_url_mode
        new_active_start = max(0, min(23, int(active_hour_start))) if active_hour_start is not None else current.active_hour_start
        new_active_end = max(0, min(23, int(active_hour_end))) if active_hour_end is not None else current.active_hour_end
        new_mind_pace = str(mind_pace) if mind_pace is not None else current.mind_pace
        new_max_hourly = max(0, int(max_replies_per_hour)) if max_replies_per_hour is not None else current.max_replies_per_hour
        new_max_daily = max(0, int(max_replies_per_day)) if max_replies_per_day is not None else current.max_replies_per_day
        new_proactive_cd = (
            max(0, int(proactive_cooldown_seconds))
            if proactive_cooldown_seconds is not None
            else current.proactive_cooldown_seconds
        )
        new_followup_enabled = bool(followup_scan_enabled) if followup_scan_enabled is not None else current.followup_scan_enabled
        new_followup_intervals = normalize_followup_intervals(
            followup_scan_intervals
            if followup_scan_intervals is not None
            else list(current.followup_scan_intervals)
        )
        ts = time.time() if now is None else float(now)

        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE care_surfer_config SET
                    sandbox_channel_id = ?,
                    enabled = ?,
                    interval_minutes = ?,
                    tag_mode = ?,
                    rss_feeds_json = ?,
                    search_keywords_json = ?,
                    behavior_mode = ?,
                    chat_reply_enabled = ?,
                    interject_threshold = ?,
                    interject_score_threshold = ?,
                    interject_cooldown_seconds = ?,
                    active_chat_window_seconds = ?,
                    multi_message_enabled = ?,
                    emoji_reaction_enabled = ?,
                    include_url_mode = ?,
                    active_hour_start = ?,
                    active_hour_end = ?,
                    mind_pace = ?,
                    max_replies_per_hour = ?,
                    max_replies_per_day = ?,
                    proactive_cooldown_seconds = ?,
                    followup_scan_enabled = ?,
                    followup_scan_intervals_json = ?,
                    updated_at = ?
                WHERE id = 1
                """,
                (
                    new_channel,
                    1 if new_enabled else 0,
                    new_interval,
                    new_tag_mode,
                    json.dumps(new_feeds, ensure_ascii=False),
                    json.dumps(new_keywords, ensure_ascii=False),
                    new_behavior_mode,
                    1 if new_chat_reply else 0,
                    new_interject_th,
                    new_interject_score,
                    new_cooldown,
                    new_active_window,
                    1 if new_multi_msg else 0,
                    1 if new_emoji else 0,
                    new_include_url,
                    new_active_start,
                    new_active_end,
                    new_mind_pace,
                    new_max_hourly,
                    new_max_daily,
                    new_proactive_cd,
                    1 if new_followup_enabled else 0,
                    json.dumps(new_followup_intervals),
                    ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_surfer_config()

    # ------------------------------------------------------------------
    # 賽博群友：房間狀態／心智／決策帳本／頻道模式／roster／scene
    # ------------------------------------------------------------------
    def get_room(self, channel_id: int | str) -> SocialRoom:
        """讀取頻道房間狀態；沒有資料列時回傳全空預設值。"""
        cid = _as_str(channel_id)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM social_rooms WHERE channel_id = ?", (cid,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return SocialRoom(channel_id=cid)
        return SocialRoom(
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            last_human_at=float(row["last_human_at"]),
            last_bot_at=float(row["last_bot_at"]),
            last_npc_at=float(row["last_npc_at"]),
            last_speaker_key=row["last_speaker_key"],
            cooldown_until=float(row["cooldown_until"]),
            topic=row["topic"],
            updated_at=float(row["updated_at"]),
        )

    def upsert_room(
        self,
        channel_id: int | str,
        *,
        guild_id: str | int | None = None,
        last_human_at: float | None = None,
        last_bot_at: float | None = None,
        last_npc_at: float | None = None,
        last_speaker_key: str | None = None,
        cooldown_until: float | None = None,
        topic: str | None = None,
        now: float | None = None,
    ) -> SocialRoom:
        """更新房間狀態（僅覆寫有傳入的欄位）。"""
        current = self.get_room(channel_id)
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO social_rooms (
                    channel_id, guild_id, last_human_at, last_bot_at, last_npc_at,
                    last_speaker_key, cooldown_until, topic, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    last_human_at = excluded.last_human_at,
                    last_bot_at = excluded.last_bot_at,
                    last_npc_at = excluded.last_npc_at,
                    last_speaker_key = excluded.last_speaker_key,
                    cooldown_until = excluded.cooldown_until,
                    topic = excluded.topic,
                    updated_at = excluded.updated_at
                """,
                (
                    current.channel_id,
                    _as_str(guild_id) if guild_id is not None else current.guild_id,
                    float(last_human_at) if last_human_at is not None else current.last_human_at,
                    float(last_bot_at) if last_bot_at is not None else current.last_bot_at,
                    float(last_npc_at) if last_npc_at is not None else current.last_npc_at,
                    str(last_speaker_key) if last_speaker_key is not None else current.last_speaker_key,
                    float(cooldown_until) if cooldown_until is not None else current.cooldown_until,
                    str(topic) if topic is not None else current.topic,
                    ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_room(channel_id)

    def touch_room(
        self,
        channel_id: int | str,
        *,
        role: str,
        persona_key: str = "",
        guild_id: str | int | None = None,
        now: float | None = None,
    ) -> SocialRoom:
        """記錄一次發言（human / bot / npc），供冷場與輪流規則使用。"""
        ts = time.time() if now is None else float(now)
        if role == "human":
            return self.upsert_room(
                channel_id,
                guild_id=guild_id,
                last_human_at=ts,
                last_speaker_key=persona_key or "human",
                now=ts,
            )
        if role == "npc":
            return self.upsert_room(
                channel_id,
                guild_id=guild_id,
                last_npc_at=ts,
                last_speaker_key=persona_key,
                now=ts,
            )
        return self.upsert_room(
            channel_id,
            guild_id=guild_id,
            last_bot_at=ts,
            last_speaker_key=persona_key or "bot",
            now=ts,
        )

    def get_social_mind(self, channel_id: int | str) -> SocialMind:
        """讀取頻道心智狀態；沒有資料列時回傳中性預設值。"""
        cid = _as_str(channel_id)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM social_mind WHERE channel_id = ?", (cid,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return SocialMind(channel_id=cid)
        return SocialMind(
            channel_id=row["channel_id"],
            mood=float(row["mood"]),
            energy=float(row["energy"]),
            boredom=float(row["boredom"]),
            last_pulse_at=float(row["last_pulse_at"]),
            updated_at=float(row["updated_at"]),
        )

    def upsert_social_mind(
        self,
        channel_id: int | str,
        *,
        mood: float | None = None,
        energy: float | None = None,
        boredom: float | None = None,
        last_pulse_at: float | None = None,
        now: float | None = None,
    ) -> SocialMind:
        """更新頻道心智狀態（數值一律夾在 0~1）。"""
        current = self.get_social_mind(channel_id)
        ts = time.time() if now is None else float(now)

        def _clamp(value: float | None, fallback: float) -> float:
            raw = fallback if value is None else float(value)
            return round(max(0.0, min(1.0, raw)), 4)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO social_mind (
                    channel_id, mood, energy, boredom, last_pulse_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    mood = excluded.mood,
                    energy = excluded.energy,
                    boredom = excluded.boredom,
                    last_pulse_at = excluded.last_pulse_at,
                    updated_at = excluded.updated_at
                """,
                (
                    current.channel_id,
                    _clamp(mood, current.mood),
                    _clamp(energy, current.energy),
                    _clamp(boredom, current.boredom),
                    float(last_pulse_at) if last_pulse_at is not None else current.last_pulse_at,
                    ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_social_mind(channel_id)

    def record_decision(
        self,
        *,
        channel_id: int | str,
        mode: str,
        trigger_type: str,
        action: str,
        guild_id: str | int = "",
        scene_id: int | None = None,
        trigger_ref: str = "",
        persona_key: str = "",
        score: float = 0.0,
        reasons: Iterable[str] | None = None,
        gate: dict[str, Any] | None = None,
        latency_ms: int = 0,
        model: str = "",
        tokens: int = 0,
        now: float | None = None,
    ) -> int:
        """寫入一筆決策（包含 SILENT），回傳 decision_id。"""
        ts = time.time() if now is None else float(now)
        reason_list = [str(r) for r in (reasons or []) if str(r)]
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO social_decisions (
                    channel_id, guild_id, scene_id, mode, trigger_type, trigger_ref,
                    persona_key, action, score, reasons_json, gate_json,
                    latency_ms, model, tokens, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _as_str(channel_id),
                    _as_str(guild_id),
                    scene_id,
                    str(mode),
                    str(trigger_type),
                    str(trigger_ref),
                    str(persona_key),
                    str(action),
                    float(score),
                    json.dumps(reason_list, ensure_ascii=False),
                    json.dumps(gate or {}, ensure_ascii=False),
                    int(max(0, latency_ms)),
                    str(model),
                    int(max(0, tokens)),
                    ts,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    def count_speaks(self, channel_id: int | str, *, since: float) -> int:
        """計算某頻道在指定時間之後的實際發言次數（給發言額度用）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM social_decisions
                WHERE channel_id = ? AND action = 'SPEAK' AND created_at >= ?
                """,
                (_as_str(channel_id), float(since)),
            ).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def list_decisions(
        self,
        *,
        channel_id: int | str | None = None,
        action: str | None = None,
        trigger_type: str | None = None,
        limit: int = 50,
        since: float | None = None,
    ) -> list[dict[str, Any]]:
        """列出決策帳本（新到舊）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(_as_str(channel_id))
        if action:
            clauses.append("action = ?")
            params.append(str(action))
        if trigger_type:
            clauses.append("trigger_type = ?")
            params.append(str(trigger_type))
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(float(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT * FROM social_decisions "
            f"{where} ORDER BY decision_id DESC LIMIT ?"
        )
        params.append(max(1, min(500, int(limit))))
        conn = self._connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["reasons"] = json.loads(item.pop("reasons_json") or "[]")
            except Exception:
                item["reasons"] = []
            try:
                item["gate"] = json.loads(item.pop("gate_json") or "{}")
            except Exception:
                item["gate"] = {}
            result.append(item)
        return result

    def decision_stats(
        self,
        *,
        channel_id: int | str | None = None,
        hours: float = 24.0,
        now: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """統計各 action 的次數與平均分數（預設最近 24 小時）。"""
        ts = time.time() if now is None else float(now)
        since = ts - max(0.0, float(hours)) * 3600
        clauses = ["created_at >= ?"]
        params: list[Any] = [since]
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(_as_str(channel_id))
        sql = (
            "SELECT action, COUNT(*) AS cnt, AVG(score) AS avg_score "
            f"FROM social_decisions WHERE {' AND '.join(clauses)} "
            "GROUP BY action"
        )
        conn = self._connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
        return {
            str(row["action"]): {
                "count": int(row["cnt"]),
                "avg_score": round(float(row["avg_score"] or 0.0), 2),
            }
            for row in rows
        }

    def get_channel_mode(self, channel_id: int | str) -> SocialChannelMode | None:
        """讀取頻道模式設定；None 代表正式群預設 SOLO。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM social_channel_modes WHERE channel_id = ?",
                (_as_str(channel_id),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return SocialChannelMode(
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            mode=row["mode"],
            enabled=bool(row["enabled"]),
            allow_webhooks=bool(row["allow_webhooks"]),
            note=row["note"],
            updated_at=float(row["updated_at"]),
        )

    def list_channel_modes(self) -> list[SocialChannelMode]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM social_channel_modes ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return [
            SocialChannelMode(
                channel_id=row["channel_id"],
                guild_id=row["guild_id"],
                mode=row["mode"],
                enabled=bool(row["enabled"]),
                allow_webhooks=bool(row["allow_webhooks"]),
                note=row["note"],
                updated_at=float(row["updated_at"]),
            )
            for row in rows
        ]

    def upsert_channel_mode(
        self,
        channel_id: int | str,
        *,
        mode: str = "SOLO",
        enabled: bool = False,
        allow_webhooks: bool = False,
        guild_id: str | int = "",
        note: str = "",
        now: float | None = None,
    ) -> SocialChannelMode:
        """設定頻道模式；mode 只接受 SOLO / SANDBOX_GROUP。"""
        normalized = str(mode).upper()
        if normalized not in ("SOLO", "SANDBOX_GROUP"):
            raise ValueError(f"不支援的頻道模式: {mode}")
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO social_channel_modes (
                    channel_id, guild_id, mode, enabled, allow_webhooks, note, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    mode = excluded.mode,
                    enabled = excluded.enabled,
                    allow_webhooks = excluded.allow_webhooks,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    _as_str(channel_id),
                    _as_str(guild_id),
                    normalized,
                    1 if enabled else 0,
                    1 if allow_webhooks else 0,
                    str(note),
                    ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        record = self.get_channel_mode(channel_id)
        assert record is not None
        return record

    def get_persona(self, persona_key: str) -> SocialPersonaRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM social_personas WHERE persona_key = ?",
                (str(persona_key),),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_persona(row) if row is not None else None

    @staticmethod
    def _row_to_persona(row: Any) -> SocialPersonaRecord:
        return SocialPersonaRecord(
            persona_key=row["persona_key"],
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            soul_name=row["soul_name"],
            display_name=row["display_name"],
            avatar_path=row["avatar_path"],
            role=row["role"],
            is_primary=bool(row["is_primary"]),
            enabled=bool(row["enabled"]),
            webhook_id=row["webhook_id"],
            speech_style=row["speech_style"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def list_personas(
        self, channel_id: int | str | None = None, *, include_disabled: bool = True
    ) -> list[SocialPersonaRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(_as_str(channel_id))
        if not include_disabled:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM social_personas {where} "
                "ORDER BY is_primary DESC, persona_key ASC",
                tuple(params),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_persona(row) for row in rows]

    def upsert_persona(
        self,
        *,
        persona_key: str,
        channel_id: int | str,
        soul_name: str,
        display_name: str,
        guild_id: str | int = "",
        avatar_path: str = "",
        role: str = "npc",
        is_primary: bool = False,
        enabled: bool = True,
        webhook_id: str = "",
        speech_style: str = "",
        now: float | None = None,
    ) -> SocialPersonaRecord:
        """新增或更新 persona。primary persona 一律強制 enabled。"""
        key = str(persona_key).strip().lower()
        if not key:
            raise ValueError("persona_key 不可為空")
        ts = time.time() if now is None else float(now)
        existing = self.get_persona(key)
        created_at = existing.created_at if existing else ts
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO social_personas (
                    persona_key, channel_id, guild_id, soul_name, display_name,
                    avatar_path, role, is_primary, enabled, webhook_id,
                    speech_style, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(persona_key) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    guild_id = excluded.guild_id,
                    soul_name = excluded.soul_name,
                    display_name = excluded.display_name,
                    avatar_path = excluded.avatar_path,
                    role = excluded.role,
                    is_primary = excluded.is_primary,
                    enabled = excluded.enabled,
                    webhook_id = excluded.webhook_id,
                    speech_style = excluded.speech_style,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    _as_str(channel_id),
                    _as_str(guild_id),
                    str(soul_name),
                    str(display_name),
                    str(avatar_path),
                    str(role),
                    1 if is_primary else 0,
                    1 if (enabled or is_primary) else 0,
                    str(webhook_id),
                    str(speech_style),
                    created_at,
                    ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        record = self.get_persona(key)
        assert record is not None
        return record

    def set_persona_enabled(self, persona_key: str, enabled: bool) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE social_personas SET enabled = ?, updated_at = ? "
                "WHERE persona_key = ? AND is_primary = 0",
                (1 if enabled else 0, time.time(), str(persona_key)),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def set_persona_webhook(self, persona_key: str, webhook_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE social_personas SET webhook_id = ?, updated_at = ? WHERE persona_key = ?",
                (str(webhook_id), time.time(), str(persona_key)),
            )
            conn.commit()
        finally:
            conn.close()

    def open_scene(
        self,
        channel_id: int | str,
        *,
        guild_id: str | int = "",
        topic: str = "",
        max_turns: int = 12,
        now: float | None = None,
    ) -> SocialSceneRecord:
        """開啟 scene；同頻道已有 open scene 時直接回傳既有那一筆（I3）。"""
        cid = _as_str(channel_id)
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM social_scenes WHERE channel_id = ? AND status = 'open' "
                "ORDER BY scene_id DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if row is not None:
                conn.commit()
                return self._row_to_scene(row)

            cursor = conn.execute(
                """
                INSERT INTO social_scenes (
                    channel_id, guild_id, topic, status, max_turns, turns_used,
                    close_reason, started_at
                ) VALUES (?, ?, ?, 'open', ?, 0, '', ?)
                """,
                (cid, _as_str(guild_id), str(topic), max(1, int(max_turns)), ts),
            )
            scene_id = int(cursor.lastrowid or 0)
            conn.commit()
        finally:
            conn.close()
        record = self.get_scene(scene_id)
        assert record is not None
        return record

    @staticmethod
    def _row_to_scene(row: Any) -> SocialSceneRecord:
        return SocialSceneRecord(
            scene_id=int(row["scene_id"]),
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            topic=row["topic"],
            status=row["status"],
            max_turns=int(row["max_turns"]),
            turns_used=int(row["turns_used"]),
            close_reason=row["close_reason"],
            started_at=float(row["started_at"]),
            closed_at=float(row["closed_at"]) if row["closed_at"] is not None else None,
        )

    def get_scene(self, scene_id: int) -> SocialSceneRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM social_scenes WHERE scene_id = ?", (int(scene_id),)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_scene(row) if row is not None else None

    def get_open_scene(self, channel_id: int | str) -> SocialSceneRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM social_scenes WHERE channel_id = ? AND status = 'open' "
                "ORDER BY scene_id DESC LIMIT 1",
                (_as_str(channel_id),),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_scene(row) if row is not None else None

    def list_scenes(
        self, channel_id: int | str | None = None, *, status: str | None = None, limit: int = 20
    ) -> list[SocialSceneRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(_as_str(channel_id))
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM social_scenes {where} ORDER BY scene_id DESC LIMIT ?",
                tuple(params) + (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_scene(row) for row in rows]

    def close_scene(
        self,
        scene_id: int,
        *,
        reason: str = "",
        status: str = "closed",
        now: float | None = None,
    ) -> bool:
        """關閉 scene；status 只接受 closed / killed。"""
        normalized = str(status)
        if normalized not in ("closed", "killed"):
            raise ValueError(f"不支援的 scene 結束狀態: {status}")
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE social_scenes SET status = ?, close_reason = ?, closed_at = ? "
                "WHERE scene_id = ? AND status = 'open'",
                (normalized, str(reason), ts, int(scene_id)),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def add_scene_turn(
        self,
        *,
        scene_id: int,
        channel_id: int | str,
        role: str,
        persona_key: str = "",
        content: str = "",
        message_id: str = "",
        reply_to: str = "",
        decision_id: int | None = None,
        now: float | None = None,
    ) -> int:
        """新增一個 turn 並累加 scene.turns_used（同一交易）。"""
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO social_scene_turns (
                    scene_id, channel_id, role, persona_key, content,
                    message_id, reply_to, decision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(scene_id),
                    _as_str(channel_id),
                    str(role),
                    str(persona_key),
                    str(content),
                    str(message_id),
                    str(reply_to),
                    decision_id,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE social_scenes SET turns_used = turns_used + 1 WHERE scene_id = ?",
                (int(scene_id),),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    def list_scene_turns(self, scene_id: int, *, limit: int = 50) -> list[SocialSceneTurn]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM social_scene_turns WHERE scene_id = ? "
                "ORDER BY turn_id ASC LIMIT ?",
                (int(scene_id), max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
        return [
            SocialSceneTurn(
                turn_id=int(row["turn_id"]),
                scene_id=int(row["scene_id"]),
                channel_id=row["channel_id"],
                role=row["role"],
                persona_key=row["persona_key"],
                content=row["content"],
                message_id=row["message_id"],
                reply_to=row["reply_to"],
                decision_id=int(row["decision_id"]) if row["decision_id"] is not None else None,
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    def count_turns_since(
        self,
        channel_id: int | str,
        *,
        since: float,
        role: str | None = None,
        persona_key: str | None = None,
    ) -> int:
        """統計時間窗內的 turn 數（用於每小時／每日上限）。"""
        clauses = ["channel_id = ?", "created_at >= ?"]
        params: list[Any] = [_as_str(channel_id), float(since)]
        if role:
            clauses.append("role = ?")
            params.append(str(role))
        if persona_key:
            clauses.append("persona_key = ?")
            params.append(str(persona_key))
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM social_scene_turns WHERE {' AND '.join(clauses)}",
                tuple(params),
            ).fetchone()
        finally:
            conn.close()
        return int(row["cnt"]) if row is not None else 0

    def last_turn(self, scene_id: int) -> SocialSceneTurn | None:
        """取得 scene 最後一個 turn（判斷連續發言者）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM social_scene_turns WHERE scene_id = ? "
                "ORDER BY turn_id DESC LIMIT 1",
                (int(scene_id),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return SocialSceneTurn(
            turn_id=int(row["turn_id"]),
            scene_id=int(row["scene_id"]),
            channel_id=row["channel_id"],
            role=row["role"],
            persona_key=row["persona_key"],
            content=row["content"],
            message_id=row["message_id"],
            reply_to=row["reply_to"],
            decision_id=int(row["decision_id"]) if row["decision_id"] is not None else None,
            created_at=float(row["created_at"]),
        )

    def kill_channel_sandbox(self, channel_id: int | str) -> dict[str, int]:
        """L2 kill switch：降回 SOLO、關閉 open scene、停用所有 NPC（單一交易）。"""
        cid = _as_str(channel_id)
        ts = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            modes = conn.execute(
                "UPDATE social_channel_modes SET mode = 'SOLO', enabled = 0, "
                "allow_webhooks = 0, note = 'kill switch', updated_at = ? "
                "WHERE channel_id = ?",
                (ts, cid),
            ).rowcount
            scenes = conn.execute(
                "UPDATE social_scenes SET status = 'killed', "
                "close_reason = 'kill_switch', closed_at = ? "
                "WHERE channel_id = ? AND status = 'open'",
                (ts, cid),
            ).rowcount
            personas = conn.execute(
                "UPDATE social_personas SET enabled = 0, updated_at = ? "
                "WHERE channel_id = ? AND is_primary = 0",
                (ts, cid),
            ).rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"modes": modes, "scenes": scenes, "personas": personas}

    def set_surfer_last_run(self, now: float | None = None) -> None:
        """更新上次衝浪執行時間戳記。"""
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE care_surfer_config SET last_run_at = ?, updated_at = ? WHERE id = 1",
                (ts, ts),
            )
            conn.commit()
        finally:
            conn.close()


_default_repository: CareRepository | None = None


def get_care_repository() -> CareRepository:
    """取得行程內共用的輕量 repository（實際連線仍為每次操作建立）。"""
    global _default_repository
    if _default_repository is None:
        _default_repository = CareRepository()
    return _default_repository


def reset_care_repository() -> None:
    """測試用：清除共用實例。"""
    global _default_repository
    _default_repository = None


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
