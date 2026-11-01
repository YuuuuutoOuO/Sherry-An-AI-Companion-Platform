"""
core/compat.py
==============
Compatibility layer across Python versions (3.10 through 3.12+).
Provides standard async timeout shims and cross-version helpers.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any


class _TimeoutContextManager:
    """Async context manager replicating Python 3.11+ asyncio.timeout."""

    def __init__(self, delay: float | None) -> None:
        self._delay = float(delay) if delay is not None and float(delay) > 0 else None
        self._task: asyncio.Task[Any] | None = None
        self._handle: asyncio.TimerHandle | None = None
        self._timed_out = False

    async def __aenter__(self) -> _TimeoutContextManager:
        if self._delay is None:
            return self
        loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        if self._task is not None:
            self._handle = loop.call_later(self._delay, self._on_timeout)
        return self

    def _on_timeout(self) -> None:
        self._timed_out = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool | None:
        if self._handle is not None:
            self._handle.cancel()
        if self._timed_out:
            # Task was cancelled by our timeout timer -> convert to TimeoutError
            raise asyncio.TimeoutError() from None
        return None

    def expired(self) -> bool:
        return self._timed_out


_NATIVE_ASYNCIO_TIMEOUT = getattr(asyncio, "timeout", None)


def async_timeout(delay: float | None) -> Any:
    """Cross-version async timeout context manager.

    Uses native `asyncio.timeout` on Python 3.11+,
    or uses async_timeout library if present, or internal timer shim on Python <= 3.10.
    """
    if _NATIVE_ASYNCIO_TIMEOUT is not None:
        return _NATIVE_ASYNCIO_TIMEOUT(delay)
    try:
        import async_timeout  # type: ignore[import-untyped]
        return async_timeout.timeout(delay)
    except ImportError:
        return _TimeoutContextManager(delay)


# Auto-patch asyncio module if running on Python < 3.11 so that `asyncio.timeout(...)`
# works seamlessly across all callers without code changes.
if _NATIVE_ASYNCIO_TIMEOUT is None:
    setattr(asyncio, "timeout", async_timeout)
