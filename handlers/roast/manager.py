"""
handlers.roast.manager
======================
Manager for persisting and querying roast (tsukkomi) rules.

Handles reading and writing `./data/roast_rules.json` safely with
in-memory caching and keyword matching algorithms.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
from pathlib import Path

import settings
from handlers.roast.models import MatchMode, RoastConfig, RoastRule

logger = logging.getLogger(__name__)

# Default rules initialized when roast_rules.json is created for the first time
DEFAULT_ROAST_RULES: list[dict[str, object]] = [
    {
      "id": "roast_default_1",
      "keyword": "==",
      "reply_guide": "= = 中間要空格啦！連顏文字都不會打，真是無可救藥的大笨蛋～",
      "probability": 100,
      "match_mode": "contains",
      "enabled": True,
    },
    {
      "id": "roast_default_2",
      "keyword": "你是笨蛋",
      "reply_guide": "說誰是笨蛋呢！本名偵探智商 180，你才是全天下最大的大笨蛋！",
      "probability": 80,
      "match_mode": "contains",
      "enabled": True,
    },
]


from core.db import get_db_connection, db_transaction

MEMORY_DB_PATH = settings.DATA_DIR / "sherry_memory.db"


class RoastManager:
    """Thread-safe manager for Roast rules storage and matching with SQLite support."""

    def __init__(self, file_path: Path | None = None, db_path: Path | None = None) -> None:
        """Initialize the RoastManager with target file path.

        Args:
            file_path: Custom file path for rules, defaults to settings.ROAST_RULES_PATH.
            db_path: Custom db path, defaults to memory db.
        """
        self.file_path: Path = file_path or settings.ROAST_RULES_PATH
        if db_path is not None:
            self.db_path = db_path
        elif file_path is not None and file_path != settings.ROAST_RULES_PATH:
            self.db_path = file_path.parent / "chat_history.db"
        else:
            self.db_path = settings.DATA_DIR / "chat_history.db"

        self._lock: threading.Lock = threading.Lock()
        self._cached_config: RoastConfig | None = None
        self._ensure_storage_exists()

    def _ensure_storage_exists(self) -> None:
        """確保資料庫與預設規則已建立。"""
        with self._lock:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                with db_transaction(self.db_path) as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS roast_config (
                            id INTEGER PRIMARY KEY CHECK(id = 1),
                            enabled INTEGER NOT NULL DEFAULT 1,
                            global_cooldown_seconds REAL NOT NULL DEFAULT 8.0,
                            updated_at TEXT NOT NULL
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS roast_rules (
                            id TEXT PRIMARY KEY,
                            keyword TEXT NOT NULL,
                            reply_guide TEXT NOT NULL,
                            probability INTEGER NOT NULL DEFAULT 100,
                            match_mode TEXT NOT NULL DEFAULT 'contains',
                            enabled INTEGER NOT NULL DEFAULT 1,
                            is_deleted INTEGER NOT NULL DEFAULT 0,
                            deleted_at TEXT,
                            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                        )
                    """)

                    # 若磁碟存在舊版/現有 roast_rules.json，自動匯入自訂規則至 SQLite
                    if self.file_path and self.file_path.exists():
                        try:
                            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
                            rules_data = raw.get("rules", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                            cfg_enabled = raw.get("enabled", True) if isinstance(raw, dict) else True
                            cfg_cd = raw.get("global_cooldown_seconds", 8.0) if isinstance(raw, dict) else 8.0

                            for r_idx, r in enumerate(rules_data):
                                r_id = str(r.get("id") or f"roast_{r_idx}")
                                kw = str(r.get("keyword") or r.get("pattern", "")).strip()
                                rg = str(r.get("reply_guide") or r.get("reply", "")).strip()
                                prob = int(r.get("probability", 100))
                                mode = str(r.get("match_mode", "contains"))
                                en = 1 if r.get("enabled", True) else 0

                                if kw and rg:
                                    # R7：回灌僅補缺失、不覆寫刪除標記（報告 §5.2.3）
                                    # —— 已軟刪的規則不得因 JSON 回灌而復活或被改寫。
                                    cur.execute("""
                                        INSERT INTO roast_rules (id, keyword, reply_guide, probability, match_mode, enabled, is_deleted, created_at, updated_at)
                                        VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now', 'localtime'), datetime('now', 'localtime'))
                                        ON CONFLICT(id) DO NOTHING;
                                    """, (r_id, kw, rg, prob, mode, en))

                            cur.execute("""
                                INSERT INTO roast_config (id, enabled, global_cooldown_seconds, updated_at)
                                VALUES (1, ?, ?, datetime('now', 'localtime'))
                                ON CONFLICT(id) DO UPDATE SET
                                    enabled = excluded.enabled,
                                    global_cooldown_seconds = excluded.global_cooldown_seconds;
                            """, (1 if cfg_enabled else 0, float(cfg_cd)))
                        except Exception as j_exc:
                            logger.warning("RoastManager: 自動自 roast_rules.json 匯入規則失敗: %s", j_exc)

                    cur.execute("SELECT COUNT(*) FROM roast_rules WHERE is_deleted = 0")
                    cnt = cur.fetchone()[0]

                    if cnt == 0:
                        # 初始化預設規則 (僅在全無規則時)
                        for r in DEFAULT_ROAST_RULES:
                            cur.execute("""
                                INSERT OR IGNORE INTO roast_rules (id, keyword, reply_guide, probability, match_mode, enabled, is_deleted)
                                VALUES (?, ?, ?, ?, ?, ?, 0)
                            """, (
                                str(r["id"]),
                                str(r["keyword"]),
                                str(r["reply_guide"]),
                                int(r.get("probability", 100)),
                                str(r.get("match_mode", "contains")),
                                1 if r.get("enabled", True) else 0,
                            ))
                        cur.execute("""
                            INSERT OR IGNORE INTO roast_config (id, enabled, global_cooldown_seconds, updated_at)
                            VALUES (1, 1, 8.0, datetime('now', 'localtime'))
                        """)
            except Exception as exc:
                logger.error("Failed to initialize roast storage: %s", exc, exc_info=True)

    def _read_from_db_locked(self) -> RoastConfig:
        """從 SQLite 載入有效設定 (WHERE is_deleted = 0)。"""
        try:
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT enabled, global_cooldown_seconds FROM roast_config WHERE id = 1")
            cfg_row = cur.fetchone()

            cur.execute("""
                SELECT id, keyword, reply_guide, probability, match_mode, enabled
                FROM roast_rules
                WHERE is_deleted = 0
            """)
            rule_rows = cur.fetchall()
            conn.close()

            rules: list[RoastRule] = []
            for r in rule_rows:
                raw_mode = str(r["match_mode"]).strip().lower()
                mode: MatchMode = raw_mode if raw_mode in ("contains", "exact", "regex") else "contains"  # type: ignore[assignment]
                rules.append(
                    RoastRule(
                        id=str(r["id"]),
                        keyword=str(r["keyword"]),
                        reply_guide=str(r["reply_guide"]),
                        probability=int(r["probability"]),
                        match_mode=mode,
                        enabled=bool(r["enabled"]),
                    )
                )

            enabled = bool(cfg_row["enabled"]) if cfg_row else True
            cd = round(float(str(cfg_row["global_cooldown_seconds"]))) if cfg_row else 8
            return RoastConfig(enabled=enabled, global_cooldown_seconds=cd, rules=rules)
        except Exception as exc:
            logger.error("讀取 SQLite roast 規則失敗: %s", exc)
            return RoastConfig(enabled=True, global_cooldown_seconds=8, rules=[])

    def _write_to_db_locked(self, config: RoastConfig) -> None:
        """寫入設定至 SQLite。"""
        try:
            with db_transaction(self.db_path) as cur:
                cur.execute("""
                    INSERT OR REPLACE INTO roast_config (id, enabled, global_cooldown_seconds, updated_at)
                    VALUES (1, ?, ?, datetime('now', 'localtime'))
                """, (1 if config.enabled else 0, config.global_cooldown_seconds))

                for r in config.rules:
                    cur.execute("""
                        INSERT INTO roast_rules (id, keyword, reply_guide, probability, match_mode, enabled, is_deleted, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now', 'localtime'))
                        ON CONFLICT(id) DO UPDATE SET
                            keyword = excluded.keyword,
                            reply_guide = excluded.reply_guide,
                            probability = excluded.probability,
                            match_mode = excluded.match_mode,
                            enabled = excluded.enabled,
                            is_deleted = 0,
                            updated_at = datetime('now', 'localtime')
                    """, (
                        r.id,
                        r.keyword,
                        r.reply_guide,
                        r.probability,
                        r.match_mode,
                        1 if r.enabled else 0,
                    ))
        except Exception as exc:
            logger.error("寫入 SQLite roast 規則失敗: %s", exc)

    def get_config(self) -> RoastConfig:
        """Get the current roast configuration (cached or fresh).

        Returns:
            Current RoastConfig.
        """
        with self._lock:
            if self._cached_config is None:
                self._cached_config = self._read_from_db_locked()
            return self._cached_config

    def reload(self) -> RoastConfig:
        """Force reload configuration from disk.

        Returns:
            Freshly loaded RoastConfig.
        """
        with self._lock:
            self._cached_config = self._read_from_db_locked()
            return self._cached_config

    def is_enabled(self) -> bool:
        """Check if the roast subsystem master switch is enabled.

        Returns:
            True if enabled, False otherwise.
        """
        return self.get_config().enabled

    def set_enabled(self, enabled: bool) -> RoastConfig:
        """Toggle the master switch for the roast subsystem.

        Args:
            enabled: True to enable, False to disable.

        Returns:
            Updated RoastConfig.
        """
        with self._lock:
            current = self._read_from_db_locked()
            updated = RoastConfig(
                enabled=enabled,
                global_cooldown_seconds=current.global_cooldown_seconds,
                rules=current.rules,
            )
            self._write_to_db_locked(updated)
            self._cached_config = updated
            logger.info("Roast system master switch set to: %s", enabled)
            return updated

    def set_cooldown(self, cooldown_seconds: int) -> RoastConfig:
        """Set the global cooldown duration between roasts per channel.

        Args:
            cooldown_seconds: Cooldown in seconds.

        Returns:
            Updated RoastConfig.
        """
        with self._lock:
            current = self._read_from_db_locked()
            updated = RoastConfig(
                enabled=current.enabled,
                global_cooldown_seconds=max(0, cooldown_seconds),
                rules=current.rules,
            )
            self._write_to_db_locked(updated)
            self._cached_config = updated
            return updated

    def get_rules(self) -> list[RoastRule]:
        """Get all registered roast rules.

        Returns:
            List of all RoastRules.
        """
        return self.get_config().rules

    def add_rule(
        self,
        keyword: str,
        reply_guide: str,
        probability: int = 100,
        match_mode: MatchMode = "contains",
        enabled: bool = True,
    ) -> RoastRule:
        """Add a new roast keyword rule.

        Args:
            keyword: Trigger word or phrase.
            reply_guide: Roast guidance/template for Sherry.
            probability: Trigger probability percentage (0 to 100).
            match_mode: Match mode ("contains", "exact", "regex").
            enabled: Whether rule is enabled immediately.

        Returns:
            Newly created RoastRule.

        Raises:
            ValueError: If keyword or reply_guide is empty.
        """
        clean_kw = keyword.strip()
        clean_guide = reply_guide.strip()
        if not clean_kw:
            raise ValueError("關鍵字不可為空！")
        if not clean_guide:
            raise ValueError("吐槽回覆導引不可為空！")

        new_rule = RoastRule(
            keyword=clean_kw,
            reply_guide=clean_guide,
            probability=max(0, min(100, probability)),
            match_mode=match_mode,
            enabled=enabled,
        )

        with self._lock:
            current = self._read_from_db_locked()
            new_rules = list(current.rules) + [new_rule]
            updated = RoastConfig(
                enabled=current.enabled,
                global_cooldown_seconds=current.global_cooldown_seconds,
                rules=new_rules,
            )
            self._write_to_db_locked(updated)
            self._cached_config = updated
            logger.info("Added new roast rule [%s] for keyword '%s'", new_rule.id, clean_kw)
            return new_rule

    def update_rule(
        self,
        rule_id: str,
        keyword: str | None = None,
        reply_guide: str | None = None,
        probability: int | None = None,
        match_mode: MatchMode | None = None,
        enabled: bool | None = None,
    ) -> RoastRule | None:
        """Update an existing roast rule.

        Args:
            rule_id: ID of the rule to update.
            keyword: New keyword or None to retain.
            reply_guide: New reply guide or None to retain.
            probability: New probability (0-100) or None to retain.
            match_mode: New match mode or None to retain.
            enabled: New enabled state or None to retain.

        Returns:
            Updated RoastRule if found, None otherwise.
        """
        with self._lock:
            current = self._read_from_db_locked()
            updated_rules: list[RoastRule] = []
            target_rule: RoastRule | None = None

            for r in current.rules:
                if r.id == rule_id:
                    new_kw = keyword.strip() if keyword is not None else r.keyword
                    new_guide = reply_guide.strip() if reply_guide is not None else r.reply_guide
                    new_prob = max(0, min(100, probability)) if probability is not None else r.probability
                    new_mode = match_mode if match_mode is not None else r.match_mode
                    new_enabled = enabled if enabled is not None else r.enabled

                    target_rule = RoastRule(
                        id=r.id,
                        keyword=new_kw,
                        reply_guide=new_guide,
                        probability=new_prob,
                        match_mode=new_mode,
                        enabled=new_enabled,
                        created_at=r.created_at,
                    )
                    updated_rules.append(target_rule)
                else:
                    updated_rules.append(r)

            if target_rule is not None:
                updated_config = RoastConfig(
                    enabled=current.enabled,
                    global_cooldown_seconds=current.global_cooldown_seconds,
                    rules=updated_rules,
                )
                self._write_to_db_locked(updated_config)
                self._cached_config = updated_config
                logger.info("Updated roast rule [%s]", rule_id)
                return target_rule

            return None

    def delete_rule(self, rule_id: str) -> bool:
        """Soft delete a roast rule by its ID (sets is_deleted = 1).

        Args:
            rule_id: ID of the rule to delete.

        Returns:
            True if deleted, False if rule not found.
        """
        with self._lock:
            try:
                with db_transaction(self.db_path) as cur:
                    cur.execute("""
                        UPDATE roast_rules
                        SET is_deleted = 1, deleted_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                        WHERE id = ? AND is_deleted = 0;
                    """, (rule_id,))
                    affected = cur.rowcount

                if affected > 0:
                    self._cached_config = None
                    logger.info("Soft deleted roast rule [%s]", rule_id)
                    return True
                return False
            except Exception as exc:
                logger.error("Failed to soft delete roast rule: %s", exc)
                return False

    def find_matching_rules(self, message_text: str) -> list[RoastRule]:
        """Find all enabled roast rules that match the given message text.

        Args:
            message_text: The user's chat message content.

        Returns:
            List of matching enabled RoastRules.
        """
        if not message_text:
            return []

        config = self.get_config()
        if not config.enabled:
            return []

        normalized_text = message_text.strip()
        matched: list[RoastRule] = []

        for rule in config.rules:
            if not rule.enabled or not rule.keyword:
                continue

            if self._matches(normalized_text, rule.keyword, rule.match_mode):
                matched.append(rule)

        return matched

    @staticmethod
    def _matches(text: str, keyword: str, mode: MatchMode) -> bool:
        """Check if text matches keyword according to mode.

        Args:
            text: Message text.
            keyword: Rule keyword.
            mode: Match mode.

        Returns:
            True if matches, False otherwise.
        """
        if mode == "exact":
            return text.lower() == keyword.lower()
        elif mode == "regex":
            try:
                return bool(re.search(keyword, text, re.IGNORECASE))
            except re.error:
                return False
        else:  # "contains"
            return keyword.lower() in text.lower()

    @staticmethod
    def roll_probability(rule: RoastRule) -> bool:
        """Roll random dice against the rule's probability (0 to 100).

        Args:
            rule: RoastRule with probability attribute.

        Returns:
            True if triggered, False otherwise.
        """
        if rule.probability <= 0:
            return False
        if rule.probability >= 100:
            return True
        return random.random() * 100.0 < float(rule.probability)


# Global singleton instance
roast_manager: RoastManager = RoastManager()
