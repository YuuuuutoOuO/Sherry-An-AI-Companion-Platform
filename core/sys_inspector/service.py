"""core.sys_inspector.service
=============================
微核心系統監控統一服務門面。
整合 sampler、formatter 與快取控制，提供乾淨的呼叫端點。
"""

from __future__ import annotations

from typing import Any

from .formatter import format_compact_system_report
from .sampler import get_system_metrics, get_system_metrics_async


class SysInspectorService:
    """微核心系統監控服務門面。"""

    @classmethod
    def get_metrics_sync(cls) -> dict[str, Any]:
        """同步取得即時系統指標字典。"""
        return get_system_metrics()

    @classmethod
    async def get_metrics_async(cls, ttl: float = 2.0) -> dict[str, Any]:
        """非同步取得系統指標字典（具備防抖快取）。"""
        return await get_system_metrics_async(ttl=ttl)

    @classmethod
    async def get_compact_report(cls, is_admin: bool = False, ttl: float = 2.0) -> str:
        """非同步取得 LLM 友善之高密度純文字系統快報。"""
        metrics = await get_system_metrics_async(ttl=ttl)
        return format_compact_system_report(metrics, is_admin=is_admin)
