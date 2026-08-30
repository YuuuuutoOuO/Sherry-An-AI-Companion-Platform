"""
core/state.py
=============
雪莉即時心智狀態機 (SherryMindState)。

100% 駐留於記憶體中，提供 O(1) 零開銷的心智狀態追蹤、
自我感知快照 (MindSnapshot) 與健康狀態維護。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Literal

from core.contracts import MindSnapshot


class SherryMindState:
    """雪莉即時心智狀態機。"""

    def __init__(self) -> None:
        self._start_time: float = time.time()
        self._active_tasks: int = 0
        self._recent_actions: deque[str] = deque(maxlen=5)
        self._mood: str = "元氣敏銳"
        self._health_score: float = 1.0
        self._total_requests: int = 0
        self._failed_requests: int = 0

    def record_action_start(self, action_name: str) -> None:
        """記錄新動作/請求開始。

        Args:
            action_name: 動作描述（如 '對話請求', '繪圖中'）。
        """
        self._active_tasks += 1
        self._total_requests += 1
        ts_str = time.strftime("%H:%M:%S", time.localtime())
        self._recent_actions.append(f"[{ts_str}] 執行: {action_name}")

    def record_action_finish(self, action_name: str, success: bool = True) -> None:
        """記錄動作完成或失敗。

        Args:
            action_name: 動作名稱。
            success: 是否成功完成。
        """
        if self._active_tasks > 0:
            self._active_tasks -= 1
        if not success:
            self._failed_requests += 1
            self._health_score = max(0.2, self._health_score - 0.05)
        else:
            # 每次成功緩步回升健康分
            self._health_score = min(1.0, self._health_score + 0.01)

    def update_mood(self, mood: str) -> None:
        """更新當前情緒氛圍。

        Args:
            mood: 新的情緒描述（如 '沉思認真', '活潑調皮'）。
        """
        self._mood = mood

    def get_snapshot(self) -> MindSnapshot:
        """獲取當前記憶體心智快照。

        Returns:
            MindSnapshot: 唯讀之即時狀態快照。
        """
        now = time.time()
        uptime = int(now - self._start_time)

        status: Literal["idle", "thinking", "generating_art", "busy"] = "idle"
        if self._active_tasks > 0:
            if any("繪圖" in a or "DRAW" in a for a in self._recent_actions):
                status = "generating_art"
            elif self._active_tasks > 2:
                status = "busy"
            else:
                status = "thinking"

        return MindSnapshot(
            status=status,
            current_task_count=self._active_tasks,
            recent_actions=list(self._recent_actions),
            current_mood=self._mood,
            system_health_score=round(self._health_score, 2),
            uptime_seconds=uptime,
        )
