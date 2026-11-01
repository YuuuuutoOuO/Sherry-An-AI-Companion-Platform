"""
web_ui/server.py
================
Quart Web UI server for Sherry Bot console.

Provides:
  GET  /                      — Main dashboard UI
  GET  /api/logs/files        — List available log files under DATA_DIR
  GET  /api/logs              — Read log file (?file=xxx&page=N&size=50)
  GET  /api/models/ollama     — Proxy Ollama /api/tags for model list
  GET  /api/models/lms        — Proxy LM Studio /v1/models for model list
  GET  /api/settings          — Return editable settings with meta info
  POST /api/settings          — Batch update settings

Security: all routes except /static require HTTP Basic Auth.
Sensitive keys (TOKEN, PASSWORD, KEY, SECRET) are never exposed.
"""

from __future__ import annotations

import asyncio
import gzip
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Coroutine

import httpx
from quart import Quart, Response, g, jsonify, render_template, request

import settings
from core.error_report import log_safe_error
from core.state_store import LoginLockout, SessionStore, SQLiteJSONMap
from mod.user_tracker import user_connection_tracker
from version import APP_VERSION

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))  # 台灣標準時間 UTC+8

app = Quart(__name__)
app.secret_key = os.urandom(24)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # 開發模式：停用靜態檔案快取

# Dynamic plugins are mounted automatically during server initialization

# ---------------------------------------------------------------------------
# Session & Auth Helpers (Token-based with 10-min Inactivity Expiration)
# ---------------------------------------------------------------------------

_admin_sessions: SessionStore = SessionStore("admin_sessions")
_SESSION_TIMEOUT_SEC: float = 600.0  # 10 分鐘無動作自動過期

# R1 認證硬化：CSRF double-submit cookie（JS 可讀、供 X-CSRF-Token 回送比對）
_CSRF_COOKIE_NAME = "sherry_csrf"
_CSRF_HEADER_NAME = "X-CSRF-Token"
_CSRF_EXEMPT_PATHS = (
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/check",
    "/api/user/heartbeat",
    "/api/user/disconnect",
)
_bot_instance: Any = None
# R17：登入失敗與鎖定狀態外置至 SQLite（多實例共用；介面可換 Redis）
_login_attempts: SQLiteJSONMap[str, list[float]] = SQLiteJSONMap("login_attempts")
_locked_ips: SQLiteJSONMap[str, float] = SQLiteJSONMap("locked_ips")
_login_lockout = LoginLockout(_login_attempts, _locked_ips)


def _get_client_ip(req: Any) -> str:
    """提取客戶端真實 IP 位址（R3；報告 §5.3.3）。

    - 僅當連線來源（remote_addr）屬於 WEB_UI_TRUSTED_PROXIES 時才信任 X-Forwarded-For。
    - XFF 鏈取「最右」合法值：最右值由最後一跳受控代理附加，
      左側值可被客戶端自由偽造（影響登入鎖定與限流歸屬）。
    """
    remote = str(getattr(req, "remote_addr", "127.0.0.1") or "127.0.0.1")
    configured_proxies = getattr(settings, "WEB_UI_TRUSTED_PROXIES", None) or [
        "127.0.0.1",
        "::1",
        "localhost",
        "testclient",
    ]
    trusted_proxies = set(configured_proxies)
    if remote in trusted_proxies and hasattr(req, "headers"):
        forwarded = req.headers.get("X-Forwarded-For")
        if forwarded:
            candidates = [p.strip() for p in str(forwarded).split(",") if p.strip()]
            for client_candidate in reversed(candidates):
                try:
                    ipaddress.ip_address(client_candidate)
                    return client_candidate
                except ValueError:
                    continue
    return remote


# R3（報告 §5.3.8）：登入鎖定/失敗追蹤表 TTL 清理與硬上限，避免偽造來源灌爆記憶體。
_MAX_LOGIN_TRACKED_IPS: int = 1000
_LOGIN_TRACKER_SWEEP_INTERVAL_SEC: float = 60.0
_last_login_tracker_sweep: float = 0.0


def _sweep_login_tracker(now: float | None = None) -> None:
    """清理過期的登入失敗/鎖定紀錄並限制追蹤表上限（近似 LRU 淘汰）。"""
    global _last_login_tracker_sweep
    now = time.time() if now is None else now
    if (
        (now - _last_login_tracker_sweep) < _LOGIN_TRACKER_SWEEP_INTERVAL_SEC
        and len(_login_attempts) <= _MAX_LOGIN_TRACKED_IPS
        and len(_locked_ips) <= _MAX_LOGIN_TRACKED_IPS
    ):
        return
    _last_login_tracker_sweep = now
    _login_lockout.sweep(max_tracked=_MAX_LOGIN_TRACKED_IPS, now=now)


def _is_ip_locked(ip: str) -> tuple[bool, int]:
    """Check if client IP is currently locked out due to repeated failed login attempts."""
    _sweep_login_tracker(time.time())
    return _login_lockout.is_locked(ip)


def _clean_expired_sessions() -> None:
    """Purge sessions that have exceeded the inactivity timeout."""
    import time

    now = time.time()
    expired = [t for t, last in _admin_sessions.items() if now - last > _SESSION_TIMEOUT_SEC]
    for t in expired:
        _admin_sessions.pop(t, None)


def _get_git_info() -> dict[str, Any]:
    """Retrieve Git version metadata from current working directory."""
    import subprocess

    branch = "unknown"
    commit_hash = "unknown"
    full_hash = "unknown"
    commit_msg = "unknown"
    commit_date = "unknown"
    commit_author = "unknown"

    base_dir = getattr(settings, "BASE_DIR", Path("."))
    data_dir = getattr(settings, "DATA_DIR", base_dir / "data")

    try:
        res_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=base_dir,
            timeout=3,
        )
        if res_branch.returncode == 0:
            branch = res_branch.stdout.strip()
    except Exception as e:
        logger.debug("_get_git_info branch error: %s", e)

    try:
        res_log = subprocess.run(
            ["git", "log", "-1", "--date=format:%Y-%m-%d %H:%M:%S", "--format=%h|%H|%s|%cd|%an"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=base_dir,
            timeout=3,
        )
        if res_log.returncode == 0 and "|" in res_log.stdout:
            parts = res_log.stdout.strip().split("|")
            if len(parts) >= 5:
                commit_hash, full_hash, commit_msg, commit_date, commit_author = parts[:5]
    except Exception as e:
        logger.debug("_get_git_info log error: %s", e)

    pull_log_path = data_dir / "gitpull_result.txt"
    pull_log = ""
    pull_time = ""
    if pull_log_path.exists():
        try:
            content = pull_log_path.read_text(encoding="utf-8", errors="ignore").strip()
            lines = content.splitlines()
            pull_log = lines[-1] if lines else ""
            if lines and lines[0].startswith("["):
                pull_time = lines[0].split("]")[0].lstrip("[")
        except Exception:
            pass

    return {
        "branch": branch,
        "commit_hash": commit_hash,
        "full_hash": full_hash,
        "commit_msg": commit_msg,
        "commit_date": commit_date,
        "commit_author": commit_author,
        "pull_log": pull_log,
        "pull_time": pull_time,
        "tag": APP_VERSION,
    }


# R12：啟動期 git 資訊改由背景預熱填入；模組匯入不再同步執行 git 子進程。
_STARTUP_GIT_INFO: dict[str, Any] = {}
_CACHED_GIT_INFO: dict[str, Any] = {}
_LAST_GIT_INFO_TIME: float = 0.0
_GIT_INFO_CACHE_TTL: float = 120.0  # 延長快取至 2 分鐘，避免頻繁呼叫 git.exe 子進程阻塞 Windows I/O


async def _async_get_git_info() -> dict[str, Any]:
    """Retrieve Git version metadata asynchronously with TTL cache to prevent subprocess blocking."""
    global _CACHED_GIT_INFO, _LAST_GIT_INFO_TIME
    now = time.time()
    if _CACHED_GIT_INFO and now - _LAST_GIT_INFO_TIME < _GIT_INFO_CACHE_TTL:
        return _CACHED_GIT_INFO
    info = await asyncio.to_thread(_get_git_info)
    _CACHED_GIT_INFO = info
    _LAST_GIT_INFO_TIME = time.time()
    return info


def _get_request_token(req: Any) -> str:
    """Extract Session Token from HttpOnly cookie or Authorization headers."""
    if hasattr(req, "cookies") and "admin_session" in req.cookies:
        token = req.cookies.get("admin_session", "").strip()
        if token:
            return str(token)
    if hasattr(req, "headers"):
        auth_header = req.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return str(auth_header[7:].strip())
        if "X-Admin-Token" in req.headers:
            return str(req.headers.get("X-Admin-Token", "").strip())
    return ""


def _is_valid_session(token: str) -> bool:
    """檢查 token 是否為有效且未逾時的管理員 Session（不刷新活躍時間）。"""
    import time

    if not token or token not in _admin_sessions:
        return False
    if time.time() - _admin_sessions[token] > _SESSION_TIMEOUT_SEC:
        _admin_sessions.pop(token, None)
        return False
    return True


def _is_admin_request(req: Any) -> bool:
    """Validate Bearer Token, X-Admin-Token, or HttpOnly admin_session cookie."""
    import time

    _clean_expired_sessions()

    token = _get_request_token(req)

    if not _is_valid_session(token):
        return False

    # Refresh last active timestamp
    _admin_sessions[token] = time.time()
    return True


def _set_csrf_cookie(resp: Response, token: str | None = None) -> Response:
    """簽發（或補發）CSRF double-submit cookie：Secure＋SameSite，但 JS 可讀（非 HttpOnly）。"""
    import secrets

    resp.set_cookie(
        _CSRF_COOKIE_NAME,
        token or secrets.token_urlsafe(32),
        max_age=int(_SESSION_TIMEOUT_SEC),
        httponly=False,  # 前端需讀取後放入 X-CSRF-Token 標頭（double-submit 模式）
        secure=True,
        samesite="Strict",
        path="/",
    )
    return resp


def _check_csrf(req: Any) -> Response | tuple[Response, int] | None:
    """R1：對「Cookie 鑑權」之變更型請求強制 CSRF double-submit 比對。

    回傳 None 表示放行；否則回傳 403 Response。
    - Bearer／X-Admin-Token 標頭鑑權（非瀏覽器/指令碼）本質免疫 CSRF，予以豁免。
    - 未持有有效 Cookie Session 時交由既有 401 邏輯處理，不在此攔截。
    """
    import hmac
    import time

    if req.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if req.path in _CSRF_EXEMPT_PATHS:
        return None

    cookie_token = str(req.cookies.get("admin_session", "")).strip()
    if not cookie_token or cookie_token not in _admin_sessions:
        return None
    if time.time() - _admin_sessions[cookie_token] > _SESSION_TIMEOUT_SEC:
        _admin_sessions.pop(cookie_token, None)
        return None

    # 僅以標頭鑑權（未帶有效 Cookie Session）之請求豁免
    auth_header = str(req.headers.get("Authorization", ""))
    header_token = ""
    if auth_header.startswith("Bearer "):
        header_token = auth_header[7:].strip()
    elif "X-Admin-Token" in req.headers:
        header_token = str(req.headers.get("X-Admin-Token", "")).strip()
    if header_token and header_token != cookie_token and _is_valid_session(header_token):
        return None

    sent_token = str(req.headers.get(_CSRF_HEADER_NAME, "")).strip()
    cookie_csrf = str(req.cookies.get(_CSRF_COOKIE_NAME, "")).strip()
    if not sent_token or not cookie_csrf or not hmac.compare_digest(sent_token, cookie_csrf):
        return jsonify(
            {
                "status": "error",
                "code": "CSRF_FAILED",
                "message": "CSRF 驗證失敗：缺少或無效的 X-CSRF-Token，請重新整理頁面或重新登入。",
            }
        ), 403

    return None


_EXCLUDED_MOD_DIRS: set[str] = {"clients", "repositories", "__pycache__"}
_MOD_CONFIG_PATH: Path = settings.DATA_DIR / "mod_config.json"
_mounted_mod_blueprints: dict[str, Any] = {}
# R3（報告 §5.3.2）：外掛公開路由改為結構化白名單（default deny）。
# 每筆格式：{"path": "/api/x/", "methods": frozenset({"GET"}), "auth": "public"}
_public_mod_routes: list[dict[str, Any]] = []

# R3（報告 §5.3.1）：未認證 GET 放行清單收斂為「無機敏內容之公開展示端點」。
# 任務排程／Bot 狀態／稽核與自我監控／日誌／交易／深度研究／Spark 一律需管理員權限。
_PUBLIC_GET_PREFIXES: tuple[str, ...] = (
    "/api/feed_sentinel/",
    "/api/painter/",
    "/api/models/",
    "/api/services/",
    "/api/modules",
    "/api/roast/config",
    "/api/git/",
    "/api/system/dashboard",
    "/api/system/metrics",
    "/api/system/background_metrics",
    "/api/system/components/metrics",
    "/api/system/incidents",
    "/api/system/emotion",
    "/handlers/",
    "/mod/",
)


def _load_mod_config() -> dict[str, bool]:
    """Load mod enabled/disabled state from data/mod_config.json.

    Returns:
        dict[str, bool]: Mapping of mod_key -> enabled (True = enabled).
    """
    if _MOD_CONFIG_PATH.exists():
        try:
            raw = _MOD_CONFIG_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): bool(v) for k, v in data.items()}
        except Exception as exc:
            logger.warning("Failed to read mod_config.json: %s", exc)
    return {}


def _save_mod_config(config: dict[str, bool]) -> None:
    """Persist mod enabled/disabled state to data/mod_config.json.

    Args:
        config: Mapping of mod_key -> enabled.
    """
    _MOD_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MOD_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_mod_enabled(mod_key: str, config: dict[str, bool]) -> bool:
    """Check if a mod is enabled in the config (defaults to True if not set).

    Args:
        mod_key: The mod identifier string.
        config: The loaded mod config dict.

    Returns:
        bool: True if enabled (default), False if explicitly disabled.
    """
    return config.get(mod_key, True)


def _normalize_public_route_declarations(declared: Any) -> list[dict[str, Any]]:
    """R3：將 MOD_INFO.public_routes 正規化為結構化白名單（default deny）。

    僅接受 ``{"path": "/x/", "methods": ["GET", ...], "auth": "public"}`` 形式；
    缺漏 path/methods、方法集合為空、``auth != "public"``、或舊式字串條目
    一律視為「非公開」，不自動放行。
    """
    normalized: list[dict[str, Any]] = []
    if not isinstance(declared, (list, tuple, set)):
        return normalized
    for entry in declared:
        if not isinstance(entry, dict):
            logger.warning(
                "public_routes 偵測到非結構化條目 %r：R3 起 default deny，不予放行（請改用 {path, methods, auth}）。",
                entry,
            )
            continue
        route_path = str(entry.get("path", "")).strip()
        auth = str(entry.get("auth", "public")).strip().lower()
        methods = entry.get("methods")
        if not route_path.startswith("/"):
            logger.warning("public_routes 條目缺少合法 path：%r（略過）。", entry)
            continue
        if auth != "public":
            continue  # 顯式標記非公開 → 交由一般認證流程（default deny）
        if not isinstance(methods, (list, tuple, set)) or not methods:
            logger.warning("public_routes 條目 %r 未聲明 methods：default deny（略過）。", entry)
            continue
        method_set = frozenset(str(m).strip().upper() for m in methods if str(m).strip())
        if not method_set:
            continue
        normalized.append({"path": route_path, "methods": method_set, "auth": "public"})
    return normalized


def _match_public_mod_route(path: str, method: str) -> bool:
    """R3：公開外掛路由採「路徑前綴＋方法」精確比對（default deny）。"""
    for entry in _public_mod_routes:
        entry_path = str(entry.get("path", ""))
        if entry_path and (path == entry_path or path.startswith(entry_path)):
            if str(method).upper() in entry.get("methods", frozenset()):
                return True
    return False


def _scan_and_update_public_mod_routes() -> list[dict[str, Any]]:
    """R3：動態掃描各 MOD_INFO.public_routes 之結構化公開路由白名單。

    Returns:
        list[dict[str, Any]]: 正規化後之公開路由條目清單。
    """
    import importlib

    global _public_mod_routes
    routes: list[dict[str, Any]] = []
    mod_config = _load_mod_config()
    mod_base = Path("mod")
    if mod_base.is_dir():
        for child in mod_base.iterdir():
            if (
                child.is_dir()
                and not child.name.startswith("_")
                and child.name not in _EXCLUDED_MOD_DIRS
                and (child / "__init__.py").exists()
            ):
                mod_key = child.name
                if not _is_mod_enabled(mod_key, mod_config):
                    continue
                try:
                    pkg = importlib.import_module(f"mod.{mod_key}")
                    info = getattr(pkg, "MOD_INFO", {}) or {}
                    declared_routes = info.get("public_routes", [])
                    routes.extend(_normalize_public_route_declarations(declared_routes))
                except Exception as exc:
                    logger.debug("Could not inspect public_routes for mod [%s]: %s", mod_key, exc)
    _public_mod_routes = routes
    return routes


# Initialize public mod routes upon server startup
_scan_and_update_public_mod_routes()


@app.before_request
async def check_admin_session_before_request() -> Response | tuple[Response, int] | None:
    """Global Request Interceptor: enforces Admin Session TTL and method guards."""
    path = request.path

    # 1. 靜態資源放行
    if path.startswith("/static") or "/static/" in path:
        return None

    # 2. 公開頁面與視圖放行 (包含 PWA Service Worker 與版本檢查)
    #    R1：/api/mod/<mod_key>/view 已收斂為需管理員 Session（報告 §5.1.1 P0）
    if path in ("/", "/sw.js", "/api/version"):
        return None

    # 3. 認證與使用者連線端點放行 (登入 / 登出 / 狀態查詢 / 訪客心跳 / 離線 Beacon)
    if path in (
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/check",
        "/api/user/heartbeat",
        "/api/user/disconnect",
    ):
        return None

    # 4. R3：外掛結構化公開路由白名單（{path, methods, auth}；default deny）
    if _match_public_mod_route(path, request.method):
        return None

    # 5. 公開展示型安全查詢 GET 端點放行（R3 已收斂機敏端點；見 _PUBLIC_GET_PREFIXES）
    if request.method == "GET" and any(path.startswith(prefix) for prefix in _PUBLIC_GET_PREFIXES):
        return None

    # 6. R1：Cookie 鑑權之變更型請求需通過 CSRF double-submit 驗證
    csrf_failure = _check_csrf(request)
    if csrf_failure is not None:
        return csrf_failure

    # 7. 其餘敏感 GET 端點 (如 /api/brain/queue, /api/settings, /api/soul/content, /api/audit/logs)
    #    以及所有未授權的寫入操作 (POST/PUT/DELETE) 強制需要管理員權限
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "需要管理員權限或管理員 Session 已過期，請進行管理員登入。"}), 401

    return None


@app.before_request
async def track_user_connection_before_request() -> None:
    """一般使用者與訪客連線狀態追蹤：自動簽發 visitor_id 並更新活躍狀態。"""
    path = request.path
    # 靜態資源排除
    if (
        path.startswith("/static")
        or "/static/" in path
        or path.endswith((".png", ".jpg", ".jpeg", ".svg", ".css", ".js", ".woff2", ".ico"))
    ):
        return None

    vid = request.cookies.get("visitor_id", "").strip()
    if not vid:
        vid = f"vis_{secrets.token_hex(8)}"
        g.new_visitor_id = vid

    # 排除心跳與斷線自身端點的高頻路徑覆蓋
    if path not in ("/api/user/heartbeat", "/api/user/disconnect"):
        ip = _get_client_ip(request)
        ua = str(request.headers.get("User-Agent", ""))
        await asyncio.to_thread(
            user_connection_tracker.touch_connection,
            visitor_id=vid,
            ip=ip,
            user_agent=ua,
            path=path,
        )

    return None


@app.after_request
async def apply_security_headers(response: Response) -> Response:
    """R1：統一套用安全標頭（CSP / nosniff / X-Frame-Options / Referrer-Policy / HSTS）。

    註：CSP 目前為暫行版本——R5 已完成主頁 inline `<script>` 外置化（`sw_register.js`）
    與動態 HTML 淨化入口（DOMPurify vendored）；惟模板仍有 inline 事件處理器（清點與
    例外清單見 `Docs/plans/26.10.19/`），故 `script-src`/`style-src` 暫含 `'unsafe-inline'`；
    移除條件：inline handler 全數改事件代理（後續回合／複審追蹤）。
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://cdn.discordapp.com; "
            "media-src 'self' data: blob:; "
            "frame-src https://www.youtube.com https://player.bilibili.com; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
    if request.is_secure or getattr(settings, "WEB_UI_HTTPS", False):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")

    # CSRF Cookie 自我修復：已持有有效 Cookie Session 但 CSRF Cookie 遺失（過期/被清除）時自動補發
    if _CSRF_COOKIE_NAME not in request.cookies:
        cookie_token = str(request.cookies.get("admin_session", "")).strip()
        if cookie_token and _is_valid_session(cookie_token):
            _set_csrf_cookie(response)

    # 訪客 Cookie 自動簽發
    new_vid = getattr(g, "new_visitor_id", None)
    if new_vid:
        response.set_cookie(
            "visitor_id",
            new_vid,
            max_age=30 * 86400,
            httponly=False,
            samesite="Lax",
            path="/",
        )

    return response


@app.after_request
async def handle_response_cache_headers(response: Response) -> Response:
    """套用 HTTP 快取標頭並對純文字、靜態資源與 JSON 回應自動執行 Gzip 壓縮（大幅減少 VPN/弱網傳輸延遲）。

    Args:
        response: Quart 原始回應物件。

    Returns:
        Response: 附加快取標頭與選擇性 Gzip 壓縮後的回應物件。
    """
    path = request.path
    # 1. 靜態資源（CSS、JS、圖檔、字型）套用瀏覽器高效能快取（搭配版本戳 ?v= 可隨版本即時更新）
    if (
        path.startswith("/static")
        or "/static/" in path
        or path.endswith((".png", ".jpg", ".jpeg", ".svg", ".css", ".js", ".woff2", ".ico"))
    ):
        response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=3600"
    else:
        # 動態 API 與 HTML 頁面強制禁止快取，保證資料即時性
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    # 2. 自動 Gzip 壓縮（若客戶端支援且內容未被壓縮）
    accept_encoding = request.headers.get("Accept-Encoding", "")
    if "gzip" in accept_encoding and 200 <= response.status_code < 300:
        if "Content-Encoding" not in response.headers:
            content_type = response.headers.get("Content-Type", "")
            if any(t in content_type for t in ("text/", "application/json", "application/javascript", "image/svg+xml")):
                raw_data = await response.get_data()
                data_bytes = raw_data.encode("utf-8") if isinstance(raw_data, str) else raw_data
                if len(data_bytes) > 400:
                    # R12：gzip 為 CPU 密集工作，移至工作執行緒避免阻塞事件迴圈。
                    compressed = await asyncio.to_thread(gzip.compress, data_bytes, compresslevel=6)
                    if len(compressed) < len(data_bytes):
                        response.set_data(compressed)
                        response.headers["Content-Encoding"] = "gzip"
                        response.headers["Content-Length"] = str(len(compressed))
    return response


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.route("/")
async def index() -> str:
    return await render_template(
        "index.html",
        live2d_enabled=getattr(settings, "WEB_UI_LIVE2D_ENABLED", True) is True,
    )


_SW_JS_CACHE: dict[str, tuple[int, str]] = {}


def _read_service_worker_source_sync() -> str:
    """讀取 Service Worker 原始碼並依 mtime_ns 快取（於工作執行緒呼叫）。"""
    sw_path = Path("web_ui/static/sw.js")
    if not sw_path.exists():
        base_dir = getattr(settings, "BASE_DIR", Path("."))
        sw_path = base_dir / "web_ui" / "static" / "sw.js"
    if not sw_path.exists():
        return ""
    cache_key = str(sw_path)
    try:
        mtime_ns = sw_path.stat().st_mtime_ns
        cached = _SW_JS_CACHE.get(cache_key)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]
        content = sw_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    _SW_JS_CACHE[cache_key] = (mtime_ns, content)
    return content


@app.route("/sw.js")
async def service_worker() -> Response:
    """提供 PWA Service Worker 腳本（掛載於根路徑 Scope 並設定更新標頭）。

    Returns:
        Response: JavaScript MIME 類型之 Service Worker 檔案內容。
    """
    # R12：檔案讀取移至工作執行緒；內容依 mtime 快取，避免每次請求重讀磁碟。
    content = await asyncio.to_thread(_read_service_worker_source_sync)
    response = Response(content, mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/api/version", methods=["GET"])
async def get_app_version() -> Response | tuple[Response, int] | Any:
    """回傳系統版本與最新 Commit Hash，供前端 Service Worker 進行本地快取比對與熱更新。

    Returns:
        Response: 包含 version、commit_hash 與 timestamp 之 JSON 數據。
    """
    git_info = await _async_get_git_info()
    return jsonify(
        {
            "status": "success",
            "version": APP_VERSION,
            "commit_hash": git_info.get("commit_hash", "unknown"),
            "timestamp": time.time(),
        }
    )


# ---------------------------------------------------------------------------
# API: Logs
# ---------------------------------------------------------------------------

# Log 檔副檔名白名單（避免讀取非日誌檔案）
_LOG_EXTENSIONS = {".jsonl", ".json", ".log"}
# 敏感字串黑名單（不允許讀取）
_LOG_BLACKLIST = {"config_override"}


_DB_LOG_SOURCES = [
    {
        "db": "chat_history.db",
        "table": "runtime_events",
        "name": "chat_history.db [runtime_events]",
        "label": "🔥 系統即時運作日誌 (runtime_events)",
    },
    {
        "db": "chat_history.db",
        "table": "error_events",
        "name": "chat_history.db [error_events]",
        "label": "⚠️ 系統錯誤日誌 (error_events)",
    },
    {
        "db": "chat_history.db",
        "table": "chat_messages",
        "name": "chat_history.db [chat_messages]",
        "label": "💬 對話歷史紀錄 (chat_messages)",
    },
    {
        "db": "chat_history.db",
        "table": "audit_logs",
        "name": "chat_history.db [audit_logs]",
        "label": "🛡️ 管理員審計紀錄 (audit_logs)",
    },
    {
        "db": "chat_history.db",
        "table": "roast_rules",
        "name": "chat_history.db [roast_rules]",
        "label": "🎯 吐槽關鍵字規則 (roast_rules)",
    },
    {
        "db": "sherry_memory.db",
        "table": "user_profiles",
        "name": "sherry_memory.db [user_profiles]",
        "label": "👤 使用者畫像名冊 (user_profiles)",
    },
    {
        "db": "sherry_memory.db",
        "table": "user_facts",
        "name": "sherry_memory.db [user_facts]",
        "label": "🧠 使用者長期記憶庫 (user_facts)",
    },
    {
        "db": "sherry_memory.db",
        "table": "user_aliases",
        "name": "sherry_memory.db [user_aliases]",
        "label": "🏷️ 使用者別名暱稱 (user_aliases)",
    },
    {
        "db": "sherry_memory.db",
        "table": "user_relations",
        "name": "sherry_memory.db [user_relations]",
        "label": "❤️ 使用者好感度關係 (user_relations)",
    },
    {
        "db": "world_memory.db",
        "table": "world_events",
        "name": "world_memory.db [world_events]",
        "label": "🌍 世界記憶重大事件 (world_events)",
    },
    {
        "db": "world_memory.db",
        "table": "world_summary",
        "name": "world_memory.db [world_summary]",
        "label": "📜 世界記憶每日摘要 (world_summary)",
    },
    {
        "db": "sherry_stock/stock_trader.db",
        "fallback_db": "stock_trader.db",
        "table": "thought_logs",
        "name": "stock_trader.db [thought_logs]",
        "label": "📈 美股量化決策思考 (thought_logs)",
    },
    {
        "db": "sherry_stock/stock_trader.db",
        "fallback_db": "stock_trader.db",
        "table": "trades",
        "name": "stock_trader.db [trades]",
        "label": "📊 美股交易紀錄 (trades)",
    },
    {
        "db": "sherry_stock/stock_trader.db",
        "fallback_db": "stock_trader.db",
        "table": "portfolio",
        "name": "stock_trader.db [portfolio]",
        "label": "💰 美股帳戶資產持倉 (portfolio)",
    },
    {
        "db": "deep_search/deep_search.db",
        "fallback_db": "deep_search.db",
        "table": "research_sessions",
        "name": "deep_search.db [research_sessions]",
        "label": "🔬 深度研究探索歷程 (research_sessions)",
    },
    {
        "db": "dnd/dnd.db",
        "fallback_db": "dnd.db",
        "table": "dnd_history",
        "name": "dnd.db [dnd_history]",
        "label": "🎲 D&D 跑團冒險世界歷史 (dnd_history)",
    },
    {
        "db": "dnd/dnd.db",
        "fallback_db": "dnd.db",
        "table": "characters",
        "name": "dnd.db [characters]",
        "label": "🧙 D&D 冒險者角色卡 (characters)",
    },
    {
        "db": "feed_sentinel/feed_sentinel.db",
        "fallback_db": "feed_sentinel.db",
        "table": "shared_posts",
        "name": "feed_sentinel.db [shared_posts]",
        "label": "📰 AI 情報哨兵文章庫 (shared_posts)",
    },
    {
        "db": "spark/spark.db",
        "fallback_db": "spark.db",
        "table": "triggers",
        "name": "spark.db [triggers]",
        "label": "⚡ 火花即時推播觸發器 (triggers)",
    },
    {
        "db": "schedules.db",
        "table": "schedule_tasks",
        "name": "schedules.db [schedule_tasks]",
        "label": "⏰ 任務排程提醒清單 (schedule_tasks)",
    },
]


_CACHED_LOG_FILES: list[dict[str, Any]] | None = None
_LAST_LOG_FILES_SCAN_TIME: float = 0.0
_LOG_FILES_CACHE_TTL: float = 30.0  # 快取 30 秒，減少 Windows NTFS 上大量 stat() 操作


def _list_log_files(force_refresh: bool = False) -> list[dict[str, Any]]:
    """掃描可用的日誌來源（包含 SQLite 資料庫表格與純文字日誌，支援 TTL 快取）。"""
    global _CACHED_LOG_FILES, _LAST_LOG_FILES_SCAN_TIME
    now = time.time()
    if not force_refresh and _CACHED_LOG_FILES is not None and (now - _LAST_LOG_FILES_SCAN_TIME) < _LOG_FILES_CACHE_TTL:
        return _CACHED_LOG_FILES

    sources: list[dict[str, Any]] = []

    # 1. 加入 SQLite 資料庫來源
    for src in _DB_LOG_SOURCES:
        db_path = settings.DATA_DIR / src["db"]
        if not db_path.exists() and "fallback_db" in src:
            alt = settings.DATA_DIR / src["fallback_db"]
            if alt.exists():
                db_path = alt
        if db_path.exists():
            sources.append(
                {
                    "name": src["name"],
                    "stem": src["table"],
                    "size_bytes": db_path.stat().st_size,
                    "modified": db_path.stat().st_mtime,
                    "is_db": True,
                    "label": src["label"],
                }
            )

    # 2. 加入純文字日誌檔
    if settings.DATA_DIR.exists():
        for p in sorted(settings.DATA_DIR.iterdir()):
            if p.is_file() and p.suffix in _LOG_EXTENSIONS:
                if any(bl in p.stem for bl in _LOG_BLACKLIST):
                    continue
                sources.append(
                    {
                        "name": p.name,
                        "stem": p.stem,
                        "size_bytes": p.stat().st_size,
                        "modified": p.stat().st_mtime,
                        "is_db": False,
                        "label": f"📄 {p.name}",
                    }
                )

    _CACHED_LOG_FILES = sources
    _LAST_LOG_FILES_SCAN_TIME = now
    return sources


@app.route("/api/logs/files", methods=["GET"])
async def get_log_files() -> Response | tuple[Response, int] | Any:
    """Return a list of available log files and DB sources in DATA_DIR（R3：僅限管理員）。"""
    if not _is_admin_request(request):
        return jsonify(
            {
                "status": "error",
                "message": "檢視日誌清單需要管理員權限，請先登入。",
            }
        ), 401

    all_files = await asyncio.to_thread(_list_log_files)
    return jsonify(all_files)


def _allowed_log_file_names() -> set[str]:
    """R3（報告 §5.3.5）：``/api/logs?file=`` 檔名白名單。

    僅允許已登錄之 DB 來源（``_DB_LOG_SOURCES``）與 DATA_DIR 內既有日誌檔
    （``_LOG_EXTENSIONS`` 副檔名、排除 ``_LOG_BLACKLIST``）。
    ``config_override`` 與路徑穿越名稱因不在白名單中而自然被拒。
    """
    allowed: set[str] = {src["name"] for src in _DB_LOG_SOURCES}
    allowed.update({"runtime_log.jsonl", "error_log.jsonl"})
    if settings.DATA_DIR.exists():
        for p in settings.DATA_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in _LOG_EXTENSIONS:
                if any(bl in p.stem for bl in _LOG_BLACKLIST):
                    continue
                allowed.add(p.name)
    return allowed


_LINE_COUNT_CACHE: dict[str, tuple[float, int, int]] = {}


def _count_file_lines(path: Path) -> int:
    """計算文字或 JSONL 檔案之有效非空白行數（支援 mtime/size 快取與串流統計）。

    Args:
        path: 檔案路徑。

    Returns:
        int: 檔案中有效非空白行總數。
    """
    try:
        st = path.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        return 0

    cache_key = str(path.resolve())
    cached = _LINE_COUNT_CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime and cached[1] == size:
        return cached[2]

    cnt = 0
    with open(path, "rb") as f:
        for line in f:
            if line.strip():
                cnt += 1

    _LINE_COUNT_CACHE[cache_key] = (mtime, size, cnt)
    return cnt


def _read_lines_reversed(path: Path, limit: int, skip: int = 0, block_size: int = 65536) -> list[str]:
    """從檔案末尾反向讀取指定數量的非空白行（由新到舊，保留行首縮排）。

    Args:
        path: 檔案路徑。
        limit: 最多欲讀取的行數。
        skip: 從檔案末尾算起略過的行數（用於分頁 offset）。
        block_size: 每次往前反向讀取的緩衝區大小位元組數。

    Returns:
        list[str]: 反向讀取到的文字行列表（最新記錄在最前）。
    """
    lines: list[str] = []
    skipped = 0
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        if file_size == 0:
            return []

        buffer = bytearray()
        pos = file_size

        while pos > 0 and len(lines) < limit:
            read_size = min(block_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            buffer = bytearray(chunk) + buffer

            while True:
                last_nl = buffer.rfind(b"\n")
                if last_nl == -1:
                    break
                line_bytes = buffer[last_nl + 1 :]
                buffer = buffer[:last_nl]
                if line_bytes.endswith(b"\r"):
                    line_bytes = line_bytes[:-1]
                line_str = line_bytes.decode("utf-8", errors="replace")
                if line_str.strip():
                    if skipped < skip:
                        skipped += 1
                    else:
                        lines.append(line_str.rstrip("\r\n"))
                        if len(lines) >= limit:
                            break

        if pos == 0 and len(lines) < limit and buffer:
            if buffer.endswith(b"\r"):
                buffer = buffer[:-1]
            line_str = buffer.decode("utf-8", errors="replace")
            if line_str.strip():
                if skipped < skip:
                    skipped += 1
                else:
                    lines.append(line_str.rstrip("\r\n"))

    return lines


def _query_sqlite_logs_sync(db_path: Path, table_name: str, page: int, size: int) -> dict[str, Any]:
    """於 Worker Thread 同步查詢 SQLite 日誌表格，避免阻塞 Quart 主事件迴圈。

    R7（報告 §5.2.7）：若表格具 `is_deleted` 欄位，查詢一律過濾軟刪資料。
    """
    from core.db import get_db_connection

    conn = None
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()

        cur.execute(f"PRAGMA table_info({table_name})")
        col_names = [col[1] for col in cur.fetchall()]
        where_clause = " WHERE is_deleted = 0" if "is_deleted" in col_names else ""

        cur.execute(f"SELECT COUNT(*) FROM {table_name}{where_clause}")
        total_cnt = cur.fetchone()[0]

        if total_cnt == 0:
            return {
                "entries": [],
                "total": 0,
                "page": page,
                "size": size,
                "pages": 1,
            }

        offset = (page - 1) * size

        order_col = (
            "id"
            if "id" in col_names
            else (
                "updated_at" if "updated_at" in col_names else ("created_at" if "created_at" in col_names else "rowid")
            )
        )
        cur.execute(
            f"SELECT * FROM {table_name}{where_clause} ORDER BY {order_col} DESC LIMIT ? OFFSET ?", (size, offset)
        )
        rows = cur.fetchall()

        entries: list[dict[str, Any]] = []
        for r in rows:
            row_dict = dict(r)
            if "data_json" in row_dict:
                try:
                    parsed = json.loads(row_dict["data_json"])
                    if isinstance(parsed, dict):
                        row_dict = {**row_dict, **parsed}
                except Exception:
                    pass
            elif "raw_json" in row_dict:
                try:
                    parsed = json.loads(row_dict["raw_json"])
                    if isinstance(parsed, dict):
                        row_dict = {**row_dict, **parsed}
                except Exception:
                    pass

            if "ts" not in row_dict and "timestamp" in row_dict:
                row_dict["ts"] = row_dict["timestamp"]
            elif "ts" not in row_dict and "updated_at" in row_dict:
                row_dict["ts"] = row_dict["updated_at"]
            elif "ts" not in row_dict and "created_at" in row_dict:
                row_dict["ts"] = row_dict["created_at"]

            ts_str = str(row_dict.get("ts", "") or "")
            if ts_str and ("T" in ts_str or ts_str.endswith("Z") or "+" in ts_str):
                try:
                    dt_obj = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                    row_dict["ts"] = dt_obj.astimezone(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            if "event" not in row_dict:
                if table_name == "chat_messages":
                    row_dict["event"] = "chat_reply"
                    row_dict["raw_input"] = row_dict.get("content", "")
                    row_dict["user"] = row_dict.get("display_name", "")
                elif table_name == "user_facts":
                    row_dict["event"] = "user_fact"
                    row_dict["message"] = (
                        f"[{row_dict.get('category')}] {row_dict.get('fact')} (重要度: {row_dict.get('importance')})"
                    )
                    row_dict["user"] = row_dict.get("user_id", "")
                elif table_name == "user_relations":
                    row_dict["event"] = "user_relation"
                    row_dict["message"] = (
                        f"好感度: {row_dict.get('affinity')} ({row_dict.get('tier')}) · 互動: {row_dict.get('interaction_count')} 次"
                    )
                    row_dict["user"] = row_dict.get("user_id", "")
                elif table_name == "trades":
                    row_dict["event"] = "trade"
                    row_dict["message"] = (
                        f"[{row_dict.get('action')}] {row_dict.get('shares')} 股 {row_dict.get('ticker')} @ ${row_dict.get('execution_price')} (損益: ${row_dict.get('realized_pnl')}) - {row_dict.get('reason')}"
                    )
                elif table_name == "thought_logs":
                    row_dict["event"] = "thought_log"
                    row_dict["cot_thought"] = row_dict.get("thought_process", "")
                    row_dict["reply_preview"] = row_dict.get("decision_summary", "")
                elif table_name == "dnd_history":
                    row_dict["event"] = "dnd"
                    row_dict["message"] = row_dict.get("description", "")
                elif table_name == "characters":
                    row_dict["event"] = "dnd_character"
                    row_dict["message"] = (
                        f"Lv.{row_dict.get('level')} {row_dict.get('race')} {row_dict.get('character_class')} (HP: {row_dict.get('hp_current')}/{row_dict.get('hp_max')}, AC: {row_dict.get('ac')})"
                    )
                    row_dict["user"] = row_dict.get("name", "")
                elif table_name == "shared_posts":
                    row_dict["event"] = "feed"
                    row_dict["message"] = f"[{row_dict.get('source')}] {row_dict.get('title')}"
                    row_dict["reply_preview"] = row_dict.get("sherry_analysis", "")
                elif table_name == "triggers":
                    row_dict["event"] = "trigger"
                    row_dict["message"] = (
                        f"[{row_dict.get('trigger_type')}] {row_dict.get('name', '')} (狀態: {'啟用' if row_dict.get('is_active') else '停用'})"
                    )
                elif table_name == "schedule_tasks":
                    row_dict["event"] = "schedule"
                    row_dict["message"] = (
                        f"[{row_dict.get('schedule_type')}] {row_dict.get('content')} (頻道: #{row_dict.get('channel_id')})"
                    )
                    row_dict["user"] = row_dict.get("display_name", "")
                else:
                    row_dict["event"] = table_name

            entries.append(row_dict)

        return {
            "entries": entries,
            "total": total_cnt,
            "page": page,
            "size": size,
            "pages": max(1, (total_cnt + size - 1) // size),
        }
    finally:
        if conn is not None:
            conn.close()


@app.route("/api/logs", methods=["GET"])
async def get_logs() -> Response | tuple[Response, int] | Any:
    """
    線上讀取指定檔案或 SQLite 資料庫表格之日誌紀錄。

    Query params:
      file  — filename or db table (default: chat_history.db [runtime_events])
      page  — 1-indexed page number (default: 1)
      size  — entries per page (default: 50, max: 200)
    """
    raw_filename: str = request.args.get("file", "chat_history.db [runtime_events]")
    page: int = max(1, int(request.args.get("page", 1)))
    size: int = min(200, max(1, int(request.args.get("size", 50))))

    # R3（報告 §5.3.1、§5.3.5）：日誌面全面需管理員權限＋檔名白名單（僅允許既有來源）。
    if not _is_admin_request(request):
        return jsonify(
            {
                "status": "error",
                "message": "檢視日誌需要管理員權限，請先登入。",
                "entries": [],
                "total": 0,
                "page": page,
                "size": size,
            }
        ), 401

    if raw_filename not in _allowed_log_file_names():
        return jsonify(
            {
                "status": "error",
                "message": "不允許的日誌檔案名稱（僅可查詢既有日誌來源）。",
                "entries": [],
                "total": 0,
                "page": page,
                "size": size,
            }
        ), 400

    # =========================================================================
    # A. 處理 SQLite 資料庫查詢
    # =========================================================================
    db_target = None
    if "[" in raw_filename and raw_filename.endswith("]"):
        db_part, table_part = raw_filename.split("[", 1)
        db_part = db_part.strip()
        table_part = table_part.rstrip("]").strip()
        # 尋找比對的 db 設定
        for src in _DB_LOG_SOURCES:
            if (
                src["name"] == raw_filename
                or (src["db"].endswith(db_part) and src["table"] == table_part)
                or (src.get("fallback_db") and src["fallback_db"].endswith(db_part) and src["table"] == table_part)
            ):
                db_file = settings.DATA_DIR / src["db"]
                if not db_file.exists() and "fallback_db" in src:
                    alt = settings.DATA_DIR / src["fallback_db"]
                    if alt.exists():
                        db_file = alt
                db_target = (db_file, src["table"])
                break
    elif raw_filename.lower() in ("runtime_log.jsonl", "error_log.jsonl"):
        tbl = "error_events" if raw_filename.lower().startswith("error") else "runtime_events"
        db_target = (settings.DATA_DIR / "chat_history.db", tbl)

    if db_target:
        db_path, table_name = db_target
        if db_path.exists():
            try:
                res_data = await asyncio.to_thread(_query_sqlite_logs_sync, db_path, table_name, page, size)
                return jsonify(res_data)
            except Exception as sql_err:
                err_id = log_safe_error("web_ui.logs.sqlite", sql_err)
                return jsonify(
                    {
                        "status": "error",
                        "message": f"讀取 SQLite 失敗（錯誤代碼 {err_id}）",
                        "error_id": err_id,
                        "entries": [],
                        "total": 0,
                        "page": page,
                        "size": size,
                    }
                ), 500

    # =========================================================================
    # B. 檔案日誌讀取 (備援或純文字日誌如 bot.log)
    # =========================================================================
    # R3（報告 §5.3.5）：檔名取 basename 後，resolve 結果仍須位於 DATA_DIR 邊界內。
    safe_name = Path(raw_filename).name
    log_path: Path = settings.DATA_DIR / safe_name
    try:
        if not log_path.resolve().is_relative_to(Path(settings.DATA_DIR).resolve()):
            return jsonify(
                {
                    "status": "error",
                    "message": "檔案路徑超出允許範圍。",
                    "entries": [],
                    "total": 0,
                    "page": page,
                    "size": size,
                }
            ), 400
    except OSError:
        return jsonify(
            {
                "status": "error",
                "message": "日誌路徑解析失敗。",
                "entries": [],
                "total": 0,
                "page": page,
                "size": size,
            }
        ), 400

    if not log_path.exists():
        return jsonify({"entries": [], "total": 0, "page": page, "size": size})

    entries = []

    try:
        suffix = log_path.suffix.lower()
        if suffix == ".json":
            content = await asyncio.to_thread(log_path.read_text, encoding="utf-8")
            obj = json.loads(content)
            all_entries = obj if isinstance(obj, list) else [obj]
            total = len(all_entries)
            all_entries = list(reversed(all_entries))
            start = (page - 1) * size
            entries = all_entries[start : start + size]
        else:
            # .jsonl, .log, .txt 等純文字/行導向日誌：使用反向分頁讀取
            start = (page - 1) * size
            total = await asyncio.to_thread(_count_file_lines, log_path)
            raw_lines = await asyncio.to_thread(_read_lines_reversed, log_path, size, start)
            if suffix == ".jsonl":
                for ln in raw_lines:
                    try:
                        parsed = json.loads(ln)
                        if isinstance(parsed, dict):
                            entries.append(parsed)
                        else:
                            entries.append({"event": "raw", "message": ln})
                    except Exception:
                        entries.append({"event": "raw", "message": ln})
            else:
                for ln in raw_lines:
                    entries.append({"event": "raw", "message": ln})

            if total < start + len(entries):
                total = start + len(entries)

    except Exception as exc:
        return jsonify(
            {
                "error": "日誌讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.logs.read", exc),
                "entries": [],
                "total": 0,
                "page": page,
                "size": size,
            }
        )

    return jsonify(
        {
            "entries": entries,
            "total": total,
            "page": page,
            "size": size,
            "pages": max(1, (total + size - 1) // size),
        }
    )


# ---------------------------------------------------------------------------
# API: Models
# ---------------------------------------------------------------------------


@app.route("/api/models/ollama", methods=["GET"])
async def get_ollama_models() -> Response | tuple[Response, int] | Any:
    """Proxy Ollama /api/tags to retrieve locally available model names."""
    base_url: str = settings.OLLAMA_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/api/tags")
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return jsonify({"models": models, "source": "ollama", "base_url": base_url})
    except Exception as exc:
        return jsonify(
            {
                "models": [],
                "source": "ollama",
                "error": "模型列表讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.models.ollama", exc),
            }
        )


@app.route("/api/models/freetoken", methods=["GET"])
@app.route("/api/models/llm", methods=["GET"])
async def get_freetoken_models() -> Response | tuple[Response, int] | Any:
    """Proxy FreeToken /v1/models to retrieve locally loaded Hugging Face MoE models."""
    base_url: str = getattr(
        settings, "LLM_BASE_URL", getattr(settings, "FREETOKEN_BASE_URL", "http://localhost:1919/v1")
    ).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/models")
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])] if isinstance(data, dict) else []
            return jsonify({"models": models, "source": "freetoken", "base_url": base_url})
    except Exception as exc:
        return jsonify(
            {
                "models": [],
                "source": "freetoken",
                "error": "模型列表讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.models.freetoken", exc),
            }
        )


@app.route("/api/models/lms", methods=["GET"])
async def get_lms_models() -> Response | tuple[Response, int] | Any:
    """Proxy LM Studio /v1/models to retrieve locally loaded model names."""
    base_url: str = settings.LMS_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/models")
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            return jsonify({"models": models, "source": "lms", "base_url": base_url})
    except Exception as exc:
        return jsonify(
            {
                "models": [],
                "source": "lms",
                "error": "模型列表讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.models.lms", exc),
            }
        )


@app.route("/api/models/vllm", methods=["GET"])
async def get_vllm_models() -> Response | tuple[Response, int] | Any:
    """Proxy vLLM /v1/models to retrieve locally loaded model names."""
    base_url: str = getattr(settings, "VLLM_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/models")
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            return jsonify({"models": models, "source": "vllm", "base_url": base_url})
    except Exception as exc:
        return jsonify(
            {
                "models": [],
                "source": "vllm",
                "error": "模型列表讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.models.vllm", exc),
            }
        )


# ---------------------------------------------------------------------------
# API: Auth
# ---------------------------------------------------------------------------


@app.route("/api/auth/login", methods=["POST"])
async def auth_login() -> tuple[Response, int] | Response:
    """Authenticate with password using constant-time comparison & brute-force protection."""
    import logging
    import secrets
    import time

    logger = logging.getLogger(__name__)
    ip = _get_client_ip(request)
    is_locked, remaining_sec = _is_ip_locked(ip)
    if is_locked:
        remaining_min = max(1, (remaining_sec + 59) // 60)
        logger.warning("[SECURITY] Blocked login attempt from locked IP: %s (%d sec remaining)", ip, remaining_sec)
        try:
            from mod.audit_manager import audit_manager

            await asyncio.to_thread(
                audit_manager.record_security_event,
                action="IP_BLOCKED_ATTEMPT",
                ip=ip,
                username="admin",
                details=f"已鎖定之 IP 嘗試存取登入介面，尚餘 {remaining_sec} 秒",
            )
        except Exception:
            pass
        return jsonify(
            {
                "status": "error",
                "message": f"登入失敗次數過多，該 IP 已暫時鎖定，請於 {remaining_min} 分鐘後再試！",
                "retry_after_sec": remaining_sec,
            }
        ), 429

    # R2（§5.3.7）：fail-closed——未配置任何憑證（WEB_UI_PASSWORD_HASH / WEB_UI_PASSWORD）時一律拒絕登入
    if not settings.is_web_ui_password_configured():
        logger.warning("[SECURITY] Login rejected: no admin credential configured (fail-closed)")
        return jsonify(
            {
                "status": "error",
                "code": "PASSWORD_NOT_CONFIGURED",
                "message": "管理員密碼尚未設定：請設定 WEB_UI_PASSWORD_HASH（或 WEB_UI_PASSWORD）環境變數，"
                "或執行「python -m web_ui.server --gen-password」生成憑證後寫入 .env。",
            }
        ), 403

    data: dict[str, Any] = await request.get_json() or {}
    password = str(data.get("password", ""))
    username = str(data.get("username", "admin")).strip() or "admin"

    # 恆定時間密碼比對（WEB_UI_PASSWORD_HASH 優先），徹底消滅時序攻擊 (Timing Attack)
    is_valid = settings.verify_web_ui_password(password)

    if not is_valid:
        max_attempts = getattr(settings, "WEB_UI_MAX_LOGIN_ATTEMPTS", 5)
        lockout_mins = getattr(settings, "WEB_UI_LOCKOUT_MINUTES", 15)
        attempt_count, locked_sec = _login_lockout.record_failure(
            ip,
            max_attempts=max_attempts,
            lockout_sec=lockout_mins * 60,
        )

        if locked_sec:
            logger.warning("[SECURITY] IP %s triggered brute-force lockout (%d attempts in 1 min)", ip, attempt_count)
            try:
                from mod.audit_manager import audit_manager

                await asyncio.to_thread(
                    audit_manager.record_security_event,
                    action="IP_LOCKED",
                    ip=ip,
                    username=username,
                    details=f"密碼連續錯誤達 {max_attempts} 次，觸發安全封鎖 {lockout_mins} 分鐘",
                )
            except Exception:
                pass
            return jsonify(
                {
                    "status": "error",
                    "message": f"密碼錯誤連達 {max_attempts} 次，該 IP 已觸發安全機制封鎖 {lockout_mins} 分鐘！",
                    "retry_after_sec": lockout_mins * 60,
                }
            ), 429

        logger.warning(
            "[SECURITY] Failed login attempt from IP %s for user '%s' (%d/%d)",
            ip,
            username,
            attempt_count,
            max_attempts,
        )
        try:
            from mod.audit_manager import audit_manager

            await asyncio.to_thread(
                audit_manager.record_security_event,
                action="LOGIN_FAILED",
                ip=ip,
                username=username,
                details=f"管理員登入密碼錯誤 ({attempt_count}/{max_attempts})",
            )
        except Exception:
            pass
        return jsonify(
            {
                "status": "error",
                "message": f"管理員密碼錯誤！（警告：連續失敗 {max_attempts} 次將自動鎖定 IP）",
            }
        ), 401

    # 登入成功 — 重置該 IP 的失敗記錄
    _login_lockout.clear(ip)

    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = time.time()
    logger.info("[SECURITY] Admin login successful: User='%s', IP=%s", username, ip)

    # 寫入審計紀錄 (Audit Log)（R12：同步 SQLite/檔案寫入移至工作執行緒）
    try:
        from mod.audit_manager import audit_manager

        await asyncio.to_thread(
            audit_manager.record_login,
            session_id=token,
            username=username,
            ip=ip,
            user_agent=request.headers.get("User-Agent", ""),
        )
    except Exception as exc:
        logger.warning("Failed to record audit login: %s", exc)

    resp = jsonify(
        {
            "status": "success",
            "token": token,
            "username": username,
            "expires_in_sec": int(_SESSION_TIMEOUT_SEC),
            "message": f"管理員 {username} 登入成功 (已發行 HttpOnly 資安 Cookie，10分鐘無動作自動登出)",
        }
    )
    # 設定 HttpOnly Secure Cookie 防止 XSS 腳本竊取 Session Token
    resp.set_cookie(
        "admin_session",
        token,
        max_age=int(_SESSION_TIMEOUT_SEC),
        httponly=True,
        secure=True,
        samesite="Strict",
        path="/",
    )
    # R1：簽發 CSRF double-submit cookie（登入即建立，前端放入 X-CSRF-Token）
    _set_csrf_cookie(resp)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
async def auth_logout() -> Response | tuple[Response, int] | Any:
    """Logout, revoke session token, and purge HttpOnly cookie."""
    data: dict[str, Any] = await request.get_json() or {}
    token = data.get("token", "")
    if not token:
        token = _get_request_token(request)

    if token:
        _admin_sessions.pop(token, None)
        try:
            from mod.audit_manager import audit_manager

            await asyncio.to_thread(audit_manager.record_logout, token)
        except Exception:
            pass

    resp = jsonify({"status": "success", "message": "已成功登出管理員模式"})
    resp.delete_cookie("admin_session", path="/")
    resp.delete_cookie(_CSRF_COOKIE_NAME, path="/")
    return resp


@app.route("/api/auth/check", methods=["GET"])
async def auth_check() -> Response | tuple[Response, int] | Any:
    """Check if the current session token is valid and active."""
    is_admin = _is_admin_request(request)
    return jsonify(
        {
            "is_admin": is_admin,
            "timeout_sec": int(_SESSION_TIMEOUT_SEC),
        }
    )


_SERVICES_CACHE: dict[str, Any] | None = None
_SERVICES_CACHE_TIME: float = 0.0
_SERVICES_CACHE_TTL: float = 5.0  # 快取 5 秒，與前端輪詢對齊，並避免短時間高頻重複探測
_LOCAL_PROBE_TIMEOUT = httpx.Timeout(connect=0.8, read=2.0, write=2.0, pool=2.0)


async def _probe_ollama(client: httpx.AsyncClient) -> dict[str, Any]:
    """探測 Ollama 服務狀態與可用模型清單。"""
    ollama_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        t0 = time.time()
        resp = await client.get(f"{ollama_url}/api/tags")
        ping_ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("name") for m in data.get("models", [])]
            return {
                "id": "ollama",
                "name": "Ollama LLM 服務",
                "icon": "🦙",
                "url": ollama_url,
                "status": "online",
                "ping_ms": ping_ms,
                "details": f"服務線上，共偵測到 {len(models)} 個可用模型",
                "models": models[:6],
            }
        return {
            "id": "ollama",
            "name": "Ollama LLM 服務",
            "icon": "🦙",
            "url": ollama_url,
            "status": "offline",
            "ping_ms": ping_ms,
            "details": f"HTTP {resp.status_code}",
            "models": [],
        }
    except Exception:
        return {
            "id": "ollama",
            "name": "Ollama LLM 服務",
            "icon": "🦙",
            "url": ollama_url,
            "status": "offline",
            "ping_ms": 0,
            "details": "未啟動或連線超時",
            "models": [],
        }


async def _probe_freetoken(client: httpx.AsyncClient) -> dict[str, Any]:
    """探測 FreeToken MoE 引擎狀態。"""
    freetoken_url = getattr(
        settings, "LLM_BASE_URL", getattr(settings, "FREETOKEN_BASE_URL", "http://localhost:1919/v1")
    ).rstrip("/")
    try:
        t0 = time.time()
        resp = await client.get(f"{freetoken_url}/models")
        ping_ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("id") for m in data.get("data", [])] if isinstance(data, dict) else []
            return {
                "id": "freetoken",
                "name": "FreeToken MoE 引擎",
                "icon": "🧠",
                "url": freetoken_url,
                "status": "online",
                "ping_ms": ping_ms,
                "details": f"服務線上，已加載 {len(models)} 個 MoE 專家模型",
                "models": models[:6],
            }
        return {
            "id": "freetoken",
            "name": "FreeToken MoE 引擎",
            "icon": "🧠",
            "url": freetoken_url,
            "status": "offline",
            "ping_ms": ping_ms,
            "details": f"HTTP {resp.status_code}",
            "models": [],
        }
    except Exception:
        return {
            "id": "freetoken",
            "name": "FreeToken MoE 引擎",
            "icon": "🧠",
            "url": freetoken_url,
            "status": "offline",
            "ping_ms": 0,
            "details": "未啟動或連線超時 (Port 1919)",
            "models": [],
        }


async def _probe_lms(client: httpx.AsyncClient) -> dict[str, Any]:
    """探測 LM Studio 服務狀態。"""
    lms_url = getattr(settings, "LMS_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    try:
        t0 = time.time()
        resp = await client.get(f"{lms_url}/models")
        ping_ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("id") for m in data.get("data", [])]
            return {
                "id": "lms",
                "name": "LM Studio 服務",
                "icon": "🤖",
                "url": lms_url,
                "status": "online",
                "ping_ms": ping_ms,
                "details": f"服務線上，已加載 {len(models)} 個模型",
                "models": models[:6],
            }
        return {
            "id": "lms",
            "name": "LM Studio 服務",
            "icon": "🤖",
            "url": lms_url,
            "status": "offline",
            "ping_ms": ping_ms,
            "details": f"HTTP {resp.status_code}",
            "models": [],
        }
    except Exception:
        return {
            "id": "lms",
            "name": "LM Studio 服務",
            "icon": "🤖",
            "url": lms_url,
            "status": "offline",
            "ping_ms": 0,
            "details": "未啟動或連線超時",
            "models": [],
        }


async def _probe_vllm(client: httpx.AsyncClient) -> dict[str, Any]:
    """探測 vLLM 引擎服務狀態。"""
    vllm_url = getattr(settings, "VLLM_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    try:
        t0 = time.time()
        resp = await client.get(f"{vllm_url}/models")
        ping_ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("id") for m in data.get("data", [])]
            return {
                "id": "vllm",
                "name": "vLLM 引擎服務",
                "icon": "⚡",
                "url": vllm_url,
                "status": "online",
                "ping_ms": ping_ms,
                "details": f"服務線上，已加載 {len(models)} 個模型",
                "models": models[:6],
            }
        return {
            "id": "vllm",
            "name": "vLLM 引擎服務",
            "icon": "⚡",
            "url": vllm_url,
            "status": "offline",
            "ping_ms": ping_ms,
            "details": f"HTTP {resp.status_code}",
            "models": [],
        }
    except Exception:
        return {
            "id": "vllm",
            "name": "vLLM 引擎服務",
            "icon": "⚡",
            "url": vllm_url,
            "status": "offline",
            "ping_ms": 0,
            "details": "未啟動或連線超時 (Port 8000)",
            "models": [],
        }


async def _probe_sd_webui(client: httpx.AsyncClient) -> dict[str, Any]:
    """探測 Stable Diffusion WebUI / ComfyUI 繪圖引擎狀態。"""
    sd_url = getattr(settings, "SD_WEBUI_URL", "http://127.0.0.1:7860").rstrip("/")
    try:
        t0 = time.time()
        resp = await client.get(f"{sd_url}/sdapi/v1/options")
        ping_ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            return {
                "id": "sd_webui",
                "name": "Stable Diffusion WebUI",
                "icon": "🎨",
                "url": sd_url,
                "status": "online",
                "ping_ms": ping_ms,
                "details": "地端繪圖引擎 API 運作良好",
                "models": [],
            }
        return {
            "id": "sd_webui",
            "name": "Stable Diffusion WebUI",
            "icon": "🎨",
            "url": sd_url,
            "status": "offline",
            "ping_ms": ping_ms,
            "details": f"HTTP {resp.status_code}",
            "models": [],
        }
    except Exception:
        return {
            "id": "sd_webui",
            "name": "Stable Diffusion WebUI",
            "icon": "🎨",
            "url": sd_url,
            "status": "offline",
            "ping_ms": 0,
            "details": "未啟動或連線超時 (Port 7860)",
            "models": [],
        }


@app.route("/api/services/status", methods=["GET"])
async def get_services_status() -> Response | tuple[Response, int] | Any:
    """即時檢測 Ollama, LM Studio, Stable Diffusion / ComfyUI 等地端 AI 服務連線狀態與延遲（並行探測與快取）。"""
    global _SERVICES_CACHE, _SERVICES_CACHE_TIME
    now = time.time()
    if _SERVICES_CACHE is not None and (now - _SERVICES_CACHE_TIME) < _SERVICES_CACHE_TTL:
        return jsonify(_SERVICES_CACHE)

    async with httpx.AsyncClient(timeout=_LOCAL_PROBE_TIMEOUT) as client:
        results = await asyncio.gather(
            _probe_ollama(client),
            _probe_freetoken(client),
            _probe_lms(client),
            _probe_vllm(client),
            _probe_sd_webui(client),
            return_exceptions=True,
        )

    services: list[dict[str, Any]] = []
    _probe_defaults: list[tuple[str, str, str]] = [
        ("ollama", "Ollama LLM 服務", "🦙"),
        ("freetoken", "FreeToken MoE 引擎", "🧠"),
        ("lms", "LM Studio 服務", "🤖"),
        ("vllm", "vLLM 引擎服務", "⚡"),
        ("sd_webui", "Stable Diffusion WebUI", "🎨"),
    ]
    for idx, r in enumerate(results):
        if isinstance(r, dict):
            services.append(r)
        elif isinstance(r, Exception):
            srv_id, srv_name, srv_icon = (
                _probe_defaults[idx] if idx < len(_probe_defaults) else ("unknown", "未知服務", "❓")
            )
            err_id = log_safe_error("web_ui.services.probe", r, level=logging.WARNING)
            services.append(
                {
                    "id": srv_id,
                    "name": srv_name,
                    "icon": srv_icon,
                    "url": "",
                    "status": "offline",
                    "ping_ms": 0,
                    "details": f"探測異常（錯誤代碼 {err_id}）",
                    "models": [],
                }
            )

    response_data = {
        "services": services,
        "checked_at": datetime.now(_TZ_TW).strftime("%H:%M:%S"),
    }
    _SERVICES_CACHE = response_data
    _SERVICES_CACHE_TIME = now
    return jsonify(response_data)


@app.route("/api/audit/logs", methods=["GET"])
async def get_audit_logs() -> tuple[Response, int] | Response:
    """取得管理員登入與操作歷史審計紀錄（僅限登入管理員）。"""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "審計紀錄僅限登入管理員查看！"}), 403
    from mod.audit_manager import audit_manager

    history = await asyncio.to_thread(audit_manager.get_audit_history, limit=50)
    return jsonify({"status": "success", "history": history})


@app.route("/api/audit/user-connections", methods=["GET"])
async def get_user_connections() -> tuple[Response, int] | Response:
    """取得一般使用者與訪客即時連線狀況與歷史紀錄（僅限登入管理員）。"""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "需要登入管理員權限！"}), 403

    summary = await asyncio.to_thread(user_connection_tracker.get_online_summary)
    history = await asyncio.to_thread(user_connection_tracker.get_connection_history, limit=100)
    return jsonify(
        {
            "status": "success",
            "summary": summary,
            "active_visitors": summary.get("active_visitors", []),
            "history": history,
        }
    )


@app.route("/api/user/heartbeat", methods=["POST"])
async def handle_user_heartbeat() -> Response | tuple[Response, int]:
    """接收一般訪客客戶端定期心跳，維護連線池活躍狀態。"""
    try:
        data = await request.get_json(silent=True)
        if not data:
            raw = await request.get_data(as_text=True)
            if raw:
                data = json.loads(raw)
        data = data or {}
    except Exception:
        data = {}
    vid = data.get("visitor_id") or request.cookies.get("visitor_id", "").strip()
    path = data.get("path") or "/"
    ip = _get_client_ip(request)
    ua = str(request.headers.get("User-Agent", ""))

    if not vid:
        vid = f"vis_{secrets.token_hex(8)}"
        g.new_visitor_id = vid
        await asyncio.to_thread(
            user_connection_tracker.touch_connection,
            visitor_id=vid,
            ip=ip,
            user_agent=ua,
            path=path,
        )
    else:
        ok = user_connection_tracker.record_heartbeat(vid)
        if not ok:
            await asyncio.to_thread(
                user_connection_tracker.touch_connection,
                visitor_id=vid,
                ip=ip,
                user_agent=ua,
                path=path,
            )

    resp = jsonify({"status": "success", "visitor_id": vid, "state": "online"})
    new_vid = getattr(g, "new_visitor_id", None)
    if new_vid:
        resp.set_cookie(
            "visitor_id",
            new_vid,
            max_age=30 * 86400,
            httponly=False,
            samesite="Lax",
            path="/",
        )
    return resp


@app.route("/api/user/disconnect", methods=["POST"])
async def handle_user_disconnect() -> Response | tuple[Response, int]:
    """接收頁面關閉或離線 Beacon，結算並標記訪客連線為離線。"""
    try:
        data = await request.get_json(silent=True)
        if not data:
            raw = await request.get_data(as_text=True)
            if raw:
                data = json.loads(raw)
        data = data or {}
    except Exception:
        data = {}
    vid = data.get("visitor_id") or request.cookies.get("visitor_id", "").strip()
    if vid:
        await asyncio.to_thread(user_connection_tracker.record_disconnect, vid)
    return jsonify({"status": "success"})


# ---------------------------------------------------------------------------
# API: Soul Content & Settings
# ---------------------------------------------------------------------------


@app.route("/api/soul/content", methods=["GET"])
async def get_soul_content() -> tuple[Response, int] | Response:
    """讀取特定人設檔之內容，供 Web UI 動態切換預覽。"""
    filename = request.args.get("file", "").strip()
    if not filename or not filename.endswith(".md"):
        return jsonify({"status": "error", "message": "無效的檔名"}), 400
    safe_name = Path(filename).name
    target_file = getattr(settings, "SOUL_DIR", Path("file/soul")) / safe_name
    if not target_file.exists():
        return jsonify({"status": "error", "message": "檔案不存在"}), 404
    try:
        content = await asyncio.to_thread(target_file.read_text, encoding="utf-8")
        return jsonify({"status": "success", "filename": safe_name, "content": content})
    except Exception as exc:
        return jsonify(
            {
                "status": "error",
                "message": "讀取人設檔失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.soul.content", exc),
            }
        ), 500


def _apply_setting_updates_sync(updates: list[tuple[str, Any]]) -> list[str]:
    """批次套用設定更新（含 config_override.json 寫檔），於工作執行緒呼叫。

    Args:
        updates: 已通過白名單與型別清洗的 (key, value) 清單。

    Returns:
        list[str]: 寫入失敗的設定鍵。
    """
    failed: list[str] = []
    for key, value in updates:
        if not settings.update_setting(key, value):
            failed.append(key)
    return failed


@app.route("/api/settings", methods=["GET", "POST"])
async def handle_settings() -> tuple[Response, int] | Response:
    """Get or update editable settings with strict Token/Key exclusion."""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "需要登入管理員權限！"}), 401

    sensitive_keywords = ("TOKEN", "PASSWORD", "SECRET", "KEY", "PATH", "DIR")
    non_sensitive_whitelist = {
        "LLM_MAX_OUTPUT_TOKENS",
        "LLM_MAX_CONTEXT_TOKENS",
        "CONTEXT_COMPRESS_TOKEN_THRESHOLD",
    }

    if request.method == "GET":
        # R12：全設定掃描與人設檔讀取皆為阻塞 I/O，移至工作執行緒。
        data = await asyncio.to_thread(settings.get_editable_settings)
        # 資安防護：徹底移除所有含有 TOKEN、KEY、PASSWORD、SECRET 的敏感欄位，完全不輸出在 Web UI
        filtered_data = {
            k: v
            for k, v in data.items()
            if k in non_sensitive_whitelist or not any(kw in k.upper() for kw in sensitive_keywords)
        }
        return jsonify(filtered_data)

    # POST — batch update with Strict Anti-Injection & Type Sanitization
    from web_ui.settings_schema import is_web_editable_key

    post_data: dict[str, Any] = await request.get_json() or {}
    failures: list[str] = []
    pending_updates: list[tuple[str, Any]] = []
    for k, raw_v in post_data.items():
        # 1. 防注入與路徑安全：設定 Key 必須轉為純文字 string 且符合合法的大寫變數命名規則
        k_str = str(k).strip()
        if not k_str.isupper() or not k_str.isidentifier():
            failures.append(k_str)
            continue

        # 2. R2（§5.1.2 A3）：白名單反轉——僅允許 SETTINGS_META 登錄之設定鍵，
        #    憑證/路徑/token 等非 schema 鍵一律拒絕（不再依賴關鍵字黑名單）。
        if not is_web_editable_key(k_str):
            failures.append(k_str)
            continue

        # 3. 防注入過濾：若非基本型別（如試圖傳遞 dict/list 等複雜結構），一律強制轉為純文字 string
        #    若是正常字串也確保以單純 str 型態處理，阻斷任何命令/語法注入
        safe_v: Any = raw_v
        if isinstance(raw_v, list):
            safe_v = [str(item).strip() for item in raw_v if isinstance(item, (str, int, float, bool))]
        elif isinstance(raw_v, str):
            safe_v = str(raw_v).strip()
            if safe_v.endswith(":") and any(
                safe_v == f"{p}:"
                for p in (
                    "freetoken",
                    "ollama",
                    "lms",
                    "vllm",
                    "openai",
                    "deepseek",
                    "gemini",
                    "openrouter",
                    "claude",
                    "groq",
                )
            ):
                safe_v = ""
        elif isinstance(raw_v, (int, float, bool)):
            safe_v = raw_v
        else:
            safe_v = str(raw_v)

        pending_updates.append((k_str, safe_v))

    if pending_updates:
        # R12：整批設定寫檔（含 config_override.json）一次移至工作執行緒。
        failures.extend(await asyncio.to_thread(_apply_setting_updates_sync, pending_updates))

    if not failures:
        try:
            from mod.audit_manager import audit_manager

            token = _get_request_token(request)
            saved_keys = ", ".join(list(post_data.keys())[:5])
            if len(post_data) > 5:
                saved_keys += f" (共{len(post_data)}項)"
            await asyncio.to_thread(
                audit_manager.record_action,
                token,
                "修改系統設定",
                f"成功保存設定: {saved_keys}",
            )
        except Exception:
            pass
        return jsonify({"status": "success"})
    return (
        jsonify({"status": "partial_error", "failed_keys": failures}),
        400,
    )


# ---------------------------------------------------------------------------
# 行程生命週期（R12）：延遲退出動作改以 async task 執行，阻塞 I/O 全數 to_thread
# ---------------------------------------------------------------------------

_SHUTDOWN_DELAY_SECONDS = 0.8
_PROCESS_ACTION_TASKS: set[asyncio.Task[Any]] = set()


def _write_shutdown_signal_sync(payload: str) -> None:
    """寫入 shutdown.signal（於工作執行緒呼叫）。"""
    try:
        (settings.DATA_DIR / "shutdown.signal").write_text(payload, encoding="utf-8")
    except Exception as exc:
        logger.debug("Failed to write shutdown signal: %s", exc)


async def _write_shutdown_signal(payload: str) -> None:
    await asyncio.to_thread(_write_shutdown_signal_sync, payload)


def _launch_exit_script_sync(script_stem: str) -> None:
    """啟動外部重啟/更新腳本（於工作執行緒呼叫；Popen 為阻塞呼叫）。"""
    import subprocess
    import sys

    if sys.platform == "win32":
        if os.environ.get("SHERRY_TAB_RUNNER") == "1":
            logger.info(
                "Running under Sherry Tab Runner (run_core.bat), in-place %s will trigger automatically.",
                script_stem,
            )
            return
        logger.info("Launching %s.bat ...", script_stem)
        bat = os.path.join(os.getcwd(), f"{script_stem}.bat")
        try:
            subprocess.Popen(
                ["cmd.exe", "/C", bat],
                cwd=os.getcwd(),
                creationflags=0x00000010,  # CREATE_NEW_CONSOLE (完全脫離 VS Code 終端)
                close_fds=True,
            )
        except Exception as e:
            logger.error("Failed to launch %s.bat: %s", script_stem, e)
    else:
        logger.info("Launching %s.sh ...", script_stem)
        sh = os.path.join(os.getcwd(), f"{script_stem}.sh")
        try:
            subprocess.Popen(
                ["/bin/bash", sh],
                cwd=os.getcwd(),
                start_new_session=True,
                close_fds=True,
            )
        except Exception as e:
            logger.error("Failed to launch %s.sh: %s", script_stem, e)


async def _checkpoint_before_exit(label: str) -> None:
    """退出前 checkpoint 所有資料庫並捕獲系統恢復快照。"""
    try:
        from core.recovery.manager import get_recovery_manager
        if _bot_instance:
            music_cog = _bot_instance.get_cog("MusicHandler")
            if music_cog:
                setattr(music_cog, "_is_shutting_down", True)
        await get_recovery_manager().shutdown_handler()
    except Exception as rec_err:
        logger.warning("Recovery snapshot before %s failed: %s", label, rec_err)

    try:
        from core.db import checkpoint_all_dbs

        await asyncio.to_thread(user_connection_tracker.flush_all_active)
        await asyncio.to_thread(checkpoint_all_dbs)
    except Exception as cp_err:
        logger.warning("Checkpoint before %s failed: %s", label, cp_err)


def _flush_logs_and_exit() -> None:
    """關閉 logging 並立即結束行程（於工作執行緒呼叫）。"""
    logging.shutdown()
    os._exit(0)  # 立即由 OS 釋放 Socket，避免 Python GC 出現 SSL Transport 錯誤


def _schedule_process_action(coro: Coroutine[Any, Any, None]) -> None:
    """排程延遲退出動作；保留 Task 強引用避免被 GC 提前回收。"""
    task = asyncio.create_task(coro)
    _PROCESS_ACTION_TASKS.add(task)
    task.add_done_callback(_PROCESS_ACTION_TASKS.discard)


async def _delayed_restart_action() -> None:
    """0.8 秒後執行自重啟：訊號、外部腳本、checkpoint 全部離開事件迴圈。"""
    await asyncio.sleep(_SHUTDOWN_DELAY_SECONDS)
    await _write_shutdown_signal("restart")
    await asyncio.to_thread(_launch_exit_script_sync, "restart_sherry")
    await _checkpoint_before_exit("restart")
    await asyncio.to_thread(_flush_logs_and_exit)


async def _delayed_stop_action() -> None:
    """0.8 秒後執行自我關閉。"""
    await asyncio.sleep(_SHUTDOWN_DELAY_SECONDS)
    logger.info("Stopping Sherry bot per admin request...")
    await _write_shutdown_signal(f"stop:{time.time()}")
    await _checkpoint_before_exit("stop")
    await asyncio.to_thread(_flush_logs_and_exit)


async def _delayed_update_action() -> None:
    """0.8 秒後執行 git pull + 重啟。"""
    await asyncio.sleep(_SHUTDOWN_DELAY_SECONDS)
    await _write_shutdown_signal("update")
    await asyncio.to_thread(_launch_exit_script_sync, "update_and_restart")
    await _checkpoint_before_exit("update")
    await asyncio.to_thread(_flush_logs_and_exit)


@app.route("/api/restart", methods=["POST"])
async def restart_bot() -> Response | tuple[Response, int]:
    """Trigger a self-restart via bat launcher (conda-env safe, VS Code compatible)."""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "重啟雪莉僅限登入管理員使用，請先登入！"}), 403

    try:
        from mod.audit_manager import audit_manager

        token = _get_request_token(request)
        await asyncio.to_thread(audit_manager.record_action, token, "系統操作", "觸發自重啟雪莉機器人")
    except Exception:
        pass

    _schedule_process_action(_delayed_restart_action())
    return jsonify({"status": "restarting", "message": "雪莉機器人正在重新啟動中，請稍候約 5-10 秒..."})


@app.route("/api/stop", methods=["POST"])
async def stop_bot() -> Response | tuple[Response, int]:
    """Trigger a self-shutdown of the Sherry bot process. Admin-only."""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "關閉雪莉僅限登入管理員使用，請先登入！"}), 403

    try:
        from mod.audit_manager import audit_manager

        token = _get_request_token(request)
        await asyncio.to_thread(audit_manager.record_action, token, "系統操作", "觸發關閉/停止雪莉機器人")
    except Exception:
        pass

    _schedule_process_action(_delayed_stop_action())
    return jsonify({"status": "stopping", "message": "雪莉機器人已接收關閉指令，即將停止運行。"})


@app.route("/api/update", methods=["POST"])
async def update_and_restart() -> Response | tuple[Response, int] | Any:
    """Git pull origin dev and restart via bat launcher."""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "需要登入管理員權限！"}), 401

    _schedule_process_action(_delayed_update_action())
    return jsonify({"status": "updating", "message": "正在從 dev 分支拉取最新代碼並重新啟動雪莉，請稍候約 10-20 秒..."})


# ---------------------------------------------------------------------------
# Web UI Chat (CHAT 強制觸發與穩定性測試 API)
# ---------------------------------------------------------------------------


@app.route("/api/chat/test", methods=["POST"])
async def test_chat() -> Response | tuple[Response, int]:
    """Web UI CHAT 強制調用與穩定性測試接口。"""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "測試與聊天功能僅限管理員使用，請先登入！"}), 403

    if not _bot_instance:
        return jsonify({"status": "error", "message": "機器人主程序尚未連線，無法執行 BotBrain 推理！"}), 500

    data: dict[str, Any] = await request.get_json() or {}
    user_message = str(data.get("message", "")).strip()
    if not user_message:
        return jsonify({"status": "error", "message": "訊息不可為空！"}), 400

    brain = _bot_instance.get_cog("BotBrain")
    if not brain:
        return jsonify({"status": "error", "message": "BotBrain 模組未載入，無法進行對話生成！"}), 500

    import asyncio
    import logging
    import time
    from handlers.bot_brain import InferenceTask
    from handlers.social.persona import build_dynamic_persona
    from handlers.social.tools.chat_tools import _should_trigger_search
    from handlers.social.tools.search_tools import fetch_web_and_entity_context

    logger = logging.getLogger(__name__)

    web_context = ""
    ref_link = ""
    try:
        if _should_trigger_search(user_message):
            web_context, ref_link = await fetch_web_and_entity_context(user_message)
    except Exception as e:
        logger.warning("[WebChat] 即時檢索失敗，將以普通對話生成: %s", e)

    head_persona, tail_lore = build_dynamic_persona(user_message, "CHAT", channel_id=None)

    sys_prompt = (
        f"{head_persona}\n\n"
        + (
            f"\n{web_context}\n⚠️【情報整合分析指令】：請參考上述『即時網路與實體檢索情報』，結合事實數據與妳的角色性格，為對方給出深入且充滿特色的解答與分析！\n\n"
            if web_context
            else ""
        )
        + f"{user_message}\n"
        + (f"\n{tail_lore}\n" if tail_lore else "")
        + "【回覆規則】\n"
        + "1. 🎯 將 100% 注意力集中在『當下必須回應的目標』，給出最直接的回覆。\n"
        + "2. 【豐富生動的對話】：請徹底貫徹妳當前的角色人設特質與語氣風格進行對話！可以講 2～4 句話，內容要有情緒起伏與自然互動。\n"
        + "3. 不知道的事請直說，不要臆造資訊。\n"
        + "4. 只輸出妳說的話，不要任何標籤、前綴。\n"
        + "5. 絕對禁止使用 Markdown 格式 (包含反引號、粗體等)。"
    )

    user_instruct = f"🎯 對方（Web 穩定性測試員）剛剛對妳說：「{user_message}」\n妳的回覆（正在協助進行穩定性測試）："

    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    async def _on_reply(reply: str) -> None:
        if not fut.done():
            fut.set_result(reply.strip())

    t_start = time.time()
    task = InferenceTask(
        priority=settings.PRIORITY_TAG_RESPONSE,
        system_prompt=sys_prompt,
        user_prompt=user_instruct,
        callback=_on_reply,
        model=settings.PRIMARY_MODEL,
        task_id=f"web-chat-{int(time.time() * 1000)}",
    )
    await brain.enqueue_task(task)

    try:
        raw_reply = await asyncio.wait_for(fut, timeout=60.0)
        elapsed_ms = int((time.time() - t_start) * 1000)

        # 抽離 CoT 思維鏈並精確寫入 runtime_log.jsonl (含耗時 latency_ms 毫秒)
        from handlers.social.utils import _process_micro_cot_extraction

        clean_reply, _ = _process_micro_cot_extraction(
            raw_reply,
            prompt=user_message,
            intent="CHAT",
            model=settings.PRIMARY_MODEL,
            system_prompt=sys_prompt,
            user_prompt=user_instruct,
            latency_ms=elapsed_ms,
        )

        if ref_link and "http" not in clean_reply:
            clean_reply += f"\n\n📂 參考來源： {ref_link}"
        logger.info("💬 [Web UI CHAT] User: '%s' -> Sherry: '%s' (%d ms)", user_message, clean_reply, elapsed_ms)
        return jsonify(
            {
                "status": "success",
                "reply": clean_reply,
                "intent": "CHAT (強制觸發測試)",
                "model": settings.PRIMARY_MODEL,
                "latency_ms": elapsed_ms,
            }
        )
    except asyncio.TimeoutError:
        return jsonify(
            {"status": "error", "message": "生成回答逾時 (超過 60 秒)，請檢查 BotBrain 與模型連線狀態！"}
        ), 504
    except Exception as e:
        logger.error("Web UI CHAT test error: %s", e, exc_info=True)
        return jsonify(
            {
                "status": "error",
                "message": "回答生成錯誤，請稍後再試",
                "error_id": log_safe_error("web_ui.chat.test", e),
            }
        ), 500


# ---------------------------------------------------------------------------
# System Metrics & Tasks APIs
# ---------------------------------------------------------------------------

from core.sys_inspector.sampler import get_system_metrics_async as _async_get_system_metrics


@app.route("/api/system/metrics", methods=["GET"])
async def get_system_metrics() -> Response | tuple[Response, int] | Any:
    """Return real-time system resource metrics."""
    metrics = await _async_get_system_metrics()
    return jsonify(metrics)


async def _async_get_background_metrics() -> dict[str, Any]:
    """收集自主治理預算、佇列深度與背景系統（SPARK/Care/Memory/Nightly/Observability）度量。"""

    def _collect_sync() -> dict[str, Any]:
        import sqlite3
        from core.autonomy_guard import global_autonomy_guard
        from handlers.social.observability import get_observability_stats

        gov_status = global_autonomy_guard.get_budget_status()
        obs_status = get_observability_stats()

        # SPARK 指標
        spark_stats: dict[str, Any] = {"active_triggers": 0, "total_triggers": 0}
        try:
            from handlers.spark.repository import SparkRepository

            spark_repo = SparkRepository()
            all_triggers = spark_repo.get_all()
            spark_stats["total_triggers"] = len(all_triggers)
            spark_stats["active_triggers"] = sum(
                1
                for t in all_triggers.values()
                if getattr(t, "is_active", True) and not getattr(t, "is_deleted", False)
            )
        except Exception as e:
            spark_stats["error"] = str(e)

        # Care 指標
        care_stats: dict[str, Any] = {"opted_in_users": 0, "pending_topics": 0, "actions_today": 0}
        try:
            from handlers.care.repository import CareRepository

            care_repo = CareRepository()
            with sqlite3.connect(str(care_repo._db_path), timeout=3.0) as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM care_preferences WHERE opted_in = 1")
                care_stats["opted_in_users"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM care_topics")
                care_stats["pending_topics"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM care_actions WHERE timestamp >= date('now', 'start of day')")
                care_stats["actions_today"] = int(cur.fetchone()[0])
        except Exception as e:
            care_stats["error"] = str(e)

        # Memory / Nightly / DB sizes 指標
        memory_stats: dict[str, Any] = {"chat_messages": 0, "user_facts": 0, "db_sizes_bytes": {}}
        try:
            chat_db = settings.DATA_DIR / "chat_history.db"
            if chat_db.exists():
                memory_stats["db_sizes_bytes"]["chat_history"] = chat_db.stat().st_size
                with sqlite3.connect(str(chat_db), timeout=3.0) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM chat_messages WHERE is_deleted = 0")
                    memory_stats["chat_messages"] = int(cur.fetchone()[0])
                    try:
                        cur.execute("SELECT COUNT(*) FROM user_facts WHERE is_deleted = 0")
                        memory_stats["user_facts"] = int(cur.fetchone()[0])
                    except Exception:
                        pass
            care_db = settings.DATA_DIR / "care.db"
            if care_db.exists():
                memory_stats["db_sizes_bytes"]["care"] = care_db.stat().st_size
            spark_db = settings.DATA_DIR / "spark" / "spark.db"
            if spark_db.exists():
                memory_stats["db_sizes_bytes"]["spark"] = spark_db.stat().st_size
        except Exception as e:
            memory_stats["error"] = str(e)

        return {
            "governance": gov_status,
            "observability": obs_status,
            "spark": spark_stats,
            "care": care_stats,
            "memory": memory_stats,
        }

    return await asyncio.to_thread(_collect_sync)


@app.route("/api/system/background_metrics", methods=["GET"])
async def get_background_system_metrics() -> Response | tuple[Response, int] | Any:
    """回傳背景系統（SPARK、主動關懷、記憶維護、自主治理預算與觀測佇列）指標快照。"""
    try:
        bg = await _async_get_background_metrics()
        return jsonify(
            {
                "status": "success",
                "data": bg,
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "status": "error",
                "message": "讀取背景系統度量失敗",
                "error_id": log_safe_error("web_ui.background_metrics", exc),
            }
        ), 500


@app.route("/api/system/components/metrics", methods=["GET"])
async def get_system_components_metrics() -> Response | tuple[Response, int] | Any:
    """回傳各組件與功能級別之執行統計、延遲分位數與 Token 消耗（僅限管理員）。

    Returns:
        Response: 包含 components 指標字典之 JSON 物件，未授權則回傳 401。
    """
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "需要管理員權限以檢視組件效能指標"}), 401

    from core.sys_inspector.metrics import component_metrics

    # R12：百分位排序與首次 SQLite bootstrap 皆為阻塞工作，移至工作執行緒。
    summary = await asyncio.to_thread(component_metrics.get_summary)
    return jsonify(summary)


@app.route("/api/system/components/metrics/reset", methods=["POST"])
async def reset_system_components_metrics() -> Response | tuple[Response, int] | Any:
    """重置所有組件之執行度量計數器（僅限管理員）。

    Returns:
        Response: 重置操作結果 JSON 物件，未授權則回傳 401。
    """
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "需要管理員權限以重置組件指標"}), 401

    from core.sys_inspector.metrics import component_metrics

    component_metrics.reset()
    return jsonify({"status": "success", "message": "組件指標計數器已重置"})


@app.route("/api/system/incidents", methods=["GET"])
async def get_system_incidents() -> Response | tuple[Response, int] | Any:
    """從集中化審計資料庫檢索近期異常與診斷事件（僅限管理員）。

    Returns:
        Response: 包含 incidents 列表之 JSON 物件，未授權則回傳 401。
    """
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "需要管理員權限以檢視異常事件紀錄"}), 401

    from core.sys_inspector.audit_service import audit_service

    incidents = await asyncio.to_thread(audit_service.get_audit_records, limit=50)
    return jsonify({"incidents": incidents, "total": len(incidents)})


@app.route("/api/system/emotion", methods=["GET"])
async def get_system_emotion() -> Response | tuple[Response, int] | Any:
    """取得動態情感狀態機 (Dynamic Affect State Machine) 之狀態清單與歷史事件。"""
    try:
        from handlers.emotion.service import get_emotion_service

        service = get_emotion_service()
        states, events = await asyncio.gather(
            asyncio.to_thread(service.get_all_states, 50),
            asyncio.to_thread(service.get_recent_events, None, None, 20),
        )
        return jsonify(
            {
                "status": "ok",
                "states": states,
                "recent_events": events,
                "total_states": len(states),
            }
        )
    except Exception as exc:
        logger.error("get_system_emotion error: %s", exc)
        return jsonify(
            {
                "status": "error",
                "message": "情感狀態讀取失敗",
                "error_id": log_safe_error("web_ui.emotion", exc),
            }
        ), 500


@app.route("/api/system/audit/kpi", methods=["GET"])
@app.route("/api/self_monitoring/kpi", methods=["GET"])
@app.route("/api/self_monitoring/stats", methods=["GET"])
async def get_audit_kpi() -> Response | tuple[Response, int] | Any:
    """取得系統稽核 KPI 統計摘要。

    Returns:
        Response: KPI 統計資訊字典 JSON。
    """
    from core.sys_inspector.audit_service import audit_service

    kpi = await asyncio.to_thread(audit_service.get_kpi_summary)
    return jsonify({"status": "success", "kpi": kpi, "data": kpi})


@app.route("/api/system/audit/records", methods=["GET"])
@app.route("/api/self_monitoring/records", methods=["GET"])
async def get_audit_records() -> Response | tuple[Response, int] | Any:
    """分頁與條件查詢歷史稽核紀錄清單。

    Query Args:
        limit: 每頁筆數（預設 50，上限 100）。
        offset: 偏移量（預設 0）。
        severity: 嚴重等級篩選。
        type: 事故類型篩選。

    Returns:
        Response: 包含 records 與分頁資訊之 JSON。
    """
    from core.sys_inspector.audit_service import audit_service

    limit = min(max(request.args.get("limit", default=50, type=int), 1), 100)
    offset = max(request.args.get("offset", default=0, type=int), 0)
    severity = request.args.get("severity", default=None, type=str)
    inc_type = request.args.get("type", default=None, type=str)

    records = await asyncio.to_thread(
        audit_service.get_audit_records,
        limit=limit,
        offset=offset,
        severity=severity,
        incident_type=inc_type,
    )
    return jsonify(
        {
            "status": "success",
            "records": records,
            "data": records,
            "count": len(records),
            "limit": limit,
            "offset": offset,
        }
    )


@app.route("/api/system/audit/records/<int:record_id>", methods=["GET"])
@app.route("/api/self_monitoring/records/<int:record_id>", methods=["GET"])
async def get_audit_record_detail(record_id: int) -> Response | tuple[Response, int] | Any:
    """依 ID 查詢單筆稽核紀錄完整細節與診斷報告（需管理員權限）。

    Args:
        record_id: 紀錄主鍵 ID。

    Returns:
        Response: 單筆紀錄字典 JSON，未授權則回傳 401。
    """
    if not _is_admin_request(request):
        return jsonify(
            {
                "status": "error",
                "message": "查看詳細技術稽核報告與日誌跡證需要管理員權限，請先登入管理員帳號。",
            }
        ), 401

    from core.sys_inspector.audit_service import audit_service

    record = await asyncio.to_thread(audit_service.get_audit_record_by_id, record_id)
    if not record:
        return jsonify({"status": "error", "message": "查無此稽核紀錄"}), 404

    return jsonify({"status": "success", "record": record})


@app.route("/api/system/audit/diagnose", methods=["POST"])
@app.route("/api/self_monitoring/diagnose", methods=["POST"])
async def trigger_manual_audit() -> Response | tuple[Response, int] | Any:
    """線上手動觸發 5 分鐘日誌技術稽核（需管理員權限）。

    Returns:
        Response: 診斷結果 JSON 物件，未授權則回傳 401。
    """
    if not _is_admin_request(request):
        return jsonify(
            {
                "status": "error",
                "message": "手動觸發技術稽核需要管理員權限，請先登入管理員帳號。",
            }
        ), 401

    from core.sys_inspector.audit_service import audit_service

    req_data: dict[str, Any] = {}
    try:
        req_data = await request.get_json(silent=True) or {}
    except Exception:
        pass

    window_min = req_data.get("window_minutes", 5)
    incident = audit_service.create_manual_check_incident(
        user_name="WebUI-Admin",
        extra_metrics={"source": "web_console_trigger"},
    )

    try:
        diag, snapshot = await audit_service.diagnose_incident(incident, window_minutes=window_min)
        return jsonify(
            {
                "status": "success",
                "message": "技術稽核完成並已存入資料庫！",
                "diagnosis": {
                    "bottleneck_category": diag.bottleneck_category,
                    "root_cause": diag.root_cause,
                    "key_evidence": diag.key_evidence,
                    "recommendations": diag.recommendations,
                    "model_name": diag.model_name,
                    "latency_ms": diag.diagnosis_latency_ms,
                },
                "snapshot_stats": {
                    "error_count": snapshot.error_count,
                    "warning_count": snapshot.warning_count,
                    "total_lines": snapshot.total_lines,
                },
            }
        )
    except Exception as exc:
        logger.error("WebUI 手動執行技術稽核失敗: %s", exc)
        return jsonify(
            {
                "status": "error",
                "message": "執行技術稽核失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.system.audit.run", exc),
            }
        ), 500


@app.route("/api/system/tasks", methods=["GET"])
async def get_scheduled_tasks() -> Response | tuple[Response, int] | Any:
    """Return all active scheduled tasks."""
    try:
        from handlers import schedule_storage

        tasks = await schedule_storage.load_all_tasks()
        task_list = []
        for t in tasks:
            task_list.append(
                {
                    "task_id": t.get("task_id", ""),
                    "type": t.get("schedule_type", t.get("type", "once")),
                    "prompt": t.get("content", t.get("prompt", "")),
                    "target_channel_id": t.get("channel_id", t.get("target_channel_id", "")),
                    "trigger_timestamp": t.get("trigger_time", t.get("trigger_timestamp")),
                    "cron_expression": t.get("cron_expr", t.get("cron_expression")),
                    "display_name": t.get("display_name", ""),
                    "user_id": t.get("user_id", ""),
                    "created_at": t.get("created_at"),
                }
            )
        return jsonify({"tasks": task_list, "total": len(task_list)})
    except Exception as exc:
        logger.error("get_scheduled_tasks error: %s", exc)
        return jsonify(
            {
                "tasks": [],
                "total": 0,
                "error": "排程任務讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.system.tasks.list", exc),
            }
        )


@app.route("/api/system/tasks/<task_id>", methods=["DELETE"])
async def delete_scheduled_task(task_id: str) -> Response | tuple[Response, int] | Any:
    """Cancel / Delete a specific scheduled task by ID."""
    try:
        from handlers import schedule_storage

        deleted = await schedule_storage.remove_task(task_id)
        if deleted:
            return jsonify({"status": "success", "task_id": task_id})
        return jsonify({"status": "error", "message": "Task not found"}), 404
    except Exception as exc:
        logger.error("delete_scheduled_task error: %s", exc)
        return jsonify(
            {
                "status": "error",
                "message": "刪除排程任務失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.system.tasks.delete", exc),
            }
        ), 500


@app.route("/api/system/dashboard", methods=["GET"])
async def get_dashboard_data() -> Response | tuple[Response, int] | Any:
    """Return unified dashboard summary payload (git info, bot summary, system summary, queues)."""
    try:
        disk_git, sys_metrics, bg_metrics = await asyncio.gather(
            _async_get_git_info(),
            _async_get_system_metrics(),
            _async_get_background_metrics(),
        )
        running_git = _STARTUP_GIT_INFO
        r_hash = running_git.get("commit_hash", "unknown")
        d_hash = disk_git.get("commit_hash", "unknown")
        needs_restart = (r_hash != d_hash) and (r_hash != "unknown") and (d_hash != "unknown")

        bot = _bot_instance
        bot_ready = bool(bot and getattr(bot, "is_ready", lambda: False)())
        bot_name = str(bot.user) if (bot and bot.user) else "雪莉 Sherry (未連線)"
        ping_ms = round(bot.latency * 1000, 1) if (bot and hasattr(bot, "latency")) else 0
        guild_count = len(bot.guilds) if (bot and hasattr(bot, "guilds")) else 0
        voice_count = len(bot.voice_clients) if (bot and hasattr(bot, "voice_clients")) else 0

        # System summary
        cpu_percent = sys_metrics.get("cpu", {}).get("percent", 0)
        ram_data = sys_metrics.get("ram", {})
        mem_used_gb = ram_data.get("used_gb", 0)
        mem_total_gb = ram_data.get("total_gb", 0)
        mem_percent = ram_data.get("percent", 0)

        gpu_list = sys_metrics.get("gpu", [])
        vram_info = {"used_gb": 0, "total_gb": 0, "percent": 0, "gpu_temp": 0, "gpu_name": "N/A"}
        if gpu_list:
            g = gpu_list[0]
            vram_info = {
                "used_gb": round(g.get("memory_used_mb", 0) / 1024, 1),
                "total_gb": round(g.get("memory_total_mb", 0) / 1024, 1),
                "percent": g.get("memory_percent", 0),
                "gpu_temp": g.get("temperature_c", 0),
                "gpu_name": g.get("name", "GPU"),
            }

        # Brain Queue & Schedule tasks
        brain_queue_count = 0
        if bot:
            brain = bot.get_cog("BotBrain")
            if brain and hasattr(brain, "get_pending_tasks"):
                brain_queue_count = len(brain.get_pending_tasks())

        schedule_count = 0
        try:
            from handlers import schedule_storage

            tasks = await schedule_storage.load_all_tasks()
            schedule_count = len(tasks)
        except Exception:
            pass

        # Users summary
        user_summary = await asyncio.to_thread(user_connection_tracker.get_online_summary)
        users_info = {
            "online_count": user_summary.get("online_count", 0),
            "today_total": user_summary.get("today_total", 0),
        }

        return jsonify(
            {
                "status": "success",
                "git": {
                    "running": running_git,
                    "disk": disk_git,
                    "needs_restart": needs_restart,
                },
                "bot": {
                    "ready": bot_ready,
                    "name": bot_name,
                    "ping_ms": ping_ms,
                    "guild_count": guild_count,
                    "voice_count": voice_count,
                },
                "system": {
                    "cpu_percent": cpu_percent,
                    "mem_used_gb": mem_used_gb,
                    "mem_total_gb": mem_total_gb,
                    "mem_percent": mem_percent,
                    "vram": vram_info,
                },
                "users": users_info,
                "queues": {
                    "brain_llm": brain_queue_count,
                    "schedule": schedule_count,
                    "observability_write": bg_metrics.get("observability", {}).get("queue_size", 0),
                    "care_pending_topics": bg_metrics.get("care", {}).get("pending_topics", 0),
                    "spark_triggers": bg_metrics.get("spark", {}).get("active_triggers", 0),
                },
                "governance": bg_metrics.get("governance", {}),
                "background_systems": {
                    "spark": bg_metrics.get("spark", {}),
                    "care": bg_metrics.get("care", {}),
                    "memory": bg_metrics.get("memory", {}),
                    "observability": bg_metrics.get("observability", {}),
                },
                "version": APP_VERSION,
            }
        )
    except Exception as exc:
        logger.error("get_dashboard_data error: %s", exc)
        return jsonify(
            {
                "status": "error",
                "message": "系統儀表板資料讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.dashboard", exc),
            }
        ), 500


@app.route("/api/brain/queue", methods=["GET"])
async def get_brain_inference_queue() -> Response | tuple[Response, int] | Any:
    """Return real-time pending LLM inference tasks in BotBrain's PriorityQueue."""
    try:
        bot = _bot_instance
        if not bot:
            return jsonify({"queue": [], "total": 0})
        brain = bot.get_cog("BotBrain")
        if not brain or not hasattr(brain, "get_pending_tasks"):
            return jsonify({"queue": [], "total": 0})
        pending = brain.get_pending_tasks()
        return jsonify({"queue": pending, "total": len(pending)})
    except Exception as exc:
        logger.error("get_brain_inference_queue error: %s", exc)
        return jsonify(
            {
                "queue": [],
                "total": 0,
                "error": "推論佇列讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.brain.queue", exc),
            }
        )


# =========================================================
# RECOVERY & RESILIENCE CENTER APIs
# =========================================================


@app.route("/api/recovery/status", methods=["GET"])
async def get_recovery_system_status() -> Response | tuple[Response, int] | Any:
    """Return system state recovery toggle, registered providers, and snapshot statistics."""
    try:
        from core.recovery.manager import get_recovery_manager
        from core.db import get_db_connection

        manager = get_recovery_manager()
        enabled = manager.is_enabled()
        providers = manager.list_providers()

        db_path = manager.storage.db_path
        total = 0
        active = 0
        quarantined = 0
        last_saved_at = None

        if db_path.exists():

            def _query_stats() -> dict[str, Any]:
                try:
                    with get_db_connection(db_path) as conn:
                        cursor = conn.execute(
                            """
                            SELECT 
                                COUNT(*) AS total,
                                SUM(CASE WHEN is_restored = 0 AND quarantined = 0 THEN 1 ELSE 0 END) AS active,
                                SUM(CASE WHEN quarantined = 1 THEN 1 ELSE 0 END) AS quarantined,
                                MAX(saved_at) AS last_saved_at
                            FROM system_recovery_snapshots;
                            """
                        )
                        row = cursor.fetchone()
                        if row:
                            return {
                                "total": int(row["total"] or 0),
                                "active": int(row["active"] or 0),
                                "quarantined": int(row["quarantined"] or 0),
                                "last_saved_at": float(row["last_saved_at"])
                                if row["last_saved_at"] is not None
                                else None,
                            }
                except Exception as db_err:
                    logger.warning("Failed to query recovery snapshots from db: %s", db_err)
                return {"total": 0, "active": 0, "quarantined": 0, "last_saved_at": None}

            stats = await asyncio.to_thread(_query_stats)
            total = stats["total"]
            active = stats["active"]
            quarantined = stats["quarantined"]
            last_saved_at = stats["last_saved_at"]

        return jsonify(
            {
                "status": "success",
                "enabled": enabled,
                "registered_providers": providers,
                "snapshots": {
                    "total": total,
                    "active": active,
                    "quarantined": quarantined,
                    "last_saved_at": last_saved_at,
                    "db_path": str(db_path),
                },
            }
        )
    except Exception as exc:
        logger.error("get_recovery_system_status error: %s", exc)
        return jsonify(
            {
                "status": "error",
                "enabled": bool(getattr(settings, "STATE_RECOVERY_ENABLED", True)),
                "registered_providers": [],
                "snapshots": {"total": 0, "active": 0, "quarantined": 0, "last_saved_at": None, "db_path": ""},
                "error_id": log_safe_error("web_ui.recovery.status", exc),
            }
        ), 500


@app.route("/api/recovery/ai-tasks", methods=["GET"])
async def get_recovery_ai_tasks() -> Response | tuple[Response, int] | Any:
    """Return active in-flight AI reasoning tasks tracked by InFlightTaskRegistry."""
    try:
        from handlers.social.task_registry import get_in_flight_registry

        registry = get_in_flight_registry()
        raw_tasks = registry.list_all()
        now = time.time()
        tasks = []
        for t in raw_tasks:
            elapsed = round(max(0.0, now - t.start_time), 1)
            tasks.append(
                {
                    "task_id": t.task_id,
                    "channel_id": str(t.channel_id),
                    "message_id": str(t.message_id),
                    "user_id": str(t.user_id) if t.user_id else "",
                    "prompt": t.prompt,
                    "task_type": t.task_type,
                    "origin_message_id": str(t.origin_message_id) if t.origin_message_id else None,
                    "start_time": t.start_time,
                    "elapsed_seconds": elapsed,
                }
            )
        return jsonify({"tasks": tasks, "total": len(tasks)})
    except Exception as exc:
        logger.error("get_recovery_ai_tasks error: %s", exc)
        return jsonify(
            {
                "tasks": [],
                "total": 0,
                "error_id": log_safe_error("web_ui.recovery.ai_tasks", exc),
            }
        ), 500


@app.route("/api/recovery/music", methods=["GET"])
async def get_recovery_music_status() -> Response | tuple[Response, int] | Any:
    """Return music playback status, queues, timeline offset, and recovery readiness per guild."""
    try:
        bot = _bot_instance
        if not bot:
            return jsonify({"guilds": [], "total": 0})

        music_cog = bot.get_cog("MusicHandler")
        if not music_cog:
            return jsonify({"guilds": [], "total": 0})

        # Collect all active guild IDs
        all_guild_ids: set[Any] = set()
        if hasattr(music_cog, "queues") and isinstance(music_cog.queues, dict):
            all_guild_ids |= set(music_cog.queues.keys())
        if hasattr(music_cog, "current_song") and isinstance(music_cog.current_song, dict):
            all_guild_ids |= set(music_cog.current_song.keys())
        if hasattr(bot, "voice_clients"):
            for vc in bot.voice_clients:
                if hasattr(vc, "guild") and vc.guild and hasattr(vc.guild, "id"):
                    all_guild_ids.add(vc.guild.id)

        guild_results = []
        for gid in sorted(all_guild_ids, key=lambda x: str(x)):
            cur_song = (
                music_cog.current_song.get(gid)
                if (hasattr(music_cog, "current_song") and isinstance(music_cog.current_song, dict))
                else None
            )
            raw_queue = (
                music_cog.queues.get(gid, [])
                if (hasattr(music_cog, "queues") and isinstance(music_cog.queues, dict))
                else []
            )
            if not isinstance(raw_queue, list):
                raw_queue = []

            guild = bot.get_guild(gid) if hasattr(bot, "get_guild") else None
            guild_name = str(getattr(guild, "name", f"Guild {gid}"))

            vc = getattr(guild, "voice_client", None) if guild else None
            voice_ch = getattr(vc, "channel", None) if vc else None
            voice_channel_id = str(voice_ch.id) if (voice_ch and hasattr(voice_ch, "id")) else None
            voice_channel_name = str(voice_ch.name) if (voice_ch and hasattr(voice_ch, "name")) else None

            msg = (
                music_cog.current_message.get(gid)
                if (hasattr(music_cog, "current_message") and isinstance(music_cog.current_message, dict))
                else None
            )
            text_ch = getattr(msg, "channel", None) if msg else None
            text_channel_id = str(text_ch.id) if (text_ch and hasattr(text_ch, "id")) else None
            text_channel_name = str(text_ch.name) if (text_ch and hasattr(text_ch, "name")) else None

            # If no song, no queue, and no voice client, skip idle guild
            if not cur_song and not raw_queue and not vc:
                continue

            is_paused = (
                bool(music_cog.is_paused.get(gid, False))
                if (hasattr(music_cog, "is_paused") and isinstance(music_cog.is_paused, dict))
                else False
            )
            is_playing = bool(vc and getattr(vc, "is_playing", lambda: False)())
            elapsed = 0
            if hasattr(music_cog, "get_elapsed_time"):
                try:
                    elapsed = int(music_cog.get_elapsed_time(gid))
                except Exception:
                    elapsed = 0

            duration = 0.0
            song_info = None
            if isinstance(cur_song, dict):
                try:
                    duration = float(cur_song.get("duration", 0) or 0)
                except (TypeError, ValueError):
                    duration = 0.0
                song_info = {
                    "title": str(cur_song.get("title", "未知歌曲")),
                    "url": str(cur_song.get("webpage_url", "")),
                    "duration": duration,
                    "requester": str(cur_song.get("requester", "未知")),
                    "thumbnail": str(cur_song.get("thumbnail", "")),
                }

            formatted_queue = []
            for item in raw_queue:
                if not isinstance(item, dict):
                    continue
                try:
                    q_duration = float(item.get("duration", 0) or 0)
                except (TypeError, ValueError):
                    q_duration = 0.0
                formatted_queue.append(
                    {
                        "title": str(item.get("title", "未知歌曲")),
                        "duration": q_duration,
                        "requester": str(item.get("requester", "未知")),
                        "url": str(item.get("webpage_url", "")),
                    }
                )

            loop_mode = (
                str(music_cog.loop_modes.get(gid, "none"))
                if (hasattr(music_cog, "loop_modes") and isinstance(music_cog.loop_modes, dict))
                else "none"
            )
            recovery_ready = bool(voice_channel_id and (cur_song or formatted_queue))

            guild_results.append(
                {
                    "guild_id": str(gid),
                    "guild_name": guild_name,
                    "voice_channel_id": voice_channel_id,
                    "voice_channel_name": voice_channel_name,
                    "text_channel_id": text_channel_id,
                    "text_channel_name": text_channel_name,
                    "current_song": song_info,
                    "elapsed_seconds": elapsed,
                    "duration_seconds": duration,
                    "is_paused": is_paused,
                    "is_playing": is_playing,
                    "loop_mode": loop_mode,
                    "queue_count": len(raw_queue),
                    "queue": formatted_queue,
                    "recovery_ready": recovery_ready,
                }
            )

        return jsonify({"guilds": guild_results, "total": len(guild_results)})
    except Exception as exc:
        logger.error("get_recovery_music_status error: %s", exc)
        return jsonify(
            {
                "guilds": [],
                "total": 0,
                "error_id": log_safe_error("web_ui.recovery.music", exc),
            }
        ), 500


@app.route("/api/bot/status", methods=["GET"])
async def get_bot_live_status() -> Response | tuple[Response, int] | Any:
    """Return real-time Discord Bot connection, voice channels, and music status."""
    try:
        bot = _bot_instance
        if not bot or not getattr(bot, "is_ready", lambda: False)():
            return jsonify(
                {
                    "ready": False,
                    "bot_name": "雪莉 Sherry (啟動中...)",
                    "guild_count": 0,
                    "ping_ms": 0,
                    "voice_connections": [],
                }
            )

        voice_connections = []
        music_cog = bot.get_cog("MusicHandler")

        for vc in bot.voice_clients:
            guild = vc.guild
            channel = vc.channel
            members = [
                {
                    "id": str(m.id),
                    "name": m.display_name,
                    "avatar": str(m.display_avatar.url) if hasattr(m, "display_avatar") else "",
                    "is_bot": m.bot,
                }
                for m in getattr(channel, "members", [])
                if not m.bot
            ]

            cur_song = None
            queue_list = []
            is_paused = False
            elapsed_seconds = 0
            loop_mode = "none"
            recovery_ready = False

            if music_cog and guild:
                g_id = guild.id
                raw_song = music_cog.current_song.get(g_id)
                if raw_song:
                    try:
                        dur = float(raw_song.get("duration", 0) or 0)
                    except (TypeError, ValueError):
                        dur = 0.0
                    cur_song = {
                        "title": str(raw_song.get("title", "未知歌曲")),
                        "url": str(raw_song.get("webpage_url", "")),
                        "duration": dur,
                        "requester": str(raw_song.get("requester", "未知")),
                    }
                is_paused = bool(music_cog.is_paused.get(g_id, False))
                raw_queue = music_cog.queues.get(g_id, [])
                if isinstance(raw_queue, list):
                    for item in raw_queue[:5]:
                        if isinstance(item, dict):
                            queue_list.append(
                                {
                                    "title": str(item.get("title", "未知歌曲")),
                                    "requester": str(item.get("requester", "未知")),
                                }
                            )

                if hasattr(music_cog, "get_elapsed_time"):
                    try:
                        elapsed_seconds = int(music_cog.get_elapsed_time(g_id))
                    except Exception:
                        elapsed_seconds = 0

                if hasattr(music_cog, "loop_modes") and isinstance(music_cog.loop_modes, dict):
                    loop_mode = str(music_cog.loop_modes.get(g_id, "none"))

                recovery_ready = bool(channel and (cur_song or queue_list))

            voice_connections.append(
                {
                    "guild_id": str(guild.id),
                    "guild_name": guild.name,
                    "guild_icon": str(guild.icon.url) if guild and guild.icon else "",
                    "channel_id": str(channel.id),
                    "channel_name": channel.name,
                    "members": members,
                    "member_count": len(members),
                    "is_playing": vc.is_playing(),
                    "is_paused": is_paused,
                    "elapsed_seconds": elapsed_seconds,
                    "loop_mode": loop_mode,
                    "recovery_ready": recovery_ready,
                    "current_song": cur_song,
                    "queue": queue_list,
                    "queue_count": len(music_cog.queues.get(guild.id, [])) if music_cog and guild else 0,
                }
            )

        return jsonify(
            {
                "ready": True,
                "bot_name": str(bot.user),
                "bot_avatar": str(bot.user.display_avatar.url) if bot.user else "",
                "guild_count": len(bot.guilds),
                "ping_ms": round(bot.latency * 1000, 1),
                "voice_connections": voice_connections,
            }
        )
    except Exception as exc:
        logger.error("get_bot_live_status error: %s", exc)
        return jsonify(
            {
                "ready": False,
                "error": "Bot 狀態讀取失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.bot.status", exc),
                "voice_connections": [],
            }
        )


# ---------------------------------------------------------------------------
# Dynamic Plugin / Mod Auto-Discovery & Blueprint Mounting
# ---------------------------------------------------------------------------


def _discover_and_mount_mod_plugins(app_instance: Quart, bot_inst: Any = None) -> None:
    """Auto-discover all modules in ./mod/ and dynamically mount Blueprints for enabled mods.

    Args:
        app_instance: Quart web application instance.
        bot_inst: Optional Discord Bot instance for Cog/service linking.
    """
    import importlib
    from quart import Blueprint

    _scan_and_update_public_mod_routes()
    mod_config = _load_mod_config()
    mod_base = Path("mod")
    if not mod_base.is_dir():
        return

    for child in sorted(mod_base.iterdir()):
        if (
            not child.is_dir()
            or child.name.startswith("_")
            or child.name in _EXCLUDED_MOD_DIRS
            or not (child / "__init__.py").exists()
        ):
            continue

        mod_key = child.name
        is_enabled = _is_mod_enabled(mod_key, mod_config)
        if not is_enabled:
            logger.info("Mod [%s] is disabled in config, skipping Web UI mount.", mod_key)
            continue

        # Check if the mod explicitly declares has_web_ui: True in MOD_INFO
        try:
            mod_pkg = importlib.import_module(f"mod.{mod_key}")
            mod_info = getattr(mod_pkg, "MOD_INFO", {}) or {}
            if not mod_info.get("has_web_ui", False):
                continue
        except Exception:
            continue

        web_py = child / "web.py"
        if not web_py.exists():
            continue

        pkg_name = f"mod.{mod_key}.web"
        try:
            mod_web = importlib.import_module(pkg_name)
            if hasattr(mod_web, "set_bot_instance") and bot_inst is not None:
                mod_web.set_bot_instance(bot_inst)

            blueprint_obj: Blueprint | None = None
            for attr_name in dir(mod_web):
                attr = getattr(mod_web, attr_name)
                if isinstance(attr, Blueprint) and not attr_name.startswith("_"):
                    blueprint_obj = attr
                    break

            if blueprint_obj is not None and mod_key not in _mounted_mod_blueprints:
                app_instance.register_blueprint(blueprint_obj)
                _mounted_mod_blueprints[mod_key] = blueprint_obj
                logger.info("✅ Dynamically mounted Web UI Blueprint for mod: [%s]", mod_key)
        except Exception as exc:
            logger.warning("Failed to auto-mount Web UI Blueprint for mod [%s]: %s", mod_key, exc)

    # ── Mount handlers.spark Web UI Blueprint ───────────
    try:
        from handlers.spark.web import set_bot_instance as set_spark_bot_instance, spark_bp

        if bot_inst is not None:
            set_spark_bot_instance(bot_inst)
        if hasattr(spark_bp, "name") and "spark" not in _mounted_mod_blueprints:
            app_instance.register_blueprint(spark_bp)
            _mounted_mod_blueprints["spark"] = spark_bp
            logger.info("✅ Mounted Web UI Blueprint for core handler: [spark]")
    except Exception as exc:
        logger.warning("Failed to mount Web UI Blueprint for handlers.spark: %s", exc)

    # ── Mount handlers.care Web UI Blueprint ───────────
    try:
        from handlers.care.web import set_bot_instance as set_care_bot_instance, care_bp

        if bot_inst is not None:
            set_care_bot_instance(bot_inst)
        if hasattr(care_bp, "name") and "care" not in _mounted_mod_blueprints:
            app_instance.register_blueprint(care_bp)
            _mounted_mod_blueprints["care"] = care_bp
            logger.info("✅ Mounted Web UI Blueprint for core handler: [care]")
    except Exception as exc:
        logger.warning("Failed to mount Web UI Blueprint for handlers.care: %s", exc)


# Initial mount of available mod blueprints
_discover_and_mount_mod_plugins(app)


# ── Static Asset & Dynamic View Routes for Mods & Handlers ─


@app.route("/handlers/<handler_key>/static/<path:filename>")
@app.route("/mod/<handler_key>/static/<path:filename>")
async def serve_handler_or_mod_static(handler_key: str, filename: str) -> Response | tuple[Response, int] | Any:
    """安全提供 handlers/<key>/static/ 或 mod/<key>/static/ 目錄下的靜態資源。

    全面防禦 Windows 磁碟代號絕對路徑 (C:/...)、路徑遍歷 (..) 與目錄逃逸。
    """
    from quart import send_file

    # 嚴格校驗 handler_key 與 filename 字元合法性，阻擋任何絕對路徑或目錄逃逸
    if (
        not handler_key
        or handler_key.startswith("_")
        or ".." in handler_key
        or "/" in handler_key
        or "\\" in handler_key
        or not re.match(r"^[a-zA-Z0-9_-]+$", handler_key)
        or not filename
        or ".." in filename
        or "\\" in filename
        or ":" in filename
        or "\0" in filename
        or filename.startswith("/")
        or filename.startswith("\\")
        or Path(filename).is_absolute()
    ):
        return Response("Forbidden", status=403)

    base_dir_handlers = (Path("handlers") / handler_key / "static").resolve()
    base_dir_mod = (Path("mod") / handler_key / "static").resolve()

    for base_dir in (base_dir_handlers, base_dir_mod):
        if not base_dir.is_dir():
            continue
        try:
            target_file = (base_dir / filename).resolve()
            # 確保檔案解析後的完整真實路徑嚴格隸屬於 base_dir 目錄之下
            if target_file.is_relative_to(base_dir) and target_file.is_file():
                return await send_file(target_file)
        except (ValueError, Exception):
            continue

    return Response("Not Found", status=404)


_MOD_VIEW_CACHE: dict[str, str] = {}
_MODULES_CACHE: dict[str, Any] | None = None
_MODULES_CACHE_TIME: float = 0.0
_MODULES_CACHE_TTL: float = 30.0


def _load_snippet_sync(mod_key: str) -> str | None:
    """同步讀取特定模組之視圖模板（於 worker thread 執行）。"""
    import importlib

    mod_info: dict[str, Any] = {}
    try:
        mod_pkg = importlib.import_module(f"mod.{mod_key}")
        mod_info = getattr(mod_pkg, "MOD_INFO", {}) or {}
    except Exception:
        pass

    template_paths: list[Path] = []
    if mod_info.get("template_file"):
        template_paths.append(Path("mod") / mod_key / "templates" / str(mod_info["template_file"]))
    template_paths.append(Path("mod") / mod_key / "templates" / f"{mod_key}.html")
    if mod_info.get("nav_id"):
        template_paths.append(Path("mod") / mod_key / "templates" / f"{mod_info['nav_id']}.html")
    template_paths.append(Path("mod") / mod_key / "templates" / "view.html")
    template_paths.append(Path("mod") / mod_key / "templates" / "index.html")

    for p in template_paths:
        if p and p.is_file():
            return p.read_text(encoding="utf-8")
    return None


@app.route("/api/mod/<mod_key>/view", methods=["GET"])
async def get_mod_view_snippet(mod_key: str) -> Response | tuple[Response, int] | Any:
    """Fetch and return rendered HTML view snippet for a specific mod with cache."""
    # R4：mod_key 白名單驗證（僅 [A-Za-z0-9_-]+），避免 HTML 反射與路徑探測
    if ".." in mod_key or mod_key.startswith("_") or not re.fullmatch(r"[A-Za-z0-9_-]+", mod_key):
        return Response("Invalid mod key", status=400)

    if mod_key in _MOD_VIEW_CACHE:
        return Response(_MOD_VIEW_CACHE[mod_key], mimetype="text/html")

    content = await asyncio.to_thread(_load_snippet_sync, mod_key)
    if content is not None:
        _MOD_VIEW_CACHE[mod_key] = content
        return Response(content, mimetype="text/html")

    return Response(
        f"<div class='view hidden' id='view-{html.escape(mod_key)}'><h3>模組 {html.escape(mod_key)} 視圖未提供</h3></div>",
        mimetype="text/html",
    )


def _scan_dynamic_modules_sync() -> dict[str, Any]:
    """同步掃描與載入模組元資料（於 worker thread 執行）。"""
    import importlib

    mod_config = _load_mod_config()
    modules: dict[str, Any] = {}
    mod_base = Path("mod")

    discovered_keys: list[str] = []
    if mod_base.is_dir():
        for child in sorted(mod_base.iterdir()):
            if (
                child.is_dir()
                and not child.name.startswith("_")
                and child.name not in _EXCLUDED_MOD_DIRS
                and (child / "__init__.py").exists()
            ):
                discovered_keys.append(child.name)

    for mod_key in discovered_keys:
        enabled = _is_mod_enabled(mod_key, mod_config)
        python_pkg = f"mod.{mod_key}"

        mod_info: dict[str, Any] = {}
        try:
            mod = importlib.import_module(python_pkg)
            if hasattr(mod, "MOD_INFO"):
                mod_info = getattr(mod, "MOD_INFO", {}) or {}
        except Exception as exc:
            logger.debug("Could not load info for mod '%s': %s", mod_key, exc)

        mod_dir = Path("mod") / mod_key
        has_web = bool(mod_info.get("has_web_ui", False))
        has_view = has_web and (
            (mod_dir / "templates" / f"{mod_key}.html").exists()
            or (mod_dir / "templates" / "view.html").exists()
            or (mod_dir / "templates" / "games.html").exists()
        )
        has_script = has_web and (
            (mod_dir / "static" / f"{mod_key}.js").exists()
            or (mod_dir / "static" / "games.js").exists()
            or bool(mod_info.get("script_file") and (mod_dir / "static" / str(mod_info.get("script_file"))).exists())
        )
        script_file = mod_info.get("script_file") or (
            f"{mod_key}.js" if (mod_dir / "static" / f"{mod_key}.js").exists() else None
        )
        script_url = f"/mod/{mod_key}/static/{script_file}" if (has_script and script_file) else None

        display_name = mod_info.get("name") or mod_info.get("display_name") or f"📦 {mod_key}"
        if mod_info.get("icon") and not display_name.startswith(mod_info.get("icon", "")):
            display_name = f"{mod_info.get('icon')} {display_name}"

        description = mod_info.get("description") or "未提供描述的自訂模組。"
        category = mod_info.get("category") or "自訂 / Custom"
        nav_id = mod_info.get("nav_id") or (mod_key if has_web else None)
        nav_title = mod_info.get("nav_title") or mod_info.get("name") or display_name
        nav_icon = mod_info.get("nav_icon") or mod_info.get("icon") or "🧩"
        view_title = mod_info.get("view_title") or f"{nav_icon} {nav_title}"
        admin_only = bool(mod_info.get("admin_only", False))
        order = int(mod_info.get("order", 100))

        modules[mod_key] = {
            "id": mod_key,
            "enabled": enabled,
            "display_name": display_name,
            "description": description,
            "category": category,
            "version": mod_info.get("version", "1.0.0"),
            "author": mod_info.get("author", "Sherry Community"),
            "has_web_ui": has_web,
            "has_view": has_view,
            "view_url": f"/api/mod/{mod_key}/view" if has_view else None,
            "script_url": script_url,
            "nav_id": nav_id,
            "nav_title": nav_title,
            "nav_icon": nav_icon,
            "view_title": view_title,
            "admin_only": admin_only,
            "order": order,
            "info": mod_info,
        }

    return modules


@app.route("/api/modules", methods=["GET"])
async def get_dynamic_modules() -> Response | tuple[Response, int] | Any:
    """Return available optional modules detected under ./mod/. (Async with TTL Cache)."""
    global _MODULES_CACHE, _MODULES_CACHE_TIME
    now = time.time()
    if _MODULES_CACHE is not None and (now - _MODULES_CACHE_TIME) < _MODULES_CACHE_TTL:
        return jsonify({"modules": _MODULES_CACHE})

    modules = await asyncio.to_thread(_scan_dynamic_modules_sync)
    _MODULES_CACHE = modules
    _MODULES_CACHE_TIME = now
    return jsonify({"modules": modules})


@app.route("/api/modules/toggle", methods=["POST"])
async def toggle_module() -> tuple[Response, int] | Response:
    """Enable or disable a mod module. Admin-only.

    Expects JSON body: { "mod_key": str, "enabled": bool }

    Returns:
        Response: JSON status response.
    """
    global _MODULES_CACHE, _MOD_VIEW_CACHE
    _MODULES_CACHE = None
    _MOD_VIEW_CACHE.clear()

    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "模組管理需要管理員身份，請先登入！"}), 403

    data: dict[str, Any] = await request.get_json() or {}
    mod_key = str(data.get("mod_key", "")).strip()
    enable = bool(data.get("enabled", True))

    if not mod_key or mod_key.startswith("_") or "/" in mod_key or "\\" in mod_key:
        return jsonify({"status": "error", "message": "無效的模組名稱！"}), 400

    # Verify the mod directory actually exists
    mod_path = Path("mod") / mod_key
    if not mod_path.is_dir():
        return jsonify({"status": "error", "message": f"找不到模組目錄: mod/{mod_key}"}), 404

    # Load, update, and persist config（R12：磁碟讀寫移至工作執行緒）
    mod_config = await asyncio.to_thread(_load_mod_config)
    mod_config[mod_key] = enable
    await asyncio.to_thread(_save_mod_config, mod_config)

    # Dynamic hot-load / hot-unload Cog on Discord Bot instance
    ext_name = f"mod.{mod_key}.cog"
    if _bot_instance is not None:
        try:
            bot_extensions = getattr(_bot_instance, "extensions", {})
            if not enable:
                if ext_name in bot_extensions:
                    await _bot_instance.unload_extension(ext_name)
                    logger.info("✅ Dynamically unloaded extension [%s] on bot instance.", ext_name)
                    if hasattr(_bot_instance, "tree") and hasattr(_bot_instance.tree, "sync"):
                        try:
                            await _bot_instance.tree.sync()
                        except Exception as sync_err:
                            logger.debug("Tree sync after unload [%s]: %s", ext_name, sync_err)
            else:
                cog_file = Path("mod") / mod_key / "cog.py"
                if cog_file.exists() and ext_name not in bot_extensions:
                    await _bot_instance.load_extension(ext_name)
                    logger.info("✅ Dynamically loaded extension [%s] on bot instance.", ext_name)
                    if hasattr(_bot_instance, "tree") and hasattr(_bot_instance.tree, "sync"):
                        try:
                            await _bot_instance.tree.sync()
                        except Exception as sync_err:
                            logger.debug("Tree sync after load [%s]: %s", ext_name, sync_err)
        except Exception as bot_exc:
            logger.warning("Failed to dynamically toggle extension [%s] on bot: %s", ext_name, bot_exc)

    # Audit log（R12：SQLite/JSONL 寫入移至工作執行緒）
    try:
        from mod.audit_manager import audit_manager

        token = _get_request_token(request)
        action_desc = "啟用" if enable else "停用"
        await asyncio.to_thread(
            audit_manager.record_action,
            token,
            "模組管理",
            f"{action_desc}模組: {mod_key}",
        )
    except Exception:
        pass

    action_zh = "啟用" if enable else "停用"
    logger.info("Mod '%s' %s by admin request.", mod_key, action_zh)
    return jsonify(
        {
            "status": "success",
            "mod_key": mod_key,
            "enabled": enable,
            "message": f"模組「{mod_key}」已{action_zh}！",
        }
    )


# ---------------------------------------------------------------------------
# API: Roast / Tsukkomi Management (Core Cog Extension)
# ---------------------------------------------------------------------------


@app.route("/api/roast/config", methods=["GET", "POST"])
async def handle_roast_config() -> tuple[Response, int] | Response:
    """Get or update roast system global settings."""
    from handlers.roast.manager import roast_manager

    if request.method == "GET":
        return jsonify(roast_manager.get_config().to_dict())

    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "此操作需要管理員身份，請先登入！"}), 403

    data: dict[str, Any] = await request.get_json() or {}
    if "enabled" in data:
        roast_manager.set_enabled(bool(data["enabled"]))
    if "global_cooldown_seconds" in data:
        try:
            roast_manager.set_cooldown(int(data["global_cooldown_seconds"]))
        except (ValueError, TypeError):
            pass

    return jsonify({"status": "success", "config": roast_manager.get_config().to_dict()})


@app.route("/api/roast/rules", methods=["POST"])
async def add_roast_rule() -> tuple[Response, int] | Response:
    """Add a new roast keyword rule."""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "此操作需要管理員身份，請先登入！"}), 403

    from handlers.roast.manager import roast_manager

    data: dict[str, Any] = await request.get_json() or {}
    keyword = str(data.get("keyword", "")).strip()
    reply_guide = str(data.get("reply_guide", "")).strip()
    probability = data.get("probability", 100)
    match_mode = str(data.get("match_mode", "contains")).lower()
    enabled = bool(data.get("enabled", True))

    if not keyword:
        return jsonify({"status": "error", "message": "請輸入觸發關鍵字！"}), 400
    if not reply_guide:
        return jsonify({"status": "error", "message": "請輸入吐槽回覆導引！"}), 400

    try:
        prob_int = max(0, min(100, int(float(probability))))
    except (ValueError, TypeError):
        prob_int = 100

    if match_mode not in ("contains", "exact", "regex"):
        match_mode = "contains"

    try:
        new_rule = roast_manager.add_rule(
            keyword=keyword,
            reply_guide=reply_guide,
            probability=prob_int,
            match_mode=match_mode,  # type: ignore[arg-type]
            enabled=enabled,
        )
        return jsonify({"status": "success", "rule": new_rule.to_dict()})
    except Exception as exc:
        return jsonify(
            {
                "status": "error",
                "message": "吐槽規則建立失敗，請稍後再試",
                "error_id": log_safe_error("web_ui.roast.rules.create", exc),
            }
        ), 500


@app.route("/api/roast/rules/<rule_id>", methods=["PUT", "DELETE"])
async def modify_roast_rule(rule_id: str) -> tuple[Response, int] | Response:
    """Update or delete an existing roast rule."""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "此操作需要管理員身份，請先登入！"}), 403

    from handlers.roast.manager import roast_manager

    if request.method == "DELETE":
        deleted = roast_manager.delete_rule(rule_id)
        if deleted:
            return jsonify({"status": "success", "message": "規則已刪除"})
        return jsonify({"status": "error", "message": "找不到該吐槽規則！"}), 404

    # PUT
    data: dict[str, Any] = await request.get_json() or {}
    keyword = str(data["keyword"]).strip() if "keyword" in data else None
    reply_guide = str(data["reply_guide"]).strip() if "reply_guide" in data else None
    probability = None
    if "probability" in data:
        try:
            probability = max(0, min(100, int(float(data["probability"]))))
        except (ValueError, TypeError):
            pass
    match_mode = str(data["match_mode"]).lower() if "match_mode" in data else None
    if match_mode and match_mode not in ("contains", "exact", "regex"):
        match_mode = "contains"
    enabled = bool(data["enabled"]) if "enabled" in data else None

    updated = roast_manager.update_rule(
        rule_id=rule_id,
        keyword=keyword,
        reply_guide=reply_guide,
        probability=probability,
        match_mode=match_mode,  # type: ignore[arg-type]
        enabled=enabled,
    )
    if updated:
        return jsonify({"status": "success", "rule": updated.to_dict()})
    return jsonify({"status": "error", "message": "找不到該吐槽規則！"}), 404


@app.route("/api/roast/test", methods=["POST"])
async def test_roast_preview() -> tuple[Response, int] | Response:
    """Simulate a chat message and test Sherry's roast response."""
    import time
    from handlers.roast.manager import roast_manager

    data: dict[str, Any] = await request.get_json() or {}
    message_text = str(data.get("message", "")).strip()
    force = bool(data.get("force", False))

    if not message_text:
        return jsonify({"status": "error", "message": "請輸入測試訊息內容！"}), 400

    matching_rules = roast_manager.find_matching_rules(message_text)
    if not matching_rules:
        return jsonify(
            {
                "status": "success",
                "matched": False,
                "message": "未命中任何已啟用的吐槽關鍵字",
                "roast_reply": None,
            }
        )

    selected_rule = None
    for rule in matching_rules:
        if force or roast_manager.roll_probability(rule):
            selected_rule = rule
            break

    if not selected_rule:
        return jsonify(
            {
                "status": "success",
                "matched": True,
                "triggered": False,
                "message": f"命中關鍵字 '{matching_rules[0].keyword}' 但因機率判定（{matching_rules[0].probability}%）未觸發",
                "rule": matching_rules[0].to_dict(),
                "roast_reply": None,
            }
        )

    # Try LLM generation if bot brain available
    from handlers.roast.utils import clean_roast_reply, extract_fallback_roast

    roast_reply = extract_fallback_roast(selected_rule.reply_guide, user_content=message_text)
    if _bot_instance:
        brain = _bot_instance.get_cog("BotBrain")
        if brain:
            import asyncio
            from handlers.bot_brain import InferenceTask
            from handlers.social.persona import build_dynamic_persona
            from handlers.social.utils import SAFETY_FOOTER

            head_persona, _tail = build_dynamic_persona(message_text, "CHAT")
            prompt = (
                f"{head_persona}\n\n"
                "🚨 【即時吐槽測試任務 (Tsukkomi Directive)】\n"
                f"使用者發言：「{message_text}」\n"
                f"觸發關鍵字「{selected_rule.keyword}」\n"
                f"【核心吐槽指引】：「{selected_rule.reply_guide}」\n\n"
                "🎯 【吐槽生成四大核心法則】（極為重要！）：\n"
                "1. 🔗 深度結合對方發言情境：妳必須敏銳抓住對方句子裡的具體人事物、動作或場景，將「核心吐槽指引」的語氣與觀點自然融合進去進行精準反駁！絕對禁止忽視對方說的內容只講生硬的罐頭台詞。\n"
                "2. 🍊 傲嬌毒舌名偵探人設：語氣自信、傲嬌且犀利，一針見血地戳破對方的幻想或吐槽對方的行為！\n"
                '3. ⚡ 俐落生動短句：長度約 15 ~ 55 字，必須是妳直接說出口的生動對白，絕對禁止在最外層包裹「」或 "" 引號，禁止任何機器人客套話！\n'
                "4. 🚫 嚴禁複製指引標籤：【核心吐槽指引】是提供給妳的思維邏輯與參考指引，請將其內化為妳的傲嬌口吻。若指引中包含方括號條件（例如 [若提及雪莉:...]）或提示詞，請依據使用者發言情境判定符合的條件並轉化為自然的日常說話對白，**絕對禁止**把方括號標籤或提示詞原樣輸出！\n"
            ) + SAFETY_FOOTER

            future = _bot_instance.loop.create_future()

            async def _cb(res: str) -> None:
                if not future.done():
                    future.set_result(res)

            task = InferenceTask(
                priority=settings.PRIORITY_TAG_RESPONSE,
                system_prompt=prompt,
                user_prompt=f"【使用者發言內容】: {message_text}",
                callback=_cb,
                model=settings.PRIMARY_MODEL,
                task_id=f"roast-test-{int(time.time())}",
            )
            await brain.enqueue_task(task)
            try:
                raw = await asyncio.wait_for(
                    future,
                    timeout=getattr(settings, "ROAST_REPLY_TIMEOUT", 30.0),
                )
                cleaned = clean_roast_reply(raw)
                if cleaned:
                    roast_reply = cleaned
            except Exception:
                pass

    return jsonify(
        {
            "status": "success",
            "matched": True,
            "triggered": True,
            "rule": selected_rule.to_dict(),
            "roast_reply": roast_reply,
        }
    )


def _ensure_self_signed_cert() -> tuple[str, str]:
    """Generate self-signed SSL cert and key if missing.

    Returns:
        tuple[str, str]: Paths to (certfile, keyfile).
    """
    ssl_dir = settings.DATA_DIR / "ssl"
    ssl_dir.mkdir(parents=True, exist_ok=True)
    cert_path = ssl_dir / "cert.pem"
    key_path = ssl_dir / "key.pem"

    if cert_path.exists() and key_path.exists():
        return str(cert_path), str(key_path)

    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Sherry Web UI"),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)

    san_list: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv4Address("0.0.0.0")),
    ]

    host_str = str(settings.WEB_UI_HOST).strip()
    if host_str not in ("127.0.0.1", "0.0.0.0", "localhost"):
        try:
            san_list.append(x509.IPAddress(ipaddress.ip_address(host_str)))
        except ValueError:
            san_list.append(x509.DNSName(host_str))

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return str(cert_path), str(key_path)


# ---------------------------------------------------------------------------
# 啟動預熱（R12）：git 版本資訊與組件指標於背景載入，避免首次請求阻塞
# ---------------------------------------------------------------------------

_STARTUP_WARMUP_TASKS: set[asyncio.Task[Any]] = set()


async def _warm_git_info() -> None:
    """背景載入啟動版本資訊（取代匯入期同步 git 子進程）。"""
    global _STARTUP_GIT_INFO
    try:
        info = await _async_get_git_info()
        if not _STARTUP_GIT_INFO:
            _STARTUP_GIT_INFO = info
    except Exception as exc:
        logger.debug("Startup git info warmup failed: %s", exc)


async def _warm_component_metrics() -> None:
    """背景預熱組件指標（含 SQLite 歷史 bootstrap 與百分位計算）。"""
    try:
        from core.sys_inspector.metrics import component_metrics

        await asyncio.to_thread(component_metrics.get_summary)
    except Exception as exc:
        logger.debug("Component metrics warmup failed: %s", exc)


async def _run_startup_warmup() -> None:
    """並行預熱啟動資訊；個別失敗不影響伺服器啟動。"""
    await asyncio.gather(
        _warm_git_info(),
        _warm_component_metrics(),
        return_exceptions=True,
    )


@app.before_serving
async def _schedule_startup_warmup() -> None:
    """伺服器開始服務前，以背景 Task 預熱啟動資訊與組件指標。"""
    task = asyncio.create_task(_run_startup_warmup())
    _STARTUP_WARMUP_TASKS.add(task)
    task.add_done_callback(_STARTUP_WARMUP_TASKS.discard)


async def start_server_async(bot: Any = None) -> None:
    """Start the Quart server as an asyncio coroutine."""
    global _bot_instance
    _bot_instance = bot
    if bot is not None:
        _discover_and_mount_mod_plugins(app, bot)
    import uvicorn

    if getattr(settings, "_WEB_UI_PASSWORD_GENERATED", False):
        print(
            "\n======================================================\n"
            "⚠️ [SECURITY] 尚未配置管理員登入憑證（WEB_UI_PASSWORD_HASH / WEB_UI_PASSWORD）。\n"
            "Web UI 登入目前為 Fail-Closed（一律拒絕），請先產生憑證並寫入 .env：\n"
            "    python -m web_ui.server --gen-password\n"
            "（基於資安，本系統不再於啟動時自動生成或列印任何明文密碼）\n"
            "======================================================\n"
        )

    ssl_certfile = settings.WEB_UI_SSL_CERT
    ssl_keyfile = settings.WEB_UI_SSL_KEY

    if settings.WEB_UI_HTTPS or ssl_certfile or ssl_keyfile:
        if not ssl_certfile or not ssl_keyfile:
            ssl_certfile, ssl_keyfile = _ensure_self_signed_cert()

    # 確保 Web UI 專屬 Log 目錄存在
    settings.WEB_UI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 設定 Uvicorn / Quart 存取日誌分流至 data/web_ui.log，不干擾主視窗控制台
    web_log_handler = logging.FileHandler(settings.WEB_UI_LOG_PATH, encoding="utf-8")
    web_log_handler.setLevel(logging.INFO)
    web_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))

    # 存取日誌 (uvicorn.access)：清掉預設 console handler，只寫入 web_ui.log
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = [web_log_handler]
    access_logger.propagate = False

    # 其餘 Web 相關 logger：附加一份到 web_ui.log（不重複掛同一 handler，
    # 避免同檔號誌被寫入兩次；也不動 propagate，保留主視窗可見性）
    for logger_name in ["uvicorn.error", "quart.app", "quart.serving", "web_ui.server"]:
        web_logger = logging.getLogger(logger_name)
        web_logger.setLevel(logging.INFO)
        if web_log_handler not in web_logger.handlers:
            web_logger.addHandler(web_log_handler)

    kwargs: dict[str, Any] = {
        "app": app,
        "host": str(settings.WEB_UI_HOST),
        "port": int(settings.WEB_UI_PORT),
        "loop": "asyncio",
        "log_config": None,
        "access_log": True,
    }

    if ssl_certfile and ssl_keyfile:
        kwargs["ssl_certfile"] = ssl_certfile
        kwargs["ssl_keyfile"] = ssl_keyfile

    try:
        config = uvicorn.Config(**kwargs)
        server = uvicorn.Server(config)
        await server.serve()
    except (OSError, SystemExit) as e:
        # R2（§5.3.9）：綁定失敗一律 fail-fast，不再靜默回退 0.0.0.0；
        # 僅在顯式設定 ALLOW_FALLBACK_BIND=1 時允許回退（供知情的自訂佈署使用）。
        allow_fallback = str(os.getenv("ALLOW_FALLBACK_BIND", "")).strip().lower() in ("1", "true", "yes", "on")
        if allow_fallback and kwargs.get("host") != "0.0.0.0":
            logger.warning(
                "Failed to bind Web UI on %s:%s (%s), falling back to 0.0.0.0 (ALLOW_FALLBACK_BIND=1)...",
                kwargs.get("host"),
                kwargs.get("port"),
                e,
            )
            kwargs["host"] = "0.0.0.0"
            config = uvicorn.Config(**kwargs)
            server = uvicorn.Server(config)
            await server.serve()
        else:
            logger.error(
                "Failed to bind Web UI on %s:%s (%s). 「Fail-fast」：不自動回退 0.0.0.0；"
                "如需允許回退，請顯式設定 ALLOW_FALLBACK_BIND=1 後重啟。",
                kwargs.get("host"),
                kwargs.get("port"),
                e,
            )
            raise


def _cli_main(argv: list[str] | None = None) -> int:
    """web_ui.server 命令列入口：目前支援 --gen-password 一次性生成管理員憑證。

    僅列印、不寫入任何檔案；請將輸出自行寫入 .env。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "用法: python -m web_ui.server --gen-password\n"
            "  --gen-password  生成一組高強度管理員密碼與對應 WEB_UI_PASSWORD_HASH（僅列印一次）"
        )
        return 1
    if args[0] in ("-h", "--help"):
        print(
            "用法: python -m web_ui.server --gen-password\n"
            "  --gen-password  生成一組高強度管理員密碼與對應 WEB_UI_PASSWORD_HASH（僅列印一次）"
        )
        return 0
    if args[0] == "--gen-password":
        password = secrets.token_urlsafe(16)
        password_hash = settings.hash_web_ui_password(password)
        print("=====================================================")
        print("管理員登入憑證（僅本次顯示，請立即寫入 .env 保存）：")
        print(f"WEB_UI_PASSWORD_HASH={password_hash}")
        print(f"（對應明文密碼，供首次登入使用：{password}）")
        print("建議僅保留 WEB_UI_PASSWORD_HASH；確認可登入後即可移除明文。")
        print("=====================================================")
        return 0
    print(f"未知參數: {args[0]}（僅支援 --gen-password）")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli_main(sys.argv[1:]))
