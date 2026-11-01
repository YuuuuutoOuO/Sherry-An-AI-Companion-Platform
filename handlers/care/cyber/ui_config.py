from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("data") / "cyber_ui.json"
_LOCK = threading.RLock()
_CACHE: "CyberUIConfig | None" = None
_CACHE_MTIME = 0.0


@dataclass(slots=True)
class CyberUIConfig:
    """WebUI v2 架構設定。

    與舊 CareSurferConfig 分開存放，避免破壞既有 DB schema。
    真正需要與舊設定合併時，由 CyberEngine 以 merge 方式處理。
    """

    engine_mode: str = "shadow"
    gateway_enabled: bool = False
    owns_mentions: bool = False
    mention_scope: str = "sandbox"

    autonomous_enabled: bool = True
    tools_enabled: bool = True
    allowed_tools: list[str] = field(default_factory=lambda: ["SEARCH", "STOCK", "SUMMARY"])
    tool_max_rounds: int = 3

    dynamic_context_enabled: bool = True
    identity_tags_enabled: bool = True
    memory_context_enabled: bool = True
    schedule_context_enabled: bool = True

    max_bubbles: int = 2
    sanitize_output: bool = True

    # 連續訊息分段等待：最後一句後靜止這麼久才生成，避免一次打很多句被拆成多輪。
    debounce_seconds: int = 10
    mention_debounce_seconds: int = 2
    reply_scoring_enabled: bool = True
    ambient_score_threshold: int = 6
    idle_proactive_seconds: int = 1800
    # L2 自主：程式只開機會窗，由雪莉自己決定說／安靜／晚點說。
    autonomy_enabled: bool = True
    autonomy_hourly: int = 2
    autonomy_daily: int = 12
    autonomy_wait_max_minutes: int = 90
    autonomy_thought_enabled: bool = True
    # 生成速度：短對話回覆不需要深度推理。Gemini 等支援 thinking 的後端
    # 在 medium 會先花數千個思考 Token，體感延遲會非常明顯。
    reasoning_effort: str = "low"

    contact_hourly: int = 8
    contact_daily: int = 120
    contact_cooldown: int = 120
    window_seconds: int = 300
    followup_window_seconds: int = 900
    active_hour_start: int = 9
    active_hour_end: int = 4
    mind_pace: str = "normal"

    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "CyberUIConfig":
        data = payload or {}
        cfg = cls()
        for key in (
            "engine_mode",
            "gateway_enabled",
            "owns_mentions",
            "mention_scope",
            "autonomous_enabled",
            "tools_enabled",
            "tool_max_rounds",
            "dynamic_context_enabled",
            "identity_tags_enabled",
            "memory_context_enabled",
            "schedule_context_enabled",
            "max_bubbles",
            "sanitize_output",
            "debounce_seconds",
            "mention_debounce_seconds",
            "reply_scoring_enabled",
            "ambient_score_threshold",
            "idle_proactive_seconds",
            "autonomy_enabled",
            "autonomy_hourly",
            "autonomy_daily",
            "autonomy_wait_max_minutes",
            "autonomy_thought_enabled",
            "reasoning_effort",
            "contact_hourly",
            "contact_daily",
            "contact_cooldown",
            "window_seconds",
            "followup_window_seconds",
            "active_hour_start",
            "active_hour_end",
            "mind_pace",
        ):
            if key in data:
                setattr(cfg, key, data[key])
        allowed = data.get("allowed_tools")
        if isinstance(allowed, list):
            cfg.allowed_tools = [str(item).strip().upper() for item in allowed if str(item).strip()]
        if cfg.engine_mode not in {"off", "shadow", "live"}:
            cfg.engine_mode = "shadow"
        if cfg.mention_scope not in {"sandbox", "all"}:
            cfg.mention_scope = "sandbox"
        cfg.tool_max_rounds = max(0, min(5, int(cfg.tool_max_rounds)))
        # 氣泡上限：允許到 8 顆。舊版硬鎖 3 顆，WebUI 設 5 會被悄悄改成 3。
        cfg.max_bubbles = max(1, min(8, int(cfg.max_bubbles)))
        cfg.debounce_seconds = max(0, min(60, int(cfg.debounce_seconds)))
        cfg.mention_debounce_seconds = max(0, min(30, int(cfg.mention_debounce_seconds)))
        cfg.ambient_score_threshold = max(0, min(10, int(cfg.ambient_score_threshold)))
        cfg.idle_proactive_seconds = max(300, min(86400, int(cfg.idle_proactive_seconds)))
        cfg.autonomy_hourly = max(0, min(12, int(cfg.autonomy_hourly)))
        cfg.autonomy_daily = max(0, min(48, int(cfg.autonomy_daily)))
        cfg.autonomy_wait_max_minutes = max(30, min(180, int(cfg.autonomy_wait_max_minutes)))
        cfg.reasoning_effort = str(cfg.reasoning_effort or "low").strip().lower()
        if cfg.reasoning_effort not in {"low", "medium", "high"}:
            cfg.reasoning_effort = "low"
        cfg.contact_hourly = max(0, int(cfg.contact_hourly))
        cfg.contact_daily = max(0, int(cfg.contact_daily))
        cfg.contact_cooldown = max(0, int(cfg.contact_cooldown))
        cfg.window_seconds = max(30, int(cfg.window_seconds))
        # 追話寬限窗：主動／被動發言後，對方在多久內回話仍算「同一段對話」。
        # 這是為了解決「熱聊窗口只有 60 秒，超過就被當成路人」的體感問題；
        # 真正的防洗版交給 max_humans_since_bot、冷卻與每小時／每日額度。
        cfg.followup_window_seconds = max(60, min(7200, int(cfg.followup_window_seconds)))
        cfg.active_hour_start = int(cfg.active_hour_start) % 24
        cfg.active_hour_end = int(cfg.active_hour_end) % 24
        return cfg


def _path() -> Path:
    try:
        import settings

        base = Path(getattr(settings, "DATA_DIR", Path("data")))
    except Exception:
        base = Path("data")
    return base / "cyber_ui.json"


def load_cyber_ui_config(*, force: bool = False) -> CyberUIConfig:
    global _CACHE, _CACHE_MTIME
    path = _path()
    with _LOCK:
        try:
            mtime = path.stat().st_mtime if path.exists() else 0.0
        except Exception:
            mtime = 0.0
        if not force and _CACHE is not None and mtime == _CACHE_MTIME:
            return _CACHE
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[CyberUI] 讀取設定失敗，使用預設值: %s", exc)
                payload = {}
        cfg = CyberUIConfig.from_dict(payload)
        cfg.updated_at = float(payload.get("updated_at", 0.0) or 0.0)
        if not path.exists():
            try:
                import settings

                cfg.engine_mode = str(getattr(settings, "CYBER_ENGINE_MODE", cfg.engine_mode) or cfg.engine_mode)
                cfg.gateway_enabled = bool(getattr(settings, "INTERACTION_GATEWAY_ENABLED", cfg.gateway_enabled))
                cfg.owns_mentions = bool(getattr(settings, "CYBER_V2_OWNS_MENTIONS", cfg.owns_mentions))
                cfg.mention_scope = str(getattr(settings, "INTERACTION_MENTION_SCOPE", cfg.mention_scope) or cfg.mention_scope)
                cfg.tools_enabled = bool(getattr(settings, "CYBER_V2_TOOLS_ENABLED", cfg.tools_enabled))
            except Exception:
                pass
        _CACHE = cfg
        _CACHE_MTIME = mtime
        return cfg


def save_cyber_ui_config(cfg: CyberUIConfig) -> CyberUIConfig:
    global _CACHE, _CACHE_MTIME
    path = _path()
    cfg.updated_at = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    with _LOCK:
        _CACHE = cfg
        _CACHE_MTIME = path.stat().st_mtime
    return cfg


def get_cyber_ui_config() -> CyberUIConfig:
    return load_cyber_ui_config()


def configured_max_bubbles(default: int = 2) -> int:
    """舊 lane 也沿用 WebUI 的氣泡上限，避免兩條 lane 行為不一致。"""
    try:
        return max(1, min(8, int(get_cyber_ui_config().max_bubbles)))
    except Exception:
        return max(1, int(default))
