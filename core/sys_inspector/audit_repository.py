"""core.sys_inspector.audit_repository
=====================================
系統自我監控與延遲稽核 SQLite 持久化倉儲。

儲存於 ./data/sys_inspector/audit.db，採用 SQLite WAL 模式，
負責所有系統延遲、超時、阻塞事故與雲端稽核診斷報告之讀寫與統計。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any

from core.db import db_transaction, get_db_connection
from core.sys_inspector.config import get_audit_db_path
from core.sys_inspector.models import (
    DiagnosticResult,
    IncidentReport,
    IncidentType,
    LogSnapshot,
)

logger = logging.getLogger(__name__)

_TZ_TW = timezone(timedelta(hours=8))  # 台灣時間 UTC+8


class AuditDBRepository:
    """系統稽核資料庫倉儲。"""

    def __init__(self, db_path: Path | None = None) -> None:
        """初始化稽核資料庫倉儲並確保資料表結構就緒。

        Args:
            db_path: 自訂資料庫路徑（預設 get_audit_db_path()）。
        """
        self._db_path = db_path or get_audit_db_path()
        self._init_db()

    def _init_db(self) -> None:
        """初始化 SQLite 資料表與索引。"""
        try:
            with db_transaction(self._db_path) as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS audit_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        incident_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        latency_ms INTEGER NOT NULL DEFAULT 0,
                        trigger_reason TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        channel_id INTEGER NOT NULL DEFAULT 0,
                        user_name TEXT NOT NULL DEFAULT '',
                        raw_prompt TEXT DEFAULT '',
                        bottleneck_category TEXT NOT NULL,
                        root_cause TEXT NOT NULL,
                        key_evidence_json TEXT NOT NULL DEFAULT '[]',
                        recommendations_json TEXT NOT NULL DEFAULT '[]',
                        raw_model_response TEXT DEFAULT '',
                        model_name TEXT DEFAULT '',
                        diagnosis_latency_ms INTEGER DEFAULT 0,
                        log_snapshot_json TEXT DEFAULT '{}',
                        extra_metrics_json TEXT DEFAULT '{}'
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_records(created_at DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_severity ON audit_records(severity);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_records(incident_type);")
        except Exception as exc:
            logger.error("AuditDBRepository: 初始化 audit.db 資料表失敗: %s", exc, exc_info=True)

    def insert_audit_record(
        self,
        incident: IncidentReport,
        diagnostic: DiagnosticResult,
        snapshot: LogSnapshot,
    ) -> int:
        """將單次稽核事件與診斷結果持久化寫入資料庫。

        Args:
            incident: 異常事件報告。
            diagnostic: 雲端搜尋模型診斷結果。
            snapshot: 5 分鐘日誌快照。

        Returns:
            int: 插入成功之紀錄主鍵 ID。
        """
        now_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
        evidence_json = json.dumps(diagnostic.key_evidence, ensure_ascii=False)
        recs_json = json.dumps(diagnostic.recommendations, ensure_ascii=False)
        metrics_json = json.dumps(incident.extra_metrics, ensure_ascii=False)

        snapshot_dict = {
            "window_minutes": snapshot.window_minutes,
            "total_lines": snapshot.total_lines,
            "error_count": snapshot.error_count,
            "warning_count": snapshot.warning_count,
            "bot_log_snippet": snapshot.bot_log_snippet,
            "runtime_event_snippet": snapshot.runtime_event_snippet,
        }
        snapshot_json = json.dumps(snapshot_dict, ensure_ascii=False)

        try:
            with db_transaction(self._db_path) as cur:
                cur.execute(
                    """
                    INSERT INTO audit_records (
                        created_at, incident_type, severity, latency_ms,
                        trigger_reason, trace_id, channel_id, user_name, raw_prompt,
                        bottleneck_category, root_cause, key_evidence_json, recommendations_json,
                        raw_model_response, model_name, diagnosis_latency_ms,
                        log_snapshot_json, extra_metrics_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_str,
                        incident.incident_type.value,
                        incident.severity.value,
                        incident.latency_ms,
                        incident.trigger_reason,
                        incident.trace_id,
                        incident.channel_id,
                        incident.user_name,
                        incident.raw_prompt,
                        diagnostic.bottleneck_category,
                        diagnostic.root_cause,
                        evidence_json,
                        recs_json,
                        diagnostic.raw_model_response,
                        diagnostic.model_name,
                        diagnostic.diagnosis_latency_ms,
                        snapshot_json,
                        metrics_json,
                    ),
                )
                record_id = int(cur.lastrowid or 0)
                logger.info("AuditDBRepository: 已成功寫入稽核紀錄 ID=%d (事故=%s)", record_id, incident.incident_type.value)
                return record_id
        except Exception as exc:
            logger.error("AuditDBRepository: 寫入稽核紀錄失敗: %s", exc, exc_info=True)
            return 0

    def get_audit_records(
        self,
        limit: int = 50,
        offset: int = 0,
        severity: str | None = None,
        incident_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """查詢稽核紀錄清單（依時間降序排列）。

        Args:
            limit: 查詢筆數上限。
            offset: 分頁偏移量。
            severity: 可選的嚴重度過濾 (INFO / WARNING / CRITICAL)。
            incident_type: 可選的事故類型過濾。

        Returns:
            list[dict[str, Any]]: 格式化之稽核紀錄清單。
        """
        query = "SELECT * FROM audit_records"
        params: list[Any] = []
        conditions: list[str] = []

        if severity:
            conditions.append("severity = ?")
            params.append(severity.upper())
        if incident_type:
            conditions.append("incident_type = ?")
            params.append(incident_type.upper())

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        results: list[dict[str, Any]] = []
        try:
            conn = get_db_connection(self._db_path)
            cur = conn.cursor()
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            for r in rows:
                results.append(self._row_to_dict(r))
            conn.close()
        except Exception as exc:
            logger.error("AuditDBRepository: 查詢稽核紀錄列表失敗: %s", exc)

        return results

    def get_audit_record_by_id(self, record_id: int) -> dict[str, Any] | None:
        """依 ID 取得單筆稽核紀錄詳細內容。

        Args:
            record_id: 紀錄主鍵 ID。

        Returns:
            dict[str, Any] | None: 若存在回傳字典，否則回傳 None。
        """
        try:
            conn = get_db_connection(self._db_path)
            cur = conn.cursor()
            cur.execute("SELECT * FROM audit_records WHERE id = ?", (record_id,))
            row = cur.fetchone()
            conn.close()
            return self._row_to_dict(row) if row else None
        except Exception as exc:
            logger.error("AuditDBRepository: 查詢稽核紀錄 ID=%d 失敗: %s", record_id, exc)
            return None

    def get_stats_summary(self) -> dict[str, Any]:
        """計算稽核資料庫總體 KPI 指標摘要。

        Returns:
            dict[str, Any]: 包含總稽核數、平均延遲、警告數、嚴重數與最新稽核時間之字典。
        """
        stats: dict[str, Any] = {
            "total_audits": 0,
            "avg_latency_ms": 0,
            "warning_count": 0,
            "critical_count": 0,
            "blocking_count": 0,
            "manual_count": 0,
            "latest_audit_time": "-",
        }
        try:
            conn = get_db_connection(self._db_path)
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*), AVG(latency_ms), MAX(created_at) FROM audit_records")
            row = cur.fetchone()
            if row:
                stats["total_audits"] = int(row[0] or 0)
                stats["avg_latency_ms"] = int(row[1] or 0)
                stats["latest_audit_time"] = str(row[2] or "-")

            cur.execute("SELECT severity, COUNT(*) FROM audit_records GROUP BY severity")
            for sev_row in cur.fetchall():
                sev = str(sev_row[0]).upper()
                count = int(sev_row[1] or 0)
                if sev == "WARNING":
                    stats["warning_count"] = count
                elif sev == "CRITICAL":
                    stats["critical_count"] = count

            cur.execute("SELECT incident_type, COUNT(*) FROM audit_records GROUP BY incident_type")
            for type_row in cur.fetchall():
                itype = str(type_row[0]).upper()
                count = int(type_row[1] or 0)
                if itype == IncidentType.SYSTEM_BLOCKING.value:
                    stats["blocking_count"] = count
                elif itype == IncidentType.MANUAL_CHECK.value:
                    stats["manual_count"] = count

            conn.close()
        except Exception as exc:
            logger.error("AuditDBRepository: 計算稽核統計摘要失敗: %s", exc)

        return stats

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """將 SQLite Row 轉換為對外 JSON 相容字典。"""
        if row is None:
            return {}
        d = dict(row)
        try:
            d["key_evidence"] = json.loads(d.get("key_evidence_json", "[]"))
        except Exception:
            d["key_evidence"] = []
        try:
            d["recommendations"] = json.loads(d.get("recommendations_json", "[]"))
        except Exception:
            d["recommendations"] = []
        try:
            d["log_snapshot"] = json.loads(d.get("log_snapshot_json", "{}"))
        except Exception:
            d["log_snapshot"] = {}
        try:
            d["extra_metrics"] = json.loads(d.get("extra_metrics_json", "{}"))
        except Exception:
            d["extra_metrics"] = {}
        return d
