"""
handlers/memory/world_memory.py
===============================
世界層級記憶系統（World Memory）。

使用 JSONL append-only 模式記錄世界事件（與 chat_logger.py 相同架構）。
每日凌晨由 NightlyDigest 觸發壓縮為摘要存入 world_memory.json。

用途：
- D&D 世界歷史記錄
- 跨群組/跨頻道的全域事件追蹤
- 供 persona.py 注入「世界近期大事」到 LLM prompt

資料結構：
  原始 log：data/world_history/YYYY-MM-DD.jsonl
    {"ts": "2026-07-16T14:30:00+08:00", "event_type": "exploration",
     "location": "晨風村", "summary": "...", "participants": [...], "importance": 3}

  壓縮摘要：data/world_memory.json
    [{"timestamp": "...", "summary": "...", "importance": 3, "event_id": "..."}, ...]
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import settings

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))
_WORLD_LOG_DIR: Path = settings.WORLD_LOG_DIR
_WORLD_MEMORY_PATH: Path = settings.WORLD_MEMORY_PATH


def _today_str() -> str:
    return datetime.now(_TZ_TW).strftime("%Y-%m-%d")


def _log_path(date_str: str) -> Path:
    return _WORLD_LOG_DIR / f"{date_str}.jsonl"


# ---------------------------------------------------------------------------
# 寫入事件
# ---------------------------------------------------------------------------

def log_world_event(
    event_type: str,
    summary: str,
    location: str = "",
    participants: list[str] | None = None,
    importance: int = 3,
) -> str | None:
    """
    記錄一筆世界事件到今日的 JSONL 檔案。
    importance: 1(瑣碎)~5(改變世界格局)

    回傳 event_id，失敗則回傳 None。
    """
    try:
        _WORLD_LOG_DIR.mkdir(parents=True, exist_ok=True)
        event_id = str(uuid.uuid4())[:8]
        entry = {
            "event_id": event_id,
            "ts": datetime.now(_TZ_TW).isoformat(),
            "event_type": event_type,
            "location": location,
            "summary": summary,
            "participants": participants or [],
            "importance": max(1, min(5, importance)),
        }
        path = _log_path(_today_str())
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return event_id
    except Exception as exc:
        logger.warning("world_memory: 寫入事件失敗: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 讀取事件
# ---------------------------------------------------------------------------

def read_world_events(date_str: str) -> list[dict]:
    """讀取指定日期的所有世界事件。"""
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
        logger.warning("world_memory: 讀取 %s 失敗: %s", date_str, exc)
    return records


def read_recent_events(days: int = 1) -> list[dict]:
    """讀取最近 N 天的所有世界事件。"""
    events: list[dict] = []
    for i in range(days):
        date_str = (datetime.now(_TZ_TW) - timedelta(days=i)).strftime("%Y-%m-%d")
        events.extend(read_world_events(date_str))
    return events


import time

_WORLD_MEMORY_CACHE_TTL: float = 60.0
_WORLD_MEMORY_SNIPPET_CACHE: dict[tuple[int, int], tuple[float, str]] = {}


def clear_world_memory_cache() -> None:
    """清除世界記憶的記憶體快取。"""
    _WORLD_MEMORY_SNIPPET_CACHE.clear()


def get_world_memory_snippet(days: int = 1, min_importance: int = 3) -> str:
    """
    取得可注入 LLM prompt 的世界近期事件摘要文字（具備 60 秒 TTL 記憶體快取）。
    優先讀取壓縮檔 world_memory.json，若不存在則從 raw log 即時產生。
    """
    now = time.time()
    cache_key = (days, min_importance)
    if cache_key in _WORLD_MEMORY_SNIPPET_CACHE:
        cached_ts, cached_snippet = _WORLD_MEMORY_SNIPPET_CACHE[cache_key]
        if now - cached_ts < _WORLD_MEMORY_CACHE_TTL:
            return cached_snippet

    events = _load_compressed_memory(days, min_importance)
    if not events:
        events = [
            e for e in read_recent_events(days)
            if e.get("importance", 3) >= min_importance
        ]

    if not events:
        _WORLD_MEMORY_SNIPPET_CACHE[cache_key] = (now, "")
        return ""

    lines = ["🌍【世界近期大事】"]
    for e in sorted(events, key=lambda x: x.get("ts", ""), reverse=True)[:10]:
        ts = e.get("ts", "")[:16].replace("T", " ")
        loc = f" [{e['location']}]" if e.get("location") else ""
        lines.append(f"- {ts}{loc} {e['summary']}")

    result = "\n".join(lines)
    _WORLD_MEMORY_SNIPPET_CACHE[cache_key] = (now, result)
    return result


def _load_compressed_memory(days: int, min_importance: int) -> list[dict]:
    """讀取壓縮後的世界記憶檔案。"""
    if not _WORLD_MEMORY_PATH.exists():
        return []
    try:
        with _WORLD_MEMORY_PATH.open("r", encoding="utf-8") as f:
            data: Any = json.load(f)
        if not isinstance(data, list):
            return []
        cutoff = datetime.now(_TZ_TW) - timedelta(days=days)
        return [
            e for e in data
            if e.get("importance", 3) >= min_importance
            and datetime.fromisoformat(e.get("ts", "")) > cutoff
        ]
    except Exception as exc:
        logger.warning("world_memory: 讀取壓縮記憶失敗: %s", exc)
        return []


def compress_daily_events(date_str: str) -> int:
    """
    將指定日期的 raw log 壓縮為摘要並寫入 world_memory.json。
    回傳壓縮後新增的事件數。
    """
    records = read_world_events(date_str)
    if not records:
        return 0

    existing: list[dict] = []
    if _WORLD_MEMORY_PATH.exists():
        try:
            with _WORLD_MEMORY_PATH.open("r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing_ids = {e.get("event_id", "") for e in existing}
    added = 0
    for rec in records:
        if rec.get("event_id") in existing_ids:
            continue
        existing.append({
            "event_id": rec.get("event_id", ""),
            "ts": rec.get("ts", ""),
            "event_type": rec.get("event_type", ""),
            "location": rec.get("location", ""),
            "summary": rec.get("summary", ""),
            "importance": rec.get("importance", 3),
            "participants": rec.get("participants", []),
        })
        existing_ids.add(rec.get("event_id", ""))
        added += 1

    # 保留最近 90 天
    cutoff = datetime.now(_TZ_TW) - timedelta(days=90)
    existing = [
        e for e in existing
        if datetime.fromisoformat(e.get("ts", "")) > cutoff
    ]

    try:
        _WORLD_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _WORLD_MEMORY_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        tmp.replace(_WORLD_MEMORY_PATH)
        clear_world_memory_cache()
    except Exception as exc:
        logger.error("world_memory: 寫入壓縮記憶失敗: %s", exc)

    return added


def cleanup_old_logs(days: int = 30) -> int:
    """清理超過指定天數的原始 log 檔案。回傳刪除的檔案數。"""
    removed = 0
    if not _WORLD_LOG_DIR.exists():
        return 0
    cutoff = datetime.now(_TZ_TW) - timedelta(days=days)
    for f in _WORLD_LOG_DIR.glob("*.jsonl"):
        try:
            file_date = datetime.strptime(f.stem, "%Y-%m-%d").replace(tzinfo=_TZ_TW)
            if file_date < cutoff:
                f.unlink()
                removed += 1
        except ValueError:
            pass
    return removed