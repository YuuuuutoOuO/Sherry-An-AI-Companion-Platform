"""
handlers/heartbeat/parser.py
=============================
解析 heartbeat.md 設定檔之排程任務資料型態與解析器。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeartbeatTask:
    """心跳排程單一任務定義。"""

    title: str
    task_type: str  # "time" 或 "condition"
    schedule_time: str  # 例如 "08:00" 或 "週五 17:30"
    condition_quiet_minutes: int  # 冷場發言門檻分鐘數（例如 45）
    channel_id: int  # Discord 頻道 ID
    prompt: str  # 任務指令描述


def parse_heartbeat_markdown(file_path: Path) -> list[HeartbeatTask]:
    """解析 heartbeat.md 文字內容並轉化成 HeartbeatTask 結構清單。"""
    if not file_path.exists():
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            default_content = (
                "# Sherry Heartbeat Schedule\n\n"
                "## 早上主動問候\n"
                "- schedule_time: 08:30\n"
                "- channel_id: 1519967448982487201\n"
                "- prompt: 用橘雪莉元氣名偵探口吻跟大家說聲早安！\n"
            )
            file_path.write_text(default_content, encoding="utf-8")
        except Exception:
            return []

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("[HEARTBEAT] 讀取 %s 失敗: %s", file_path, exc)
        return []

    tasks: list[HeartbeatTask] = []
    # 依 `## ` 標題分割各區段
    sections = re.split(r"\n(?=##\s+)", content)

    for sec in sections:
        sec = sec.strip()
        if not sec.startswith("## "):
            continue

        lines = sec.splitlines()
        title = lines[0].replace("## ", "").strip()

        schedule_time = ""
        condition_quiet_minutes = 0
        channel_id = getattr(settings, "DEFAULT_HEARTBEAT_CHANNEL_ID", 1519967448982487201)
        prompt = ""
        task_type = "time"

        for line in lines[1:]:
            line_str = line.strip()
            if not line_str.startswith("- "):
                continue

            kv = line_str[2:].split("：", 1)
            if len(kv) != 2:
                kv = line_str[2:].split(":", 1)
            if len(kv) != 2:
                continue

            key = kv[0].strip()
            val = kv[1].strip()

            if key in ("時間", "time"):
                schedule_time = val
                task_type = "time"
            elif key in ("條件", "condition"):
                task_type = "condition"
                quiet_match = re.search(r"(\d+)\s*分鐘", val)
                if quiet_match:
                    condition_quiet_minutes = int(quiet_match.group(1))
            elif key in ("頻道", "channel", "channel_id"):
                digits = re.findall(r"\d+", val)
                if digits:
                    channel_id = int(digits[0])
            elif key in ("任務", "task", "prompt"):
                prompt = val

        if prompt:
            tasks.append(
                HeartbeatTask(
                    title=title,
                    task_type=task_type,
                    schedule_time=schedule_time,
                    condition_quiet_minutes=condition_quiet_minutes,
                    channel_id=channel_id,
                    prompt=prompt,
                )
            )

    return tasks
