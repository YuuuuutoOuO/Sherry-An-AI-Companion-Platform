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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

import settings
from core.db import db_transaction
from core.sys_inspector import notify_failure_sync, notify_slow_response_sync

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))  # 台灣時間 UTC+8

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

_CHAT_DB_PATH: Path = settings.DATA_DIR / "chat_history.db"


def _init_events_db() -> None:
    """初始化 chat_history.db 中的 runtime_events 與 error_events 資料表。"""
    try:
        with db_transaction(_CHAT_DB_PATH) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runtime_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    trace_id TEXT,
                    event TEXT NOT NULL,
                    user TEXT,
                    channel_id TEXT,
                    latency_ms INTEGER DEFAULT 0,
                    data_json TEXT NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_runtime_time ON runtime_events(timestamp DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_runtime_event ON runtime_events(event);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_runtime_trace ON runtime_events(trace_id);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS error_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    trace_id TEXT,
                    event TEXT NOT NULL,
                    error_type TEXT,
                    message TEXT,
                    data_json TEXT NOT NULL
                )
            """)
    except Exception as e:
        logger.debug("observability: 初始化 SQLite 資料表失敗: %s", e)


_DB_INITIALIZED = False


# ---------------------------------------------------------------------------
# 背景非同步寫入緩衝
# ---------------------------------------------------------------------------
# 說明：log_event/log_error 在回覆熱路徑被大量同步呼叫，若每次都直接做
#「開連線→INSERT→commit→close + JSONL append」會阻塞事件迴圈。
# 故透過記憶體緩衝 + 背景 daemon 執行緒批次落盤，讓熱路徑寫入近乎 O(1) 非阻塞，
# 同時保持 log_event 等既有同步 API 不變。
import threading
import queue as _pyqueue

_WRITE_QUEUE: "_pyqueue.Queue[tuple[Path, dict] | None]" = _pyqueue.Queue()
_WRITE_THREAD_STARTED = False


def _flush_batch(batch: list[tuple[Path, dict]]) -> None:
    """實際批次落盤（SQLite + JSONL），於背景執行緒中單一交易執行。"""
    if not batch:
        return
    global _DB_INITIALIZED
    try:
        if not _DB_INITIALIZED:
            _init_events_db()
            _DB_INITIALIZED = True

        runtime_rows = []
        error_rows = []
        files_map: dict[Path, list[str]] = {}

        for path, entry in batch:
            ts = str(entry.get("ts", ""))
            tid = str(entry.get("trace_id", ""))
            event = str(entry.get("event", "unknown"))
            data_json = json.dumps(entry, ensure_ascii=False)

            if path.name.startswith("error"):
                error_rows.append((
                    ts,
                    tid,
                    event,
                    str(entry.get("error_type", "")),
                    str(entry.get("message", "")),
                    data_json,
                ))
            else:
                latency = int(entry.get("latency_ms", 0))
                user = str(entry.get("user", ""))
                ch_id = str(entry.get("channel_id", ""))
                runtime_rows.append((ts, tid, event, user, ch_id, latency, data_json))

            files_map.setdefault(path, []).append(data_json)

        # 1. 單一 Transaction 批次寫入 SQLite
        with db_transaction(_CHAT_DB_PATH) as cur:
            if error_rows:
                cur.executemany("""
                    INSERT INTO error_events (timestamp, trace_id, event, error_type, message, data_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, error_rows)
            if runtime_rows:
                cur.executemany("""
                    INSERT INTO runtime_events (timestamp, trace_id, event, user, channel_id, latency_ms, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, runtime_rows)

        # 2. 批次寫入 JSONL
        for target_path, lines in files_map.items():
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with target_path.open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            except Exception as json_err:
                logger.debug("observability: 批次寫入 JSONL 失敗: %s", json_err)

    except Exception as exc:
        logger.debug("observability: 批次寫入 SQLite 失敗: %s", exc)


def _writer_worker() -> None:
    """背景執行緒：持續取出事件並批次寫入 SQLite 與 JSONL。"""
    batch: list[tuple[Path, dict]] = []
    while True:
        item = _WRITE_QUEUE.get()
        try:
            if item is None:
                if batch:
                    _flush_batch(batch)
                break
            batch.append(item)

            # 累積最多 50 筆或排空當前隊列
            while len(batch) < 50:
                try:
                    next_item = _WRITE_QUEUE.get_nowait()
                    if next_item is None:
                        break
                    batch.append(next_item)
                except _pyqueue.Empty:
                    break

            _flush_batch(batch)
        except Exception:
            logger.debug("observability: 背景寫入異常", exc_info=True)
        finally:
            for _ in range(len(batch)):
                _WRITE_QUEUE.task_done()
            batch.clear()


def _ensure_writer_thread() -> None:
    """確保背景寫入執行緒已啟動（daemon，隨行程結束）。"""
    global _WRITE_THREAD_STARTED
    if _WRITE_THREAD_STARTED:
        return
    _WRITE_THREAD_STARTED = True
    t = threading.Thread(target=_writer_worker, name="observability-writer", daemon=True)
    t.start()


def _write_line(path: Path, entry: dict) -> None:
    """將事件推入背景佇列，非阻塞。由 _writer_worker 執行緒實際批次落盤。"""
    try:
        _ensure_writer_thread()
        _WRITE_QUEUE.put_nowait((path, entry))
    except Exception:
        # 佇列滿（不應發生）或執行緒未就緒時，改為同步兜底，避免丟失事件
        try:
            _flush_batch([(path, entry)])
        except Exception:
            logger.debug("observability: 同步兜底寫入失敗", exc_info=True)


def flush_observability_logs() -> None:
    """等待背景寫入佇列清空（供測試與優雅關機使用）。"""
    if _WRITE_THREAD_STARTED:
        _WRITE_QUEUE.join()


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
    # 效能與細粒度 APM 指標 (V1.9 Prefill / KV Cache / Latency Breakdown)
    latency_ms: int = 0,
    queue_size: int = 0,
    prompt_tokens_cached: int = 0,
    prompt_tokens_uncached: int = 0,
    decode_tokens: int = 0,
    ttft_ms: int = 0,
    prefill_latency_ms: int = 0,
    decode_latency_ms: int = 0,
    cache_hit_ratio: float = 0.0,
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
        "ts": datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S"),
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
        "prompt_tokens_cached": prompt_tokens_cached,
        "prompt_tokens_uncached": prompt_tokens_uncached,
        "decode_tokens": decode_tokens,
        "ttft_ms": ttft_ms,
        "prefill_latency_ms": prefill_latency_ms,
        "decode_latency_ms": decode_latency_ms,
        "cache_hit_ratio": round(cache_hit_ratio, 4),
    }
    if extra:
        entry["extra"] = {
            k: (v if isinstance(v, (int, float, bool, dict, list)) else str(v)[:1000])
            for k, v in extra.items()
        }

    _write_line(_RUNTIME_LOG_PATH, entry)

    # 自動向 component_metrics 記錄組件與功能度量
    try:
        from core.sys_inspector.metrics import component_metrics
        lat_f = float(max(0, latency_ms))

        # 計算/估算 Token
        p_tokens = len(raw_input) if raw_input else 0
        c_tokens = (len(reply_preview) if reply_preview else 0) + (len(cot_thought) if cot_thought else 0)
        if extra:
            if "prompt_length" in extra and isinstance(extra["prompt_length"], (int, float)):
                p_tokens = int(extra["prompt_length"])
            elif "prompt_tokens" in extra and isinstance(extra["prompt_tokens"], (int, float)):
                p_tokens = int(extra["prompt_tokens"])
            if "completion_length" in extra and isinstance(extra["completion_length"], (int, float)):
                c_tokens = int(extra["completion_length"])
            elif "completion_tokens" in extra and isinstance(extra["completion_tokens"], (int, float)):
                c_tokens = int(extra["completion_tokens"])

        if event == "chat_reply":
            component_metrics.record_execution(
                "CHAT",
                latency_ms=lat_f,
                success=True,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                trace_id=actual_tid,
            )
            component_metrics.record_execution(
                "BOT_BRAIN",
                latency_ms=lat_f,
                success=True,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                trace_id=actual_tid,
            )
        elif event == "draw":
            component_metrics.record_execution("DRAW", latency_ms=lat_f, success=True, trace_id=actual_tid)
        elif event == "search_rag":
            component_metrics.record_execution("SEARCH", latency_ms=lat_f, success=True, trace_id=actual_tid, prompt_tokens=p_tokens, completion_tokens=c_tokens)
        elif event == "stock_query":
            component_metrics.record_execution("STOCK", latency_ms=lat_f, success=True, trace_id=actual_tid)
        elif event == "image_search":
            component_metrics.record_execution("PIXIV", latency_ms=lat_f, success=True, trace_id=actual_tid)
        elif event == "music_action":
            component_metrics.record_execution("MUSIC", latency_ms=lat_f, success=True, trace_id=actual_tid)
        elif event == "schedule_action":
            component_metrics.record_execution("SCHEDULE", latency_ms=lat_f, success=True, trace_id=actual_tid)

        if tools_invoked:
            for t_name in tools_invoked:
                if t_name:
                    component_metrics.record_execution(
                        t_name.upper(),
                        latency_ms=lat_f,
                        success=True,
                        trace_id=actual_tid,
                        prompt_tokens=p_tokens,
                        completion_tokens=c_tokens,
                    )
    except Exception:
        pass

    # 自我監控鉤子：單筆回覆超過 90 秒時發送慢速警報信號
    if latency_ms >= 90000:
        try:
            notify_slow_response_sync(
                latency_ms=latency_ms,
                trace_id=actual_tid,
                channel_id=channel_id,
                user_name=user,
                raw_prompt=raw_input,
            )
        except Exception:
            pass


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
        "ts": datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S"),
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

    # 自我監控鉤子：超過 120 秒失敗、推論中斷或重大連線崩潰發送警報信號
    lat_val = int(extra.get("latency_ms", 0)) if extra else 0
    is_fatal = bool(extra.get("is_fatal", False)) if extra else False
    err_lower = f"{error_type} {message}".lower()
    has_fatal_keyword = any(
        kw in err_lower
        for kw in (
            "timeout", "connectionreset", "cannot connect", "give-up",
            "transferencoding", "connection error", "refused", "unauthorized",
            "brokenpipe", "clientconnectorerror", "inferencegiveup", "fatal"
        )
    )
    if lat_val >= 120000 or is_fatal or has_fatal_keyword:
        try:
            ch_id = int(extra.get("channel_id", 0)) if extra else 0
            u_name = str(extra.get("user", "")) if extra else ""
            raw_p = str(extra.get("raw_input", "")) if extra else ""
            notify_failure_sync(
                latency_ms=lat_val,
                error_msg=f"{error_type}: {message}" if error_type else message,
                trace_id=actual_tid,
                channel_id=ch_id,
                user_name=u_name,
                raw_prompt=raw_p,
                task_id=task_id,
                is_fatal=is_fatal or has_fatal_keyword,
            )
        except Exception:
            pass


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

    try:
        from core.sys_inspector.metrics import component_metrics
        c_name = tool_name.upper() if tool_name else "UNKNOWN"
        p_tok = len(tool_input) if tool_input else 0
        c_tok = len(observation) if observation else 0
        component_metrics.record_execution(
            c_name,
            latency_ms=float(latency_ms),
            success=(status == "success"),
            error_msg=error_msg,
            trace_id=trace_id,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
        )
    except Exception:
        pass


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

    try:
        from core.sys_inspector.metrics import component_metrics
        component_metrics.record_execution(
            "BOT_BRAIN",
            latency_ms=float(inference_latency_ms),
            prompt_tokens=prompt_length,
            completion_tokens=completion_length,
            success=(status == "success"),
            error_msg=error_msg,
            trace_id=trace_id,
        )
    except Exception:
        pass


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

