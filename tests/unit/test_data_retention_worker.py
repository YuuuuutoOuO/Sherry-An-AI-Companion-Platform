"""
tests/unit/test_data_retention_worker.py
========================================
針對 handlers.memory.retention_worker (DataRetentionWorker) 進行單元測試。

測試涵蓋：
1. 正常清理過期暫存檔案與保留未過期檔案。
2. 暫存目錄不存在、非目錄與唯讀檔案處理。
3. 日誌輪轉（超過天數且大於 50MB 截斷至 10000 行，小於 50MB 或新檔案不截斷）。
4. 非 UTF-8 損毀字元容錯處理 (errors="replace")。
5. 檔案權限錯誤、異常捕捉與 errors 列表記錄。
6. 完整保留週期 (run_retention_cycle) 執行與邊界情況。
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import time
from unittest.mock import patch

from handlers.memory.retention_worker import (
    DataRetentionWorker,
    RetentionCleanReport,
    RetentionPolicy,
)


def test_retention_clean_report_default_factory() -> None:
    """驗證 RetentionCleanReport 的 errors 欄位使用 default_factory=list 獨立實例化。"""
    # Arrange & Act
    report_1 = RetentionCleanReport()
    report_2 = RetentionCleanReport()
    report_1.errors.append("error_1")

    # Assert
    assert report_1.errors == ["error_1"]
    assert report_2.errors == []
    assert report_1.cleaned_files_count == 0
    assert report_1.reclaimed_bytes == 0
    assert report_1.purged_log_entries == 0


def test_clean_temp_directory_normal(tmp_path: Path) -> None:
    """驗證正常清理過期暫存檔案並保留未過期檔案。"""
    # Arrange
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir(parents=True)

    old_file = temp_dir / "old_audio.wav"
    new_file = temp_dir / "new_audio.wav"
    old_file.write_text("old audio content", encoding="utf-8")
    new_file.write_text("new audio content", encoding="utf-8")

    past_time = time.time() - (48 * 3600)
    os.utime(old_file, (past_time, past_time))

    worker = DataRetentionWorker(
        base_data_dir=tmp_path,
        policy=RetentionPolicy(temp_files_max_hours=24),
    )
    errors: list[str] = []

    # Act
    cleaned_count, reclaimed_bytes = worker.clean_temp_directory(temp_dir, errors_log=errors)

    # Assert
    assert cleaned_count == 1
    assert reclaimed_bytes > 0
    assert not old_file.exists()
    assert new_file.exists()
    assert errors == []


def test_clean_temp_directory_nonexistent_or_file(tmp_path: Path) -> None:
    """驗證目錄不存在或目標為一般檔案時優雅返回 (0, 0)。"""
    # Arrange
    nonexistent_dir = tmp_path / "nonexistent"
    dummy_file = tmp_path / "dummy.txt"
    dummy_file.write_text("dummy", encoding="utf-8")
    worker = DataRetentionWorker(base_data_dir=tmp_path)

    # Act
    count_1, bytes_1 = worker.clean_temp_directory(nonexistent_dir)
    count_2, bytes_2 = worker.clean_temp_directory(dummy_file)

    # Assert
    assert count_1 == 0
    assert bytes_1 == 0
    assert count_2 == 0
    assert bytes_2 == 0


def test_clean_temp_directory_readonly_file(tmp_path: Path) -> None:
    """驗證遇到唯讀檔案時重設權限後能順利刪除。"""
    # Arrange
    temp_dir = tmp_path / "temp_readonly"
    temp_dir.mkdir(parents=True)
    readonly_file = temp_dir / "readonly.tmp"
    readonly_file.write_text("readonly content", encoding="utf-8")

    past_time = time.time() - (48 * 3600)
    os.utime(readonly_file, (past_time, past_time))
    os.chmod(readonly_file, stat.S_IREAD)

    worker = DataRetentionWorker(
        base_data_dir=tmp_path,
        policy=RetentionPolicy(temp_files_max_hours=24),
    )
    errors: list[str] = []

    # Act
    cleaned_count, reclaimed_bytes = worker.clean_temp_directory(temp_dir, errors_log=errors)

    # Assert
    assert cleaned_count == 1
    assert reclaimed_bytes > 0
    assert not readonly_file.exists()
    assert errors == []


def test_clean_temp_directory_permission_error_recorded(tmp_path: Path) -> None:
    """驗證檔案無法刪除時記錄錯誤至 errors_log 且不中斷其他流程。"""
    # Arrange
    temp_dir = tmp_path / "temp_err"
    temp_dir.mkdir(parents=True)
    locked_file = temp_dir / "locked.tmp"
    locked_file.write_text("locked content", encoding="utf-8")

    past_time = time.time() - (48 * 3600)
    os.utime(locked_file, (past_time, past_time))

    worker = DataRetentionWorker(
        base_data_dir=tmp_path,
        policy=RetentionPolicy(temp_files_max_hours=24),
    )
    errors: list[str] = []

    # Act
    with patch.object(Path, "unlink", side_effect=PermissionError("拒絕存取")):
        with patch("os.chmod", side_effect=PermissionError("無法修改權限")):
            cleaned_count, reclaimed_bytes = worker.clean_temp_directory(temp_dir, errors_log=errors)

    # Assert
    assert cleaned_count == 0
    assert reclaimed_bytes == 0
    assert len(errors) == 1
    assert "清理暫存檔案失敗" in errors[0]


def test_clean_temp_directory_glob_exception_recorded(tmp_path: Path) -> None:
    """驗證 glob 掃描目錄拋出例外時被捕獲並記錄至 errors_log。"""
    # Arrange
    temp_dir = tmp_path / "temp_glob_err"
    temp_dir.mkdir(parents=True)
    worker = DataRetentionWorker(base_data_dir=tmp_path)
    errors: list[str] = []

    # Act
    with patch.object(Path, "glob", side_effect=OSError("檔案系統損毀")):
        cleaned_count, reclaimed_bytes = worker.clean_temp_directory(temp_dir, errors_log=errors)

    # Assert
    assert cleaned_count == 0
    assert reclaimed_bytes == 0
    assert len(errors) == 1
    assert "存取暫存目錄失敗" in errors[0]


def test_purge_expired_jsonl_logs_over_size_and_age(tmp_path: Path) -> None:
    """驗證日誌檔超過保留天數且大於 50MB 時截斷保留最新的 10000 行。"""
    # Arrange
    log_file = tmp_path / "runtime_log.jsonl"
    total_lines = 12000
    log_content = "\n".join(f'{{"index": {i}, "msg": "log entry {i}"}}' for i in range(total_lines)) + "\n"
    log_file.write_text(log_content, encoding="utf-8")

    past_time = time.time() - (20 * 86400)
    os.utime(log_file, (past_time, past_time))

    worker = DataRetentionWorker(base_data_dir=tmp_path)
    errors: list[str] = []

    # Act
    # 模擬 stat 回傳 st_size 大於 50MB (55MB)
    fake_stat = os.stat_result((
        stat.S_IFREG | 0o644,
        12345,
        1,
        1,
        1000,
        1000,
        55 * 1024 * 1024,
        past_time,
        past_time,
        past_time,
    ))

    with patch.object(Path, "stat", return_value=fake_stat):
        purged_count = worker.purge_expired_jsonl_logs(log_file, max_days=14, errors_log=errors)

    # Assert
    assert purged_count == 2000
    assert errors == []
    remaining_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(remaining_lines) == 10000
    assert '{"index": 2000,' in remaining_lines[0]
    assert '{"index": 11999,' in remaining_lines[-1]


def test_purge_expired_jsonl_logs_under_size_limit_not_truncated(tmp_path: Path) -> None:
    """驗證日誌檔雖超過天數但未大於 50MB 時不進行截斷。"""
    # Arrange
    log_file = tmp_path / "runtime_log.jsonl"
    total_lines = 12000
    log_content = "\n".join(f'{{"index": {i}}}' for i in range(total_lines)) + "\n"
    log_file.write_text(log_content, encoding="utf-8")

    past_time = time.time() - (20 * 86400)
    os.utime(log_file, (past_time, past_time))

    worker = DataRetentionWorker(base_data_dir=tmp_path)
    errors: list[str] = []

    # Act (真實大小未達 50MB)
    purged_count = worker.purge_expired_jsonl_logs(log_file, max_days=14, errors_log=errors)

    # Assert
    assert purged_count == 0
    assert errors == []
    remaining_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(remaining_lines) == total_lines


def test_purge_expired_jsonl_logs_new_file_not_truncated(tmp_path: Path) -> None:
    """驗證日誌檔未超過天數限制時不進行截斷。"""
    # Arrange
    log_file = tmp_path / "runtime_log.jsonl"
    total_lines = 15000
    log_content = "\n".join(f'{{"index": {i}}}' for i in range(total_lines)) + "\n"
    log_file.write_text(log_content, encoding="utf-8")

    worker = DataRetentionWorker(base_data_dir=tmp_path)
    errors: list[str] = []

    # Act
    fake_stat = os.stat_result((
        stat.S_IFREG | 0o644,
        12345,
        1,
        1,
        1000,
        1000,
        60 * 1024 * 1024,
        time.time(),
        time.time(),
        time.time(),
    ))
    with patch.object(Path, "stat", return_value=fake_stat):
        purged_count = worker.purge_expired_jsonl_logs(log_file, max_days=14, errors_log=errors)

    # Assert
    assert purged_count == 0
    assert errors == []


def test_purge_expired_jsonl_logs_corrupted_non_utf8(tmp_path: Path) -> None:
    """驗證遇到非 UTF-8 損毀位元組時使用 replace 模式容錯處理不崩潰。"""
    # Arrange
    log_file = tmp_path / "corrupted_log.jsonl"
    raw_data = (
        b'{"line": 1, "msg": "valid line 1"}\n'
        b'{"line": 2, "msg": "corrupted \xff\xfe\xfa byte"}\n'
        b'{"line": 3, "msg": "valid line 3"}\n'
    )
    log_file.write_bytes(raw_data)

    past_time = time.time() - (20 * 86400)
    os.utime(log_file, (past_time, past_time))

    worker = DataRetentionWorker(base_data_dir=tmp_path)
    errors: list[str] = []

    # Act
    fake_stat = os.stat_result((
        stat.S_IFREG | 0o644,
        12345,
        1,
        1,
        1000,
        1000,
        55 * 1024 * 1024,
        past_time,
        past_time,
        past_time,
    ))
    with patch.object(Path, "stat", return_value=fake_stat):
        purged_count = worker.purge_expired_jsonl_logs(log_file, max_days=14, errors_log=errors)

    # Assert (總行數 3 行小於 10000 行，因此截斷數為 0，且讀取順利完成無錯誤)
    assert purged_count == 0
    assert errors == []
    assert log_file.exists()


def test_purge_expired_jsonl_logs_nonexistent_or_not_file(tmp_path: Path) -> None:
    """驗證不存在或非檔案之日誌路徑安全返回 0。"""
    # Arrange
    nonexistent = tmp_path / "missing.jsonl"
    dir_path = tmp_path / "directory_as_log"
    dir_path.mkdir(parents=True)
    worker = DataRetentionWorker(base_data_dir=tmp_path)

    # Act
    count_1 = worker.purge_expired_jsonl_logs(nonexistent, max_days=14)
    count_2 = worker.purge_expired_jsonl_logs(dir_path, max_days=14)

    # Assert
    assert count_1 == 0
    assert count_2 == 0


def test_purge_expired_jsonl_logs_exception_handling(tmp_path: Path) -> None:
    """驗證日誌讀寫發生 I/O 異常時記錄至 errors_log 且返回 0。"""
    # Arrange
    log_file = tmp_path / "locked_log.jsonl"
    log_file.write_text("line 1\nline 2\n", encoding="utf-8")
    worker = DataRetentionWorker(base_data_dir=tmp_path)
    errors: list[str] = []

    # Act
    with patch("builtins.open", side_effect=OSError("磁碟唯讀")):
        purged_count = worker.purge_expired_jsonl_logs(log_file, max_days=14, errors_log=errors)

    # Assert
    assert purged_count == 0
    assert len(errors) == 1
    assert "輪轉日誌" in errors[0]


def test_run_retention_cycle_full_success(tmp_path: Path) -> None:
    """驗證完整保留週期包含暫存清理與日誌輪轉。"""
    # Arrange
    worker = DataRetentionWorker(
        base_data_dir=tmp_path,
        policy=RetentionPolicy(
            runtime_log_max_days=14,
            error_log_max_days=30,
            temp_files_max_hours=24,
        ),
    )

    # 建立暫存目錄與過期檔案（R7：正確路徑為 chat_logs/temp，報告 §5.5.2）
    temp_dir = tmp_path / "temp"
    cache_dir = tmp_path / "cache"
    chat_temp = tmp_path / "chat_logs" / "temp"
    for d in [temp_dir, cache_dir, chat_temp]:
        d.mkdir(parents=True)
        f = d / "test.tmp"
        f.write_text("temporary data", encoding="utf-8")
        past = time.time() - (48 * 3600)
        os.utime(f, (past, past))

    # 建立日誌檔案
    runtime_log = tmp_path / "runtime_log.jsonl"
    runtime_log.write_text("dummy\n", encoding="utf-8")
    error_log = tmp_path / "error_log.jsonl"
    error_log.write_text("dummy\n", encoding="utf-8")

    # Act
    report = worker.run_retention_cycle()

    # Assert
    assert report.cleaned_files_count == 3
    assert report.reclaimed_bytes > 0
    assert report.errors == []


def test_run_retention_cycle_nonexistent_base_dir(tmp_path: Path) -> None:
    """驗證根目錄不存在時安全返回空白報告。"""
    # Arrange
    worker = DataRetentionWorker(base_data_dir=tmp_path / "not_found_dir")

    # Act
    report = worker.run_retention_cycle()

    # Assert
    assert report.cleaned_files_count == 0
    assert report.reclaimed_bytes == 0
    assert report.purged_log_entries == 0
    assert report.errors == []


def test_run_retention_cycle_with_exceptions_in_substeps(tmp_path: Path) -> None:
    """驗證保留週期在子步驟拋出未預期異常時仍持續執行並記錄至 report.errors。"""
    # Arrange
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir(parents=True)
    runtime_log = tmp_path / "runtime_log.jsonl"
    runtime_log.write_text("entry\n", encoding="utf-8")

    worker = DataRetentionWorker(base_data_dir=tmp_path)

    # Act
    with patch.object(worker, "clean_temp_directory", side_effect=RuntimeError("清理崩潰")):
        with patch.object(worker, "purge_expired_jsonl_logs", side_effect=RuntimeError("輪轉崩潰")):
            report = worker.run_retention_cycle()

    # Assert
    assert len(report.errors) == 2
    assert "清理目錄" in report.errors[0]
    assert "輪轉日誌" in report.errors[1]
