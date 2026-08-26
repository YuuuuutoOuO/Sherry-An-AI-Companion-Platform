"""
handlers/social/observability.py
==================================
集中式 JSONL 觀測寫入器。

全系統唯一的 debug log 入口點。
所有觀測事件都透過 log_event() 寫入 data/runtime_log.jsonl。
錯誤/降級事件額外寫入 data/error_log.jsonl。

寫入策略：
  - JSON Lines 格式（每行一個獨立 JSON），O(1) append，不讀整個檔案
  - 寫入失敗時靜默（try/except），絕對不影響主流程
  - runtime_log.jsonl 超過 50MB 自動封存為 .jsonl.1
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import settings

logger = logging.getLogger(__name__)

_RUNTIME_LOG_PATH: Path = settings.RUNTIME_LOG_PATH
_ERROR_LOG_PATH: Path = settings.ERROR_LOG_PATH
_MAX_LOG_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_line(path: Path, entry: dict) -> None:
    """Append a single JSON line to a JSONL file. Silently ignores all errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Rotate if file exceeds size limit
        if path.exists() and path.stat().st_size > _MAX_LOG_SIZE_BYTES:
            rotated = path.with_suffix(".jsonl.1")
            try:
                path.replace(rotated)
            except OSError:
                pass  # If rotation fails, keep writing to same file

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, indent=2) + "\n\n")
    except Exception as exc:
        # Never raise from observability layer — logging is best-effort
        logger.debug("observability: 寫入失敗 (%s): %s", path.name, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_event(
    event: str,
    *,
    # 互動識別
    message_id: int = 0,
    user: str = "",
    channel_id: int = 0,
    # 輸入
    raw_input: str = "",
    # 意圖分類
    intent: str = "",
    intent_source: str = "",   # regex_fast | fast_filter | llm_classifier | veto_override
    # 特殊觸發狀態
    veto_fired: bool = False,
    self_aware_triggered: bool = False,
    vision_hit_count: int = 0,
    # 搜圖
    tags_resolved: list[str] | None = None,
    # 回覆
    cot_thought: str = "",
    reply_preview: str = "",
    model_used: str = "",
    # 效能
    latency_ms: int = 0,
    queue_size: int = 0,
    # 任意附加欄位
    **extra: Any,
) -> None:
    """記錄一筆互動事件到 runtime_log.jsonl。

    event 類型清單：
      chat_reply      — CHAT Tool 完成回覆
      image_search    — PIXIV Tool 完成搜圖
      draw            — DRAW Tool 完成（或失敗）
      search_rag      — SEARCH Tool 完成 RAG 回覆
      music_action    — MUSIC Tool 執行動作
      schedule_action — SCHEDULE Tool 執行動作
      stock_query     — STOCK Tool 查詢完成
      weather_query   — WEATHER Tool 查詢完成
      interject       — 插話引擎執行評分（不論是否插話）
      vision_analysis — Vision 圖片辨識完成
      intent_classify — LLM Classifier 完成 Intent 判斷
      veto_fired      — Veto 機制觸發降級
      self_aware      — 自我辨識信號被觸發
      user_debug_report — 使用者對回覆按 ❓ 表情符號
      error           — 任何 exception 發生
    """
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "message_id": message_id,
        "user": user,
        "channel_id": channel_id,
        "raw_input": raw_input[:200] if raw_input else "",
        "intent": intent,
        "intent_source": intent_source,
        "veto_fired": veto_fired,
        "self_aware_triggered": self_aware_triggered,
        "vision_hit_count": vision_hit_count,
        "tags_resolved": tags_resolved or [],
        "cot_thought": cot_thought[:300] if cot_thought else "",
        "reply_preview": reply_preview[:120] if reply_preview else "",
        "model_used": model_used,
        "latency_ms": latency_ms,
        "queue_size": queue_size,
    }
    if extra:
        entry["extra"] = {k: str(v)[:200] for k, v in extra.items()}

    _write_line(_RUNTIME_LOG_PATH, entry)


def log_error(
    *,
    event: str = "error",
    message_id: int = 0,
    task_id: str = "",
    error_type: str = "",
    message: str = "",
    **extra: Any,
) -> None:
    """記錄一筆錯誤/降級事件，同時寫入 runtime_log 與 error_log。"""
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "message_id": message_id,
        "task_id": task_id,
        "error_type": error_type,
        "message": message[:500] if message else "",
    }
    if extra:
        entry["extra"] = {k: str(v)[:200] for k, v in extra.items()}

    # Write to both logs
    _write_line(_RUNTIME_LOG_PATH, entry)
    _write_line(_ERROR_LOG_PATH, entry)


from typing import Any, Callable


def make_timer() -> Callable[[], int]:
    """回傳一個 callable，呼叫時回傳距離建立時的毫秒數。

    使用方式：
        elapsed = make_timer()
        # ... 執行一些工作 ...
        latency_ms = elapsed()
    """
    _start = time.monotonic()

    def _get_ms() -> int:
        return int((time.monotonic() - _start) * 1000)

    return _get_ms
