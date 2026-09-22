"""
tests/unit/test_memory_archive.py
=================================
世界記憶長期歸檔（每週/每月 Markdown）單元測試。
涵蓋：非歸檔日跳過、週歸檔建立、月歸檔合併、冪等跳過、舊週檔清理。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import settings
from handlers.memory import memory_archive as arc
from handlers.memory import world_memory as wm


def _setup(tmp_path: Path, monkeypatch) -> Path:
    wm._WORLD_DB_PATH = tmp_path / "test_world.db"
    wm._DB_INITIALIZED = False
    wm.clear_world_memory_cache()
    wm._init_db()
    archive_dir = tmp_path / "data" / "memory" / "world"
    monkeypatch.setattr(settings, "WORLD_ARCHIVE_DIR", archive_dir)
    return archive_dir


def _log_event(date_str: str, summary: str, server_id: str, importance: int = 3, participants=None) -> None:
    wm.log_world_event(
        "CHAT",
        summary,
        participants=participants or [],
        importance=importance,
        server_id=server_id,
        date_str=date_str,
    )


def test_archive_finished_week_skips_non_trigger_day(tmp_path, monkeypatch) -> None:
    """非歸檔日（缺省週日）呼叫應回傳 None 且不產檔。"""
    _setup(tmp_path, monkeypatch)
    # 2026-09-07 是週一（weekday 0），不等於預設 6
    result = arc.archive_finished_week("2026-09-07")
    assert result is None
    assert not list(arc.weekly_dir().glob("*.md"))


def test_archive_creates_weekly_and_monthly(tmp_path, monkeypatch) -> None:
    """週日（2026-09-06）歸檔整週：事件、統計、月歸檔與 meta 均正確。"""
    _setup(tmp_path, monkeypatch)

    # 該週（08-31 ~ 09-06）跨兩台伺服器的事件
    _log_event("2026-08-31", "群友討論楓之谷新活動", "guild_A", importance=4, participants=["Yuuto"])
    _log_event("2026-09-02", "專案程式除錯進入最後階段", "guild_B", importance=4, participants=["Alice"])
    _log_event("2026-09-06", "週日深夜夜話大會串", "guild_A", importance=3, participants=["Yuuto", "雪莉"])

    result = arc.archive_finished_week("2026-09-06")

    assert result is not None
    assert result.name.startswith("2026-08-31_")
    assert result.name.endswith(".md")

    content = result.read_text(encoding="utf-8")
    assert "楓之谷" in content
    assert "程式除錯" in content
    assert "夜話大會串" in content
    assert "⭐4" in content
    assert "⭐3" in content
    assert "Yuuto" in content
    assert "guild_A" in content and "guild_B" in content

    meta = arc._parse_meta(result)
    assert meta["servers"] == 2
    assert meta["events"] == 3

    # 週一起始日 08-31 屬八月 → 月歸檔 2026-08
    monthly = arc.monthly_path(date(2026, 8, 31))
    assert monthly.exists()
    monthly_content = monthly.read_text(encoding="utf-8")
    assert "世界記憶月歸檔　2026-08" in monthly_content
    assert result.name in monthly_content


def test_archive_is_idempotent(tmp_path, monkeypatch) -> None:
    """同一週重複歸檔應跳過（回傳 None），不重複覆寫。"""
    _setup(tmp_path, monkeypatch)
    _log_event("2026-09-06", "週日事件", "guild_A", importance=3)

    first = arc.archive_finished_week("2026-09-06")
    assert first is not None
    original = first.read_text(encoding="utf-8")

    second = arc.archive_finished_week("2026-09-06")
    assert second is None
    assert first.read_text(encoding="utf-8") == original


def test_archive_manual_week_without_weekday_check(tmp_path, monkeypatch) -> None:
    """archive_week 可在任何日子手動歸檔指定週。"""
    _setup(tmp_path, monkeypatch)
    _log_event("2026-08-31", "週一手動歸檔事件", "guild_Z", importance=5, participants=["Tester"])

    result = arc.archive_week(date(2026, 9, 6))  # 不以週日作為「今日」也照常歸檔
    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "週一手動歸檔事件" in content
    assert "⭐5" in content


def test_archive_purges_old_weekly(tmp_path, monkeypatch) -> None:
    """超過 WORLD_ARCHIVE_KEEP_WEEKS 的舊週檔應在歸檔時被清理。"""
    _setup(tmp_path, monkeypatch)

    weekly_dir = arc.weekly_dir()
    old_file = weekly_dir / "2025-01-06_2025-W02.md"
    old_file.write_text(
        "<!-- world-week; servers=1; events=2; participants=1 -->\n# 很舊的週\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(settings, "WORLD_ARCHIVE_KEEP_WEEKS", 20)
    _log_event("2026-09-06", "新週事件", "guild_A", importance=3)

    result = arc.archive_finished_week("2026-09-06")
    assert result is not None

    assert not old_file.exists()  # 舊檔被清掉
    assert result.exists()        # 新週檔仍在