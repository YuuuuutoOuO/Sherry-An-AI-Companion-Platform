"""
mod/livestream/web.py
=====================
Dedicated Quart Web Server for the Sherry Livestream display UI on PORT 8764 with SSE real-time sync,
and HTTP Byte-Range streaming support for local video files.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import time
from typing import Any, AsyncGenerator, Optional
from urllib.parse import quote, unquote
import webbrowser

from quart import Blueprint, Quart, Response, g, jsonify, render_template, request, send_file, send_from_directory
import uvicorn

from core.error_report import safe_error_payload
from mod.livestream import config
from mod.livestream.clients.broadcaster import LivestreamBroadcaster
from mod.livestream.domain import (
    ChatMessage,
    PlayAction,
    PlayMode,
    StreamEvent,
    StreamPlaybackState,
)
from mod.livestream.service import LivestreamService

logger = logging.getLogger(__name__)

# Template & Static folder resolution
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "web_ui" / "static"

# Dedicated Standalone Quart App on PORT 8764
livestream_app: Quart = Quart(
    "livestream_display",
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)
_configured_livestream_key: str = os.getenv("LIVESTREAM_SECRET_KEY", "").strip()
if _configured_livestream_key:
    livestream_app.secret_key = _configured_livestream_key
else:
    livestream_app.secret_key = secrets.token_hex(24)
    logger.warning("未設定 LIVESTREAM_SECRET_KEY 環境變數，使用臨時隨機金鑰；重啟服務後既存 Session 將會失效。")

livestream_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# ---------------------------------------------------------------------------
# R2（報告 §5.3.9 / §5.4.2）：8764 專用服務簡易存取權杖防護
# ---------------------------------------------------------------------------
_ACCESS_COOKIE_NAME = "livestream_access"
_ACCESS_COOKIE_MAX_AGE = 86400  # 24 小時


def _sign_access_cookie(timestamp: str) -> str:
    """以 App secret_key 對時間戳簽章，避免 cookie 被偽造。"""
    secret = livestream_app.secret_key
    key = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    return hmac.new(key, timestamp.encode("utf-8"), "sha256").hexdigest()


def _is_valid_access_cookie(value: str) -> bool:
    """驗證 `ts.sig` 格式 cookie 的簽章與時效（24 小時）。"""
    ts, _, sig = (value or "").partition(".")
    if not ts or not sig or not ts.isdigit():
        return False
    if time.time() - int(ts) > _ACCESS_COOKIE_MAX_AGE:
        return False
    return hmac.compare_digest(sig, _sign_access_cookie(ts))


@livestream_app.before_request
async def _guard_livestream_access() -> Any:
    """未設定 ACCESS_TOKEN 時開放（依賴 loopback 邊界）；設定後強制驗證權杖或已簽章 cookie。"""
    g.livestream_issue_access_cookie = False
    expected = (config.ACCESS_TOKEN or "").strip()
    if not expected:
        return None
    path = request.path or ""
    if path.startswith("/static/"):
        return None  # 靜態資源（Live2D 素材等）豁免，不含敏感資料

    expected_bytes = expected.encode("utf-8", "ignore")
    supplied_header = str(request.headers.get("X-Livestream-Token", ""))
    supplied_query = str(request.args.get("token", ""))
    if supplied_header and hmac.compare_digest(supplied_header.encode("utf-8", "ignore"), expected_bytes):
        return None
    if supplied_query and hmac.compare_digest(supplied_query.encode("utf-8", "ignore"), expected_bytes):
        # 由查詢參數（頁面載入路徑）登入成功 → 核發 cookie，後續 SSE/API 請求免帶 token
        g.livestream_issue_access_cookie = True
        return None
    if _is_valid_access_cookie(str(request.cookies.get(_ACCESS_COOKIE_NAME, ""))):
        return None
    return jsonify({
        "status": "error",
        "code": "LIVESTREAM_UNAUTHORIZED",
        "message": "需要有效的 Livestream 存取權杖（X-Livestream-Token 標頭或 ?token= 查詢參數）。",
    }), 401


@livestream_app.after_request
async def _issue_livestream_access_cookie(response: Response) -> Response:
    """查詢參數驗證成功時核發 HttpOnly 簽章 cookie（SSE／後續 API 免重帶 token）。"""
    if getattr(g, "livestream_issue_access_cookie", False):
        ts = str(int(time.time()))
        response.set_cookie(
            _ACCESS_COOKIE_NAME,
            f"{ts}.{_sign_access_cookie(ts)}",
            max_age=_ACCESS_COOKIE_MAX_AGE,
            httponly=True,
            secure=bool(config.HTTPS),
            samesite="Strict",
            path="/",
        )
    return response

# Blueprint (for optional backward compatibility with main web_ui)
livestream_bp: Blueprint = Blueprint(
    "livestream",
    __name__,
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_service: Optional[LivestreamService] = None
_server_task: Optional[asyncio.Task[None]] = None
_browser_process: Optional[object] = None  # subprocess.Popen instance, tracked for auto-close on exit


def get_service() -> LivestreamService:
    """Returns the singleton LivestreamService instance."""
    global _service
    if _service is None:
        broadcaster = LivestreamBroadcaster()
        _service = LivestreamService(broadcaster=broadcaster)
    return _service


# ---------------------------------------------------------------------------
# API & View Handlers
# ---------------------------------------------------------------------------


def _serialize_chat_message(msg: Optional[ChatMessage]) -> Optional[dict[str, object]]:
    """Serializes ChatMessage domain object to JSON-compatible dict."""
    if not msg:
        return None
    return {
        "id": msg.id,
        "author_name": msg.author_name,
        "author_avatar": msg.author_avatar,
        "content": msg.content,
        "timestamp": msg.timestamp,
        "is_owner": msg.is_owner,
        "badge": msg.badge,
        "channel_id": msg.channel_id,
    }


def _serialize_media(media: Optional[object]) -> Optional[dict[str, object]]:
    """Serializes MediaItem domain object to JSON-compatible dict."""
    if not media:
        return None
    action_val: Any = getattr(media, "action", "")
    action_str = action_val.value if hasattr(action_val, "value") else str(action_val)
    mode_val: Any = getattr(media, "play_mode", PlayMode.AUTOPLAY)
    mode_str = mode_val.value if hasattr(mode_val, "value") else str(mode_val)
    return {
        "action": action_str,
        "video_id": getattr(media, "video_id", ""),
        "original_url": getattr(media, "original_url", ""),
        "title": getattr(media, "title", ""),
        "requester_name": getattr(media, "requester_name", ""),
        "requester_avatar": getattr(media, "requester_avatar", ""),
        "requested_at": getattr(media, "requested_at", 0.0),
        "start_seconds": getattr(media, "start_seconds", 0),
        "series_name": getattr(media, "series_name", ""),
        "episode_index": getattr(media, "episode_index", 0),
        "total_episodes": getattr(media, "total_episodes", 0),
        "stream_url": getattr(media, "stream_url", ""),
        "play_mode": mode_str,
        "playlist_id": getattr(media, "playlist_id", ""),
        "page_index": getattr(media, "page_index", 1),
    }


def _serialize_state(state: StreamPlaybackState) -> dict[str, object]:
    """Serializes StreamPlaybackState to JSON-compatible dict."""
    return {
        "status": state.status.value,
        "current_media": _serialize_media(state.current_media),
        "started_at": state.started_at,
        "volume": state.volume,
        "is_muted": state.is_muted,
        "queue": [_serialize_media(m) for m in state.queue],
    }


def _serialize_event(event: StreamEvent) -> dict[str, object]:
    """Serializes StreamEvent for SSE dispatch."""
    data: dict[str, object] = {
        "event_type": event.event_type,
        "state": _serialize_state(event.state),
        "timestamp": event.timestamp,
        "message": event.message,
    }
    if event.chat_message:
        data["chat_message"] = _serialize_chat_message(event.chat_message)
    return data



async def _handle_livestream_screen() -> str:
    """Renders the HTML5 / YouTube fullscreen live display UI."""
    return await render_template("livestream.html")


async def _handle_get_state() -> tuple[Response, int]:
    """Returns current playback state as JSON."""
    service = get_service()
    state = service.get_current_state()
    return jsonify({"status": "success", "data": _serialize_state(state)}), 200


async def _handle_events_stream() -> Response | tuple[Response, int] | Any:
    """SSE (Server-Sent Events) endpoint broadcasting playback updates."""
    service = get_service()
    broadcaster = service._broadcaster

    async def event_generator() -> AsyncGenerator[str, None]:
        queue = await broadcaster.subscribe()
        try:
            # Yield initial state immediately upon connection
            initial_state = service.get_current_state()
            initial_event = StreamEvent(
                event_type="sync",
                state=initial_state,
                timestamp=time.time(),
                message="Initial state sync",
            )
            yield f"data: {json.dumps(_serialize_event(initial_event), ensure_ascii=False)}\n\n"

            while True:
                event = await queue.get()
                yield f"data: {json.dumps(_serialize_event(event), ensure_ascii=False)}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            await broadcaster.unsubscribe(queue)


    response = Response(
        event_generator(),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


async def _handle_stream_video() -> Response | tuple[Response, int] | Any:
    """Streams a local video file with Byte-Range HTTP 206 support."""
    series = unquote(request.args.get("series", "").strip())
    episode_raw = request.args.get("episode", "1")
    try:
        episode = int(episode_raw)
    except ValueError:
        episode = 1

    service = get_service()
    file_path = service.get_video_file_path(series, episode)

    if not file_path or not file_path.is_file():
        return jsonify({"status": "error", "message": f"找不到影片: {series} 第 {episode} 集"}), 404

    # Quart's send_file supports conditional=True for HTTP 206 Partial Content
    return await send_file(
        str(file_path),
        conditional=True,
    )


async def _handle_next_episode() -> tuple[Response, int]:
    """Handles frontend request when video ends to advance to next episode in loop."""
    service = get_service()
    new_state = await service.next_episode()
    return jsonify({"status": "success", "data": _serialize_state(new_state)}), 200


async def _handle_control() -> tuple[Response, int]:
    """Handles playback controls (play, pause, resume, stop, next) via Web API."""
    data = await request.get_json() or {}
    action_type = data.get("action", "").lower()
    service = get_service()

    try:
        if action_type == "stop":
            new_state = await service.stop()
        elif action_type == "pause":
            new_state = await service.pause()
        elif action_type == "resume":
            new_state = await service.resume()
        elif action_type == "next":
            new_state = await service.next_episode()
        elif action_type == "play":
            target = data.get("context") or data.get("video_name") or ""
            act = PlayAction(data.get("source", "youtube"))
            raw_mode = data.get("play_mode") or data.get("mode") or "autoplay"
            try:
                play_mode = PlayMode(raw_mode)
            except ValueError:
                play_mode = PlayMode.AUTOPLAY
            episode = int(data.get("episode", 1))
            new_state = await service.play_video(
                action=act,
                context=target,
                play_mode=play_mode,
                episode=episode,
                requester_name=data.get("requester_name", "WebUser"),
            )
        else:
            return jsonify({"status": "error", "message": f"未知的控制動作: {action_type}"}), 400

        return jsonify({"status": "success", "data": _serialize_state(new_state)}), 200

    except Exception as exc:
        logger.warning("Livestream control action '%s' failed: %s", action_type, exc)
        return jsonify(safe_error_payload(
            "mod.livestream.web.control",
            exc,
            message="控制指令執行失敗，請稍後再試。",
            level=logging.WARNING,
        )), 400


async def _handle_get_chat() -> tuple[Response, int]:
    """Returns the recent chat history as JSON."""
    service = get_service()
    messages = service.get_chat_history()
    return jsonify({
        "status": "success",
        "data": [_serialize_chat_message(m) for m in messages],
        "channel_id": str(config.DISCORD_CHAT_CHANNEL_ID),
    }), 200


async def _handle_post_chat() -> tuple[Response, int]:
    """Receives and broadcasts a new chat message to all connected clients."""
    data = await request.get_json() or {}
    author_name = str(data.get("author_name", "網頁觀眾")).strip() or "網頁觀眾"
    content = str(data.get("content", "")).strip()

    if not content:
        return jsonify({"status": "error", "message": "留言內容不可為空"}), 400

    service = get_service()
    chat_msg = await service.post_chat_message(
        author_name=author_name,
        author_avatar=str(data.get("author_avatar", "")),
        content=content,
        is_owner=bool(data.get("is_owner", False)),
        badge=str(data.get("badge", "💬 觀眾")),
        channel_id=str(data.get("channel_id", config.DISCORD_CHAT_CHANNEL_ID)),
    )
    return jsonify({"status": "success", "data": _serialize_chat_message(chat_msg)}), 200


async def _handle_serve_static(filename: str) -> Response:
    """Serves static assets (Live2D JS, textures, CSS) on the dedicated Livestream Port 8764."""
    return await send_from_directory(str(_STATIC_DIR), filename)


# Register routes to Standalone Quart App
livestream_app.add_url_rule("/", view_func=_handle_livestream_screen, methods=["GET"])
livestream_app.add_url_rule("/livestream", view_func=_handle_livestream_screen, methods=["GET"])
livestream_app.add_url_rule("/stream", view_func=_handle_livestream_screen, methods=["GET"])
livestream_app.add_url_rule("/api/state", view_func=_handle_get_state, methods=["GET"])
livestream_app.add_url_rule("/api/livestream/state", view_func=_handle_get_state, methods=["GET"])
livestream_app.add_url_rule("/api/events", view_func=_handle_events_stream, methods=["GET"])
livestream_app.add_url_rule("/api/livestream/events", view_func=_handle_events_stream, methods=["GET"])
livestream_app.add_url_rule("/api/video/stream", view_func=_handle_stream_video, methods=["GET"])
livestream_app.add_url_rule("/api/livestream/next", view_func=_handle_next_episode, methods=["POST"])
livestream_app.add_url_rule("/api/control", view_func=_handle_control, methods=["POST"])
livestream_app.add_url_rule("/api/livestream/control", view_func=_handle_control, methods=["POST"])
livestream_app.add_url_rule("/api/chat", view_func=_handle_get_chat, methods=["GET"])
livestream_app.add_url_rule("/api/chat", view_func=_handle_post_chat, methods=["POST"])
livestream_app.add_url_rule("/api/livestream/chat", view_func=_handle_get_chat, methods=["GET"])
livestream_app.add_url_rule("/api/livestream/chat", view_func=_handle_post_chat, methods=["POST"])
livestream_app.add_url_rule("/static/<path:filename>", view_func=_handle_serve_static, methods=["GET"])

# Register routes to Blueprint
livestream_bp.add_url_rule("/livestream", view_func=_handle_livestream_screen, methods=["GET"])
livestream_bp.add_url_rule("/stream", view_func=_handle_livestream_screen, methods=["GET"])
livestream_bp.add_url_rule("/api/livestream/state", view_func=_handle_get_state, methods=["GET"])
livestream_bp.add_url_rule("/api/livestream/events", view_func=_handle_events_stream, methods=["GET"])
livestream_bp.add_url_rule("/api/video/stream", view_func=_handle_stream_video, methods=["GET"])
livestream_bp.add_url_rule("/api/livestream/next", view_func=_handle_next_episode, methods=["POST"])
livestream_bp.add_url_rule("/api/livestream/control", view_func=_handle_control, methods=["POST"])
livestream_bp.add_url_rule("/api/livestream/chat", view_func=_handle_get_chat, methods=["GET"])
livestream_bp.add_url_rule("/api/livestream/chat", view_func=_handle_post_chat, methods=["POST"])



# ---------------------------------------------------------------------------
# Server Runner & SSL
# ---------------------------------------------------------------------------


def _ensure_ssl_cert() -> tuple[str, str]:
    """Ensures SSL certificates exist for HTTPS livestreaming."""
    cert_path = config.SSL_CERT
    key_path = config.SSL_KEY

    if cert_path and key_path and Path(cert_path).is_file() and Path(key_path).is_file():
        return str(cert_path), str(key_path)

    # Check shared ssl directory
    ssl_dir = config.DATA_DIR / "ssl"
    ssl_dir.mkdir(parents=True, exist_ok=True)
    shared_cert = ssl_dir / "cert.pem"
    shared_key = ssl_dir / "key.pem"

    if shared_cert.exists() and shared_key.exists():
        return str(shared_cert), str(shared_key)

    try:
        from web_ui.server import _ensure_self_signed_cert
        return _ensure_self_signed_cert()
    except Exception:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Sherry Livestream Display"),
        ])
        now = datetime.datetime.now(datetime.timezone.utc)
        san_entries = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.IPAddress(ipaddress.IPv4Address("0.0.0.0")),
        ]
        if config.RADMIN_IP:
            try:
                san_entries.append(x509.IPAddress(ipaddress.IPv4Address(config.RADMIN_IP)))
            except ValueError:
                san_entries.append(x509.DNSName(config.RADMIN_IP))

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName(san_entries),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        shared_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        shared_key.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        return str(shared_cert), str(shared_key)


def _keep_display_awake() -> None:
    """Prevents Windows from sleeping or locking the display while livestreaming.

    Uses SetThreadExecutionState with ES_DISPLAY_REQUIRED | ES_CONTINUOUS to signal
    that the display must remain on. This prevents the screen from going black during
    Discord screen capture sessions.
    """
    try:
        import ctypes
        # ES_CONTINUOUS = 0x80000000, ES_DISPLAY_REQUIRED = 0x00000002, ES_SYSTEM_REQUIRED = 0x00000001
        ES_CONTINUOUS: int = 0x80000000
        ES_DISPLAY_REQUIRED: int = 0x00000002
        ES_SYSTEM_REQUIRED: int = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED)
        logger.info("Display keep-awake activated (SetThreadExecutionState). Screen will not lock during livestream.")
    except Exception as exc:
        logger.warning("Could not activate display keep-awake (non-Windows platform?): %s", exc)


def _restore_display_sleep() -> None:
    """Restores normal Windows sleep/lock behavior after livestream ends."""
    try:
        import ctypes
        ES_CONTINUOUS: int = 0x80000000
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        logger.info("Display keep-awake deactivated. Normal sleep/lock behavior restored.")
    except Exception as exc:
        logger.warning("Could not restore display sleep settings: %s", exc)


def _launch_streaming_browser(url: str) -> None:
    """Launches Edge or Chrome with autoplay policy disabled and app mode for dedicated streaming.

    Stores the launched process globally so it can be killed on program exit.
    Also activates Windows display keep-awake to prevent screen lock during Discord streaming.
    """
    import atexit
    import os
    import shutil
    import subprocess

    global _browser_process

    candidate_paths = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]

    browser_bin: Optional[str] = None
    for p in candidate_paths:
        if os.path.isfile(p):
            browser_bin = p
            break

    if not browser_bin:
        browser_bin = shutil.which("msedge") or shutil.which("chrome")

    if browser_bin:
        # Use an isolated user-data-dir so it doesn't hijack the user's existing Chrome session
        import tempfile
        user_data_dir = os.path.join(tempfile.gettempdir(), "sherry_livestream_browser")
        cmd = [
            browser_bin,
            f"--user-data-dir={user_data_dir}",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=PreloadMediaEngagementData,MediaEngagementBypassAutoplayPolicies",
            "--ignore-certificate-errors",
            "--allow-insecure-localhost",
            "--no-first-run",
            "--disable-infobars",
            f"--app={url}",
        ]
        try:
            proc = subprocess.Popen(cmd)
            _browser_process = proc
            logger.info(
                "Launched dedicated streaming browser (PID=%d) with autoplay flags: %s (%s)",
                proc.pid, browser_bin, url,
            )

            # Prevent screen lock / display sleep during streaming
            _keep_display_awake()

            def _kill_browser_on_exit() -> None:
                """Kills the dedicated streaming browser and restores display sleep on Python exit."""
                if _browser_process is not None and _browser_process.poll() is None:
                    try:
                        _browser_process.terminate()
                        logger.info("Terminated dedicated streaming browser (PID=%d) on exit.", _browser_process.pid)
                    except Exception as e:
                        logger.warning("Could not terminate streaming browser: %s", e)
                _restore_display_sleep()

            atexit.register(_kill_browser_on_exit)
            return
        except Exception as exc:
            logger.warning("Failed to launch dedicated browser process: %s", exc)

    # Fallback to standard webbrowser (no auto-close in this case)
    webbrowser.open(url)



async def start_livestream_server_async() -> None:
    """Starts the dedicated Livestream Web Server on Port 8764 with HTTPS/HTTP."""
    host = config.HOST
    port = config.PORT

    try:
        ssl_cert = None
        ssl_key = None
        if config.HTTPS:
            ssl_cert, ssl_key = _ensure_ssl_cert()

        config_uvicorn = uvicorn.Config(
            app=livestream_app,
            host=host,
            port=port,
            loop="asyncio",
            log_level="warning",
            ssl_certfile=ssl_cert if config.HTTPS else None,
            ssl_keyfile=ssl_key if config.HTTPS else None,
        )
        server = uvicorn.Server(config_uvicorn)

        proto = "https" if config.HTTPS else "http"
        logger.info("Starting dedicated Sherry Livestream %s Server on %s://%s:%d ...", proto.upper(), proto, host, port)

        # Auto-open dedicated streaming browser window (with autoplay flags)
        if config.AUTO_OPEN:
            async def _open_browser_delayed() -> None:
                await asyncio.sleep(1.5)
                display_host = "127.0.0.1" if host == "0.0.0.0" else host
                url = f"{proto}://{display_host}:{port}/"
                access_token = (config.ACCESS_TOKEN or "").strip()
                if access_token:
                    url += f"?token={quote(access_token, safe='')}"
                logger.info("Auto-opening dedicated Livestream Screen browser: %s", url)
                try:
                    _launch_streaming_browser(url)
                except Exception as e:
                    logger.warning("Could not auto-open browser: %s", e)

            asyncio.create_task(_open_browser_delayed())

        await server.serve()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Livestream Web Server received shutdown signal.")
    except Exception as err:
        logger.error("Livestream Web Server critical error on %s:%d: %s", host, port, err, exc_info=True)


def launch_livestream_server_task() -> asyncio.Task[None]:
    """Spawns the background server task if not already running."""
    global _server_task
    if _server_task is None or _server_task.done():
        _server_task = asyncio.create_task(start_livestream_server_async())
        logger.info("Livestream UI server background task launched.")
    return _server_task

