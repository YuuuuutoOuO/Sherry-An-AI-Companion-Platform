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


class RoastManager:
    """Thread-safe manager for Roast rules storage and matching."""

    def __init__(self, file_path: Path | None = None) -> None:
        """Initialize the RoastManager with target file path.

        Args:
            file_path: Custom file path for rules, defaults to settings.ROAST_RULES_PATH.
        """
        self.file_path: Path = file_path or settings.ROAST_RULES_PATH
        self._lock: threading.Lock = threading.Lock()
        self._cached_config: RoastConfig | None = None
        self._ensure_storage_exists()

    def _ensure_storage_exists(self) -> None:
        """Ensure the storage directory and JSON file exist with default content."""
        with self._lock:
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                if not self.file_path.exists():
                    initial_config = RoastConfig(
                        enabled=True,
                        global_cooldown_seconds=8,
                        rules=[RoastRule.from_dict(r) for r in DEFAULT_ROAST_RULES],
                    )
                    self._write_to_file_locked(initial_config)
                    self._cached_config = initial_config
                    logger.info("Created default roast rules at %s", self.file_path)
            except Exception as exc:
                logger.error("Failed to initialize roast storage: %s", exc, exc_info=True)

    def _read_from_file_locked(self) -> RoastConfig:
        """Read and parse the JSON storage file (must be called with _lock held).

        Returns:
            Parsed RoastConfig instance.
        """
        if not self.file_path.exists():
            return RoastConfig(enabled=True, global_cooldown_seconds=8, rules=[])

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            if isinstance(raw_data, dict):
                return RoastConfig.from_dict(raw_data)
        except Exception as exc:
            logger.error("Failed to read roast rules from %s: %s", self.file_path, exc)

        return RoastConfig(enabled=True, global_cooldown_seconds=8, rules=[])

    def _write_to_file_locked(self, config: RoastConfig) -> None:
        """Write the configuration directly to JSON (must be called with _lock held).

        Args:
            config: RoastConfig instance to write.
        """
        temp_path = self.file_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
            temp_path.replace(self.file_path)
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            logger.error("Failed to write roast rules to %s: %s", self.file_path, exc)
            raise

    def get_config(self) -> RoastConfig:
        """Get the current roast configuration (cached or fresh).

        Returns:
            Current RoastConfig.
        """
        with self._lock:
            if self._cached_config is None:
                self._cached_config = self._read_from_file_locked()
            return self._cached_config

    def reload(self) -> RoastConfig:
        """Force reload configuration from disk.

        Returns:
            Freshly loaded RoastConfig.
        """
        with self._lock:
            self._cached_config = self._read_from_file_locked()
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
            current = self._read_from_file_locked()
            updated = RoastConfig(
                enabled=enabled,
                global_cooldown_seconds=current.global_cooldown_seconds,
                rules=current.rules,
            )
            self._write_to_file_locked(updated)
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
            current = self._read_from_file_locked()
            updated = RoastConfig(
                enabled=current.enabled,
                global_cooldown_seconds=max(0, cooldown_seconds),
                rules=current.rules,
            )
            self._write_to_file_locked(updated)
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
            current = self._read_from_file_locked()
            new_rules = list(current.rules) + [new_rule]
            updated = RoastConfig(
                enabled=current.enabled,
                global_cooldown_seconds=current.global_cooldown_seconds,
                rules=new_rules,
            )
            self._write_to_file_locked(updated)
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
            current = self._read_from_file_locked()
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
                self._write_to_file_locked(updated_config)
                self._cached_config = updated_config
                logger.info("Updated roast rule [%s]", rule_id)
                return target_rule

            return None

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a roast rule by its ID.

        Args:
            rule_id: ID of the rule to delete.

        Returns:
            True if deleted, False if rule not found.
        """
        with self._lock:
            current = self._read_from_file_locked()
            initial_count = len(current.rules)
            filtered_rules = [r for r in current.rules if r.id != rule_id]

            if len(filtered_rules) == initial_count:
                return False

            updated_config = RoastConfig(
                enabled=current.enabled,
                global_cooldown_seconds=current.global_cooldown_seconds,
                rules=filtered_rules,
            )
            self._write_to_file_locked(updated_config)
            self._cached_config = updated_config
            logger.info("Deleted roast rule [%s]", rule_id)
            return True

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
