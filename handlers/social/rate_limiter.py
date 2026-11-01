from __future__ import annotations

import logging
import time
from collections.abc import MutableMapping
from typing import Any

import settings
from core.rate_limit import consume_window, prune_window, remaining_cooldown
from core.state_store import RateLimitStore

logger = logging.getLogger(__name__)


def _build_store() -> MutableMapping[Any, list[float]]:
    try:
        return RateLimitStore("social_rate_limit")
    except Exception as exc:  # pragma: no cover - 磁碟不可用時降級
        logger.warning("[RateLimiter] SQLite 狀態儲存不可用，降級為記憶體：%s", exc)
        return {}


class RateLimiter:
    """使用者請求頻率限制器（SQLite 滑動窗口；與 CoreGuard 共用演算法）。"""

    def __init__(self, store: MutableMapping[Any, list[float]] | None = None) -> None:
        self._user_timestamps = store if store is not None else _build_store()

    def is_allowed(self, uid: int) -> bool:
        allowed, _ = consume_window(
            self._user_timestamps,
            uid,
            max_calls=settings.RATE_LIMIT_MAX_REQUESTS,
            window_sec=settings.RATE_LIMIT_WINDOW_SECONDS,
            now=time.time(),
        )
        return allowed

    def get_remaining_cooldown(self, uid: int) -> float:
        return remaining_cooldown(
            self._user_timestamps,
            uid,
            window_sec=settings.RATE_LIMIT_WINDOW_SECONDS,
            now=time.time(),
        )

    def cleanup_expired(self, uid: int) -> None:
        prune_window(
            self._user_timestamps,
            uid,
            window_sec=settings.RATE_LIMIT_WINDOW_SECONDS,
            now=time.time(),
        )


rate_limiter = RateLimiter()
