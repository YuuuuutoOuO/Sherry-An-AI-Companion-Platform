"""共用的滑動窗口限流核心（R17）。

``core.guard`` 與 ``handlers.social.rate_limiter`` 皆委派此處，
避免兩套限流器語意漂移。
"""

from __future__ import annotations

import time
from collections.abc import MutableMapping
from typing import Any


def prune_window(
    store: MutableMapping[Any, list[float]],
    key: Any,
    window_sec: float,
    now: float | None = None,
) -> list[float]:
    """回傳窗口內時間戳並同步清除過期資料（空則移除 key）。"""

    now = time.monotonic() if now is None else now
    history = [float(ts) for ts in store.get(key, []) if now - float(ts) < window_sec]
    if history:
        store[key] = history
    else:
        store.pop(key, None)
    return history


def consume_window(
    store: MutableMapping[Any, list[float]],
    key: Any,
    *,
    max_calls: int,
    window_sec: float,
    now: float | None = None,
) -> tuple[bool, int]:
    """嘗試消耗一次額度；回傳（是否允許, 需等待秒數）。"""

    now = time.monotonic() if now is None else now
    history = prune_window(store, key, window_sec, now)
    if len(history) >= max(1, int(max_calls)):
        oldest = history[0]
        cooldown = int(window_sec - (now - oldest)) + 1
        return False, max(1, cooldown)
    history.append(now)
    store[key] = history
    return True, 0


def remaining_cooldown(
    store: MutableMapping[Any, list[float]],
    key: Any,
    *,
    window_sec: float,
    now: float | None = None,
) -> float:
    now = time.monotonic() if now is None else now
    history = prune_window(store, key, window_sec, now)
    if not history:
        return 0.0
    return max(0.0, history[0] + window_sec - now)
