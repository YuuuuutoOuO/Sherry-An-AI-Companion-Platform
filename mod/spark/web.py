"""mod/spark/web.py - Spark 推播系統 Web REST API 與 Blueprint。

提供 Web UI 控制台對接端點，支援觸發器管理、手動測試與即時統計。
符合 mod規範 §3（模組完全自治與動態掛載）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from quart import Blueprint, Response, jsonify, request

from .clients import RssClient, StockClient, WeatherClient
from .repository import SparkRepository
from .service import SparkService, build_spark_service

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

spark_bp = Blueprint(
    "spark",
    __name__,
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_bot_instance: Optional[Any] = None
_service_instance: Optional[SparkService] = None


def set_bot_instance(bot: Any) -> None:
    """由 Web UI 伺服器啟動時自動注入 Discord Bot 實例。"""
    global _bot_instance
    _bot_instance = bot


def _get_service() -> SparkService:
    """獲取 SparkService 實例（優先從已掛載的 Cog 獲取，以確保狀態同步）。"""
    global _service_instance
    if _bot_instance is not None:
        cog = getattr(_bot_instance, "get_cog", lambda name: None)("SparkCog")
        if cog is not None and hasattr(cog, "service"):
            return cog.service  # type: ignore[no-any-return]

    if _service_instance is None:
        repo = SparkRepository()
        clients = {
            "stock": StockClient(),
            "rss": RssClient(),
            "weather": WeatherClient(),
        }
        _service_instance = build_spark_service(repo, clients)
    return _service_instance


@spark_bp.route("/view", methods=["GET"])
async def get_view() -> Response | tuple[Response, int] | Any:
    """回傳 Spark 獨立 HTML 視圖片段。"""
    html_file = _TEMPLATE_DIR / "spark.html"
    if html_file.exists():
        content = html_file.read_text(encoding="utf-8")
        return Response(content, mimetype="text/html")
    return Response(
        "<div class='view hidden' id='view-spark'><h3>📡 Spark 視圖片段遺失</h3></div>",
        mimetype="text/html",
    )


@spark_bp.route("/api/spark/triggers", methods=["GET"])
async def list_triggers() -> Response | tuple[Response, int] | Any:
    """取得所有觸發器列表 (支援狀態與類型篩選)。"""
    try:
        service = _get_service()
        trigger_type = request.args.get("type", "").strip()
        is_active_param = request.args.get("is_active")

        is_active: Optional[bool] = None
        if is_active_param is not None:
            is_active = is_active_param.lower() in ("true", "1", "yes")

        triggers = service.list_triggers(is_active=is_active)
        if trigger_type:
            triggers = [t for t in triggers if t.trigger_type == trigger_type]

        return jsonify({
            "status": "success",
            "triggers": [t.to_dict() for t in triggers],
            "total": len(triggers),
        })
    except Exception as exc:
        logger.exception("[Spark Web] 獲取觸發器列表失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc), "triggers": [], "total": 0}), 500


@spark_bp.route("/api/spark/triggers", methods=["POST"])
async def create_trigger() -> Response | tuple[Response, int] | Any:
    """建立新的推播觸發器。"""
    try:
        data: dict[str, Any] = await request.get_json() or {}
        user_id = int(data.get("user_id", 0))
        channel_id = int(data.get("channel_id", 0))
        trigger_type = str(data.get("trigger_type", "")).strip()
        condition = dict(data.get("condition", {}))
        message = str(data.get("message", "")).strip()
        cooldown_seconds = int(data.get("cooldown_seconds", 3600))
        is_active = bool(data.get("is_active", True))

        if not trigger_type:
            return jsonify({"status": "error", "message": "必須指定 trigger_type"}), 400

        service = _get_service()
        trigger = service.create_trigger(
            user_id=user_id,
            channel_id=channel_id,
            trigger_type=trigger_type,
            condition=condition,
            message=message,
            cooldown_seconds=cooldown_seconds,
            is_active=is_active,
        )

        return jsonify({
            "status": "success",
            "trigger": trigger.to_dict(),
            "message": "觸發器建立成功",
        })
    except Exception as exc:
        logger.exception("[Spark Web] 建立觸發器失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@spark_bp.route("/api/spark/triggers/<trigger_id>", methods=["DELETE"])
async def delete_trigger(trigger_id: str) -> Response | tuple[Response, int] | Any:
    """刪除指定觸發器。"""
    try:
        service = _get_service()
        success = service.delete_trigger(trigger_id)
        if success:
            return jsonify({"status": "success", "message": "刪除成功"})
        return jsonify({"status": "error", "message": "找不到該觸發器"}), 404
    except Exception as exc:
        logger.exception("[Spark Web] 刪除觸發器失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@spark_bp.route("/api/spark/triggers/<trigger_id>/toggle", methods=["POST"])
async def toggle_trigger(trigger_id: str) -> Response | tuple[Response, int] | Any:
    """切換觸發器啟用/暫停狀態。"""
    try:
        service = _get_service()
        updated = service.toggle_trigger(trigger_id)
        if updated is not None:
            return jsonify({
                "status": "success",
                "trigger": updated.to_dict(),
                "is_active": updated.is_active,
            })
        return jsonify({"status": "error", "message": "找不到該觸發器"}), 404
    except Exception as exc:
        logger.exception("[Spark Web] 切換觸發器狀態失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@spark_bp.route("/api/spark/triggers/<trigger_id>/test", methods=["POST"])
async def test_trigger(trigger_id: str) -> Response | tuple[Response, int] | Any:
    """手動立即測試觸發器。"""
    try:
        service = _get_service()
        trigger = service.get_trigger(trigger_id)
        if not trigger:
            return jsonify({"status": "error", "message": "找不到該觸發器"}), 404

        result, _ = await service.check_trigger(trigger)
        return jsonify({
            "status": "success",
            "should_fire": result.should_fire,
            "current_value": result.current_value,
            "detail_data": result.detail_data,
        })
    except Exception as exc:
        logger.exception("[Spark Web] 測試觸發器異常: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@spark_bp.route("/api/spark/stats", methods=["GET"])
async def get_stats() -> Response | tuple[Response, int] | Any:
    """取得 Spark 系統統計概況。"""
    try:
        service = _get_service()
        stats = service.get_stats()
        return jsonify({
            "status": "success",
            "stats": stats,
        })
    except Exception as exc:
        logger.exception("[Spark Web] 獲取統計資料失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
