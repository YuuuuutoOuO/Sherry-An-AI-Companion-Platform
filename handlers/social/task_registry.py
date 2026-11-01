"""
handlers/social/task_registry.py
================================
AI 推演中斷未結案任務即時登記簿 (InFlightTaskRegistry)。
用於追蹤使用者 @雪莉 推演階段的訊息與任務狀態，配合快照提供者進行開機自癒。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import time
from typing import Any

import settings

logger = logging.getLogger(__name__)


@dataclass
class InFlightTask:
    """未結案 AI 推演任務資料結構。"""

    task_id: str
    channel_id: int
    message_id: int
    prompt: str
    user_id: int = 0
    start_time: float = field(default_factory=time.time)
    task_type: str = "chat"
    origin_message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "prompt": self.prompt,
            "user_id": self.user_id,
            "start_time": self.start_time,
            "task_type": self.task_type,
            "origin_message_id": self.origin_message_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InFlightTask:
        return cls(
            task_id=str(data["task_id"]),
            channel_id=int(data["channel_id"]),
            message_id=int(data["message_id"]),
            prompt=str(data.get("prompt", "")),
            user_id=int(data.get("user_id", 0)),
            start_time=float(data.get("start_time", time.time())),
            task_type=str(data.get("task_type", "chat")),
            origin_message_id=int(data["origin_message_id"]) if data.get("origin_message_id") is not None else None,
        )


class InFlightTaskRegistry:
    """即時活躍 AI 任務登記簿。"""

    def __init__(self) -> None:
        self._tasks: dict[str, InFlightTask] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def register(
        self,
        task_id: str,
        channel_id: int,
        message_id: int,
        prompt: str,
        user_id: int = 0,
        task_type: str = "chat",
        origin_message_id: int | None = None,
    ) -> InFlightTask:
        """登記新的未結案 AI 任務並觸發快照保存。"""
        task = InFlightTask(
            task_id=task_id,
            channel_id=channel_id,
            message_id=message_id,
            prompt=prompt,
            user_id=user_id,
            start_time=time.time(),
            task_type=task_type,
            origin_message_id=origin_message_id,
        )
        self._tasks[task_id] = task
        self._trigger_snapshot()
        logger.debug("[InFlightTaskRegistry] 已登記活躍任務: %s (msg: %s)", task_id, message_id)
        return task

    def unregister(self, task_id: str) -> InFlightTask | None:
        """結案並自登記簿移除任務，同時觸發快照同步。"""
        task = self._tasks.pop(task_id, None)
        if task is not None:
            self._trigger_snapshot()
            logger.debug("[InFlightTaskRegistry] 已結案並註銷任務: %s", task_id)
        return task

    def get(self, task_id: str) -> InFlightTask | None:
        return self._tasks.get(task_id)

    def list_all(self) -> list[InFlightTask]:
        return list(self._tasks.values())

    def clear(self) -> None:
        self._tasks.clear()
        self._trigger_snapshot()

    def _trigger_snapshot(self) -> None:
        if getattr(settings, "STATE_RECOVERY_ENABLED", True):
            try:
                from core.recovery.manager import get_recovery_manager

                manager = get_recovery_manager()
                loop = None
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                if loop and loop.is_running():
                    task = loop.create_task(
                        manager.capture_provider("in_flight_ai_tasks"),
                        name="capture-provider-in_flight_ai_tasks",
                    )
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
            except Exception:
                pass


_GLOBAL_REGISTRY: InFlightTaskRegistry | None = None


def get_in_flight_registry() -> InFlightTaskRegistry:
    """取得或初始化全域 InFlightTaskRegistry 單例。"""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = InFlightTaskRegistry()
    return _GLOBAL_REGISTRY
