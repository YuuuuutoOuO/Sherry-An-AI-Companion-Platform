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

import contextvars
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trace ID & ContextVar (全域鏈路追蹤機制)
# ---------------------------------------------------------------------------
current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_trace_id", default="")


def get_trace_id() -> str:
    """取得當前協程/執行緒的 Trace ID。

    Returns:
        str: 當前的 Trace ID 字串，若未設定則回傳空字串。
    """
    return current_trace_id.get()


def new_trace_id(prefix: str = "tr") -> str:
    """生成一個新的唯一 Trace ID。

    Args:
        prefix: 前綴字串，預設為 'tr'。可為 'cmd', 'btn', 'chat', 'llm' 等。

    Returns:
        str: 組合後的唯一 Trace ID，如 'cmd-a1b2c3d4'。
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def set_trace_id(tid: str | None = None) -> str:
    """設定當前協程的 Trace ID。

    Args:
        tid: 指定的 Trace ID。若為 None 或空字串，則自動生成一組新 ID。

    Returns:
        str: 設定成功的 Trace ID 字串。
    """
    final_id = tid or new_trace_id()
    current_trace_id.set(final_id)
    return final_id


class TraceIdFilter(logging.Filter):
    """將當前 ContextVar 中的 trace_id 自動注入所有 LogRecord 的過濾器。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """過濾並補充 trace_id 屬性。

        Args:
            record: Python 日誌記錄物件。

        Returns:
            bool: 恆為 True（放行所有記錄）。
        """
        tid = get_trace_id()
        record.trace_id = tid if tid else "-"
        return True

_RUNTIME_LOG_PATH: Path = settings.RUNTIME_LOG_PATH
_ERROR_LOG_PATH: Path = settings.ERROR_LOG_PATH
_MAX_LOG_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB


_WRITE_COUNTS: dict[str, int] = {}
_STAT_CHECK_INTERVAL: int = 100


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_line(path: Path, entry: dict) -> None:
    """Append a single JSON line to a JSONL file. Silently ignores all errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path_key = str(path)
        count = _WRITE_COUNTS.get(path_key, 0) + 1
        _WRITE_COUNTS[path_key] = count

        # Rotate if file exceeds size limit (checked once every _STAT_CHECK_INTERVAL writes)
        if (count == 1 or count % _STAT_CHECK_INTERVAL == 0) and path.exists():
            try:
                if path.stat().st_size > _MAX_LOG_SIZE_BYTES:
                    rotated = path.with_suffix(".jsonl.1")
                    path.replace(rotated)
                    _WRITE_COUNTS[path_key] = 0
            except OSError:
                pass  # If rotation fails, keep writing to same file

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
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
    trace_id: str = "",
    # 輸入
    raw_input: str = "",
    # 意圖分類
    intent: str = "",
    intent_source: str = "",   # regex_fast | fast_filter | llm_classifier | veto_override | command
    # 特殊觸發狀態
    veto_fired: bool = False,
    self_aware_triggered: bool = False,
    vision_hit_count: int = 0,
    # 搜圖
    tags_resolved: list[str] | None = None,
    # 工具
    tools_invoked: list[str] | None = None,
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
      chat_reply           — CHAT / Agent 完成回覆
      command_exec         — Discord Slash / Prefix 指令執行
      component_interaction — Discord 按鈕 / 選單互動
      modal_submit         — Discord 表單提交
      tool_exec            — 工具獨立執行（輸入、輸出與耗時）
      inference_done       — BotBrain LLM 推論任務完成
      image_search         — PIXIV Tool 完成搜圖
      draw                 — DRAW Tool 完成（或失敗）
      search_rag           — SEARCH Tool 完成 RAG 回覆
      music_action         — MUSIC Tool 執行動作
      schedule_action      — SCHEDULE Tool 執行動作
      stock_query          — STOCK Tool 查詢完成
      weather_query        — WEATHER Tool 查詢完成
      interject            — 插話引擎執行評分（不論是否插話）
      vision_analysis      — Vision 圖片辨識完成
      intent_classify      — LLM Classifier 完成 Intent 判斷
      veto_fired           — Veto 機制觸發降級
      self_aware           — 自我辨識信號被觸發
      user_debug_report    — 使用者對回覆按 ❓ 表情符號
      error                — 任何 exception 發生
    """
    actual_tid = trace_id or get_trace_id()
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "trace_id": actual_tid,
        "event": event,
        "message_id": message_id,
        "user": user,
        "channel_id": channel_id,
        "raw_input": raw_input[:500] if raw_input else "",
        "intent": intent,
        "intent_source": intent_source,
        "veto_fired": veto_fired,
        "self_aware_triggered": self_aware_triggered,
        "vision_hit_count": vision_hit_count,
        "tags_resolved": tags_resolved or [],
        "tools_invoked": tools_invoked or [],
        "cot_thought": cot_thought[:1000] if cot_thought else "",
        "reply_preview": reply_preview[:500] if reply_preview else "",
        "model_used": model_used,
        "latency_ms": latency_ms,
        "queue_size": queue_size,
    }
    if extra:
        entry["extra"] = {
            k: (v if isinstance(v, (int, float, bool, dict, list)) else str(v)[:1000])
            for k, v in extra.items()
        }

    _write_line(_RUNTIME_LOG_PATH, entry)


def log_error(
    *,
    event: str = "error",
    message_id: int = 0,
    task_id: str = "",
    trace_id: str = "",
    error_type: str = "",
    message: str = "",
    **extra: Any,
) -> None:
    """記錄一筆錯誤/降級事件，同時寫入 runtime_log 與 error_log。"""
    actual_tid = trace_id or get_trace_id()
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "trace_id": actual_tid,
        "event": event,
        "message_id": message_id,
        "task_id": task_id,
        "error_type": error_type,
        "message": message[:1000] if message else "",
    }
    if extra:
        entry["extra"] = {
            k: (v if isinstance(v, (int, float, bool, dict, list)) else str(v)[:1000])
            for k, v in extra.items()
        }

    # Write to both logs
    _write_line(_RUNTIME_LOG_PATH, entry)
    _write_line(_ERROR_LOG_PATH, entry)


def log_command(
    *,
    command_type: str = "slash",
    command_name: str = "",
    user: str = "",
    user_id: int = 0,
    guild_name: str = "",
    guild_id: int = 0,
    channel_name: str = "",
    channel_id: int = 0,
    options: dict[str, Any] | None = None,
    latency_ms: int = 0,
    status: str = "success",
    error_msg: str = "",
    trace_id: str = "",
    **extra: Any,
) -> None:
    """記錄 Discord 指令與元件互動生命週期事件。

    Args:
        command_type: 指令類型 ('slash', 'prefix', 'button', 'select', 'modal', 'autocomplete')。
        command_name: 指令名稱（如 'vibe', 'market', 'play' 或按鈕 custom_id）。
        user: 使用者顯示名稱與帳號。
        user_id: Discord 使用者 ID。
        guild_name: 伺服器名稱。
        guild_id: 伺服器 ID。
        channel_name: 頻道名稱。
        channel_id: 頻道 ID。
        options: 傳入的參數字典。
        latency_ms: 執行耗時（毫秒）。
        status: 執行狀態 ('success', 'error', 'deferred')。
        error_msg: 若出錯時的錯誤訊息。
        trace_id: 指定的 Trace ID，預設自動自 ContextVar 取得。
        **extra: 任意附加欄位。
    """
    opts_str = json.dumps(options, ensure_ascii=False) if options else ""
    event_name = (
        "command_exec" if command_type in ("slash", "prefix")
        else "component_interaction" if command_type in ("button", "select")
        else "modal_submit" if command_type == "modal"
        else "interaction_event"
    )
    raw_input_desc = f"/{command_name} {opts_str}".strip() if command_type == "slash" else (
        f"!{command_name} {opts_str}".strip() if command_type == "prefix" else f"[{command_type.upper()}] {command_name}"
    )

    combined_extra: dict[str, Any] = {
        "command_type": command_type,
        "command_name": command_name,
        "user_id": user_id,
        "guild_name": guild_name,
        "guild_id": guild_id,
        "channel_name": channel_name,
        "status": status,
    }
    if options:
        combined_extra["options"] = options
    if error_msg:
        combined_extra["error_msg"] = error_msg
    if extra:
        combined_extra.update(extra)

    log_event(
        event=event_name,
        user=user,
        channel_id=channel_id,
        trace_id=trace_id,
        raw_input=raw_input_desc,
        intent=f"{command_type.upper()}: {command_name}",
        intent_source="discord_interaction",
        latency_ms=latency_ms,
        reply_preview=f"Status: {status}" + (f" | Err: {error_msg}" if error_msg else ""),
        **combined_extra,
    )


def log_tool_exec(
    *,
    tool_name: str = "",
    tool_input: str = "",
    observation: str = "",
    latency_ms: int = 0,
    status: str = "success",
    user: str = "",
    channel_id: int = 0,
    message_id: int = 0,
    error_msg: str = "",
    trace_id: str = "",
    **extra: Any,
) -> None:
    """記錄 Agent 內部工具調用事件（Tool Execution）。

    Args:
        tool_name: 工具名稱（如 'SEARCH', 'PIXIV', 'DRAW', 'MUSIC', 'STOCK' 等）。
        tool_input: 傳入工具之參數內容。
        observation: 工具回傳之 Observation 觀測數據。
        latency_ms: 工具執行耗時（毫秒）。
        status: 執行狀態 ('success', 'error')。
        user: 關聯使用者名稱。
        channel_id: 頻道 ID。
        message_id: 訊息 ID。
        error_msg: 錯誤訊息（若有）。
        trace_id: Trace ID。
        **extra: 任意附加欄位。
    """
    combined_extra: dict[str, Any] = {
        "tool_name": tool_name,
        "status": status,
        "observation": observation[:500] if observation else "",
    }
    if error_msg:
        combined_extra["error_msg"] = error_msg
    if extra:
        combined_extra.update(extra)

    log_event(
        event="tool_exec",
        message_id=message_id,
        user=user,
        channel_id=channel_id,
        trace_id=trace_id,
        raw_input=f"[{tool_name}] {tool_input}"[:300],
        intent=f"TOOL: {tool_name}",
        intent_source="agent_dispatch",
        cot_thought=f"Observation: {observation[:300]}" if observation else "",
        reply_preview=observation[:120] if observation else "",
        latency_ms=latency_ms,
        tools_invoked=[tool_name] if tool_name else [],
        **combined_extra,
    )


def log_inference(
    *,
    task_id: str = "",
    model: str = "",
    prompt_length: int = 0,
    completion_length: int = 0,
    queue_latency_ms: int = 0,
    inference_latency_ms: int = 0,
    status: str = "success",
    prompt_preview: str = "",
    completion_preview: str = "",
    error_msg: str = "",
    trace_id: str = "",
    **extra: Any,
) -> None:
    """記錄 BotBrain LLM 推論任務生命週期事件。

    Args:
        task_id: 任務 ID。
        model: 使用之模型名稱。
        prompt_length: 輸入 Prompt 字數。
        completion_length: 生成回覆字數。
        queue_latency_ms: 佇列排隊等候耗時（毫秒）。
        inference_latency_ms: 實際推論耗時（毫秒）。
        status: 狀態 ('success', 'retry', 'error')。
        prompt_preview: Prompt 前段摘要。
        completion_preview: 生成內容摘要。
        error_msg: 錯誤訊息（若有）。
        trace_id: Trace ID。
        **extra: 任意附加欄位。
    """
    combined_extra: dict[str, Any] = {
        "task_id": task_id,
        "prompt_length": prompt_length,
        "completion_length": completion_length,
        "queue_latency_ms": queue_latency_ms,
        "status": status,
    }
    if error_msg:
        combined_extra["error_msg"] = error_msg
    if extra:
        combined_extra.update(extra)

    log_event(
        event="inference_done",
        trace_id=trace_id,
        raw_input=prompt_preview[:200] if prompt_preview else f"Task: {task_id}",
        intent=f"INFERENCE: {model}",
        intent_source="bot_brain",
        reply_preview=completion_preview[:120] if completion_preview else "",
        model_used=model,
        latency_ms=inference_latency_ms,
        **combined_extra,
    )


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

