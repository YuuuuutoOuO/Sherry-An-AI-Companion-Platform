"""
tests/unit/test_retention_worker.py
===================================
針對 handlers.memory.retention_worker 模組之單元測試，驗證過期暫存檔清理與保留週期執行。
"""

from __future__ import annotations

from pathlib import Path
import time

from handlers.memory.retention_worker import (
    DataRetentionWorker,
    RetentionPolicy,
)


def test_clean_temp_directory(tmp_path: Path) -> None:
    # Arrange
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    file_old = temp_dir / "old_audio.wav"
    file_new = temp_dir / "new_audio.wav"

    file_old.write_text("dummy old data", encoding="utf-8")
    file_new.write_text("dummy new data", encoding="utf-8")

    # Set file_old mtime to 48 hours ago
    past_time = time.time() - (48 * 3600)
    import os
    os.utime(file_old, (past_time, past_time))

    worker = DataRetentionWorker(
        base_data_dir=tmp_path,
        policy=RetentionPolicy(temp_files_max_hours=24),
    )

    # Act
    cleaned_count, reclaimed_bytes = worker.clean_temp_directory(temp_dir)

    # Assert
    assert cleaned_count == 1
    assert not file_old.exists()
    assert file_new.exists()


def test_retention_cycle_run(tmp_path: Path) -> None:
    # Arrange
    worker = DataRetentionWorker(base_data_dir=tmp_path)

    # Act
    report = worker.run_retention_cycle()

    # Assert
    assert report.cleaned_files_count == 0
    assert len(report.errors) == 0
