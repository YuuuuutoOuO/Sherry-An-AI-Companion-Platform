"""handlers/care/surfer_server.py
==============================
雪莉上網衝浪實況台專屬 Quart 伺服器（PORT 8765）。

架構對齊 Port 8764 影院實況伺服器（mod/livestream/web.py）：
- 獨立運作於 Port 8765（預設）。
- 支援 SSE (Server-Sent Events) 即時推送雪莉虛擬瀏覽器動作（打開網頁、掃描新聞卡片、內心 OS、挑文發布、寫入自我記憶）。
- 提供同源驗證與可選存取權杖防護。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from quart import Quart, Response, g, jsonify, render_template, request, send_from_directory
import uvicorn

import settings
from core.error_report import safe_error_payload
from .repository import CareRepository, get_care_repository
from .surfer import CareSurfer, SurferBroadcaster, get_surfer_broadcaster

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_GLOBAL_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "web_ui" / "static"

surfer_app: Quart = Quart(
    "sherry_surfer_live",
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_secret_key = os.getenv("CARE_SURFER_SECRET_KEY", "").strip()
if _secret_key:
    surfer_app.secret_key = _secret_key
else:
    surfer_app.secret_key = secrets.token_hex(24)

surfer_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

_ACCESS_COOKIE_NAME = "surfer_access"
_ACCESS_COOKIE_MAX_AGE = 86400  # 24 hours

_bot_instance: Any = None
_server_task: asyncio.Task[None] | None = None


def set_bot_instance(bot: Any) -> None:
    global _bot_instance
    _bot_instance = bot


def get_bot_instance() -> Any:
    global _bot_instance
    if _bot_instance is not None:
        return _bot_instance
    try:
        from handlers.care import web
        if getattr(web, "_bot_instance", None) is not None:
            return web._bot_instance
    except Exception:
        pass
    return None


def get_surfer() -> CareSurfer:
    from .surfer import get_care_surfer
    return get_care_surfer()


def set_surfer(surfer: CareSurfer) -> None:
    from .surfer import set_care_surfer
    set_care_surfer(surfer)


def _sign_access_cookie(timestamp: str) -> str:
    secret = surfer_app.secret_key
    key = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    return hmac.new(key, timestamp.encode("utf-8"), "sha256").hexdigest()


def _is_valid_access_cookie(value: str) -> bool:
    ts, _, sig = (value or "").partition(".")
    if not ts or not sig or not ts.isdigit():
        return False
    if time.time() - int(ts) > _ACCESS_COOKIE_MAX_AGE:
        return False
    return hmac.compare_digest(sig, _sign_access_cookie(ts))


@surfer_app.before_request
async def _guard_surfer_access() -> Any:
    g.surfer_issue_access_cookie = False
    expected = os.getenv("CARE_SURFER_TOKEN", "").strip()
    if not expected:
        return None  # 預設依賴 loopback 邊界

    path = request.path or ""
    if path.startswith("/static/"):
        return None

    expected_bytes = expected.encode("utf-8", "ignore")
    supplied_header = str(request.headers.get("X-Surfer-Token", ""))
    supplied_query = str(request.args.get("token", ""))

    if supplied_header and hmac.compare_digest(supplied_header.encode("utf-8", "ignore"), expected_bytes):
        return None
    if supplied_query and hmac.compare_digest(supplied_query.encode("utf-8", "ignore"), expected_bytes):
        g.surfer_issue_access_cookie = True
        return None

    cookie_val = request.cookies.get(_ACCESS_COOKIE_NAME, "")
    if cookie_val and _is_valid_access_cookie(cookie_val):
        return None

    return jsonify({"status": "error", "message": "未授權存取實況台（請提供有效權杖）"}), 401


@surfer_app.after_request
async def _set_access_cookie(response: Response) -> Response:
    if getattr(g, "surfer_issue_access_cookie", False):
        ts = str(int(time.time()))
        sig = _sign_access_cookie(ts)
        response.set_cookie(
            _ACCESS_COOKIE_NAME,
            f"{ts}.{sig}",
            max_age=_ACCESS_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
    return response


# ---------------------------------------------------------------------------
# 靜態檔案路由
# ---------------------------------------------------------------------------

@surfer_app.route("/static/global/<path:filename>")
async def serve_global_static(filename: str) -> Response | Any:
    return await send_from_directory(str(_GLOBAL_STATIC_DIR), filename)


# ---------------------------------------------------------------------------
# 實況頁面與 API
# ---------------------------------------------------------------------------

@surfer_app.route("/")
async def surfer_index() -> str:
    """渲染雪莉虛擬瀏覽器上網實況台主頁面。"""
    repo = get_care_repository()
    config = await asyncio.to_thread(repo.get_surfer_config)
    broadcaster = get_surfer_broadcaster()
    return await render_template(
        "surfer_live.html",
        config=config.to_dict(),
        snapshot=broadcaster.last_session_snapshot,
    )


@surfer_app.route("/api/surfer/state", methods=["GET"])
async def surfer_state() -> Response | Any:
    """取得當前衝浪狀態快照與最新設定。"""
    repo = get_care_repository()
    config = await asyncio.to_thread(repo.get_surfer_config)
    broadcaster = get_surfer_broadcaster()
    return jsonify({
        "status": "success",
        "snapshot": broadcaster.last_session_snapshot,
        "config": config.to_dict(),
    })


@surfer_app.route("/api/surfer/stream", methods=["GET"])
async def surfer_stream() -> Response:
    """SSE 即時事件串流端點。"""
    broadcaster = get_surfer_broadcaster()
    queue = await broadcaster.subscribe()

    async def _event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    # 發送 SSE keepalive 註解行，防止連線因超時被瀏覽器或反向代理中斷
                    yield ": keepalive\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            await broadcaster.unsubscribe(queue)

    response = Response(
        _event_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
    response.timeout = None  # 關閉超時斷開
    return response


@surfer_app.route("/api/surfer/trigger", methods=["POST"])
async def surfer_trigger() -> Response | tuple[Response, int] | Any:
    """手動觸發衝浪任務（支援 dry_run）。"""
    try:
        data: dict[str, Any] = (await request.get_json(silent=True)) or {}
        dry_run = bool(data.get("dry_run", False))
        mode = str(data.get("mode", "auto")).strip() or "auto"
        surfer = get_surfer()
        if surfer.is_busy:
            return jsonify({
                "status": "busy",
                "message": "衝浪任務已在執行中，請稍候再試！",
            }), 409
        bot = get_bot_instance()

        # 非同步在背景啟動衝浪，不阻塞 HTTP 回應
        asyncio.create_task(surfer.surf_once(bot, dry_run=dry_run, mode=mode))

        return jsonify({
            "status": "success",
            "message": f"雪莉已開始【{mode}】模式上網衝浪演出（dry_run={dry_run}）",
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.surfer_server.surfer_trigger", exc)
        return jsonify(payload), 500


# ---------------------------------------------------------------------------
# 伺服器啟動與背景任務管理
# ---------------------------------------------------------------------------

async def start_surfer_server_async(host: str = "0.0.0.0", port: int | None = None) -> None:
    """非同步啟動 Port 8765 實況伺服器（支援 HTTPS / HTTP 自動適配）。"""
    listen_port = port or int(getattr(settings, "CARE_SURFER_PORT", 8765))
    use_https = bool(getattr(settings, "WEB_UI_HTTPS", True))
    ssl_cert = None
    ssl_key = None
    if use_https:
        try:
            from web_ui.server import _ensure_self_signed_cert
            ssl_cert, ssl_key = _ensure_self_signed_cert()
        except Exception as exc:
            logger.warning("[CareSurferServer] 取得 SSL 憑證失敗，將回退至 HTTP: %s", exc)
            use_https = False

    try:
        config_uvicorn = uvicorn.Config(
            app=surfer_app,
            host=host,
            port=listen_port,
            loop="asyncio",
            log_level="warning",
            ssl_certfile=ssl_cert if use_https else None,
            ssl_keyfile=ssl_key if use_https else None,
        )
        server = uvicorn.Server(config_uvicorn)
        proto = "https" if use_https else "http"
        logger.info("[CareSurferServer] 正在啟動雪莉上網衝浪實況台：%s://%s:%d ...", proto, host, listen_port)
        await server.serve()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("[CareSurferServer] 衝浪實況台伺服器收到關閉信號。")
    except Exception as err:
        logger.error("[CareSurferServer] 實況台伺服器異常 (%s:%d): %s", host, listen_port, err, exc_info=True)


def launch_surfer_server_task(host: str = "0.0.0.0", port: int | None = None) -> asyncio.Task[None]:
    """在目前事件迴圈背景啟動實況伺服器任務。"""
    global _server_task
    if _server_task is None or _server_task.done():
        _server_task = asyncio.create_task(start_surfer_server_async(host=host, port=port))
        logger.info("[CareSurferServer] 實況台伺服器背景任務已啟動。")
    return _server_task


def stop_surfer_server_task() -> None:
    """停止實況伺服器任務。"""
    global _server_task
    if _server_task is not None and not _server_task.done():
        _server_task.cancel()
        _server_task = None
        logger.info("[CareSurferServer] 實況台伺服器背景任務已取消。")
