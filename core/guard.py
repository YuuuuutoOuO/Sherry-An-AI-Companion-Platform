"""
core/guard.py
=============
雪莉微核心安全與顯存守衛 (CoreGuard)。

功能：
1. 頻率限制 (Rate Limiting) 檢查。
2. GPU 顯存資源守衛 (VRAM Lock Coordination)。
3. 死循環與異常熔斷偵測 (Infinite Loop Circuit Breaker)。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

import settings

logger = logging.getLogger(__name__)


class CoreGuard:
    """微核心安全防護與資源協調守衛。"""

    def __init__(self) -> None:
        self._user_call_history: dict[int, list[float]] = defaultdict(list)
        self._tool_history: dict[int, list[tuple[str, str]]] = defaultdict(list)
        self._gpu_lock: asyncio.Lock = asyncio.Lock()

    def check_rate_limit(self, user_id: int, max_calls: int = 5, window_sec: float = 10.0) -> tuple[bool, int]:
        """檢查特定使用者是否觸發頻率限制。

        Args:
            user_id: Discord User ID。
            max_calls: 時間窗口內允許之最大請求數。
            window_sec: 時間窗口秒數。

        Returns:
            tuple[bool, int]: (是否允許通過, 需冷卻之秒數)。
        """
        now = time.monotonic()
        history = self._user_call_history[user_id]

        # 清除過期的時間戳記
        self._user_call_history[user_id] = [t for t in history if now - t < window_sec]
        history = self._user_call_history[user_id]

        if len(history) >= max_calls:
            oldest = history[0]
            cooldown = int(window_sec - (now - oldest)) + 1
            return False, max(1, cooldown)

        self._user_call_history[user_id].append(now)
        return True, 0

    def check_tool_loop(self, user_id: int, tool_name: str, tool_input: str, max_consecutive: int = 3) -> bool:
        """偵測 Agent 是否陷入調用相同工具與相同參數之死循環。

        Args:
            user_id: 使用者 ID。
            tool_name: 當前調用工具名。
            tool_input: 工具輸入字串。
            max_consecutive: 觸發熔斷之連續重複次數上限。

        Returns:
            bool: True 表示偵測到死循環（需熔斷），False 表示正常。
        """
        history = self._tool_history[user_id]
        history.append((tool_name.upper(), tool_input.strip()))
        if len(history) > 10:
            self._tool_history[user_id] = history[-10:]

        recent = self._tool_history[user_id][-max_consecutive:]
        if len(recent) == max_consecutive:
            # 檢查最近 N 次是否完全一模一樣
            if all(item == recent[0] for item in recent):
                logger.warning(
                    "CoreGuard: 偵測到死循環熔斷! User %d 工具 [%s] 連續調用 %d 次相同參數",
                    user_id,
                    tool_name,
                    max_consecutive,
                )
                return True

        return False

    def clear_user_history(self, user_id: int) -> None:
        """清除使用者的工具歷史快取。"""
        self._tool_history.pop(user_id, None)
