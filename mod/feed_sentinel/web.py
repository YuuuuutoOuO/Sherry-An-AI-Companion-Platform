"""
mod.feed_sentinel.web
=====================
Quart Blueprint 與 Web REST API 路由，為 AI 社群情報哨兵提供 Web UI 服務。
包含歷史推播文章檢閱、多來源篩選、即時統計與手動觸發巡邏功能。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from quart import Blueprint, Response, jsonify, request
from core.error_report import safe_error_payload

from .repository import SentinelRepository

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

feed_sentinel_bp = Blueprint(
    "feed_sentinel",
    __name__,
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_bot_instance: Any = None


def set_bot_instance(bot: Any) -> None:
    """由 Web UI 伺服器啟動時自動注入 Discord Bot 實例。"""
    global _bot_instance
    _bot_instance = bot


@feed_sentinel_bp.route("/view", methods=["GET"])
async def get_view() -> Response | tuple[Response, int] | Any:
    """回傳情報哨兵的獨立 HTML 視圖片段。"""
    html_file = _TEMPLATE_DIR / "feed_sentinel.html"
    if html_file.exists():
        content = html_file.read_text(encoding="utf-8")
        return Response(content, mimetype="text/html")
    return Response(
        "<div class='view hidden' id='view-feed-sentinel'><h3>📡 情報哨兵視圖遺失</h3></div>",
        mimetype="text/html",
    )


@feed_sentinel_bp.route("/api/feed_sentinel/posts", methods=["GET"])
@feed_sentinel_bp.route("/posts", methods=["GET"])
async def get_shared_posts() -> Response | tuple[Response, int] | Any:
    """取得已推播的文章列表 (支援分頁、來源篩選與全文檢索)。"""
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(100, max(1, int(request.args.get("limit", 20))))
        source = str(request.args.get("source", "")).strip()
        query = str(request.args.get("q", "")).strip()

        offset = (page - 1) * limit
        repo = SentinelRepository()
        items, total = repo.list_posts(limit=limit, offset=offset, source=source, query=query)

        total_pages = max(1, (total + limit - 1) // limit) if total > 0 else 1

        return jsonify({
            "status": "success",
            "posts": [p.to_dict() for p in items],
            "total": total,
            "page": page,
            "page_size": limit,
            "total_pages": total_pages,
        })
    except Exception as exc:
        payload = safe_error_payload("mod.feed_sentinel.web", exc)
        payload.update({"posts": [], "total": 0})
        return jsonify(payload), 500


@feed_sentinel_bp.route("/api/feed_sentinel/stats", methods=["GET"])
@feed_sentinel_bp.route("/stats", methods=["GET"])
async def get_sentinel_stats() -> Response | tuple[Response, int] | Any:
    """取得情報哨兵之匯總統計數據與 Cog 運行狀態。"""
    try:
        repo = SentinelRepository()
        stats = repo.get_stats()

        sentinel_info: dict[str, Any] = {
            "attached": False,
            "enabled": True,
            "is_processing": False,
            "in_schedule_window": True,
        }

        cog = None
        if _bot_instance is not None:
            cog = getattr(_bot_instance, "get_cog", lambda name: None)("FeedSentinelCog")

        if cog is not None and hasattr(cog, "get_status_info"):
            sentinel_info = cog.get_status_info()
            sentinel_info["attached"] = True

        return jsonify({
            "status": "success",
            "stats": stats,
            "sentinel": sentinel_info,
        })
    except Exception as exc:
        return jsonify(safe_error_payload("mod.feed_sentinel.web", exc)), 500


@feed_sentinel_bp.route("/api/feed_sentinel/trigger", methods=["POST"])
@feed_sentinel_bp.route("/trigger", methods=["POST"])
async def trigger_sentinel_patrol() -> tuple[Response, int] | Response:
    """從 Web UI 手動觸發一次社群情報巡邏與 Discord 推播。"""
    try:
        cog = None
        if _bot_instance is not None:
            cog = getattr(_bot_instance, "get_cog", lambda name: None)("FeedSentinelCog")

        if cog is None:
            return jsonify({
                "status": "error",
                "message": "情報哨兵 Cog 尚未掛載至 Bot 實例，無法執行即時推播。",
            }), 503

        if cog._is_processing:
            return jsonify({
                "status": "warning",
                "message": "情報哨兵目前正在執行抓取與 AI 推理中，請稍候再試。",
            }), 429

        # 背景執行抓取
        success = await cog._process_feed_and_post(force=True)
        if success:
            return jsonify({
                "status": "success",
                "message": "🎉 已成功抓取最新情報並推播至 Discord！",
            })
        else:
            return jsonify({
                "status": "info",
                "message": "已完成抓取巡邏，但目前所有情報均已推播過或目標頻道未就緒。",
            })
    except Exception as exc:
        return jsonify(safe_error_payload("mod.feed_sentinel.web", exc)), 500


@feed_sentinel_bp.route("/api/feed_sentinel/posts/<path:post_id>", methods=["DELETE"])
@feed_sentinel_bp.route("/posts/<path:post_id>", methods=["DELETE"])
async def delete_shared_post(post_id: str) -> Response | tuple[Response, int] | Any:
    """刪除指定文章封存記錄。"""
    try:
        repo = SentinelRepository()
        success = repo.delete_post(post_id)
        if success:
            return jsonify({"status": "success", "message": f"已刪除文章紀錄: {post_id}"})
        return jsonify({"status": "error", "message": f"找不到文章識別碼: {post_id}"}), 404
    except Exception as exc:
        return jsonify(safe_error_payload("mod.feed_sentinel.web", exc)), 500
