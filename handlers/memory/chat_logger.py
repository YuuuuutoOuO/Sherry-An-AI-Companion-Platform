"""
handlers/memory/chat_logger.py
================================
每日聊天記錄工具。

每一則群組訊息都追加寫入 data/chat_logs/YYYY-MM-DD.jsonl（台灣時間日期）。
凌晨整合結束後會在同目錄建立 YYYY-MM-DD.jsonl.processed 標記，避免重複整合。

格式（每行一個 JSON）：
  {"ts": "2026-07-16T23:15:00+08:00", "user_id": "123", "display_name": "Yuuto", "content": "..."}
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import discord
import settings

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))   # 台灣時間 UTC+8


def _today_str() -> str:
    """回傳台灣時間的今日日期字串 YYYY-MM-DD。"""
    return datetime.now(_TZ_TW).strftime("%Y-%m-%d")


def _log_path(date_str: str) -> Path:
    return settings.CHAT_LOG_DIR / f"{date_str}.jsonl"


def _processed_path(date_str: str) -> Path:
    return settings.CHAT_LOG_DIR / f"{date_str}.jsonl.processed"


import threading
import time

_BUFFER_LOCK = threading.Lock()
_LOG_BUFFER: list[tuple[str, dict]] = []
_BUFFER_MAX_SIZE: int = 10


def flush_chat_logs() -> None:
    """將緩衝區內所有未落盤的聊天記錄批次寫入對應日期的 JSONL 檔案中。"""
    with _BUFFER_LOCK:
        if not _LOG_BUFFER:
            return
        to_flush = list(_LOG_BUFFER)
        _LOG_BUFFER.clear()

    # 依日期分組（處理跨午夜的訊息）
    grouped: dict[str, list[str]] = {}
    for date_str, entry in to_flush:
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        grouped.setdefault(date_str, []).append(line)

    try:
        settings.CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        for date_str, lines in grouped.items():
            path = _log_path(date_str)
            with path.open("a", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception as exc:
        logger.debug("chat_logger flush: 寫入失敗: %s", exc)


def append_chat_log(message: discord.Message) -> None:
    """
    把一則訊息加入記憶體緩衝區；若滿 _BUFFER_MAX_SIZE 筆則批次落盤。
    靜默處理所有錯誤，絕不影響主流程。
    """
    try:
        content = message.clean_content.strip()
        if not content:
            return

        # 把 @mention 替換為 [某群友]，與記憶萃取的前處理保持一致
        content_safe = re.sub(r"@\S+", "[某群友]", content)

        entry = {
            "ts": datetime.now(_TZ_TW).isoformat(),
            "user_id": str(message.author.id),
            "display_name": message.author.display_name,
            "content": content_safe,
        }
        date_str = _today_str()

        should_flush = False
        with _BUFFER_LOCK:
            _LOG_BUFFER.append((date_str, entry))
            if len(_LOG_BUFFER) >= _BUFFER_MAX_SIZE:
                should_flush = True

        if should_flush:
            flush_chat_logs()
    except Exception as exc:
        logger.debug("chat_logger: 加入緩衝失敗: %s", exc)


async def async_append_chat_log(message: discord.Message) -> None:
    """非同步版本的追加寫入，於需要 Flush 時在 ThreadPool 中執行，避免阻塞事件迴圈。"""
    try:
        content = message.clean_content.strip()
        if not content:
            return

        content_safe = re.sub(r"@\S+", "[某群友]", content)
        entry = {
            "ts": datetime.now(_TZ_TW).isoformat(),
            "user_id": str(message.author.id),
            "display_name": message.author.display_name,
            "content": content_safe,
        }
        date_str = _today_str()

        should_flush = False
        with _BUFFER_LOCK:
            _LOG_BUFFER.append((date_str, entry))
            if len(_LOG_BUFFER) >= _BUFFER_MAX_SIZE:
                should_flush = True

        if should_flush:
            import asyncio
            await asyncio.to_thread(flush_chat_logs)
    except Exception as exc:
        logger.debug("chat_logger async: 寫入失敗: %s", exc)


# ---------------------------------------------------------------------------
# 讀取
# ---------------------------------------------------------------------------

def read_chat_log(date_str: str) -> list[dict]:
    """
    讀取指定日期的所有訊息記錄。
    回傳 list[dict]，順序為寫入順序（時間正序）。
    """
    flush_chat_logs()
    path = _log_path(date_str)
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as exc:
        logger.warning("chat_logger: 讀取 %s 失敗: %s", date_str, exc)
    return records


# ---------------------------------------------------------------------------
# 處理標記
# ---------------------------------------------------------------------------

def is_date_processed(date_str: str) -> bool:
    """確認該日期是否已整合過。"""
    return _processed_path(date_str).exists()


def mark_date_processed(date_str: str, stats: dict | None = None) -> None:
    """
    建立 .processed 標記檔，避免重複整合。
    stats 可選，用來記錄整合結果摘要（使用者數、facts 數等）。
    """
    try:
        settings.CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        info: dict = {
            "processed_at": datetime.now(_TZ_TW).isoformat(),
            "date": date_str,
        }
        if stats:
            info.update(stats)
        path = _processed_path(date_str)
        with path.open("w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("chat_logger: 標記失敗: %s", exc)


def get_yesterday_str() -> str:
    """回傳台灣時間的昨日日期字串 YYYY-MM-DD。"""
    yesterday = datetime.now(_TZ_TW) - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")
