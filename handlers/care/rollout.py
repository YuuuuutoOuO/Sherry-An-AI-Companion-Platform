"""主動關懷的灰度投放（R20）。

灰度以「伺服器」為單位：沒有 ``care_guilds`` 資料列即視為未開啟（0%），
因此未開放的 guild 不會有任何候選通過 ``CareEngine.evaluate``。私訊
（``guild_id=0``）沒有伺服器可灰度，僅受使用者 opt-in 控制。
"""

from __future__ import annotations

from dataclasses import dataclass

from .repository import CareRepository, get_care_repository, rollout_bucket


@dataclass(frozen=True)
class RolloutDecision:
    """單次灰度判定。"""

    allowed: bool
    reason: str
    rollout_percent: int = 0


class CareRollout:
    """查詢並判定伺服器灰度；由 production 入口注入 CareEngine。"""

    def __init__(self, repository: CareRepository | None = None) -> None:
        self.repository = repository or get_care_repository()

    def check(self, guild_id: int | str, user_id: int | str = 0) -> RolloutDecision:
        gid = "" if guild_id in (None, "", 0, "0") else str(guild_id)
        if not gid:
            return RolloutDecision(True, "私訊不受伺服器灰度限制", 100)

        record = self.repository.get_guild_rollout(gid)
        if not record.enabled or record.rollout_percent <= 0:
            return RolloutDecision(
                False, f"伺服器 {gid} 尚未開啟主動關懷灰度", record.rollout_percent
            )
        if record.rollout_percent >= 100:
            return RolloutDecision(
                True, f"伺服器 {gid} 已全量開放", record.rollout_percent
            )

        bucket = rollout_bucket(gid, user_id)
        if bucket < record.rollout_percent:
            return RolloutDecision(
                True,
                f"伺服器 {gid} 灰度 {record.rollout_percent}% 命中（bucket={bucket}）",
                record.rollout_percent,
            )
        return RolloutDecision(
            False,
            f"伺服器 {gid} 灰度 {record.rollout_percent}% 未命中（bucket={bucket}）",
            record.rollout_percent,
        )

    def is_enabled(self, guild_id: int | str, user_id: int | str = 0) -> bool:
        return self.check(guild_id, user_id).allowed
