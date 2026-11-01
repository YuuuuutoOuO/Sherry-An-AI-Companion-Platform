"""
handlers/memory/retention_worker.py
===================================
資料生命週期管理與自動清理工作器 (DataRetentionWorker)。

依據隱私保護原則與磁碟治理規範，定期清理超過保留期限 (TTL)
的對話歷史、運行日誌、暫存圖片與音訊快取，防止磁碟空間耗盡與敏感資料無界限殘留。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import stat
import time

import settings
from core.db import db_transaction

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))

# R7：軟刪除資料表的物理清除目標（保留治理；報告 §5.5.2）
# 檔案路徑相對於 base_data_dir，表名需具備 is_deleted/deleted_at 欄位。
_SOFT_DELETE_TABLE_TARGETS: tuple[tuple[str, str], ...] = (
    ("chat_history.db", "chat_messages"),
    ("chat_history.db", "roast_rules"),
    ("sherry_memory.db", "user_profiles"),
    ("sherry_memory.db", "user_facts"),
    ("sherry_memory.db", "user_aliases"),
    ("sherry_memory.db", "user_relations"),
    ("world_memory.db", "world_events"),
    ("world_memory.db", "world_summary"),
    ("schedules.db", "schedule_tasks"),
)


def _parse_line_timestamp(line_str: str) -> float | None:
    """嘗試從單行 JSONL 記錄中解析時間戳記 (支援 ISO 格式、UNIX timestamp、標準日期)。"""
    try:
        data = json.loads(line_str)
        if isinstance(data, dict):
            for field_name in ("timestamp", "ts", "time", "created_at", "date"):
                val = data.get(field_name)
                if val is not None:
                    if isinstance(val, (int, float)):
                        return float(val)
                    if isinstance(val, str) and val.strip():
                        try:
                            iso_str = val.strip().replace("Z", "+00:00")
                            dt = datetime.fromisoformat(iso_str)
                            return dt.timestamp()
                        except Exception:
                            pass
    except Exception:
        pass
    return None


@dataclass
class RetentionPolicy:
    """資料保留原則配置。"""

    runtime_log_max_days: int = 14  # 運作日誌保留天數
    error_log_max_days: int = 30  # 錯誤日誌保留天數
    admin_audit_max_days: int = 90  # 管理審計日誌保留天數
    temp_files_max_hours: int = 24  # 暫存圖檔與音效保留小時數
    chat_messages_max_days: int = 180  # 對話訊息保留天數（R7：軟刪除）
    soft_delete_grace_days: int = 30  # 軟刪除寬限期，超過後才物理清除（R7）


@dataclass
class RetentionCleanReport:
    """清理作業成果報告。"""

    cleaned_files_count: int = 0
    reclaimed_bytes: int = 0
    purged_log_entries: int = 0
    soft_deleted_db_rows: int = 0  # R7：軟刪除的資料列數（如過期對話）
    purged_db_rows: int = 0  # R7：物理清除的軟刪除列數
    errors: list[str] = field(default_factory=list)


class DataRetentionWorker:
    """資料保留管理與自動輪轉工作器。"""

    def __init__(
        self,
        base_data_dir: Path | str | None = None,
        policy: RetentionPolicy | None = None,
    ) -> None:
        """初始化保留工作器。

        Args:
            base_data_dir: 資料檔案儲存根目錄（預設為絕對路徑 settings.DATA_DIR；
                R7 修正：不再使用相對路徑 "data"，避免工作目錄相依）。
            policy: 保留政策物件。
        """
        self.base_data_dir = Path(base_data_dir) if base_data_dir is not None else Path(settings.DATA_DIR)
        self.policy = policy or RetentionPolicy()

    def clean_temp_directory(
        self,
        temp_dir: Path | str,
        errors_log: list[str] | None = None,
    ) -> tuple[int, int]:
        """清理目錄下超過指定時效的暫存檔案。

        Args:
            temp_dir: 欲清理的暫存目錄路徑。
            errors_log: 可選的錯誤記錄列表，若提供則會將執行中的異常訊息加入其中。

        Returns:
            tuple[int, int]: (清理檔案數, 釋放位元組數)。
        """
        dir_path = Path(temp_dir)
        if not dir_path.exists() or not dir_path.is_dir():
            return 0, 0

        now = time.time()
        max_age_seconds = self.policy.temp_files_max_hours * 3600
        cleaned_count = 0
        reclaimed_bytes = 0

        try:
            for file_path in dir_path.glob("*"):
                if file_path.is_file():
                    try:
                        file_stat = file_path.stat()
                        if now - file_stat.st_mtime > max_age_seconds:
                            size = file_stat.st_size
                            try:
                                file_path.unlink()
                            except PermissionError:
                                # 嘗試移除唯讀屬性後再次刪除（相容 Windows / Linux）
                                os.chmod(file_path, stat.S_IWRITE)
                                file_path.unlink()
                            cleaned_count += 1
                            reclaimed_bytes += size
                    except Exception as exc:
                        err_msg = f"清理暫存檔案失敗 ({file_path}): {exc}"
                        logger.warning("[DataRetention] %s", err_msg)
                        if errors_log is not None:
                            errors_log.append(err_msg)
        except Exception as exc:
            err_msg = f"存取暫存目錄失敗 ({dir_path}): {exc}"
            logger.warning("[DataRetention] %s", err_msg)
            if errors_log is not None:
                errors_log.append(err_msg)

        return cleaned_count, reclaimed_bytes

    def purge_expired_jsonl_logs(
        self,
        file_path: Path | str,
        max_days: int,
        errors_log: list[str] | None = None,
    ) -> int:
        """清理單一 JSONL 日誌檔中超過天數限制的舊行。

        Args:
            file_path: JSONL 日誌檔案路徑。
            max_days: 最大保留天數。
            errors_log: 可選的錯誤記錄列表，若提供則會將執行中的異常訊息加入其中。

        Returns:
            int: 刪除的過期行數。
        """
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return 0

        now = time.time()
        max_age_seconds = max_days * 86400
        cutoff_time = now - max_age_seconds
        kept_lines: list[str] = []
        purged_count = 0
        has_line_timestamps = False
        tmp_path: Path | None = None

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    line_ts = _parse_line_timestamp(line_str)
                    if line_ts is not None:
                        has_line_timestamps = True
                        if line_ts < cutoff_time:
                            purged_count += 1
                            continue
                    kept_lines.append(line)

            # 若無行內時間戳但整個檔案 mtime 超過 max_days 且超過 50MB 則截斷輪轉 (保留最新 10000 行)
            if not has_line_timestamps:
                file_stat = path.stat()
                if now - file_stat.st_mtime > max_age_seconds and file_stat.st_size > 50 * 1024 * 1024:
                    if len(kept_lines) > 10000:
                        purged_count = len(kept_lines) - 10000
                        kept_lines = kept_lines[-10000:]

            if purged_count > 0:
                # R7：原子輪轉——先寫同目錄暫存檔再 os.replace，避免中斷時留下半截日誌
                tmp_path = path.with_name(path.name + ".tmp")
                with open(tmp_path, "w", encoding="utf-8") as out:
                    out.writelines(kept_lines)
                os.replace(tmp_path, path)
                tmp_path = None
                logger.info("[DataRetention] 日誌 %s 已清理，移除了 %d 筆過期記錄 (保留 %d 行)", path.name, purged_count, len(kept_lines))
        except Exception as e:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            err_msg = f"輪轉日誌 {path} 失敗: {e}"
            logger.error("[DataRetention] %s", err_msg)
            if errors_log is not None:
                errors_log.append(err_msg)

        return purged_count

    def soft_delete_expired_chat_messages(
        self,
        max_days: int | None = None,
        errors_log: list[str] | None = None,
    ) -> int:
        """軟刪除超過對話保留期（預設 180 天）的 chat_messages（R7；報告 §5.2.4）。

        僅標記 `is_deleted`/`deleted_at`；物理清除由 `purge_soft_deleted_rows`
        於寬限期後分批執行，確保刪除具軌跡、可稽核，且查詢面立即失效。

        Args:
            max_days: 覆寫政策之保留天數（<=0 表示停用）。
            errors_log: 可選的錯誤記錄列表。

        Returns:
            int: 軟刪除的訊息筆數。
        """
        days = self.policy.chat_messages_max_days if max_days is None else max_days
        if days <= 0:
            return 0
        db_path = self.base_data_dir / "chat_history.db"
        if not db_path.exists():
            return 0

        cutoff_date = (datetime.now(_TZ_TW) - timedelta(days=days)).strftime("%Y-%m-%d")
        now_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with db_transaction(db_path) as cur:
                cur.execute(
                    """
                    UPDATE chat_messages
                    SET is_deleted = 1, deleted_at = ?, updated_at = ?
                    WHERE date_str < ? AND is_deleted = 0
                    """,
                    (now_str, now_str, cutoff_date),
                )
                removed = int(cur.rowcount or 0)
            if removed:
                logger.info("[DataRetention] 已軟刪除 %d 筆超過 %d 天的對話訊息", removed, days)
            return removed
        except Exception as exc:
            err_msg = f"軟刪除過期對話訊息失敗 ({db_path}): {exc}"
            logger.warning("[DataRetention] %s", err_msg)
            if errors_log is not None:
                errors_log.append(err_msg)
            return 0

    def purge_soft_deleted_rows(
        self,
        grace_days: int | None = None,
        errors_log: list[str] | None = None,
        batch_size: int = 500,
    ) -> int:
        """物理清除超過寬限期之軟刪列（分批＋稽核；R7；報告 §5.5.2）。

        涵蓋對話、長期記憶、世界記憶、排程與吐槽規則等軟刪資料表。
        與 `tools/db_manager.py cleanup` 互補：此處供夜間保留週期自動執行，
        CLI 仍保有全標準表的手動清運能力。

        Args:
            grace_days: 覆寫政策之寬限期（預設 30 天）。
            errors_log: 可選的錯誤記錄列表。
            batch_size: 每批刪除上限（避免長交易鎖庫）。

        Returns:
            int: 物理刪除的總筆數。
        """
        grace = self.policy.soft_delete_grace_days if grace_days is None else grace_days
        cutoff = (datetime.now(_TZ_TW) - timedelta(days=grace)).strftime("%Y-%m-%d %H:%M:%S")
        total = 0

        for db_rel, table in _SOFT_DELETE_TABLE_TARGETS:
            db_path = self.base_data_dir / db_rel
            if not db_path.exists():
                continue
            try:
                with db_transaction(db_path) as cur:
                    cur.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table,),
                    )
                    if cur.fetchone() is None:
                        continue

                while True:
                    with db_transaction(db_path) as cur:
                        # deleted_at 兼容「localtime 字串」與「UTC ISO8601」兩種歷史格式
                        cur.execute(
                            f"DELETE FROM {table} WHERE rowid IN ("
                            f"SELECT rowid FROM {table} "
                            f"WHERE is_deleted = 1 "
                            f"AND (deleted_at IS NULL OR REPLACE(SUBSTR(deleted_at, 1, 19), 'T', ' ') <= ?) "
                            f"LIMIT {int(batch_size)})",
                            (cutoff,),
                        )
                        batch = int(cur.rowcount or 0)
                    total += batch
                    if batch < batch_size:
                        break
            except Exception as exc:
                err_msg = f"物理清除 {db_rel}:{table} 失敗: {exc}"
                logger.warning("[DataRetention] %s", err_msg)
                if errors_log is not None:
                    errors_log.append(err_msg)

        if total:
            logger.info(
                "[DataRetention] 物理清除完成，共移除 %d 筆超過 %d 天寬限期的軟刪資料",
                total,
                grace,
            )
        return total

    def run_retention_cycle(self) -> RetentionCleanReport:
        """執行一次完整的資料保留維護週期。

        Returns:
            RetentionCleanReport: 清理成果摘要。
        """
        report = RetentionCleanReport()

        if not self.base_data_dir.exists():
            return report

        # 1. 清理暫存目錄 (例如 data/temp, data/cache, data/chat_logs/temp)
        for temp_sub in ["temp", "cache", "chat_logs/temp"]:
            sub_path = self.base_data_dir / temp_sub
            if sub_path.exists():
                try:
                    count, b_reclaimed = self.clean_temp_directory(sub_path, errors_log=report.errors)
                    report.cleaned_files_count += count
                    report.reclaimed_bytes += b_reclaimed
                except Exception as exc:
                    err_msg = f"清理目錄 {sub_path} 時發生未預期異常: {exc}"
                    logger.error("[DataRetention] %s", err_msg)
                    report.errors.append(err_msg)

        # 2. 輪轉標準日誌
        runtime_log = self.base_data_dir / "runtime_log.jsonl"
        error_log = self.base_data_dir / "error_log.jsonl"

        if runtime_log.exists():
            try:
                report.purged_log_entries += self.purge_expired_jsonl_logs(
                    runtime_log,
                    self.policy.runtime_log_max_days,
                    errors_log=report.errors,
                )
            except Exception as exc:
                err_msg = f"輪轉日誌 {runtime_log} 時發生未預期異常: {exc}"
                logger.error("[DataRetention] %s", err_msg)
                report.errors.append(err_msg)

        if error_log.exists():
            try:
                report.purged_log_entries += self.purge_expired_jsonl_logs(
                    error_log,
                    self.policy.error_log_max_days,
                    errors_log=report.errors,
                )
            except Exception as exc:
                err_msg = f"輪轉日誌 {error_log} 時發生未預期異常: {exc}"
                logger.error("[DataRetention] %s", err_msg)
                report.errors.append(err_msg)

        # 3. 輪轉管理審計日誌（R7：政策 :52 落地）
        admin_audit = self.base_data_dir / "admin_audit.jsonl"
        if admin_audit.exists():
            try:
                report.purged_log_entries += self.purge_expired_jsonl_logs(
                    admin_audit,
                    self.policy.admin_audit_max_days,
                    errors_log=report.errors,
                )
            except Exception as exc:
                err_msg = f"輪轉日誌 {admin_audit} 時發生未預期異常: {exc}"
                logger.error("[DataRetention] %s", err_msg)
                report.errors.append(err_msg)

        # 4. 對話訊息保留政策（軟刪；報告 §5.2.4）
        report.soft_deleted_db_rows += self.soft_delete_expired_chat_messages(
            errors_log=report.errors
        )

        # 5. 物理清除超過寬限期的軟刪列（報告 §5.5.2）
        report.purged_db_rows += self.purge_soft_deleted_rows(errors_log=report.errors)

        logger.info(
            "[DataRetention] 保留維護週期完成: 清理檔案 %d 個, 釋放 %.2f MB, 輪轉記錄 %d 條, 對話軟刪 %d 筆, 物理清除 %d 筆, 錯誤數 %d",
            report.cleaned_files_count,
            report.reclaimed_bytes / (1024 * 1024),
            report.purged_log_entries,
            report.soft_deleted_db_rows,
            report.purged_db_rows,
            len(report.errors),
        )
        return report
