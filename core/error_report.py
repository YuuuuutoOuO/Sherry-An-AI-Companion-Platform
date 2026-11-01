"""
core/error_report.py
====================
統一「錯誤代碼化」工具：伺服器端保留完整例外細節，對外回應僅附 error_id。

R2（報告 §5.3.6）：禁止 `str(exc)` 直送 API／Discord 回應，改以短代碼對照伺服器日誌，
讓使用者回報 error_id、管理員查詢日誌即可定位，同時避免路徑／堆疊／SQL 片段外洩。

使用模式：
    from core.error_report import log_safe_error, safe_error_payload

    except Exception as exc:
        return jsonify(safe_error_payload("web_ui.models.ollama", exc)), 500
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_USER_MESSAGE = "伺服器內部錯誤，請稍後再試（可將錯誤代碼提供給管理員查詢日誌）。"


def new_error_id(prefix: str = "ERR") -> str:
    """產生短錯誤代碼（err_id），供外部回應與伺服器日誌對照使用。

    Args:
        prefix: 代碼前綴，建議使用功能區域名（如 "web_ui"）。

    Returns:
        str: 形如 "ERR-1a2b3c4d" 的錯誤代碼。
    """
    return f"{prefix}-{secrets.token_hex(4)}"


def log_safe_error(
    context: str,
    exc: BaseException,
    *,
    level: int = logging.ERROR,
    error_id: str | None = None,
) -> str:
    """將完整例外（含堆疊）寫入伺服器日誌，回傳對應之 error_id。

    Args:
        context: 事件來源描述（如 "web_ui.models.ollama"），寫入日誌便於檢索。
        exc: 捕獲的例外物件（完整細節只留在日誌，不回傳給呼叫端呈現在回應中）。
        level: 日誌等級，預設 logging.ERROR。
        error_id: 既有的 error_id（重複使用時傳入，否則自動產生）。

    Returns:
        str: 本次事件對應的 error_id。
    """
    eid = error_id or new_error_id()
    logger.log(level, "[%s] %s: %s", eid, context, exc, exc_info=True)
    return eid


def safe_error_payload(
    context: str,
    exc: BaseException,
    *,
    message: str = DEFAULT_USER_MESSAGE,
    status: str = "error",
    level: int = logging.ERROR,
) -> dict[str, Any]:
    """組合標準化錯誤回應字典：{"status", "message", "error_id"}，不含例外字串。

    Args:
        context: 事件來源描述（寫入日誌用）。
        exc: 捕獲的例外物件。
        message: 對外顯示的安全訊息（可依端點客製）。
        status: 回應狀態字串，預設 "error"。
        level: 日誌等級，預設 logging.ERROR。

    Returns:
        dict: 可直接丟給 jsonify 的安全字典。
    """
    eid = log_safe_error(context, exc, level=level)
    return {"status": status, "message": message, "error_id": eid}
