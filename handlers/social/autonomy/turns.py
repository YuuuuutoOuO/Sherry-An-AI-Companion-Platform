"""handlers/social/autonomy/turns.py
=================================
TurnScheduler：沙盒群像的輪流與煞車。

硬規則（對應規格 I4 / I5 / I6）：
1. 同一 speaker 不得連續發言。
2. 一輪最多 2 個 NPC。
3. 連續 3 個 AI turn 強制停止。
4. 人類發言會作廢所有「已排程未送出」的 AI turn。
5. 每小時 / 每日上限。
6. 同一 NPC 兩次發言間隔 >= 45 秒。
7. 相鄰 AI turn 開頭 8 字不得相同。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import settings as default_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnRules:
    """可調的輪流規則。"""

    max_npcs_per_round: int = 2
    max_consecutive_ai: int = 3
    min_gap_seconds: int = 45
    hourly_limit: int = 20
    daily_limit: int = 120
    delay_min: float = 2.0
    delay_max: float = 8.0

    @classmethod
    def from_settings(cls, settings_module: Any | None = None) -> "TurnRules":
        s = settings_module or default_settings
        return cls(
            max_npcs_per_round=max(1, int(getattr(s, "SOCIAL_NPC_MAX_PER_ROUND", 2))),
            max_consecutive_ai=max(1, int(getattr(s, "SOCIAL_NPC_MAX_CONSECUTIVE", 3))),
            min_gap_seconds=max(0, int(getattr(s, "SOCIAL_NPC_MIN_GAP_SECONDS", 45))),
            hourly_limit=max(1, int(getattr(s, "SOCIAL_NPC_HOURLY_LIMIT", 20))),
            daily_limit=max(1, int(getattr(s, "SOCIAL_NPC_DAILY_LIMIT", 120))),
        )


@dataclass(frozen=True)
class TurnDecision:
    """一次 turn 是否允許，以及建議延遲。"""

    allowed: bool
    reasons: tuple[str, ...] = ()
    delay: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reasons": list(self.reasons), "delay": self.delay}


class TurnScheduler:
    """純規則判斷 + 可取消的延遲排程。"""

    def __init__(
        self,
        repository: Any,
        rules: TurnRules | None = None,
        *,
        now_fn: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.repository = repository
        self.rules = rules or TurnRules.from_settings()
        self.now_fn = now_fn
        self.rng = rng or random.Random()
        self._pending: dict[str, asyncio.Task[Any]] = {}
        self._generation: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 規則判斷
    # ------------------------------------------------------------------
    def evaluate(
        self,
        *,
        scene: Any,
        persona_key: str,
        role: str = "npc",
        last_ai_text: str = "",
        now: float | None = None,
    ) -> TurnDecision:
        ts = self.now_fn() if now is None else float(now)
        scene_id = int(getattr(scene, "scene_id", 0))
        turns = tuple(self.repository.list_scene_turns(scene_id, limit=50))
        reasons: list[str] = []

        if getattr(scene, "status", "open") != "open":
            reasons.append(f"scene_{getattr(scene, 'status', 'unknown')}")

        if int(getattr(scene, "turns_used", 0)) >= int(getattr(scene, "max_turns", 12)):
            reasons.append("scene_full")

        last_speaker = ""
        if turns:
            last = turns[-1]
            last_speaker = str(getattr(last, "persona_key", "") or getattr(last, "role", ""))
        if last_speaker and last_speaker == persona_key:
            reasons.append("same_speaker_consecutive")

        ai_streak = _ai_consecutive(turns)
        if ai_streak >= self.rules.max_consecutive_ai:
            reasons.append(f"ai_streak={ai_streak}")

        ai_this_round = _ai_turns_this_round(turns)
        npc_this_round = _npc_turns_this_round(turns)
        if role == "npc" and npc_this_round >= self.rules.max_npcs_per_round:
            reasons.append(f"round_npc_limit={npc_this_round}")
        if ai_this_round >= self.rules.max_npcs_per_round + 1:
            reasons.append(f"round_ai_limit={ai_this_round}")

        if self.rules.min_gap_seconds > 0 and persona_key:
            recent = self.repository.count_turns_since(
                scene.channel_id,
                since=ts - self.rules.min_gap_seconds,
                persona_key=persona_key,
            )
            if recent > 0:
                reasons.append(f"persona_gap<{self.rules.min_gap_seconds}s")

        if role == "npc":
            hourly = self.repository.count_turns_since(
                scene.channel_id, since=ts - 3600, role="npc"
            )
            if hourly >= self.rules.hourly_limit:
                reasons.append(f"hourly_limit={hourly}")
            daily = self.repository.count_turns_since(
                scene.channel_id, since=ts - 86400, role="npc"
            )
            if daily >= self.rules.daily_limit:
                reasons.append(f"daily_limit={daily}")

        if last_ai_text and self.is_too_similar(last_ai_text, _last_ai_text(turns)):
            reasons.append("duplicate_opening")

        if reasons:
            return TurnDecision(allowed=False, reasons=tuple(reasons))

        delay = self.rng.uniform(self.rules.delay_min, self.rules.delay_max)
        return TurnDecision(allowed=True, delay=round(delay, 2))

    @staticmethod
    def is_too_similar(candidate: str, previous: str, *, chars: int = 8, min_overlap: int = 4) -> bool:
        """相鄰兩句的開頭不得相同（去除標點與空白後比較）。

        短句（不足 8 字）以「開頭互為前綴」判定，避免同一句只多一個語助詞
        就重複發言；前綴重疊至少 ``min_overlap`` 個字才算重複。
        """
        def _head(text: str) -> str:
            cleaned = "".join(ch for ch in str(text or "") if ch.isalnum())
            return cleaned[:chars]

        head = _head(candidate)
        prev = _head(previous)
        if not head or not prev:
            return False
        if head == prev:
            return True
        shorter, longer = (head, prev) if len(head) <= len(prev) else (prev, head)
        return len(shorter) >= min_overlap and longer.startswith(shorter)

    # ------------------------------------------------------------------
    # 取消與排程（人類優先）
    # ------------------------------------------------------------------
    def generation(self, channel_id: int | str) -> int:
        return self._generation.get(str(channel_id), 0)

    def cancel(self, channel_id: int | str) -> int:
        """作廢此頻道所有待送 turn，回傳新的 generation。"""
        cid = str(channel_id)
        self._generation[cid] = self._generation.get(cid, 0) + 1
        task = self._pending.pop(cid, None)
        if task is not None and not task.done():
            task.cancel()
        return self._generation[cid]

    def is_stale(self, channel_id: int | str, generation: int) -> bool:
        return self._generation.get(str(channel_id), 0) != generation

    def schedule(
        self,
        channel_id: int | str,
        delay: float,
        factory: Callable[[], Awaitable[Any]],
        *,
        generation: int | None = None,
    ) -> asyncio.Task[Any]:
        """延遲執行 factory；若期間人類發言或 kill switch，則整個作廢。"""
        cid = str(channel_id)
        gen = self.generation(cid) if generation is None else int(generation)

        async def _runner() -> Any:
            try:
                await asyncio.sleep(max(0.0, float(delay)))
                if self.is_stale(cid, gen):
                    logger.debug("[TurnScheduler] channel=%s generation 已失效，取消 turn", cid)
                    return None
                return await factory()
            except asyncio.CancelledError:
                logger.debug("[TurnScheduler] channel=%s turn 已取消", cid)
                raise
            finally:
                current = self._pending.get(cid)
                if current is asyncio.current_task():
                    self._pending.pop(cid, None)

        previous = self._pending.get(cid)
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(_runner(), name=f"social-turn-{cid}")
        self._pending[cid] = task
        return task

    async def wait_idle(self, channel_id: int | str, timeout: float = 15.0) -> None:
        """測試用：等待此頻道待送 turn 結束。"""
        task = self._pending.get(str(channel_id))
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


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


def _npc_turns_this_round(turns: tuple[Any, ...]) -> int:
    count = 0
    for turn in reversed(turns):
        role = str(getattr(turn, "role", ""))
        if role == "human":
            break
        if role == "npc":
            count += 1
    return count


def _last_ai_text(turns: tuple[Any, ...]) -> str:
    for turn in reversed(turns):
        if str(getattr(turn, "role", "")) in ("npc", "bot"):
            return str(getattr(turn, "content", ""))
    return ""
