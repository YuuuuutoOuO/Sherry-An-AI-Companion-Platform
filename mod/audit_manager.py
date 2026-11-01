"""audit_manager.py

管理員登入與操作歷史審計紀錄管理器。
紀錄落盤於 settings.DATA_DIR / "admin_audit.jsonl"。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import settings
from core.db import get_db_connection, db_transaction

logger = logging.getLogger(__name__)

AUDIT_LOG_PATH: Path = settings.DATA_DIR / "admin_audit.jsonl"
AUDIT_DB_PATH: Path = settings.DATA_DIR / "chat_history.db"

_AUDIT_DB_INITIALIZED: bool = False


def mask_session_id(session_id: str) -> str:
    """將敏感的 Session Token 進行單向雜湊遮罩，防止審計日誌外洩有效憑證。"""
    if not session_id:
        return ""
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"sess_{digest}"


def _init_audit_db(db_path: Path = AUDIT_DB_PATH) -> None:
    """初始化 chat_history.db 中的 audit_logs 資料表並確保欄位向後相容。"""
    try:
        with db_transaction(db_path) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    session_id TEXT,
                    username TEXT,
                    action TEXT NOT NULL DEFAULT '',
                    ip TEXT,
                    device TEXT,
                    data_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            # 確保舊資料表也有新欄位
            cur.execute("PRAGMA table_info(audit_logs);")
            columns = {row[1] for row in cur.fetchall()}
            for col, col_type in [
                ("session_id", "TEXT"),
                ("username", "TEXT"),
                ("action", "TEXT DEFAULT ''"),
                ("ip", "TEXT"),
                ("device", "TEXT"),
                ("data_json", "TEXT DEFAULT '{}'"),
            ]:
                if col not in columns:
                    cur.execute(f"ALTER TABLE audit_logs ADD COLUMN {col} {col_type};")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_logs(session_id);")
    except Exception as exc:
        logger.debug("audit_manager: 初始化 SQLite 審計表失敗: %s", exc)


def parse_user_agent(ua_str: str) -> str:
    """從 User-Agent 字串解析作業系統與瀏覽器名稱。

    Args:
        ua_str (str): HTTP User-Agent 字串。

    Returns:
        str: 可讀的裝置與瀏覽器描述（例如 "Windows 11 / Chrome"）。
    """
    ua = ua_str.lower() if ua_str else ""
    os_name = "未知裝置"
    if "windows nt 10.0" in ua or "windows nt 11.0" in ua:
        os_name = "Windows"
    elif "macintosh" in ua or "mac os x" in ua:
        os_name = "macOS"
    elif "iphone" in ua or "ipad" in ua:
        os_name = "iOS"
    elif "android" in ua:
        os_name = "Android"
    elif "linux" in ua:
        os_name = "Linux"

    browser_name = "未知瀏覽器"
    if "edg/" in ua or "edge/" in ua:
        browser_name = "Edge"
    elif "chrome/" in ua:
        browser_name = "Chrome"
    elif "firefox/" in ua:
        browser_name = "Firefox"
    elif "safari/" in ua and "chrome" not in ua:
        browser_name = "Safari"
    elif "curl" in ua or "python" in ua:
        browser_name = "API Client"

    return f"{os_name} / {browser_name}"


def parse_ip_location(ip: str) -> str:
    """判斷 IP 來源屬性（本機、局域網或外網）。

    Args:
        ip (str): IP 位址。

    Returns:
        str: 簡短的來源位置標籤。
    """
    if not ip or ip in ("127.0.0.1", "::1", "localhost", "testclient"):
        return "本機端 (Localhost)"
    if ip.startswith("26."):
        return "虛擬區域網路 (Radmin VPN)"
    try:
        import ipaddress

        addr = ipaddress.ip_address(ip)
        if addr.is_loopback:
            return "本機端 (Localhost)"
        if addr.is_private:
            return "區域網路 (LAN)"
    except ValueError:
        pass
    if ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172.16."):
        return "區域網路 (LAN)"
    return "外部網際網路 (Public IP)"


class AdminAuditManager:
    """管理員審計紀錄管理器 (支援 SQLite chat_history.db 與 JSONL 備援)。"""

    def __init__(self, log_path: Path = AUDIT_LOG_PATH, db_path: Path = AUDIT_DB_PATH) -> None:
        self.log_path = log_path
        self.db_path = db_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        _init_audit_db(self.db_path)

    def record_login(self, session_id: str, username: str, ip: str, user_agent: str) -> dict[str, Any]:
        """紀錄一次新的管理員登入事件。

        Args:
            session_id (str): Session Token。
            username (str): 管理員帳號。
            ip (str): 來源 IP 位址。
            user_agent (str): 來源 User-Agent。

        Returns:
            dict[str, Any]: 產生的審計紀錄資料物件。
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        device = parse_user_agent(user_agent)
        location = parse_ip_location(ip)

        masked_id = mask_session_id(session_id)
        record: dict[str, Any] = {
            "session_id": masked_id,
            "username": username or "admin",
            "login_time": now_str,
            "logout_time": "",
            "ip": ip,
            "device": device,
            "location": location,
            "user_agent": user_agent,
            "status": "active",
            "actions": [],
        }

        # 1. 寫入 SQLite
        self._write_db_record(
            timestamp=now_str,
            session_id=masked_id,
            username=username or "admin",
            action="LOGIN",
            ip=ip,
            device=device,
            data=record,
        )

        # 2. 寫入 JSONL 備援
        self._append_record(record)
        logger.info(
            "[AUDIT] 管理員登入成功: 帳號=%s, IP=%s, 裝置=%s, Session=%s",
            username,
            ip,
            device,
            masked_id,
        )
        return record

    def record_action(self, session_id: str, action_type: str, detail: str) -> None:
        """為指定的 Session 紀錄追加一項操作履歷歷程。

        Args:
            session_id (str): Session Token。
            action_type (str): 操作類別（例："修改系統設定", "雪莉測試室"）。
            detail (str): 詳細說明。
        """
        if not session_id:
            return

        masked_id = mask_session_id(session_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        action_item = {
            "timestamp": now_str,
            "action": action_type,
            "detail": detail,
        }

        # 1. 寫入 SQLite 動作記錄並同步主 Session
        self._write_db_record(
            timestamp=now_str,
            session_id=masked_id,
            username="admin",
            action=action_type,
            ip="",
            device="",
            data=action_item,
        )
        try:
            with db_transaction(self.db_path) as cur:
                cur.execute(
                    """
                    SELECT id, data_json FROM audit_logs
                    WHERE session_id = ? AND action = 'LOGIN'
                    ORDER BY id DESC LIMIT 1
                """,
                    (masked_id,),
                )
                row = cur.fetchone()
                if row:
                    rec_dict = json.loads(row[1]) if isinstance(row[1], str) else {}
                    rec_dict.setdefault("actions", []).append(action_item)
                    cur.execute(
                        "UPDATE audit_logs SET data_json = ? WHERE id = ?",
                        (json.dumps(rec_dict, ensure_ascii=False), row[0]),
                    )
        except Exception as exc:
            logger.debug("audit_manager: 更新 SQLite 操作記錄失敗: %s", exc)

        # 2. 更新 JSONL 備援
        records = self._read_all_records()
        updated = False
        for rec in records:
            if rec.get("session_id") in (masked_id, session_id):
                rec.setdefault("actions", []).append(action_item)
                updated = True
                break

        if updated:
            self._write_all_records(records)
            logger.info("[AUDIT] 紀錄操作: Session=%s, 動作=%s", masked_id, action_type)

    def record_logout(self, session_id: str) -> None:
        """標註指定的 Session 為已登出狀態。

        Args:
            session_id (str): Session Token。
        """
        if not session_id:
            return

        masked_id = mask_session_id(session_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. 寫入 SQLite 登出記錄並更新主 Session 狀態
        self._write_db_record(
            timestamp=now_str,
            session_id=masked_id,
            username="admin",
            action="LOGOUT",
            ip="",
            device="",
            data={"status": "logged_out", "logout_time": now_str},
        )
        try:
            with db_transaction(self.db_path) as cur:
                cur.execute(
                    """
                    SELECT id, data_json FROM audit_logs
                    WHERE session_id = ? AND action = 'LOGIN'
                    ORDER BY id DESC LIMIT 1
                """,
                    (masked_id,),
                )
                row = cur.fetchone()
                if row:
                    rec_dict = json.loads(row[1]) if isinstance(row[1], str) else {}
                    rec_dict["status"] = "logged_out"
                    rec_dict["logout_time"] = now_str
                    cur.execute(
                        "UPDATE audit_logs SET data_json = ? WHERE id = ?",
                        (json.dumps(rec_dict, ensure_ascii=False), row[0]),
                    )
        except Exception as exc:
            logger.debug("audit_manager: 更新 SQLite 登出狀態失敗: %s", exc)

        # 2. 更新 JSONL 備援
        records = self._read_all_records()
        updated = False
        for rec in records:
            if rec.get("session_id") in (masked_id, session_id):
                rec["status"] = "logged_out"
                rec["logout_time"] = now_str
                updated = True
                break

        if updated:
            self._write_all_records(records)
            logger.info("[AUDIT] 管理員登出: Session=%s", masked_id)

    def record_security_event(self, action: str, ip: str, username: str = "admin", details: str = "") -> None:
        """紀錄安全審計事件（登入失敗、IP 封鎖、未授權存取等）。

        Args:
            action: 安全事件名稱（如 "LOGIN_FAILED"、"IP_LOCKED"）。
            ip: 來源 IP 位址。
            username: 嘗試登入之使用者名稱。
            details: 詳細描述資訊。
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record: dict[str, Any] = {
            "session_id": "",
            "username": username or "admin",
            "login_time": now_str,
            "logout_time": now_str,
            "ip": ip,
            "device": "Security Alert",
            "location": parse_ip_location(ip),
            "user_agent": "",
            "status": "failed",
            "actions": [{"timestamp": now_str, "action": action, "detail": details}],
        }
        self._write_db_record(
            timestamp=now_str,
            session_id="",
            username=username or "admin",
            action=action,
            ip=ip,
            device="Security Alert",
            data=record,
        )
        self._append_record(record)
        logger.warning("[AUDIT-SECURITY] %s: IP=%s, User=%s, Details=%s", action, ip, username, details)

    def get_audit_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """讀取歷史審計紀錄列表（依登入時間倒序排列，優先讀取 SQLite）。

        Args:
            limit (int): 最大回傳筆數。

        Returns:
            list[dict[str, Any]]: 審計紀錄列表。
        """
        # 1. 優先嘗試由 SQLite 讀取
        try:
            _init_audit_db(self.db_path)
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT timestamp, session_id, username, action, ip, device, data_json
                FROM audit_logs
                WHERE action IN ('LOGIN', 'LOGIN_FAILED', 'IP_LOCKED', 'IP_BLOCKED_ATTEMPT')
                ORDER BY id DESC
                LIMIT ?
            """,
                (limit,),
            )
            rows = cur.fetchall()
            conn.close()

            if rows:
                results = []
                for r in rows:
                    try:
                        rec = json.loads(r["data_json"])
                        results.append(rec)
                    except Exception:
                        pass
                if results:
                    return results
        except Exception as exc:
            logger.debug("audit_manager: 讀取 SQLite audit_logs 失敗: %s", exc)

        # 2. 向下相容回退至 JSONL
        records = self._read_all_records()
        records.reverse()
        return records[:limit]

    def _write_db_record(
        self,
        timestamp: str,
        session_id: str,
        username: str,
        action: str,
        ip: str,
        device: str,
        data: dict[str, Any],
    ) -> None:
        """寫入單筆審計紀錄至 SQLite audit_logs 資料表。"""
        try:
            _init_audit_db(self.db_path)
            data_json = json.dumps(data, ensure_ascii=False)
            with db_transaction(self.db_path) as cur:
                cur.execute(
                    """
                    INSERT INTO audit_logs (timestamp, session_id, username, action, ip, device, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (timestamp, session_id, username, action, ip, device, data_json),
                )
        except Exception as exc:
            logger.debug("audit_manager: 寫入 SQLite audit_logs 失敗: %s", exc)

    def _append_record(self, record: dict[str, Any]) -> None:
        """以 JSONL 格式追加單筆紀錄至檔案。"""
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("寫入審計紀錄失敗: %s", e)

    def _read_all_records(self) -> list[dict[str, Any]]:
        """讀取 JSONL 檔案中的所有紀錄。"""
        if not self.log_path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception as e:
            logger.error("讀取審計紀錄失敗: %s", e)
        return records

    def _write_all_records(self, records: list[dict[str, Any]]) -> None:
        """覆寫更新 JSONL 檔案。"""
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("更新審計紀錄檔失敗: %s", e)


audit_manager = AdminAuditManager()
