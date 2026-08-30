"""mod/spark/checkers/base.py - Spark 觸發判定策略協定介面。

依據 AGENTS.md §1 優先使用 typing.Protocol 定義抽象合約。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Trigger, TriggerResult


@runtime_checkable
class BaseChecker(Protocol):
    """觸發檢查器協定：定義各種條件判定的共通合約。"""

    trigger_type: str

    async def should_fire(self, trigger: Trigger) -> TriggerResult:
        """檢查特定 Trigger 是否已達觸發條件。

        Args:
            trigger: 欲評估的觸發器實例。

        Returns:
            TriggerResult: 判定結果包含 should_fire 與當前數值摘要。
        """
        ...
