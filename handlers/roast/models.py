"""
handlers.roast.models
=====================
Data structures for the Roast (Tsukkomi) system.

Defines the configuration and individual keyword rules for Sherry's
automatic roasting/tsukkomi responses.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal

MatchMode = Literal["contains", "exact", "regex"]


@dataclass(frozen=True)
class RoastRule:
    """Represents a single roast/tsukkomi keyword trigger rule.

    Attributes:
        id: Unique identifier for the rule.
        keyword: The word, phrase, or pattern to match in chat messages.
        reply_guide: Guidance or template for Sherry's roast response.
        probability: Trigger probability as an integer percentage (0 to 100).
        match_mode: Matching strategy ("contains", "exact", "regex").
        enabled: Whether this individual rule is active.
        created_at: Unix epoch timestamp when the rule was created.
    """

    id: str = field(default_factory=lambda: f"roast_{uuid.uuid4().hex[:8]}")
    keyword: str = ""
    reply_guide: str = ""
    probability: int = 100
    match_mode: MatchMode = "contains"
    enabled: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        """Convert the rule into a JSON-serializable dictionary.

        Returns:
            Dictionary containing all rule properties.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RoastRule:
        """Create a RoastRule instance from a dictionary with validation.

        Args:
            data: Raw dictionary loaded from JSON or API payload.

        Returns:
            A validated RoastRule instance.
        """
        rule_id = str(data.get("id") or f"roast_{uuid.uuid4().hex[:8]}")
        keyword = str(data.get("keyword", "")).strip()
        reply_guide = str(data.get("reply_guide", "")).strip()

        # Sanitize probability into 0-100 integer range
        raw_prob = data.get("probability", 100)
        try:
            probability = max(0, min(100, int(float(str(raw_prob)))))
        except (ValueError, TypeError):
            probability = 100

        # Match mode validation
        raw_mode = str(data.get("match_mode", "contains")).lower()
        match_mode: MatchMode = "contains"
        if raw_mode in ("contains", "exact", "regex"):
            match_mode = raw_mode  # type: ignore[assignment]

        enabled = bool(data.get("enabled", True))

        raw_created = data.get("created_at", time.time())
        try:
            created_at = float(str(raw_created))
        except (ValueError, TypeError):
            created_at = time.time()

        return cls(
            id=rule_id,
            keyword=keyword,
            reply_guide=reply_guide,
            probability=probability,
            match_mode=match_mode,
            enabled=enabled,
            created_at=created_at,
        )


@dataclass(frozen=True)
class RoastConfig:
    """Global configuration for the Roast subsystem.

    Attributes:
        enabled: Master switch for the roast system.
        global_cooldown_seconds: Cooldown in seconds per channel between roasts.
        rules: List of active and inactive roast rules.
    """

    enabled: bool = True
    global_cooldown_seconds: int = 8
    rules: list[RoastRule] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Convert the configuration into a JSON-serializable dictionary.

        Returns:
            Dictionary containing roast config and serialized rules.
        """
        return {
            "enabled": self.enabled,
            "global_cooldown_seconds": self.global_cooldown_seconds,
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RoastConfig:
        """Create a RoastConfig instance from a dictionary.

        Args:
            data: Raw dictionary loaded from JSON.

        Returns:
            A validated RoastConfig instance.
        """
        enabled = bool(data.get("enabled", True))
        try:
            cooldown = max(0, int(str(data.get("global_cooldown_seconds", 8))))
        except (ValueError, TypeError):
            cooldown = 8

        raw_rules = data.get("rules", [])
        rules: list[RoastRule] = []
        if isinstance(raw_rules, list):
            for item in raw_rules:
                if isinstance(item, dict):
                    rules.append(RoastRule.from_dict(item))

        return cls(
            enabled=enabled,
            global_cooldown_seconds=cooldown,
            rules=rules,
        )
