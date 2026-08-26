"""
tests/test_heartbeat.py
========================
測試 Heartbeat 思考腦之 Markdown 解析、預設 Channel ID (1519967448982487201) 帶入與時間判定。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DISCORD_BOT_TOKEN", "dummy_token_for_test")

import settings
from handlers.heartbeat.parser import parse_heartbeat_markdown, HeartbeatTask


def test_heartbeat_parsing():
    """測試解析 heartbeat.md 檔案內容。"""
    heartbeat_path = settings.HEARTBEAT_PATH
    tasks = parse_heartbeat_markdown(heartbeat_path)

    assert len(tasks) >= 3, f"Expected at least 3 tasks, got {len(tasks)}"

    morning_task = next((t for t in tasks if t.title == "每日晨報"), None)
    assert morning_task is not None
    assert morning_task.schedule_time == "08:00"
    assert morning_task.channel_id == 1519967448982487201

    cold_task = next((t for t in tasks if t.title == "冷場偵測"), None)
    assert cold_task is not None
    assert cold_task.task_type == "condition"
    assert cold_task.condition_quiet_minutes == 45
    assert cold_task.channel_id == 1519967448982487201


def test_default_channel_fallback():
    """測試當未手動指定頻道時，自動降級帶入 DEFAULT_HEARTBEAT_CHANNEL_ID。"""
    tmp_path = settings.BASE_DIR / "tests" / "tmp_heartbeat.md"
    tmp_content = """
# 測試排程

## 無頻道測試
- 時間：12:00
- 任務：測試空頻道自動帶入
"""
    tmp_path.write_text(tmp_content, encoding="utf-8")
    try:
        tasks = parse_heartbeat_markdown(tmp_path)
        assert len(tasks) == 1
        assert tasks[0].channel_id == 1519967448982487201
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


if __name__ == "__main__":
    test_heartbeat_parsing()
    print("test_heartbeat_parsing passed!")
    test_default_channel_fallback()
    print("test_default_channel_fallback passed!")
    print("\n[OK] ALL HEARTBEAT TESTS PASSED SUCCESSFULLY!")
