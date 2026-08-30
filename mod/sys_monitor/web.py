"""
mod.sys_monitor.web
===================
Quart Blueprint 與 Web REST API 路由，為系統資源監控提供 Web UI 服務。
包含即時狀態、歷史趨勢與資料檔案讀取。

GET 請求 (狀態/歷史) 不需管理員授權，僅需登入即可查詢。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from quart import Blueprint, Response, jsonify, request

from . import config

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

sys_monitor_bp = Blueprint(
    "sys_monitor",
    __name__,
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_bot_instance: Any = None


def set_bot_instance(bot: Any) -> None:
    """由 Web UI 伺服器啟動時自動注入 Discord Bot 實例。"""
    global _bot_instance
    _bot_instance = bot


def _get_cog() -> Any:
    """取得已掛載的 SysMonitorCog，若不存在回傳 None。"""
    if _bot_instance is not None:
        cog = getattr(_bot_instance, "get_cog", lambda name: None)("SysMonitorCog")
        if cog is not None:
            return cog
    return None


@sys_monitor_bp.route("/view", methods=["GET"])
async def get_view() -> Response:
    """回傳系統資源監控的獨立 HTML 視圖片段。"""
    html_file = _TEMPLATE_DIR / "sys_monitor.html"
    if html_file.exists():
        content = html_file.read_text(encoding="utf-8")
        return Response(content, mimetype="text/html")
    return Response(
        "<div class='view hidden' id='view-sys-monitor'><h3>🖥️ 系統資源監控視圖遺失</h3></div>",
        mimetype="text/html",
    )


@sys_monitor_bp.route("/api/sys_monitor/status", methods=["GET"])
@sys_monitor_bp.route("/status", methods=["GET"])
async def get_status() -> Response | tuple[Response, int]:
    """取得監控器狀態與即時資源摘要。"""
    try:
        cog = _get_cog()
        status_info = cog.get_status_info() if cog is not None else {"enabled": config.ENABLED}
        summary = cog.get_status_summary() if cog is not None else {}
        return jsonify(
            {
                "status": "success",
                "attached": cog is not None,
                "monitor": status_info,
                "summary": summary,
            }
        )
    except Exception as exc:
        logger.exception("[SysMonitor Web] 獲取狀態失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@sys_monitor_bp.route("/api/sys_monitor/latest", methods=["GET"])
@sys_monitor_bp.route("/latest", methods=["GET"])
async def get_latest() -> Response | tuple[Response, int]:
    """取得最近一次完整採樣結果 (含 GPU)。"""
    try:
        cog = _get_cog()
        latest = cog.get_latest() if cog is not None else None
        if latest is None:
            return jsonify({"status": "info", "message": "尚未有採樣資料", "data": None}), 200
        return jsonify({"status": "success", "data": latest})
    except Exception as exc:
        logger.exception("[SysMonitor Web] 獲取最新採樣失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@sys_monitor_bp.route("/api/sys_monitor/history", methods=["GET"])
@sys_monitor_bp.route("/history", methods=["GET"])
async def get_history() -> Response | tuple[Response, int]:
    """取得歷史趨勢資料 (支援 limit 參數)。"""
    try:
        limit = int(request.args.get("limit", 120))
        cog = _get_cog()
        if cog is not None:
            rows = cog.get_history(limit=limit)
        else:
            # Cog 未掛載時改由檔案直接讀取
            file_rows = []
            if config.SERIES_PATH.exists():
                import json as _json

                with open(config.SERIES_PATH, encoding=config.ENCODING) as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                file_rows.append(_json.loads(line))
                            except Exception:
                                continue
            rows = file_rows[-max(1, limit) :]
        return jsonify({"status": "success", "rows": rows, "count": len(rows)})
    except Exception as exc:
        logger.exception("[SysMonitor Web] 獲取歷史失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
