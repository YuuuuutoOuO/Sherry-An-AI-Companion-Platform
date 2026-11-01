"""mod/user_tracker.py
===================
一般使用者連線狀況與訪客歷程追蹤管理器。
負責管理即時在線訪客連線池、生命週期事件（連線、心跳、離線 Beacon）、
以及 SQLite (chat_history.db) 與 JSONL (./data/user_connections.jsonl) 雙軌落盤。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import settings
from core.db import db_transaction, get_db_connection
from mod.audit_manager import parse_ip_location, parse_user_agent

logger = logging.getLogger(__name__)

USER_CONNECTIONS_DB_PATH: Path = settings.DATA_DIR / "chat_history.db"
USER_CONNECTIONS_LOG_PATH: Path = settings.DATA_DIR / "user_connections.jsonl"


def get_client_ip(req: Any) -> str:
    """提取客戶端 IP 位址，支援 Quart request 物件或直接傳入的字串。"""
    if isinstance(req, str):
        return req
    remote = str(getattr(req, "remote_addr", "127.0.0.1") or "127.0.0.1")
    configured_proxies = getattr(settings, "WEB_UI_TRUSTED_PROXIES", None) or [
        "127.0.0.1",
        "::1",
        "localhost",
        "testclient",
    ]
    trusted_proxies = set(configured_proxies)
    if remote in trusted_proxies and hasattr(req, "headers"):
        forwarded = req.headers.get("X-Forwarded-For")
        if forwarded:
            candidates = [p.strip() for p in str(forwarded).split(",") if p.strip()]
            for client_candidate in reversed(candidates):
                try:
                    ipaddress.ip_address(client_candidate)
                    return client_candidate
                except ValueError:
                    continue
    return remote


_get_client_ip = get_client_ip


class UserConnectionTracker:
    """一般使用者連線狀況與訪客歷程追蹤管理器。"""

    def __init__(
        self,
        db_path: Path | None = None,
        log_path: Path | None = None,
        max_active: int = 1000,
    ) -> None:
        self.db_path = Path(db_path) if db_path else USER_CONNECTIONS_DB_PATH
        self.log_path = Path(log_path) if log_path else USER_CONNECTIONS_LOG_PATH
        self.max_active = max_active
        self._active_connections: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

        # 確保目錄存在並初始化資料表
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化 chat_history.db 中的 user_connections 資料表並建立索引。"""
        try:
            with db_transaction(self.db_path) as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_connections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        visitor_id TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        last_active_at TEXT NOT NULL,
                        duration_seconds INTEGER NOT NULL DEFAULT 0,
                        ip TEXT,
                        device TEXT,
                        location TEXT,
                        user_agent TEXT,
                        last_path TEXT,
                        hit_count INTEGER NOT NULL DEFAULT 1,
                        status TEXT NOT NULL DEFAULT 'online',
                        data_json TEXT NOT NULL DEFAULT '{}'
                    )
                """)
                cur.execute("PRAGMA table_info(user_connections);")
                columns = {row[1] for row in cur.fetchall()}
                for col, col_type in [
                    ("visitor_id", "TEXT NOT NULL DEFAULT ''"),
                    ("first_seen_at", "TEXT NOT NULL DEFAULT ''"),
                    ("last_active_at", "TEXT NOT NULL DEFAULT ''"),
                    ("duration_seconds", "INTEGER NOT NULL DEFAULT 0"),
                    ("ip", "TEXT"),
                    ("device", "TEXT"),
                    ("location", "TEXT"),
                    ("user_agent", "TEXT"),
                    ("last_path", "TEXT"),
                    ("hit_count", "INTEGER NOT NULL DEFAULT 1"),
                    ("status", "TEXT NOT NULL DEFAULT 'online'"),
                    ("data_json", "TEXT NOT NULL DEFAULT '{}'"),
                ]:
                    if col not in columns:
                        cur.execute(f"ALTER TABLE user_connections ADD COLUMN {col} {col_type};")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_user_conn_time ON user_connections(last_active_at DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_user_conn_visitor ON user_connections(visitor_id);")
        except Exception as exc:
            logger.debug("user_tracker: 初始化 SQLite 訪客連線表失敗: %s", exc)

    def touch_connection(
        self,
        visitor_id: str | None,
        ip: str = "127.0.0.1",
        user_agent: str = "",
        path: str = "/",
    ) -> str:
        """紀錄或更新訪客的連線活動。

        Args:
            visitor_id: 訪客識別碼（若為空則自動生成）。
            ip: 客戶端來源 IP。
            user_agent: 客戶端 User-Agent。
            path: 請求路徑。

        Returns:
            str: 有效之 visitor_id。
        """
        vid = visitor_id.strip() if visitor_id else ""
        if not vid:
            vid = f"vis_{secrets.token_hex(8)}"

        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            # 檢查並維護容量限制
            if len(self._active_connections) >= self.max_active:
                to_persist = self._sweep_locked(max_idle_sec=600.0, force_evict_oldest=True)
            else:
                to_persist = []

            is_new_session = False
            if vid in self._active_connections:
                conn = self._active_connections[vid]
                # 若先前已離線，視為新會話重新計算
                if conn.get("status") == "offline":
                    conn["first_seen_at"] = now_str
                    conn["first_seen_ts"] = now_ts
                    conn["hit_count"] = 1
                    is_new_session = True
                else:
                    conn["hit_count"] = conn.get("hit_count", 1) + 1

                conn["last_active_at"] = now_str
                conn["last_active_ts"] = now_ts
                conn["duration_seconds"] = max(0, int(now_ts - conn.get("first_seen_ts", now_ts)))
                conn["last_path"] = path
                conn["status"] = "online"

                if ip and ip != "127.0.0.1" and (not conn.get("ip") or conn["ip"] == "127.0.0.1"):
                    conn["ip"] = ip
                    conn["location"] = parse_ip_location(ip)
                if user_agent and not conn.get("user_agent"):
                    conn["user_agent"] = user_agent
                    conn["device"] = parse_user_agent(user_agent)

                conn_copy = dict(conn)
            else:
                dev = parse_user_agent(user_agent)
                loc = parse_ip_location(ip)
                conn = {
                    "visitor_id": vid,
                    "first_seen_at": now_str,
                    "first_seen_ts": now_ts,
                    "last_active_at": now_str,
                    "last_active_ts": now_ts,
                    "duration_seconds": 0,
                    "ip": ip,
                    "device": dev,
                    "location": loc,
                    "user_agent": user_agent,
                    "last_path": path,
                    "hit_count": 1,
                    "status": "online",
                }
                self._active_connections[vid] = conn
                conn_copy = dict(conn)
                is_new_session = True

        # 落盤操作移至 _lock 外部，避免持鎖進行磁碟與 SQLite 交易
        if to_persist:
            for item in to_persist:
                self._update_db_record(item)
                self._append_jsonl_record(item)

        if is_new_session:
            self._write_db_record(conn_copy)
            self._append_jsonl_record(conn_copy)

        return vid

    def record_heartbeat(self, visitor_id: str) -> bool:
        """更新訪客的心跳活躍狀態。

        Args:
            visitor_id: 訪客識別碼。

        Returns:
            bool: 成功更新傳回 True，若訪客不在連線池中則傳回 False。
        """
        if not visitor_id:
            return False

        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            if visitor_id not in self._active_connections:
                return False

            conn = self._active_connections[visitor_id]
            conn["last_active_at"] = now_str
            conn["last_active_ts"] = now_ts
            conn["duration_seconds"] = max(0, int(now_ts - conn.get("first_seen_ts", now_ts)))
            conn["status"] = "online"
            return True

    def record_disconnect(self, visitor_id: str) -> None:
        """標記訪客為已離線狀態，並立即持久化連線結算。

        Args:
            visitor_id: 訪客識別碼。
        """
        if not visitor_id:
            return

        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            if visitor_id not in self._active_connections:
                return

            conn = self._active_connections[visitor_id]
            conn["last_active_at"] = now_str
            conn["last_active_ts"] = now_ts
            conn["duration_seconds"] = max(0, int(now_ts - conn.get("first_seen_ts", now_ts)))
            conn["status"] = "offline"
            conn_copy = dict(conn)

        self._update_db_record(conn_copy)
        self._append_jsonl_record(conn_copy)

    def sweep_expired(self, max_idle_sec: float = 600.0) -> None:
        """主動檢查並清理閒置或逾期連線。"""
        with self._lock:
            to_persist = self._sweep_locked(max_idle_sec=max_idle_sec, force_evict_oldest=False)
        for conn_item in to_persist:
            self._update_db_record(conn_item)
            self._append_jsonl_record(conn_item)

    def _sweep_locked(self, max_idle_sec: float = 600.0, force_evict_oldest: bool = False) -> list[dict[str, Any]]:
        """在已持有 _lock 的情況下執行連線逾期判定與記憶體淘汰（回傳需落盤清單，不於持鎖中寫磁碟）。"""
        now = time.time()
        to_persist: list[dict[str, Any]] = []

        for vid, conn in list(self._active_connections.items()):
            idle_sec = now - conn.get("last_active_ts", now)
            if idle_sec > max_idle_sec:
                if conn.get("status") != "offline":
                    conn["status"] = "offline"
                    conn["duration_seconds"] = max(
                        0, int(conn.get("last_active_ts", now) - conn.get("first_seen_ts", now))
                    )
                    to_persist.append(dict(conn))
            elif idle_sec > 45.0:
                conn["status"] = "idle"
            else:
                conn["status"] = "online"

        if force_evict_oldest and len(self._active_connections) >= self.max_active:
            # 優先淘汰已 offline 者
            offline_vids = [k for k, v in self._active_connections.items() if v.get("status") == "offline"]
            for vid in offline_vids:
                self._active_connections.pop(vid, None)
                if len(self._active_connections) < self.max_active * 0.9:
                    break

            # 若仍超出上限，依最後活躍時間淘汰最舊者
            if len(self._active_connections) >= self.max_active:
                sorted_vids = sorted(
                    self._active_connections.keys(),
                    key=lambda k: self._active_connections[k].get("last_active_ts", 0),
                )
                overflow_count = len(self._active_connections) - int(self.max_active * 0.9)
                for vid in sorted_vids[:overflow_count]:
                    self._active_connections.pop(vid, None)

        return to_persist

    def flush_all_active(self) -> None:
        """伺服器重啟或退出前，將所有當前活躍會話的最新時長持久化落盤。"""
        with self._lock:
            now = time.time()
            to_flush: list[dict[str, Any]] = []
            for conn in self._active_connections.values():
                if conn.get("status") != "offline":
                    c = dict(conn)
                    c["duration_seconds"] = max(0, int(now - c.get("first_seen_ts", now)))
                    to_flush.append(c)
        for c in to_flush:
            self._update_db_record(c)

    def get_online_summary(self) -> dict[str, Any]:
        """彙總當前在線訪客狀態與今日連線統計。"""
        with self._lock:
            to_persist = self._sweep_locked(max_idle_sec=600.0, force_evict_oldest=False)
            now = time.time()
            today_prefix = datetime.now().strftime("%Y-%m-%d")

            active_list: list[dict[str, Any]] = []
            for c in self._active_connections.values():
                if c.get("status") in ("online", "idle"):
                    item = dict(c)
                    item["duration_seconds"] = max(0, int(now - item.get("first_seen_ts", now)))
                    active_list.append(item)

            active_list.sort(key=lambda x: x.get("last_active_ts", 0), reverse=True)
            online_count = sum(1 for c in active_list if c.get("status") == "online")
            idle_count = sum(1 for c in active_list if c.get("status") == "idle")

            today_active_vids = {
                c["visitor_id"]
                for c in self._active_connections.values()
                if c.get("first_seen_at", "").startswith(today_prefix)
            }

        # 持久化逾期連線（於 lock 外）
        if to_persist:
            for item in to_persist:
                self._update_db_record(item)
                self._append_jsonl_record(item)

        today_total = 0
        avg_duration = 0
        top_locations: dict[str, int] = {}

        try:
            self._init_db()
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(DISTINCT visitor_id), AVG(duration_seconds)
                FROM user_connections
                WHERE first_seen_at LIKE ?
            """,
                (f"{today_prefix}%",),
            )
            row = cur.fetchone()
            if row:
                today_total = row[0] or 0
                avg_duration = int(row[1] or 0)

            cur.execute(
                """
                SELECT location, COUNT(*) as cnt
                FROM user_connections
                WHERE first_seen_at LIKE ?
                GROUP BY location
                ORDER BY cnt DESC
            """,
                (f"{today_prefix}%",),
            )
            for loc_row in cur.fetchall():
                loc_name = loc_row["location"] or "未知"
                top_locations[loc_name] = loc_row["cnt"]
            conn.close()
        except Exception as exc:
            logger.debug("user_tracker: 統計今日連線失敗: %s", exc)

        today_total = max(today_total, len(today_active_vids), online_count + idle_count)
        if not top_locations and active_list:
            for c in active_list:
                loc = c.get("location", "未知")
                top_locations[loc] = top_locations.get(loc, 0) + 1

        # 若 DB 均值為 0（在線連線尚未結算），以活躍連線當前時長即時動態計算均值
        if avg_duration == 0 and active_list:
            avg_duration = int(sum(c["duration_seconds"] for c in active_list) / len(active_list))

        return {
            "online_count": online_count,
            "idle_count": idle_count,
            "today_total": today_total,
            "avg_duration_seconds": avg_duration,
            "top_locations": top_locations,
            "active_visitors": active_list,
        }

    def get_connection_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """讀取歷史訪客連線紀錄列表（優先讀取 SQLite，備援回退至 JSONL）。"""
        try:
            self._init_db()
            conn = get_db_connection(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT visitor_id, first_seen_at, last_active_at, duration_seconds,
                       ip, device, location, user_agent, last_path, hit_count, status, data_json
                FROM user_connections
                ORDER BY id DESC
                LIMIT ?
            """,
                (limit,),
            )
            rows = cur.fetchall()
            conn.close()

            if rows:
                results: list[dict[str, Any]] = []
                for r in rows:
                    try:
                        if r["data_json"] and r["data_json"] != "{}":
                            rec = json.loads(r["data_json"])
                        else:
                            rec = dict(r)
                        results.append(rec)
                    except Exception:
                        results.append(dict(r))
                if results:
                    return results
        except Exception as exc:
            logger.debug("user_tracker: 讀取 SQLite user_connections 失敗: %s", exc)

        return self._read_jsonl_records(limit=limit)

    def _write_db_record(self, conn: dict[str, Any]) -> None:
        """寫入單筆初始訪客連線紀錄至 SQLite。"""
        try:
            self._init_db()
            data_json = json.dumps(conn, ensure_ascii=False)
            with db_transaction(self.db_path) as cur:
                cur.execute(
                    """
                    INSERT INTO user_connections (
                        visitor_id, first_seen_at, last_active_at, duration_seconds,
                        ip, device, location, user_agent, last_path, hit_count, status, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        conn["visitor_id"],
                        conn["first_seen_at"],
                        conn["last_active_at"],
                        conn.get("duration_seconds", 0),
                        conn.get("ip", ""),
                        conn.get("device", ""),
                        conn.get("location", ""),
                        conn.get("user_agent", ""),
                        conn.get("last_path", ""),
                        conn.get("hit_count", 1),
                        conn.get("status", "online"),
                        data_json,
                    ),
                )
        except Exception as exc:
            logger.debug("user_tracker: 寫入 SQLite user_connections 失敗: %s", exc)

    def _update_db_record(self, conn: dict[str, Any]) -> None:
        """更新 SQLite 中該 visitor 最近一筆連線紀錄之狀態與時長。"""
        try:
            self._init_db()
            data_json = json.dumps(conn, ensure_ascii=False)
            with db_transaction(self.db_path) as cur:
                cur.execute(
                    """
                    UPDATE user_connections
                    SET last_active_at = ?, duration_seconds = ?, hit_count = ?,
                        status = ?, last_path = ?, data_json = ?
                    WHERE id = (
                        SELECT id FROM user_connections
                        WHERE visitor_id = ?
                        ORDER BY id DESC LIMIT 1
                    )
                """,
                    (
                        conn["last_active_at"],
                        conn.get("duration_seconds", 0),
                        conn.get("hit_count", 1),
                        conn.get("status", "offline"),
                        conn.get("last_path", ""),
                        data_json,
                        conn["visitor_id"],
                    ),
                )
        except Exception as exc:
            logger.debug("user_tracker: 更新 SQLite user_connections 失敗: %s", exc)

    def _append_jsonl_record(self, conn: dict[str, Any]) -> None:
        """追加連線紀錄至 JSONL 檔案。"""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(conn, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("user_tracker: 寫入 JSONL 失敗: %s", exc)

    def _read_jsonl_records(self, limit: int = 50) -> list[dict[str, Any]]:
        """從 JSONL 檔案反向讀取最近的連線紀錄。"""
        if not self.log_path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except Exception:
                            pass
        except Exception as e:
            logger.error("user_tracker: 讀取 JSONL 失敗: %s", e)
        records.reverse()
        return records[:limit]


user_connection_tracker = UserConnectionTracker()
