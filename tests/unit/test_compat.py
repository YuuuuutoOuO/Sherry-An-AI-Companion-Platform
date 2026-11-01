"""
tests/unit/test_compat.py
=========================
Unit tests for Python cross-version compatibility layer (Python 3.10 to 3.12+).
"""

from __future__ import annotations

import asyncio
import pytest

import core.compat  # installs shim if on Python < 3.11
from core.compat import async_timeout


@pytest.mark.asyncio
async def test_async_timeout_shim_exists_on_asyncio() -> None:
    """確認 asyncio 模組具備 timeout 屬性（在 3.10 上已被 shim 補齊）。"""
    assert hasattr(asyncio, "timeout")


@pytest.mark.asyncio
async def test_async_timeout_success() -> None:
    """在時限內完成之協程應順利返回。"""
    async with async_timeout(1.0):
        await asyncio.sleep(0.01)
        res = 42
    assert res == 42


@pytest.mark.asyncio
async def test_async_timeout_none() -> None:
    """deadline 為 None 時不設限並正常執行。"""
    async with async_timeout(None):
        await asyncio.sleep(0.01)
        res = "ok"
    assert res == "ok"


@pytest.mark.asyncio
async def test_async_timeout_expired() -> None:
    """逾時應拋出 asyncio.TimeoutError。"""
    with pytest.raises(asyncio.TimeoutError):
        async with async_timeout(0.05):
            await asyncio.sleep(0.5)


@pytest.mark.asyncio
async def test_async_timeout_shim_no_recursion(monkeypatch: pytest.MonkeyPatch) -> None:
    """模擬 Python 3.10 無原生 timeout 時，呼叫 async_timeout 與 asyncio.timeout 不應遞迴崩潰。"""
    import core.compat
    monkeypatch.setattr(core.compat, "_NATIVE_ASYNCIO_TIMEOUT", None)
    # 呼叫 async_timeout
    async with core.compat.async_timeout(0.5):
        await asyncio.sleep(0.01)

    # 透過 asyncio.timeout 呼叫
    async with asyncio.timeout(0.5):
        await asyncio.sleep(0.01)
