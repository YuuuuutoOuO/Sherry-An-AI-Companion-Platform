"""主動關懷引擎（R19–R20）。

對外提供引擎建構、``/care`` 指令與 WebUI blueprint；production 入口
一律注入伺服器灰度（``CareRollout``），候選必須同時通過 opt-in、灰度
與額度才會被核准。
"""

from __future__ import annotations

import logging

from core.autonomy_guard import AutonomyGuard, global_autonomy_guard

from .cog import CareCog
from .engine import CareCandidate, CareDecision, CareEngine
from .repository import CarePreference, CareRepository, CareSurferConfig, get_care_repository
from .rollout import CareRollout, RolloutDecision
from .surfer import CareSurfer, SurferBroadcaster, get_surfer_broadcaster

logger = logging.getLogger(__name__)

_engine: CareEngine | None = None


def build_care_engine(
    repository: CareRepository | None = None,
    guard: AutonomyGuard | None = None,
    relationship_manager: object | None = None,
    spark_repository: object | None = None,
) -> CareEngine:
    """建立 production 用引擎：強制注入伺服器灰度。"""
    repo = repository or get_care_repository()
    return CareEngine(
        repository=repo,
        guard=guard,
        relationship_manager=relationship_manager,
        spark_repository=spark_repository,
        rollout=CareRollout(repo),
    )


def get_care_engine() -> CareEngine:
    """取得行程內共用的關懷引擎（尚未 setup 時建立預設實例）。"""
    global _engine
    if _engine is None:
        _engine = build_care_engine()
    return _engine


def set_care_engine(engine: CareEngine) -> None:
    global _engine
    _engine = engine


async def setup(bot: object) -> None:
    """載入 extension：接上關係分數、SPARK repository 與 /care 指令。"""
    memory_cog = bot.get_cog("MemoryManager") if hasattr(bot, "get_cog") else None
    spark_cog = bot.get_cog("SparkCog") if hasattr(bot, "get_cog") else None
    relations = getattr(memory_cog, "relations", None)
    spark_repo = getattr(getattr(spark_cog, "service", None), "repository", None)
    engine = build_care_engine(
        relationship_manager=relations,
        spark_repository=spark_repo,
    )
    engine.install_guard(global_autonomy_guard)
    set_care_engine(engine)
    setattr(bot, "care_engine", engine)
    if hasattr(bot, "add_cog"):
        await bot.add_cog(CareCog(bot, engine))  # type: ignore[arg-type]
    logger.info(
        "[Care] 主動關懷引擎與 /care 指令已載入（relationships=%s, spark=%s）",
        relations is not None,
        spark_repo is not None,
    )


__all__ = [
    "CareCandidate",
    "CareCog",
    "CareDecision",
    "CareEngine",
    "CarePreference",
    "CareRepository",
    "CareRollout",
    "CareSurfer",
    "CareSurferConfig",
    "RolloutDecision",
    "SurferBroadcaster",
    "build_care_engine",
    "get_care_engine",
    "get_surfer_broadcaster",
    "set_care_engine",
]
