"""
core/recovery/base.py
=====================
雪莉自癒與狀態快照架構標準協議與資料模型。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class SnapshotMetadata:
    """快照詮釋資料，包含提供者識別、建立時戳、TTL 與結構版本。"""

    provider_name: str
    snapshot_time: float = field(default_factory=time.time)
    ttl_seconds: float = 300.0  # 預設 5 分鐘有效
    version: int = 1


@runtime_checkable
class Recoverable(Protocol):
    """可恢復服務的標準協議介面。"""

    @property
    def recovery_identifier(self) -> str:
        """模組唯一識別標籤，例如 'music_player', 'in_flight_ai_tasks'。"""
        ...

    @property
    def default_ttl_seconds(self) -> float:
        """此模組快照的預設存活時效 (TTL)。"""
        ...

    async def capture_recovery_snapshot(self) -> dict[str, Any] | None:
        """收集當前即時狀態。若當前無活躍工作，回傳 None 略過存檔。"""
        ...

    async def restore_from_snapshot(self, snapshot_data: dict[str, Any], metadata: SnapshotMetadata) -> None:
        """開機自癒時接收快照並執行恢復邏輯。"""
        ...
