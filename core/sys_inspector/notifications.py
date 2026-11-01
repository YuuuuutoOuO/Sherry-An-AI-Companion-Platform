"""core.sys_inspector.notifications
====================================
全系統統一非同步與跨執行緒稽核告警通知接口。

取代原 mod/self_monitoring 暴露之 notify_slow_response_sync 與 notify_failure_sync，
供 handlers/social/observability.py、bot_brain_worker 與其他執行緒無鎖觸發慢速與失敗事件診斷。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from core.sys_inspector.audit_service import (
    DEFAULT_SLOW_THRESHOLD_SEC,
    DEFAULT_TIMEOUT_THRESHOLD_SEC,
    audit_service,
)

logger = logging.getLogger(__name__)

# 全域 Cog / Bot / Service 實例引用（由 SysInspectorCog 啟動時注入）
_GLOBAL_COG: Any = None
_GLOBAL_LOOP: asyncio.AbstractEventLoop | None = None
_GLOBAL_SERVICE: Any = None


def register_cog_instance(cog: Any, loop: asyncio.AbstractEventLoop | None = None) -> None:
    """註冊全域 Cog 實例與事件迴圈。"""
    global _GLOBAL_COG, _GLOBAL_LOOP, _GLOBAL_SERVICE
    _GLOBAL_COG = cog
    _GLOBAL_LOOP = loop
    if cog is not None and hasattr(cog, "service"):
        _GLOBAL_SERVICE = cog.service
    elif cog is None:
        _GLOBAL_SERVICE = None


def _schedule_async_task(coro: Any) -> None:
    """跨執行緒安全排程非同步任務，確保在任何環境下皆能可靠執行。"""
    loop = _GLOBAL_LOOP
    is_closed = True
    try:
        if loop is not None:
            is_closed = bool(loop.is_closed())
    except (AttributeError, RuntimeError, TypeError):
        is_closed = True

    if loop is None or is_closed:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

    if loop is not None:
        try:
            if loop.is_running():
                try:
                    loop.create_task(coro)
                    return
                except RuntimeError:
                    asyncio.run_coroutine_threadsafe(coro, loop)
                    return
        except (AttributeError, RuntimeError, TypeError):
            pass

    threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()


def notify_slow_response_sync(
    latency_ms: int,
    trace_id: str = "",
    channel_id: int | str = 0,
    user_name: str = "",
    raw_prompt: str = "",
) -> None:
    """供外部日誌模組同步觸發慢速回覆通知（若超過 90 秒且未冷卻）。"""
    if latency_ms < int(DEFAULT_SLOW_THRESHOLD_SEC * 1000):
        return

    try:
        ch_id = int(channel_id or 0)
    except (ValueError, TypeError):
        ch_id = 0

    if _GLOBAL_COG is not None and hasattr(_GLOBAL_COG, "handle_slow_response"):
        _schedule_async_task(
            _GLOBAL_COG.handle_slow_response(
                latency_ms=latency_ms,
                trace_id=trace_id,
                channel_id=ch_id,
                user_name=user_name,
                raw_prompt=raw_prompt,
            )
        )
    else:
        service = _GLOBAL_SERVICE if _GLOBAL_SERVICE is not None else audit_service
        incident = service.create_slow_response_incident(
            latency_ms=latency_ms,
            trace_id=trace_id,
            channel_id=ch_id,
            user_name=user_name,
            raw_prompt=raw_prompt,
        )
        if not service.should_suppress_alert(incident.incident_type):
            _schedule_async_task(service.diagnose_incident(incident))


def notify_failure_sync(
    latency_ms: int = 0,
    error_msg: str = "",
    trace_id: str = "",
    channel_id: int | str = 0,
    user_name: str = "",
    raw_prompt: str = "",
    task_id: str = "",
    is_fatal: bool = False,
) -> None:
    """供外部日誌模組同步觸發失敗超時或推論斷線通知。

    當 is_fatal 為 True 或包含斷線/重試放棄等致命關鍵字時，0 秒即刻觸發；
    否則需超過 120 秒失敗門檻。
    """
    err_lower = (error_msg or "").lower()

    # 內容安全過濾是確定性結果：主動放棄是預期行為，不是連線或推論故障，
    # 不應升級為嚴重異常通知（否則每次過濾都會產生噪音告警）。
    is_content_filtered = "content_filter" in err_lower or "content_filtered" in err_lower

    has_fatal_keyword = any(
        kw in err_lower
        for kw in (
            "connectionreset", "cannot connect", "give-up", "transferencoding",
            "connection error", "refused", "unauthorized", "brokenpipe",
            "clientconnectorerror", "inferencegiveup", "fatal"
        )
    )
    is_critical_failure = (is_fatal or has_fatal_keyword) and not is_content_filtered
    if is_content_filtered:
        return
    if not is_critical_failure and latency_ms < int(DEFAULT_TIMEOUT_THRESHOLD_SEC * 1000):
        return

    try:
        ch_id = int(channel_id or 0)
    except (ValueError, TypeError):
        ch_id = 0

    if _GLOBAL_COG is not None and hasattr(_GLOBAL_COG, "handle_failure"):
        _schedule_async_task(
            _GLOBAL_COG.handle_failure(
                latency_ms=latency_ms,
                error_msg=error_msg,
                trace_id=trace_id,
                channel_id=ch_id,
                user_name=user_name,
                raw_prompt=raw_prompt,
                task_id=task_id,
                is_fatal=is_critical_failure,
            )
        )
    else:
        service = _GLOBAL_SERVICE if _GLOBAL_SERVICE is not None else audit_service
        if is_critical_failure and (task_id or has_fatal_keyword or is_fatal):
            incident = service.create_inference_failure_incident(
                latency_ms=latency_ms,
                error_msg=error_msg,
                trace_id=trace_id,
                channel_id=ch_id,
                user_name=user_name,
                raw_prompt=raw_prompt,
                task_id=task_id,
            )
        else:
            incident = service.create_failure_incident(
                latency_ms=latency_ms,
                error_msg=error_msg,
                trace_id=trace_id,
                channel_id=ch_id,
                user_name=user_name,
                raw_prompt=raw_prompt,
            )
        if not service.should_suppress_alert(incident.incident_type):
            _schedule_async_task(service.diagnose_incident(incident))
