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
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from quart import Quart, Response, jsonify, render_template, request, send_file

import settings

logger = logging.getLogger(__name__)

app = Quart(__name__)
app.secret_key = os.urandom(24)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # 開發模式：停用靜態檔案快取

# Dynamic plugins are mounted automatically during server initialization

# ---------------------------------------------------------------------------
# Session & Auth Helpers (Token-based with 10-min Inactivity Expiration)
# ---------------------------------------------------------------------------

_admin_sessions: dict[str, float] = {}  # token -> last_active_timestamp
_SESSION_TIMEOUT_SEC: float = 600.0  # 10 分鐘無動作自動過期
_bot_instance: Any = None
_login_attempts: dict[str, list[float]] = {}  # IP -> 失敗時間戳記列表
_locked_ips: dict[str, float] = {}          # IP -> 解鎖時間戳記


def _get_client_ip(req: Any) -> str:
    """Extract client IP addressing reverse proxy headers."""
    if hasattr(req, "headers"):
        forwarded = req.headers.get("X-Forwarded-For")
        if forwarded:
            return str(forwarded.split(",")[0].strip())
    return str(getattr(req, "remote_addr", "127.0.0.1") or "127.0.0.1")


def _is_ip_locked(ip: str) -> tuple[bool, int]:
    """Check if client IP is currently locked out due to repeated failed login attempts."""
    import time
    now = time.time()
    if ip in _locked_ips:
        unlock_time = _locked_ips[ip]
        if now < unlock_time:
            remaining = int(unlock_time - now)
            return True, remaining
        # Expired lockout -> purge
        _locked_ips.pop(ip, None)
        _login_attempts.pop(ip, None)
    return False, 0


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
            ["git", "log", "-1", "--format=%h|%H|%s|%cd|%an"],
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
        "tag": "v1.4.1",
    }


_STARTUP_GIT_INFO: dict[str, Any] = _get_git_info()


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


def _is_admin_request(req: Any) -> bool:
    """Validate Bearer Token, X-Admin-Token, or HttpOnly admin_session cookie."""
    import time

    _clean_expired_sessions()

    token = _get_request_token(req)

    if not token or token not in _admin_sessions:
        return False

    # Check inactivity timeout
    now = time.time()
    last_active = _admin_sessions[token]
    if now - last_active > _SESSION_TIMEOUT_SEC:
        _admin_sessions.pop(token, None)
        return False

    # Refresh last active timestamp
    _admin_sessions[token] = now
    return True


_EXCLUDED_MOD_DIRS: set[str] = {"clients", "repositories", "__pycache__"}
_MOD_CONFIG_PATH: Path = settings.DATA_DIR / "mod_config.json"
_mounted_mod_blueprints: dict[str, Any] = {}
_public_mod_routes: set[str] = set()


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


def _scan_and_update_public_mod_routes() -> set[str]:
    """Dynamically discover public route prefixes declared in mod MOD_INFO dicts.

    Returns:
        set[str]: Set of public route path prefixes.
    """
    import importlib
    global _public_mod_routes
    routes: set[str] = set()
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
                    if isinstance(declared_routes, (list, tuple, set)):
                        for r in declared_routes:
                            if isinstance(r, str) and r.strip():
                                routes.add(r.strip())
                except Exception as exc:
                    logger.debug("Could not inspect public_routes for mod [%s]: %s", mod_key, exc)
    _public_mod_routes = routes
    return routes


# Initialize public mod routes upon server startup
_scan_and_update_public_mod_routes()


@app.before_request
async def check_admin_session_before_request() -> Response | tuple[Response, int] | None:
    """Global Request Interceptor: enforces Admin Session TTL and method guards."""
    if request.path.startswith("/static") or request.method == "GET":
        return None

    # Public POST routes (Core Auth Login/Logout)
    if request.path in ("/api/auth/login", "/api/auth/logout"):
        return None

    # Dynamic public routes declared by modules (MOD_INFO.public_routes)
    for pub_route in _public_mod_routes:
        if request.path.startswith(pub_route) or request.path == pub_route:
            return None

    # All other POST/DELETE/PUT write routes require Admin Auth
    if not _is_admin_request(request):
        return jsonify({
            "status": "error",
            "message": "權限不足或管理員 Session 已過期（超過10分鐘無動作），請重新登入。"
        }), 401

    return None


@app.after_request
async def add_no_cache_headers(response: Response) -> Response:
    """Force browsers to never cache static assets or API responses."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
async def index() -> str:
    return await render_template("index.html")


# ---------------------------------------------------------------------------
# API: Logs
# ---------------------------------------------------------------------------

# Log 檔副檔名白名單（避免讀取非日誌檔案）
_LOG_EXTENSIONS = {".jsonl", ".json", ".log"}
# 敏感字串黑名單（不允許讀取）
_LOG_BLACKLIST = {"config_override"}


def _list_log_files() -> list[dict]:
    """Scan DATA_DIR for log-like files and return metadata list."""
    files: list[dict] = []
    if not settings.DATA_DIR.exists():
        return files
    for p in sorted(settings.DATA_DIR.iterdir()):
        if p.is_file() and p.suffix in _LOG_EXTENSIONS:
            if any(bl in p.stem for bl in _LOG_BLACKLIST):
                continue
            files.append(
                {
                    "name": p.name,
                    "stem": p.stem,
                    "size_bytes": p.stat().st_size,
                    "modified": p.stat().st_mtime,
                }
            )
    return files


@app.route("/api/logs/files", methods=["GET"])
async def get_log_files() -> Response | tuple[Response, int] | Any:
    """Return a list of available log files in DATA_DIR (Filtered for Guest mode)."""
    all_files = _list_log_files()
    if _is_admin_request(request):
        return jsonify(all_files)

    # 訪客模式：僅允許查看 bot.log 與 runtime_log.jsonl
    guest_files = [f for f in all_files if f.get("name", "").lower() in ("bot.log", "runtime_log.jsonl")]
    return jsonify(guest_files)


@app.route("/api/logs", methods=["GET"])
async def get_logs() -> Response | tuple[Response, int] | Any:
    """
    Return log entries from a specified file.

    Query params:
      file  — filename (default: runtime_log.jsonl)
      page  — 1-indexed page number (default: 1)
      size  — entries per page (default: 50, max: 200)
    """
    filename: str = request.args.get("file", "runtime_log.jsonl")
    page: int = max(1, int(request.args.get("page", 1)))
    size: int = min(200, max(1, int(request.args.get("size", 50))))

    # Safety: strip directory traversal
    safe_name = Path(filename).name

    # 訪客權限限制：未登入狀態下僅允許查看 bot.log 與 runtime_log.jsonl
    if not _is_admin_request(request):
        if safe_name.lower() not in ("bot.log", "runtime_log.jsonl"):
            return jsonify({
                "status": "forbidden",
                "message": f"未登入狀態僅允許查看 bot.log 與 runtime.log！欲查看 '{safe_name}' 請進行管理員登入。",
                "entries": [],
                "total": 0,
                "page": page,
                "size": size
            }), 403

    log_path: Path = settings.DATA_DIR / safe_name

    if not log_path.exists():
        return jsonify({"entries": [], "total": 0, "page": page, "size": size})

    entries: list[dict] = []

    try:
        suffix = log_path.suffix.lower()
        content = log_path.read_text(encoding="utf-8")

        if suffix == ".jsonl":
            # JSONL: each line is a JSON object (may be separated by blank lines)
            raw_blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
            # Also handle plain single-object-per-line format
            if not raw_blocks:
                raw_blocks = [ln.strip() for ln in content.splitlines() if ln.strip()]
            for block in raw_blocks:
                try:
                    entries.append(json.loads(block))
                except Exception:
                    pass
        elif suffix == ".json":
            obj = json.loads(content)
            if isinstance(obj, list):
                entries = obj
            else:
                entries = [obj]
        else:
            # Plain text — wrap each line as a dict
            for ln in content.splitlines():
                if ln.strip():
                    entries.append({"event": "raw", "message": ln})

    except Exception as exc:
        return jsonify({"error": str(exc), "entries": [], "total": 0, "page": page, "size": size})

    total = len(entries)
    # Newest first
    entries = list(reversed(entries))
    start = (page - 1) * size
    paginated = entries[start : start + size]

    return jsonify(
        {
            "entries": paginated,
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
        return jsonify({"models": [], "source": "ollama", "error": str(exc)})


@app.route("/api/models/freetoken", methods=["GET"])
@app.route("/api/models/llm", methods=["GET"])
async def get_freetoken_models() -> Response | tuple[Response, int] | Any:
    """Proxy FreeToken /v1/models to retrieve locally loaded Hugging Face MoE models."""
    base_url: str = getattr(settings, "LLM_BASE_URL", getattr(settings, "FREETOKEN_BASE_URL", "http://localhost:1919/v1")).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/models")
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])] if isinstance(data, dict) else []
            return jsonify({"models": models, "source": "freetoken", "base_url": base_url})
    except Exception as exc:
        return jsonify({"models": [], "source": "freetoken", "error": str(exc)})


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
        return jsonify({"models": [], "source": "lms", "error": str(exc)})


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
        return jsonify({"models": [], "source": "vllm", "error": str(exc)})


# ---------------------------------------------------------------------------
# API: Auth
# ---------------------------------------------------------------------------

@app.route("/api/auth/login", methods=["POST"])
async def auth_login() -> tuple[Response, int] | Response:
    """Authenticate with password using constant-time comparison & brute-force protection."""
    import hmac
    import logging
    import secrets
    import time

    logger = logging.getLogger(__name__)
    ip = _get_client_ip(request)
    is_locked, remaining_sec = _is_ip_locked(ip)
    if is_locked:
        remaining_min = max(1, (remaining_sec + 59) // 60)
        logger.warning("[SECURITY] Blocked login attempt from locked IP: %s (%d sec remaining)", ip, remaining_sec)
        return jsonify({
            "status": "error",
            "message": f"登入失敗次數過多，該 IP 已暫時鎖定，請於 {remaining_min} 分鐘後再試！",
            "retry_after_sec": remaining_sec,
        }), 429

    data: dict = await request.get_json() or {}
    password = str(data.get("password", ""))
    username = str(data.get("username", "admin")).strip() or "admin"

    target_pw = str(getattr(settings, "WEB_UI_PASSWORD", "1919810"))
    # 恆定時間密碼比對，徹底消滅時序攻擊 (Timing Attack)
    is_valid = hmac.compare_digest(password.encode("utf-8"), target_pw.encode("utf-8"))

    if not is_valid:
        now = time.time()
        attempts = _login_attempts.setdefault(ip, [])
        # 清理超過 60 秒的歷史紀錄
        attempts = [t for t in attempts if now - t <= 60]
        attempts.append(now)
        _login_attempts[ip] = attempts

        max_attempts = getattr(settings, "WEB_UI_MAX_LOGIN_ATTEMPTS", 5)
        lockout_mins = getattr(settings, "WEB_UI_LOCKOUT_MINUTES", 15)

        if len(attempts) >= max_attempts:
            _locked_ips[ip] = now + lockout_mins * 60
            logger.warning("[SECURITY] IP %s triggered brute-force lockout (%d attempts in 1 min)", ip, len(attempts))
            return jsonify({
                "status": "error",
                "message": f"密碼錯誤連達 {max_attempts} 次，該 IP 已觸發安全機制封鎖 {lockout_mins} 分鐘！",
                "retry_after_sec": lockout_mins * 60,
            }), 429

        logger.warning("[SECURITY] Failed login attempt from IP %s for user '%s' (%d/%d)", ip, username, len(attempts), max_attempts)
        return jsonify({
            "status": "error",
            "message": f"管理員密碼錯誤！（警告：連續失敗 {max_attempts} 次將自動鎖定 IP）",
        }), 401

    # 登入成功 — 重置該 IP 的失敗記錄
    _login_attempts.pop(ip, None)
    _locked_ips.pop(ip, None)

    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = time.time()
    logger.info("[SECURITY] Admin login successful: User='%s', IP=%s", username, ip)

    # 寫入審計紀錄 (Audit Log)
    try:
        from mod.audit_manager import audit_manager
        audit_manager.record_login(
            session_id=token,
            username=username,
            ip=ip,
            user_agent=request.headers.get("User-Agent", ""),
        )
    except Exception as exc:
        logger.warning("Failed to record audit login: %s", exc)

    resp = jsonify({
        "status": "success",
        "token": token,
        "username": username,
        "expires_in_sec": int(_SESSION_TIMEOUT_SEC),
        "message": f"管理員 {username} 登入成功 (已發行 HttpOnly 資安 Cookie，10分鐘無動作自動登出)",
    })
    # 設定 HttpOnly Secure Cookie 防止 XSS 腳本竊取 Session Token
    resp.set_cookie(
        "admin_session",
        token,
        max_age=int(_SESSION_TIMEOUT_SEC),
        httponly=True,
        samesite="Strict",
        path="/",
    )
    return resp


@app.route("/api/auth/logout", methods=["POST"])
async def auth_logout() -> Response | tuple[Response, int] | Any:
    """Logout, revoke session token, and purge HttpOnly cookie."""
    data: dict = await request.get_json() or {}
    token = data.get("token", "")
    if not token:
        token = _get_request_token(request)

    if token:
        _admin_sessions.pop(token, None)
        try:
            from mod.audit_manager import audit_manager
            audit_manager.record_logout(token)
        except Exception:
            pass

    resp = jsonify({"status": "success", "message": "已成功登出管理員模式"})
    resp.delete_cookie("admin_session", path="/")
    return resp


@app.route("/api/auth/check", methods=["GET"])
async def auth_check() -> Response | tuple[Response, int] | Any:
    """Check if the current session token is valid and active."""
    is_admin = _is_admin_request(request)
    return jsonify({
        "is_admin": is_admin,
        "timeout_sec": int(_SESSION_TIMEOUT_SEC),
    })


@app.route("/api/services/status", methods=["GET"])
async def get_services_status() -> Response | tuple[Response, int] | Any:
    """即時檢測 Ollama, LM Studio, Stable Diffusion / ComfyUI 等地端 AI 服務連線狀態與延遲。"""
    import time
    services: list[dict[str, Any]] = []

    # 1. Ollama LLM
    ollama_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            ping_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name") for m in data.get("models", [])]
                services.append({
                    "id": "ollama",
                    "name": "Ollama LLM 服務",
                    "icon": "🦙",
                    "url": ollama_url,
                    "status": "online",
                    "ping_ms": ping_ms,
                    "details": f"服務線上，共偵測到 {len(models)} 個可用模型",
                    "models": models[:6],
                })
            else:
                services.append({"id": "ollama", "name": "Ollama LLM 服務", "icon": "🦙", "url": ollama_url, "status": "offline", "ping_ms": ping_ms, "details": f"HTTP {resp.status_code}", "models": []})
    except Exception:
        services.append({"id": "ollama", "name": "Ollama LLM 服務", "icon": "🦙", "url": ollama_url, "status": "offline", "ping_ms": 0, "details": "未啟動或連線超時", "models": []})

    # 2. FreeToken MoE 推理引擎
    freetoken_url = getattr(settings, "LLM_BASE_URL", getattr(settings, "FREETOKEN_BASE_URL", "http://localhost:1919/v1")).rstrip("/")
    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{freetoken_url}/models")
            ping_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", [])] if isinstance(data, dict) else []
                services.append({
                    "id": "freetoken",
                    "name": "FreeToken MoE 引擎",
                    "icon": "🧠",
                    "url": freetoken_url,
                    "status": "online",
                    "ping_ms": ping_ms,
                    "details": f"服務線上，已加載 {len(models)} 個 MoE 專家模型",
                    "models": models[:6],
                })
            else:
                services.append({"id": "freetoken", "name": "FreeToken MoE 引擎", "icon": "🧠", "url": freetoken_url, "status": "offline", "ping_ms": ping_ms, "details": f"HTTP {resp.status_code}", "models": []})
    except Exception:
        services.append({"id": "freetoken", "name": "FreeToken MoE 引擎", "icon": "🧠", "url": freetoken_url, "status": "offline", "ping_ms": 0, "details": "未啟動或連線超時 (Port 1919)", "models": []})

    # 3. LM Studio (向下相容)
    lms_url = getattr(settings, "LMS_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{lms_url}/models")
            ping_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", [])]
                services.append({
                    "id": "lms",
                    "name": "LM Studio 服務",
                    "icon": "🤖",
                    "url": lms_url,
                    "status": "online",
                    "ping_ms": ping_ms,
                    "details": f"服務線上，已加載 {len(models)} 個模型",
                    "models": models[:6],
                })
            else:
                services.append({"id": "lms", "name": "LM Studio 服務", "icon": "🤖", "url": lms_url, "status": "offline", "ping_ms": ping_ms, "details": f"HTTP {resp.status_code}", "models": []})
    except Exception:
        services.append({"id": "lms", "name": "LM Studio 服務", "icon": "🤖", "url": lms_url, "status": "offline", "ping_ms": 0, "details": "未啟動或連線超時", "models": []})

    # 3. vLLM Engine
    vllm_url = getattr(settings, "VLLM_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{vllm_url}/models")
            ping_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", [])]
                services.append({
                    "id": "vllm",
                    "name": "vLLM 引擎服務",
                    "icon": "⚡",
                    "url": vllm_url,
                    "status": "online",
                    "ping_ms": ping_ms,
                    "details": f"服務線上，已加載 {len(models)} 個模型",
                    "models": models[:6],
                })
            else:
                services.append({"id": "vllm", "name": "vLLM 引擎服務", "icon": "⚡", "url": vllm_url, "status": "offline", "ping_ms": ping_ms, "details": f"HTTP {resp.status_code}", "models": []})
    except Exception:
        services.append({"id": "vllm", "name": "vLLM 引擎服務", "icon": "⚡", "url": vllm_url, "status": "offline", "ping_ms": 0, "details": "未啟動或連線超時 (Port 8000)", "models": []})

    # 4. Stable Diffusion WebUI / ComfyUI 繪圖引擎
    sd_url = "http://127.0.0.1:7860"
    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=settings.WEB_UI_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{sd_url}/sdapi/v1/options")
            ping_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                services.append({
                    "id": "sd_webui",
                    "name": "Stable Diffusion WebUI",
                    "icon": "🎨",
                    "url": sd_url,
                    "status": "online",
                    "ping_ms": ping_ms,
                    "details": "地端繪圖引擎 API 運作良好",
                    "models": [],
                })
            else:
                services.append({"id": "sd_webui", "name": "Stable Diffusion WebUI", "icon": "🎨", "url": sd_url, "status": "offline", "ping_ms": ping_ms, "details": f"HTTP {resp.status_code}", "models": []})
    except Exception:
        services.append({"id": "sd_webui", "name": "Stable Diffusion WebUI", "icon": "🎨", "url": sd_url, "status": "offline", "ping_ms": 0, "details": "未啟動或連線超時 (Port 7860)", "models": []})

    return jsonify({"services": services, "checked_at": time.strftime("%H:%M:%S")})


@app.route("/api/audit/logs", methods=["GET"])
async def get_audit_logs() -> tuple[Response, int] | Response:
    """取得管理員登入與操作歷史審計紀錄（僅限登入管理員）。"""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "審計紀錄僅限登入管理員查看！"}), 403
    from mod.audit_manager import audit_manager
    history = audit_manager.get_audit_history(limit=50)
    return jsonify({"status": "success", "history": history})


# ---------------------------------------------------------------------------
# API: Settings
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET", "POST"])
async def handle_settings() -> tuple[Response, int] | Response:
    """Get or update editable settings with strict Token/Key exclusion."""
    sensitive_keywords = ("TOKEN", "PASSWORD", "SECRET", "KEY")

    if request.method == "GET":
        data = settings.get_editable_settings()
        # 資安防護：徹底移除所有含有 TOKEN、KEY、PASSWORD、SECRET 的敏感欄位，完全不輸出在 Web UI
        filtered_data = {
            k: v for k, v in data.items()
            if not any(kw in k.upper() for kw in sensitive_keywords)
        }
        return jsonify(filtered_data)

    # POST — batch update with Strict Anti-Injection & Type Sanitization
    post_data: dict = await request.get_json() or {}
    failures: list[str] = []
    for k, raw_v in post_data.items():
        # 1. 防注入與路徑安全：設定 Key 必須轉為純文字 string 且符合合法的大寫變數命名規則
        k_str = str(k).strip()
        if not k_str.isupper() or not k_str.isidentifier():
            failures.append(k_str)
            continue

        # 2. 嚴格禁止透過 Web UI POST 修改任何 Token / Key / Password 類變數
        if any(kw in k_str for kw in sensitive_keywords):
            failures.append(k_str)
            continue

        # 3. 防注入過濾：若非基本型別（如試圖傳遞 dict/list 等複雜結構），一律強制轉為純文字 string
        #    若是正常字串也確保以單純 str 型態處理，阻斷任何命令/語法注入
        safe_v: Any = raw_v
        if isinstance(raw_v, str):
            safe_v = str(raw_v).strip()
            if safe_v.endswith(":") and any(
                safe_v == f"{p}:" for p in ("freetoken", "ollama", "lms", "vllm", "openai", "deepseek", "gemini", "openrouter", "claude")
            ):
                safe_v = ""
        elif isinstance(raw_v, (int, float, bool)):
            safe_v = raw_v
        else:
            safe_v = str(raw_v)

        if not settings.update_setting(k_str, safe_v):
            failures.append(k_str)

    if not failures:
        try:
            from mod.audit_manager import audit_manager
            token = _get_request_token(request)
            saved_keys = ", ".join(list(post_data.keys())[:5])
            if len(post_data) > 5:
                saved_keys += f" (共{len(post_data)}項)"
            audit_manager.record_action(token, "修改系統設定", f"成功保存設定: {saved_keys}")
        except Exception:
            pass
        return jsonify({"status": "success"})
    return (
        jsonify({"status": "partial_error", "failed_keys": failures}),
        400,
    )


@app.route("/api/restart", methods=["POST"])
async def restart_bot() -> Response | tuple[Response, int]:
    """Trigger a self-restart via bat launcher (conda-env safe, VS Code compatible)."""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "重啟雪莉僅限登入管理員使用，請先登入！"}), 403

    import asyncio
    import logging
    import os
    import subprocess

    try:
        from mod.audit_manager import audit_manager
        token = _get_request_token(request)
        audit_manager.record_action(token, "系統操作", "觸發自重啟雪莉機器人")
    except Exception:
        pass

    def _do_restart() -> None:
        import sys
        logger = logging.getLogger(__name__)
        if sys.platform == "win32":
            logger.info("Launching restart_sherry.bat ...")
            bat = os.path.join(os.getcwd(), "restart_sherry.bat")
            try:
                subprocess.Popen(
                    ["cmd.exe", "/C", bat],
                    cwd=os.getcwd(),
                    creationflags=0x00000010,  # CREATE_NEW_CONSOLE (完全脫離 VS Code 終端)
                    close_fds=True,
                )
            except Exception as e:
                logger.error("Failed to launch restart_sherry.bat: %s", e)
        else:
            logger.info("Launching restart_sherry.sh ...")
            sh = os.path.join(os.getcwd(), "restart_sherry.sh")
            try:
                subprocess.Popen(
                    ["/bin/bash", sh],
                    cwd=os.getcwd(),
                    start_new_session=True,
                    close_fds=True,
                )
            except Exception as e:
                logger.error("Failed to launch restart_sherry.sh: %s", e)
        logging.shutdown()
        os._exit(0)  # 立即由 OS 釋放 Socket，避免 Python GC 出現 SSL Transport 錯誤

    asyncio.get_running_loop().call_later(0.8, _do_restart)
    return jsonify({
        "status": "restarting",
        "message": "雪莉機器人正在重新啟動中，請稍候約 5-10 秒..."
    })


@app.route("/api/stop", methods=["POST"])
async def stop_bot() -> Response | tuple[Response, int]:
    """Trigger a self-shutdown of the Sherry bot process. Admin-only."""
    if not _is_admin_request(request):
        return jsonify({"status": "error", "message": "關閉雪莉僅限登入管理員使用，請先登入！"}), 403

    import asyncio
    import logging
    import os

    try:
        from mod.audit_manager import audit_manager
        token = _get_request_token(request)
        audit_manager.record_action(token, "系統操作", "觸發關閉/停止雪莉機器人")
    except Exception:
        pass

    def _do_stop() -> None:
        logger = logging.getLogger(__name__)
        logger.info("Stopping Sherry bot per admin request...")
        logging.shutdown()
        os._exit(0)  # 立即終止 Python 程序與 Web UI

    asyncio.get_running_loop().call_later(0.8, _do_stop)
    return jsonify({
        "status": "stopping",
        "message": "雪莉機器人已接收關閉指令，即將停止運行。"
    })


@app.route("/api/update", methods=["POST"])
async def update_and_restart() -> Response | tuple[Response, int] | Any:
    """Git pull origin dev and restart via bat launcher."""
    import asyncio
    import logging
    import os
    import subprocess

    def _do_update() -> None:
        import sys
        logger = logging.getLogger(__name__)
        if sys.platform == "win32":
            logger.info("Launching update_and_restart.bat (git pull + restart)...")
            bat = os.path.join(os.getcwd(), "update_and_restart.bat")
            try:
                subprocess.Popen(
                    ["cmd.exe", "/C", bat],
                    cwd=os.getcwd(),
                    creationflags=0x00000010,  # CREATE_NEW_CONSOLE
                    close_fds=True,
                )
            except Exception as e:
                logger.error("Failed to launch update_and_restart.bat: %s", e)
        else:
            logger.info("Launching update_and_restart.sh (git pull + restart)...")
            sh = os.path.join(os.getcwd(), "update_and_restart.sh")
            try:
                subprocess.Popen(
                    ["/bin/bash", sh],
                    cwd=os.getcwd(),
                    start_new_session=True,
                    close_fds=True,
                )
            except Exception as e:
                logger.error("Failed to launch update_and_restart.sh: %s", e)
        logging.shutdown()
        os._exit(0)  # 立即由 OS 釋放 Socket，避免 Python GC 出現 SSL Transport 錯誤

    asyncio.get_running_loop().call_later(0.8, _do_update)
    return jsonify({
        "status": "updating",
        "message": "正在從 dev 分支拉取最新代碼並重新啟動雪莉，請稍候約 10-20 秒..."
    })


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

    data: dict = await request.get_json() or {}
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
        + (f"\n{web_context}\n⚠️【情報整合分析指令】：請參考上述『即時網路與實體檢索情報』，結合事實數據與妳的名偵探動態性格，為對方給出深入且充滿特色的解答與分析！\n\n" if web_context else "")
        + f"{user_message}\n"
        + (f"\n{tail_lore}\n" if tail_lore else "")
        + "【回覆規則】\n"
        + "1. 🎯 將 100% 注意力集中在『當下必須回應的目標』，給出最直接的回覆。\n"
        + "2. 【豐富生動的對話】：請像個話多、熱情又愛互動的 15 歲台灣女高中生一樣聊天！可以講 2～4 句話，內容要有情緒起伏與可愛的互動。\n"
        + "3. 不知道的事請直說，不要臆造資訊。\n"
        + "4. 只輸出橘雪莉說的話，不要任何標籤、前綴。\n"
        + "5. 絕對禁止使用 Markdown 格式 (包含反引號、粗體等)。"
    )

    user_instruct = (
        f"🎯 對方（Web 穩定性測試員）剛剛對妳說：「{user_message}」\n橘雪莉的回覆（語氣親切活潑，正在協助進行穩定性測試）："
    )

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
        return jsonify({
            "status": "success",
            "reply": clean_reply,
            "intent": "CHAT (強制觸發測試)",
            "model": settings.PRIMARY_MODEL,
            "latency_ms": elapsed_ms,
        })
    except asyncio.TimeoutError:
        return jsonify({"status": "error", "message": "生成回答逾時 (超過 60 秒)，請檢查 BotBrain 與模型連線狀態！"}), 504
    except Exception as e:
        logger.error("Web UI CHAT test error: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": f"回答生成錯誤: {e}"}), 500


# ---------------------------------------------------------------------------
# System Metrics & Tasks APIs
# ---------------------------------------------------------------------------

_last_net_bytes: tuple[int, int, float] | None = None


def _get_system_metrics() -> dict[str, Any]:
    """Gather CPU, RAM, Network, and GPU metrics without heavy computation."""
    import subprocess
    import time

    global _last_net_bytes

    now = time.time()
    cpu_percent = 0.0
    cpu_count = 1
    cpu_freq_mhz = 0.0
    ram_total_gb = 0.0
    ram_used_gb = 0.0
    ram_percent = 0.0
    sent_rate_kbps = 0.0
    recv_rate_kbps = 0.0
    total_sent_mb = 0.0
    total_recv_mb = 0.0

    # 1~3. CPU, RAM, Net Metrics (with psutil fallback)
    try:
        import psutil

        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count(logical=True) or 1
        cpu_freq = psutil.cpu_freq()
        cpu_freq_mhz = round(cpu_freq.current, 1) if cpu_freq else 0.0

        mem = psutil.virtual_memory()
        ram_total_gb = round(mem.total / (1024 ** 3), 2)
        ram_used_gb = round(mem.used / (1024 ** 3), 2)
        ram_percent = mem.percent

        net_io = psutil.net_io_counters()
        total_sent_mb = round(net_io.bytes_sent / (1024 ** 2), 1)
        total_recv_mb = round(net_io.bytes_recv / (1024 ** 2), 1)

        if _last_net_bytes is not None:
            prev_sent, prev_recv, prev_time = _last_net_bytes
            dt = max(0.1, now - prev_time)
            sent_rate_kbps = round((net_io.bytes_sent - prev_sent) / 1024 / dt, 1)
            recv_rate_kbps = round((net_io.bytes_recv - prev_recv) / 1024 / dt, 1)

        _last_net_bytes = (net_io.bytes_sent, net_io.bytes_recv, now)
    except ImportError:
        # Fallback if psutil is not installed in the environment
        pass

    # 4. GPU Metrics (via nvidia-smi if available)
    gpu_info: list[dict[str, Any]] = []
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, encoding="utf-8", timeout=1.5).strip()
        if out:
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    gpu_name = parts[0]
                    gpu_util = int(parts[1]) if parts[1].isdigit() else 0
                    mem_used = int(parts[2]) if parts[2].isdigit() else 0
                    mem_total = int(parts[3]) if parts[3].isdigit() else 1
                    temp = int(parts[4]) if parts[4].isdigit() else 0
                    gpu_info.append({
                        "name": gpu_name,
                        "utilization": gpu_util,
                        "memory_used_mb": mem_used,
                        "memory_total_mb": mem_total,
                        "memory_percent": round((mem_used / mem_total) * 100, 1),
                        "temperature_c": temp,
                    })
    except Exception:
        pass

    return {
        "cpu": {
            "percent": cpu_percent,
            "count": cpu_count,
            "freq_mhz": cpu_freq_mhz,
        },
        "ram": {
            "used_gb": ram_used_gb,
            "total_gb": ram_total_gb,
            "percent": ram_percent,
        },
        "network": {
            "sent_rate_kbps": sent_rate_kbps,
            "recv_rate_kbps": recv_rate_kbps,
            "total_sent_mb": round(net_io.bytes_sent / (1024 ** 2), 1),
            "total_recv_mb": round(net_io.bytes_recv / (1024 ** 2), 1),
        },
        "gpu": gpu_info,
        "timestamp": now,
    }


@app.route("/api/system/metrics", methods=["GET"])
async def get_system_metrics() -> Response | tuple[Response, int] | Any:
    """Return real-time system resource metrics."""
    return jsonify(_get_system_metrics())


@app.route("/api/system/tasks", methods=["GET"])
async def get_scheduled_tasks() -> Response | tuple[Response, int] | Any:
    """Return all active scheduled tasks."""
    try:
        from handlers import schedule_storage
        tasks = await schedule_storage.load_all_tasks()
        task_list = []
        for t in tasks:
            task_list.append({
                "task_id": t.get("task_id", ""),
                "type": t.get("schedule_type", t.get("type", "once")),
                "prompt": t.get("content", t.get("prompt", "")),
                "target_channel_id": t.get("channel_id", t.get("target_channel_id", "")),
                "trigger_timestamp": t.get("trigger_time", t.get("trigger_timestamp")),
                "cron_expression": t.get("cron_expr", t.get("cron_expression")),
                "display_name": t.get("display_name", ""),
                "user_id": t.get("user_id", ""),
                "created_at": t.get("created_at"),
            })
        return jsonify({"tasks": task_list, "total": len(task_list)})
    except Exception as exc:
        logger.error("get_scheduled_tasks error: %s", exc)
        return jsonify({"tasks": [], "total": 0, "error": str(exc)})


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
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/system/dashboard", methods=["GET"])
async def get_dashboard_data() -> Response | tuple[Response, int] | Any:
    """Return unified dashboard summary payload (git info, bot summary, system summary, queues)."""
    try:
        disk_git = _get_git_info()
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
        sys_metrics = _get_system_metrics()
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

        return jsonify({
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
            "queues": {
                "brain_llm": brain_queue_count,
                "schedule": schedule_count,
            },
            "version": "v1.4.1",
        })
    except Exception as exc:
        logger.error("get_dashboard_data error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


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
        return jsonify({"queue": [], "total": 0, "error": str(exc)})


@app.route("/api/bot/status", methods=["GET"])
async def get_bot_live_status() -> Response | tuple[Response, int] | Any:
    """Return real-time Discord Bot connection, voice channels, and music status."""
    try:
        bot = _bot_instance
        if not bot or not getattr(bot, "is_ready", lambda: False)():
            return jsonify({
                "ready": False,
                "bot_name": "雪莉 Sherry (啟動中...)",
                "guild_count": 0,
                "ping_ms": 0,
                "voice_connections": [],
            })

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

            if music_cog and guild:
                g_id = guild.id
                raw_song = music_cog.current_song.get(g_id)
                if raw_song:
                    cur_song = {
                        "title": raw_song.get("title", "未知歌曲"),
                        "url": raw_song.get("webpage_url", ""),
                        "duration": raw_song.get("duration", 0),
                        "requester": raw_song.get("requester", "未知"),
                    }
                is_paused = music_cog.is_paused.get(g_id, False)
                raw_queue = music_cog.queues.get(g_id, [])
                for item in raw_queue[:5]:
                    queue_list.append({
                        "title": item.get("title", "未知歌曲"),
                        "requester": item.get("requester", "未知"),
                    })

            voice_connections.append({
                "guild_id": str(guild.id),
                "guild_name": guild.name,
                "guild_icon": str(guild.icon.url) if guild and guild.icon else "",
                "channel_id": str(channel.id),
                "channel_name": channel.name,
                "members": members,
                "member_count": len(members),
                "is_playing": vc.is_playing(),
                "is_paused": is_paused,
                "current_song": cur_song,
                "queue": queue_list,
                "queue_count": len(music_cog.queues.get(guild.id, [])) if music_cog and guild else 0,
            })

        return jsonify({
            "ready": True,
            "bot_name": str(bot.user),
            "bot_avatar": str(bot.user.display_avatar.url) if bot.user else "",
            "guild_count": len(bot.guilds),
            "ping_ms": round(bot.latency * 1000, 1),
            "voice_connections": voice_connections,
        })
    except Exception as exc:
        logger.error("get_bot_live_status error: %s", exc)
        return jsonify({
            "ready": False,
            "error": str(exc),
            "voice_connections": [],
        })


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


# Initial mount of available mod blueprints
_discover_and_mount_mod_plugins(app)


# ── Static Asset & Dynamic View Routes for Mods ────────────

@app.route("/mod/<mod_key>/static/<path:filename>")
async def serve_mod_static(mod_key: str, filename: str) -> Response | tuple[Response, int] | Any:
    """Serve static files (JS, CSS, images) from mod/<mod_key>/static/ directory."""
    from quart import send_file

    if ".." in mod_key or ".." in filename or mod_key.startswith("_"):
        return Response("Forbidden", status=403)

    static_file = Path("mod") / mod_key / "static" / filename
    if static_file.is_file():
        return await send_file(static_file)
    return Response("Not Found", status=404)


@app.route("/api/mod/<mod_key>/view", methods=["GET"])
async def get_mod_view_snippet(mod_key: str) -> Response | tuple[Response, int] | Any:
    """Fetch and return rendered HTML view snippet for a specific mod."""
    if ".." in mod_key or mod_key.startswith("_"):
        return Response("Invalid mod key", status=400)

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
            content = p.read_text(encoding="utf-8")
            return Response(content, mimetype="text/html")

    return Response(f"<div class='view hidden' id='view-{mod_key}'><h3>模組 {mod_key} 視圖未提供</h3></div>", mimetype="text/html")


@app.route("/api/modules", methods=["GET"])
async def get_dynamic_modules() -> Response | tuple[Response, int] | Any:
    """Return available optional modules detected under ./mod/.

    Dynamically inspects each mod package for MOD_INFO without hardcoded metadata.
    """
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
        script_file = mod_info.get("script_file") or (f"{mod_key}.js" if (mod_dir / "static" / f"{mod_key}.js").exists() else None)
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

    return jsonify({"modules": modules})


@app.route("/api/modules/toggle", methods=["POST"])
async def toggle_module() -> tuple[Response, int] | Response:
    """Enable or disable a mod module. Admin-only.

    Expects JSON body: { "mod_key": str, "enabled": bool }

    Returns:
        Response: JSON status response.
    """
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

    # Load, update, and persist config
    mod_config = _load_mod_config()
    mod_config[mod_key] = enable
    _save_mod_config(mod_config)

    # Audit log
    try:
        from mod.audit_manager import audit_manager
        token = _get_request_token(request)
        action_desc = "啟用" if enable else "停用"
        audit_manager.record_action(
            token,
            "模組管理",
            f"{action_desc}模組: {mod_key}",
        )
    except Exception:
        pass

    action_zh = "啟用" if enable else "停用"
    logger.info("Mod '%s' %s by admin request.", mod_key, action_zh)
    return jsonify({
        "status": "success",
        "mod_key": mod_key,
        "enabled": enable,
        "message": f"模組「{mod_key}」已{action_zh}，請重啟雪莉以套用變更。",
    })


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
        return jsonify({"status": "error", "message": str(exc)}), 500


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
        return jsonify({
            "status": "success",
            "matched": False,
            "message": "未命中任何已啟用的吐槽關鍵字",
            "roast_reply": None,
        })

    selected_rule = None
    for rule in matching_rules:
        if force or roast_manager.roll_probability(rule):
            selected_rule = rule
            break

    if not selected_rule:
        return jsonify({
            "status": "success",
            "matched": True,
            "triggered": False,
            "message": f"命中關鍵字 '{matching_rules[0].keyword}' 但因機率判定（{matching_rules[0].probability}%）未觸發",
            "rule": matching_rules[0].to_dict(),
            "roast_reply": None,
        })

    # Try LLM generation if bot brain available
    roast_reply = selected_rule.reply_guide
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
                "🎯 【吐槽生成三大核心法則】（極為重要！）：\n"
                "1. 🔗 深度結合對方發言情境：妳必須敏銳抓住對方句子裡的具體人事物、動作或場景，將「核心吐槽指引」自然融合進去進行精準反駁！絕對禁止忽視對方說的內容只講生硬的罐頭台詞。\n"
                "2. 🍊 傲嬌毒舌名偵探人設：語氣自信、傲嬌且犀利，一針見血地戳破對方的幻想或吐槽對方的行為！\n"
                "3. ⚡ 俐落生動短句：長度約 15 ~ 55 字，必須是妳直接說出口的生動對白，絕對禁止在最外層包裹「」或 \"\" 引號，禁止任何機器人客套話！\n"
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
                raw = await asyncio.wait_for(future, timeout=6.0)
                cleaned = raw.strip().strip('"').strip("「").strip("」").strip()
                if cleaned:
                    roast_reply = cleaned
            except Exception:
                pass

    return jsonify({
        "status": "success",
        "matched": True,
        "triggered": True,
        "rule": selected_rule.to_dict(),
        "roast_reply": roast_reply,
    })



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
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Sherry Web UI"),
    ])
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


async def start_server_async(bot: Any = None) -> None:
    """Start the Quart server as an asyncio coroutine."""
    global _bot_instance
    _bot_instance = bot
    if bot is not None:
        _discover_and_mount_mod_plugins(app, bot)
    import uvicorn

    ssl_certfile = settings.WEB_UI_SSL_CERT
    ssl_keyfile = settings.WEB_UI_SSL_KEY

    if settings.WEB_UI_HTTPS or ssl_certfile or ssl_keyfile:
        if not ssl_certfile or not ssl_keyfile:
            ssl_certfile, ssl_keyfile = _ensure_self_signed_cert()

    kwargs: dict[str, Any] = {
        "app": app,
        "host": str(settings.WEB_UI_HOST),
        "port": int(settings.WEB_UI_PORT),
        "loop": "asyncio",
    }
    if ssl_certfile and ssl_keyfile:
        kwargs["ssl_certfile"] = ssl_certfile
        kwargs["ssl_keyfile"] = ssl_keyfile

    try:
        config = uvicorn.Config(**kwargs)
        server = uvicorn.Server(config)
        await server.serve()
    except (OSError, SystemExit) as e:
        if kwargs.get("host") != "0.0.0.0":
            logger.warning("Failed to bind Web UI on %s:%s (%s), falling back to 0.0.0.0...", kwargs.get("host"), kwargs.get("port"), e)
            kwargs["host"] = "0.0.0.0"
            config = uvicorn.Config(**kwargs)
            server = uvicorn.Server(config)
            await server.serve()
        else:
            raise


