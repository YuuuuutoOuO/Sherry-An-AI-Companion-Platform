"""handlers/social/autonomy/scene.py
=================================
Scene 狀態機：一次有始有終的群聊段落。

規則：
- 同一頻道同時最多一個 open scene（DB partial unique index 保證）。
- 人類發言後，已排程未送出的 AI turn 必須作廢（由 TurnScheduler 負責）。
- 連續 AI 發言到上限、turn 數到上限、閒置過久、kill switch 都會關閉 scene。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import settings as default_settings


@dataclass(frozen=True)
class SceneInspection:
    """scene 的唯讀快照，供 UI 與規則判斷使用。"""

    scene: Any
    turns: tuple[Any, ...]
    ai_consecutive: int = 0
    ai_turns_this_round: int = 0
    last_speaker: str = ""

    @property
    def turns_used(self) -> int:
        return int(getattr(self.scene, "turns_used", 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.scene.to_dict() if hasattr(self.scene, "to_dict") else {},
            "turns": [t.to_dict() if hasattr(t, "to_dict") else {} for t in self.turns],
            "ai_consecutive": self.ai_consecutive,
            "ai_turns_this_round": self.ai_turns_this_round,
            "last_speaker": self.last_speaker,
        }


class SceneManager:
    """scene 的建立、記錄與關閉。"""

    def __init__(self, repository: Any, settings_module: Any | None = None) -> None:
        self.repository = repository
        self.settings = settings_module or default_settings

    @property
    def max_turns(self) -> int:
        return int(getattr(self.settings, "SOCIAL_SCENE_MAX_TURNS", 12))

    @property
    def idle_seconds(self) -> int:
        return int(getattr(self.settings, "SOCIAL_SCENE_IDLE_SECONDS", 600))

    def ensure_scene(
        self,
        channel_id: int | str,
        *,
        guild_id: str | int = "",
        topic: str = "",
        now: float | None = None,
    ) -> Any:
        """取得或建立 open scene；若既有 scene 已閒置或滿額則先關閉。"""
        ts = time.time() if now is None else float(now)
        scene = self.repository.get_open_scene(channel_id)
        if scene is not None:
            if scene.turns_used >= scene.max_turns:
                self.repository.close_scene(scene.scene_id, reason="max_turns", now=ts)
                scene = None
            elif self.idle_seconds > 0 and ts - self._last_activity(scene.scene_id, scene.started_at) > self.idle_seconds:
                self.repository.close_scene(scene.scene_id, reason="idle", now=ts)
                scene = None
        if scene is None:
            scene = self.repository.open_scene(
                channel_id,
                guild_id=guild_id,
                topic=topic,
                max_turns=self.max_turns,
                now=ts,
            )
        return scene

    def _last_activity(self, scene_id: int, fallback: float) -> float:
        last = self.repository.last_turn(scene_id)
        return float(getattr(last, "created_at", fallback)) if last is not None else fallback

    def record_human(
        self,
        scene: Any,
        *,
        content: str,
        message_id: str = "",
        author_name: str = "",
        now: float | None = None,
    ) -> int:
        ts = time.time() if now is None else float(now)
        return self.repository.add_scene_turn(
            scene_id=int(scene.scene_id),
            channel_id=scene.channel_id,
            role="human",
            persona_key="",
            content=content[:1000],
            message_id=message_id,
            decision_id=None,
            now=ts,
        )

    def record_ai(
        self,
        scene: Any,
        *,
        role: str,
        persona_key: str,
        content: str,
        message_id: str = "",
        decision_id: int | None = None,
        now: float | None = None,
    ) -> int:
        ts = time.time() if now is None else float(now)
        return self.repository.add_scene_turn(
            scene_id=int(scene.scene_id),
            channel_id=scene.channel_id,
            role=role,
            persona_key=persona_key,
            content=content[:1000],
            message_id=message_id,
            decision_id=decision_id,
            now=ts,
        )

    def inspect(self, scene_id: int, *, limit: int = 50) -> SceneInspection:
        scene = self.repository.get_scene(scene_id)
        turns = tuple(self.repository.list_scene_turns(scene_id, limit=limit))
        return SceneInspection(
            scene=scene,
            turns=turns,
            ai_consecutive=_ai_consecutive(turns),
            ai_turns_this_round=_ai_turns_this_round(turns),
            last_speaker=_last_speaker(turns),
        )

    def consecutive_ai(self, scene_id: int) -> int:
        return _ai_consecutive(tuple(self.repository.list_scene_turns(scene_id, limit=50)))

    def close(self, scene_id: int, reason: str, *, status: str = "closed", now: float | None = None) -> bool:
        return self.repository.close_scene(scene_id, reason=reason, status=status, now=now)

    def maybe_close_if_full(self, scene: Any, *, now: float | None = None) -> bool:
        if int(getattr(scene, "turns_used", 0)) >= int(getattr(scene, "max_turns", self.max_turns)):
            self.close(scene.scene_id, reason="max_turns", now=now)
            return True
        return False


def _ai_consecutive(turns: tuple[Any, ...]) -> int:
    count = 0
    for turn in reversed(turns):
        if str(getattr(turn, "role", "")) in ("npc", "bot"):
            count += 1
        else:
            break
    return count


def _ai_turns_this_round(turns: tuple[Any, ...]) -> int:
    count = 0
    for turn in reversed(turns):
        if str(getattr(turn, "role", "")) == "human":
            break
        count += 1
    return count


def _last_speaker(turns: tuple[Any, ...]) -> str:
    if not turns:
        return ""
    last = turns[-1]
    return str(getattr(last, "persona_key", "") or getattr(last, "role", ""))
