"""
mod.deep_search.web
===================
Quart Blueprint and Web routes for Sherry Deep Search Mod.
Provides endpoints for research session history, live thought streaming,
full Markdown report retrieval, and trigger/cancel controls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quart import Blueprint, Response, jsonify, request

from mod.deep_search import config

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

deep_search_bp = Blueprint(
    "deep_search",
    __name__,
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_bot_instance: Any = None


def set_bot_instance(bot: Any) -> None:
    """Set Discord Bot instance for Cog task linking."""
    global _bot_instance
    _bot_instance = bot


@deep_search_bp.route("/view", methods=["GET"])
async def get_view() -> Response | tuple[Response, int] | Any:
    """Return the HTML snippet for Deep Search view."""
    html_file = _TEMPLATE_DIR / "deep_search.html"
    if html_file.exists():
        content = html_file.read_text(encoding="utf-8")
        return Response(content, mimetype="text/html")
    return Response("<div class='view hidden' id='view-deep-search'><h3>深度研究視圖遺失</h3></div>", mimetype="text/html")


@deep_search_bp.route("/api/deep_search/sessions", methods=["GET"])
@deep_search_bp.route("/sessions", methods=["GET"])
async def get_deep_search_sessions() -> Response | tuple[Response, int] | Any:
    """取得所有深度研究專案歷程列表。"""
    try:
        from mod.deep_search.repositories.session_repository import FileSessionRepository

        repo = FileSessionRepository()
        sessions = repo.list_sessions(limit=50)
        return jsonify({
            "status": "success",
            "sessions": [s.to_dict() for s in sessions],
        })
    except Exception as exc:
        logger.exception("[deep_search] 獲取深度研究清單失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc), "sessions": []}), 500


@deep_search_bp.route("/api/deep_search/session/<session_id>", methods=["GET"])
@deep_search_bp.route("/session/<session_id>", methods=["GET"])
async def get_deep_search_session_detail(session_id: str) -> tuple[Response, int] | Response:
    """取得特定研究專案之完整細節 (思考日誌、文獻與報告)。"""
    try:
        from mod.deep_search.repositories.session_repository import FileSessionRepository

        repo = FileSessionRepository()
        session = repo.load_session(session_id)
        if not session:
            return jsonify({"status": "error", "message": f"找不到識別碼為 {session_id} 的研究專案"}), 404
        return jsonify({
            "status": "success",
            "session": session.to_dict(),
        })
    except Exception as exc:
        logger.exception("[deep_search] 獲取深度研究詳情失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@deep_search_bp.route("/api/deep_search/trigger", methods=["POST"])
@deep_search_bp.route("/trigger", methods=["POST"])
async def trigger_deep_search_task() -> tuple[Response, int] | Response:
    """從 Web UI 發起一項新的深度研究任務。"""
    try:
        data: dict[str, Any] = await request.get_json() or {}
        topic = str(data.get("topic", "")).strip()
        if not topic:
            return jsonify({"status": "error", "message": "請輸入有效的研究主題！"}), 400

        model = str(data.get("model", "")).strip() or config.get_default_model()
        rounds = int(data.get("rounds", 4))
        session_id = f"rs-{int(time.time())}"

        cog = getattr(_bot_instance, "_deep_search_cog", None) if _bot_instance else None
        if cog is not None and hasattr(cog, "_run_research_flow"):
            task = asyncio.create_task(
                cog._run_research_flow(
                    topic=topic,
                    model=model,
                    rounds=rounds,
                    session_id=session_id,
                ),
                name=f"deep-search-web-{session_id}",
            )
            cog._active_tasks[session_id] = task
        else:
            # 備援：獨立啟動引擎推演
            from mod.deep_search.engine.deep_search_engine import DeepSearchEngine
            from mod.deep_search.repositories.llm_client import OllamaResearchClient
            from mod.deep_search.repositories.scraper_client import TrafilaturaScraperClient
            from mod.deep_search.repositories.search_client import DuckDuckGoSearchClient
            from mod.deep_search.repositories.session_repository import FileSessionRepository

            engine = DeepSearchEngine(
                search_client=DuckDuckGoSearchClient(),
                scraper_client=TrafilaturaScraperClient(),
                llm_client=OllamaResearchClient(),
                session_repository=FileSessionRepository(),
            )
            asyncio.create_task(
                engine.run_research(
                    topic=topic,
                    model=model,
                    max_rounds=rounds,
                    session_id=session_id,
                ),
                name=f"deep-search-web-standalone-{session_id}",
            )

        return jsonify({
            "status": "success",
            "message": "深度研究任務已成功啟動！",
            "session_id": session_id,
        })
    except Exception as exc:
        logger.exception("[deep_search] 手動發起深度研究失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@deep_search_bp.route("/api/deep_search/cancel/<session_id>", methods=["POST"])
@deep_search_bp.route("/cancel/<session_id>", methods=["POST"])
@deep_search_bp.route("/session/<session_id>/cancel", methods=["POST"])
async def cancel_deep_search_task(session_id: str) -> Response | tuple[Response, int] | Any:
    """手動終止正在執行中的深度研究任務。"""
    try:
        cog = getattr(_bot_instance, "_deep_search_cog", None) if _bot_instance else None
        if cog and hasattr(cog, "_active_tasks") and session_id in cog._active_tasks:
            task = cog._active_tasks.pop(session_id)
            if not task.done():
                task.cancel()

        from mod.deep_search.repositories.session_repository import FileSessionRepository

        repo = FileSessionRepository()
        session = repo.load_session(session_id)
        if session and session.status == "RUNNING":
            d = session.to_dict()
            d["status"] = "CANCELLED"
            d["current_step"] = "使用者手動取消研究任務"
            d["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            from mod.deep_search.domain.models import ResearchSession
            repo.save_session(ResearchSession.from_dict(d))

        return jsonify({"status": "success", "message": f"已取消研究任務 {session_id}"})
    except Exception as exc:
        logger.exception("[deep_search] 取消深度研究任務失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@deep_search_bp.route("/api/deep_search/session/<session_id>", methods=["DELETE"])
@deep_search_bp.route("/session/<session_id>", methods=["DELETE"])
async def delete_deep_search_session(session_id: str) -> Response | tuple[Response, int] | Any:
    """刪除指定的研究專案紀錄。"""
    try:
        from mod.deep_search.repositories.session_repository import FileSessionRepository

        repo = FileSessionRepository()
        success = repo.delete_session(session_id)
        return jsonify({"status": "success" if success else "not_found"})
    except Exception as exc:
        logger.exception("[deep_search] 刪除研究專案失敗: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
