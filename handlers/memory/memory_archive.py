"""
handlers/memory/memory_archive.py
=================================
世界記憶長期歸檔（每週 / 每月 Markdown 永久儲存，只存不注入）。

動機：
    world_summary 只保留 90 天、world_events 只保留 30 天，
    而 prompt 注入視窗更短。為了不讓「上個月討論過的程式專案」這類
    長週期話題在推理時完全消失，每週日深夜把整週世界事件歸檔為 Markdown，
    並自動合併成月歸檔，存放於 DATA_DIR/memory/world/ ：
        weekly/  {週一日期}_{ISO週}.md      （例：2026-08-31_2026-W36.md）
        monthly/ {YYYY-MM}.md               （例：2026-08.md）
    此層為「長期原始資料庫」，不受 SQLite 清理週期影響；
    日後若要注入 prompt，可直接讀取這些檔案做摘要，不需回溯聊天記錄。
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import settings
from handlers.memory import world_memory as wm

logger = logging.getLogger(__name__)

_META_PATTERN = re.compile(r"<!-- world-week; servers=(\d+); events=(\d+); participants=(\d+) -->")
_WEEK_FILE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})_\d{4}-W\d{1,2}\.md$")


def _archive_dir() -> Path:
    return Path(getattr(settings, "WORLD_ARCHIVE_DIR", settings.DATA_DIR / "memory" / "world"))


def weekly_dir() -> Path:
    """取得週歸檔存放目錄路徑（若不存在則自動遞迴建立）。

    Returns:
        Path: 週歸檔目錄路徑。
    """
    path = _archive_dir() / "weekly"
    path.mkdir(parents=True, exist_ok=True)
    return path


def monthly_dir() -> Path:
    """取得月歸檔存放目錄路徑（若不存在則自動遞迴建立）。

    Returns:
        Path: 月歸檔目錄路徑。
    """
    path = _archive_dir() / "monthly"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _week_start(week_end: date) -> date:
    """回傳該週的週一（ISO 週，week_end 為該週最後一天，預設週日）。"""
    return week_end - timedelta(days=week_end.weekday())


def weekly_path(week_end: date) -> Path:
    """計算指定週結束日對應的週歸檔檔案完整路徑。

    Args:
        week_end: 該週的最後一日（date 物件）。

    Returns:
        Path: 週歸檔 Markdown 檔案路徑（如：2026-08-31_2026-W36.md）。
    """
    ws = _week_start(week_end)
    label = week_end.strftime("%G-W%V")
    return weekly_dir() / f"{ws.isoformat()}_{label}.md"


def monthly_path(anchor: date) -> Path:
    """計算指定基準日期所屬月份之月歸檔檔案完整路徑。

    Args:
        anchor: 該月份內之任一基準日期（date 物件）。

    Returns:
        Path: 月歸檔 Markdown 檔案路徑（如：2026-08.md）。
    """
    return monthly_dir() / f"{anchor.strftime('%Y-%m')}.md"


def _collect_week_events(week_start: date, week_end: date) -> list[dict[str, Any]]:
    """從 world_events（原始表，30 天內必有資料）收集整個週的事件，依時間排序。"""
    events: list[dict[str, Any]] = []
    d = week_start
    while d <= week_end:
        events.extend(wm.read_world_events(d.isoformat(), server_id=""))
        d += timedelta(days=1)
    events.sort(key=lambda e: str(e.get("ts", "")))
    return events


def _build_weekly_md(week_start: date, week_end: date, events: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    participants: set[str] = set()
    for ev in events:
        grouped[str(ev.get("server_id", "") or "(無伺服器)")].append(ev)
        for name in ev.get("participants", []) or []:
            if name:
                participants.add(name)

    servers_n = len(grouped)
    events_n = len(events)
    lines = [
        f"<!-- world-week; servers={servers_n}; events={events_n}; participants={len(participants)} -->",
        f"# 世界記憶週歸檔　{week_start.isoformat()} ~ {week_end.isoformat()}",
        "",
        f"> 由 NightlyDigest 自動歸檔（只儲存、暫不注入 prompt）。共 {events_n} 筆事件 / {servers_n} 個伺服器。",
        "",
        "## 統計",
        f"- 事件總數：{events_n}",
        f"- 伺服器數：{servers_n}",
        f"- 參與者數：{len(participants)}",
        "",
        "## 依伺服器時序",
    ]

    if not events:
        lines.append("（本週無世界事件）")
        return "\n".join(lines)

    for sid, evs in grouped.items():
        lines.append("")
        lines.append(f"### {sid}")
        for ev in evs:
            ts = str(ev.get("ts", ""))[:16].replace("T", " ")
            imp = int(ev.get("importance", 3))
            ppl = ", ".join(ev.get("participants", []) or []) or "－"
            summary = str(ev.get("summary", "")).strip() or "（無內容）"
            lines.append(f"- {ts}　⭐{imp}　[{ppl}]　{summary}")

    return "\n".join(lines)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _merge_monthly(week_start: date) -> Path:
    """掃描該月內所有週檔，合併為月歸檔（每次重算，保持單一權威來源）。"""
    month = week_start.strftime("%Y-%m")
    path = monthly_path(week_start)
    lines = [
        f"# 世界記憶月歸檔　{month}",
        "",
        "> 由每週歸檔自動合併。舊的月歸檔不刪除，作為長期記憶永久庫。",
        "",
        "## 週次清單",
    ]

    week_files: list[Path] = []
    for p in weekly_dir().glob("*.md"):
        m = _WEEK_FILE_PATTERN.match(p.name)
        if not m:
            continue
        ws = date.fromisoformat(m.group(1))
        if ws.strftime("%Y-%m") == month:
            week_files.append(p)

    if not week_files:
        lines.append("（本月尚無週歸檔）")
    else:
        for p in sorted(week_files):
            meta = _parse_meta(p)
            ws = date.fromisoformat(_WEEK_FILE_PATTERN.match(p.name).group(1))  # type: ignore[union-attr]
            lines.append(f"- {p.name}　（{meta['events']} 事件 / {meta['participants']} 參與者，週一起於 {ws.isoformat()}）")

    _write_text(path, "\n".join(lines) + "\n")
    return path


def _parse_meta(path: Path) -> dict[str, int]:
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0] if path.exists() else ""
    except (OSError, UnicodeDecodeError):
        first = ""
    m = _META_PATTERN.match(first.strip())
    if m:
        return {
            "servers": int(m.group(1)),
            "events": int(m.group(2)),
            "participants": int(m.group(3)),
        }
    return {"servers": 0, "events": 0, "participants": 0}


def _purge_old_weekly() -> int:
    """清理超過 WORLD_ARCHIVE_KEEP_WEEKS 週的週檔（依檔名內嵌的週一日期判定）。"""
    keep_weeks = max(0, int(getattr(settings, "WORLD_ARCHIVE_KEEP_WEEKS", 52)))
    today = datetime.now(wm._TZ_TW).date()
    cutoff = _week_start(today) - timedelta(weeks=keep_weeks)
    removed = 0
    for p in weekly_dir().glob("*.md"):
        m = _WEEK_FILE_PATTERN.match(p.name)
        if not m:
            continue
        try:
            ws = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if ws < cutoff:
            try:
                p.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("memory_archive: 清理週檔失敗 %s: %s", p, exc)
    return removed


def archive_week(week_end: date) -> Path | None:
    """把 week_end 所在整個週的世界事件歸檔為 Markdown（可重複呼叫，已存在則跳過）。

    Args:
        week_end: 該週的最後一天（ISO 週日）。週一~週日對應同一週。

    Returns:
        Path | None: 新建立的週檔路徑，若該週已歸檔則回傳 None。
    """
    ws = _week_start(week_end)
    path = weekly_path(week_end)
    if path.exists():
        logger.info("memory_archive: 週檔已存在，跳過：%s", path.name)
        return None

    events = _collect_week_events(ws, week_end)
    _write_text(path, _build_weekly_md(ws, week_end, events))

    month_path = _merge_monthly(ws)
    purged = _purge_old_weekly()
    logger.info(
        "memory_archive: 週歸檔完成 %s（%d 事件），月歸檔 %s 已更新（清理 %d 個舊週檔）",
        path.name, len(events), month_path.name, purged,
    )
    return path


def archive_finished_week(date_str: str) -> Path | None:
    """由 NightlyDigest 呼叫：只在「週結束日（預設週日）」歸檔整週。

    Args:
        date_str: 昨日日期字串（YYYY-MM-DD）。

    Returns:
        Path | None: 落盤的週檔路徑，非歸檔日或已存在則回傳 None。
    """
    target = date.fromisoformat(date_str)
    trigger = int(getattr(settings, "WORLD_ARCHIVE_TRIGGER_WEEKDAY", 6))
    if target.weekday() != trigger:
        return None
    return archive_week(target)


def list_monthly_archives() -> list[Path]:
    """列出所有月歸檔（供日後注入/瀏覽使用）。"""
    return sorted(monthly_dir().glob("*.md"))


def main() -> None:
    """手動觸發歸檔用：python -m handlers.memory.memory_archive 2026-08-30"""
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now(wm._TZ_TW).date().isoformat()
    result = archive_finished_week(arg)
    logger.info("memory_archive: 手動歸檔結果: %s", result)


if __name__ == "__main__":
    main()