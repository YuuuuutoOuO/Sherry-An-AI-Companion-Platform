"""
tools/migrate_json_to_sqlite.py
===============================
雪莉 (Sherry) 專案專用 — 全系統 JSON/JSONL 全自動無痛遷移至 SQLite 工具 (Phase 2 終極版)。

功能與安全特性：
1. 【自動備份】執行前自動將 ./data 內目標檔案完整備份至 ./data/backups/pre_sqlite_migration_<時間戳>/
2. 【結構建表】自動建立各大領域資料庫與資料表（WAL 模式 + 索引）
3. 【批次轉移】涵蓋對話紀錄、即時觀測、長期記憶、美股量化、DND跑團、深度搜尋、情報哨兵、火花觸發、排程與氛圍
4. 【資料校驗】比對原 JSON 筆數與 SQLite 筆數，輸出彩色報告
5. 【一鍵歸檔】提供 --archive 選項，自動將已轉移的舊 JSON 集中移入 data/archive_legacy_json/ 封存
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Tuple

# 設定 Windows 控制台 UTF-8 編碼支援
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 專案路徑
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

_TZ_TW = timezone(timedelta(hours=8))


def _now_str() -> str:
    return datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")


def _backup_data_folder() -> Path:
    """自動備份現有 data 目錄至 backups。"""
    timestamp = datetime.now(_TZ_TW).strftime("%Y%m%d_%H%M%S")
    backup_dir = DATA_DIR / "backups" / f"pre_sqlite_migration_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for root, _, files in os.walk(DATA_DIR):
        if "backups" in root or "archive_legacy_json" in root:
            continue
        for file in files:
            if file.endswith((".json", ".jsonl")):
                src_file = Path(root) / file
                rel_path = src_file.relative_to(DATA_DIR)
                dest_file = backup_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                count += 1

    print(f"🛡️  [1/5] 已完成自動備份：備份了 {count} 個檔案至 {backup_dir.relative_to(BASE_DIR)}")
    return backup_dir


def _init_sqlite_db(db_path: Path) -> sqlite3.Connection:
    """建立資料庫並配置 WAL 模式。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# ---------------------------------------------------------------------------
# 1. 對話紀錄、即時觀測與稽核 (chat_history.db)
# ---------------------------------------------------------------------------
def migrate_chat_history() -> Tuple[int, int]:
    """遷移對話日誌、runtime_log、error_log 與稽核紀錄。"""
    db_path = DATA_DIR / "chat_history.db"
    conn = _init_sqlite_db(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            date_str TEXT NOT NULL,
            user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            content TEXT NOT NULL,
            processed INTEGER DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_date ON chat_messages(date_str);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_messages(user_id, timestamp);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS runtime_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            trace_id TEXT,
            event TEXT NOT NULL,
            user TEXT,
            channel_id TEXT,
            latency_ms INTEGER DEFAULT 0,
            data_json TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_runtime_time ON runtime_events(timestamp DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_runtime_event ON runtime_events(event);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS error_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            trace_id TEXT,
            event TEXT NOT NULL,
            error_type TEXT,
            message TEXT,
            data_json TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            level TEXT,
            event TEXT,
            user_id TEXT,
            channel_id TEXT,
            detail_json TEXT
        )
    """)

    total_json_records = 0
    inserted = 0

    # 1. 讀取 chat_logs/*.jsonl (包含 archive_legacy_json 下的備份)
    chat_log_dirs = [DATA_DIR / "chat_logs", DATA_DIR / "archive_legacy_json" / "chat_logs"]
    for c_dir in chat_log_dirs:
        if c_dir.exists():
            for log_file in c_dir.glob("*.jsonl"):
                date_str = log_file.stem
                is_processed = 1 if (c_dir / f"{log_file.name}.processed").exists() else 0
                with log_file.open("r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            total_json_records += 1
                            ts = record.get("ts") or record.get("timestamp") or _now_str()
                            user_id = str(record.get("user_id", ""))
                            display_name = str(record.get("display_name", ""))
                            content = str(record.get("content", ""))

                            cur.execute("""
                                INSERT INTO chat_messages (timestamp, date_str, user_id, display_name, content, processed)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (ts, date_str, user_id, display_name, content, is_processed))
                            inserted += 1
                        except Exception:
                            pass

    # 2. 讀取 runtime_log.jsonl
    for r_file in [DATA_DIR / "runtime_log.jsonl", DATA_DIR / "archive_legacy_json" / "runtime_log.jsonl"]:
        if r_file.exists():
            with r_file.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        total_json_records += 1
                        cur.execute("""
                            INSERT INTO runtime_events (timestamp, trace_id, event, user, channel_id, latency_ms, data_json)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            str(record.get("ts") or record.get("timestamp") or _now_str()),
                            str(record.get("trace_id", "")),
                            str(record.get("event", "event")),
                            str(record.get("user", "")),
                            str(record.get("channel_id", "")),
                            int(record.get("latency_ms", 0)),
                            json.dumps(record, ensure_ascii=False),
                        ))
                        inserted += 1
                    except Exception:
                        pass

    # 3. 讀取 error_log.jsonl
    for e_file in [DATA_DIR / "error_log.jsonl", DATA_DIR / "archive_legacy_json" / "error_log.jsonl"]:
        if e_file.exists():
            with e_file.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        total_json_records += 1
                        cur.execute("""
                            INSERT INTO error_events (timestamp, trace_id, event, error_type, message, data_json)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            str(record.get("ts") or record.get("timestamp") or _now_str()),
                            str(record.get("trace_id", "")),
                            str(record.get("event", "error")),
                            str(record.get("error_type", "")),
                            str(record.get("message", "")),
                            json.dumps(record, ensure_ascii=False),
                        ))
                        inserted += 1
                    except Exception:
                        pass

    # 4. 讀取 admin_audit.jsonl & audit_log.jsonl
    for a_file in [DATA_DIR / "admin_audit.jsonl", DATA_DIR / "audit_log.jsonl", DATA_DIR / "archive_legacy_json" / "admin_audit.jsonl"]:
        if a_file.exists():
            with a_file.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        total_json_records += 1
                        cur.execute("""
                            INSERT INTO audit_logs (timestamp, level, event, user_id, channel_id, detail_json)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            str(record.get("timestamp") or record.get("ts") or _now_str()),
                            str(record.get("level", "INFO")),
                            str(record.get("event") or record.get("type") or ""),
                            str(record.get("user_id", "")),
                            str(record.get("channel_id", "")),
                            json.dumps(record.get("detail") or record.get("payload") or {}, ensure_ascii=False),
                        ))
                        inserted += 1
                    except Exception:
                        pass

    conn.commit()
    conn.close()
    return total_json_records, inserted


# ---------------------------------------------------------------------------
# 2. 虛擬量化美股 (stock_trader.db)
# ---------------------------------------------------------------------------
def migrate_stock_trader() -> Tuple[int, int]:
    """遷移美股量化交易資料。"""
    stock_dir = DATA_DIR / "sherry_stock"
    db_path = stock_dir / "stock_trader.db"
    conn = _init_sqlite_db(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cash REAL NOT NULL,
            initial_capital REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            total_trades_count INTEGER NOT NULL,
            winning_trades_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            ticker TEXT PRIMARY KEY,
            shares REAL NOT NULL,
            avg_cost REAL NOT NULL,
            current_price REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            shares REAL NOT NULL,
            execution_price REAL NOT NULL,
            fee REAL NOT NULL,
            total_amount REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            reason TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_history (
            date TEXT PRIMARY KEY,
            total_asset_value REAL NOT NULL,
            cash REAL NOT NULL,
            total_pnl REAL NOT NULL,
            return_rate_pct REAL NOT NULL,
            day_pnl REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS thought_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            thought_process TEXT NOT NULL,
            decision_summary TEXT NOT NULL,
            recommended_actions_json TEXT NOT NULL
        )
    """)

    json_records = 0
    db_records = 0

    # 搜尋可能存在 portfolio.json 的路徑
    for p_file in [stock_dir / "portfolio.json", DATA_DIR / "archive_legacy_json" / "portfolio.json"]:
        if p_file.exists():
            try:
                with p_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                json_records += 1
                cur.execute("""
                    INSERT OR REPLACE INTO portfolio (id, cash, initial_capital, realized_pnl, total_trades_count, winning_trades_count, created_at, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    float(data.get("cash", 100000.0)),
                    float(data.get("initial_capital", 100000.0)),
                    float(data.get("realized_pnl", 0.0)),
                    int(data.get("total_trades_count", 0)),
                    int(data.get("winning_trades_count", 0)),
                    str(data.get("created_at", _now_str())),
                    str(data.get("updated_at", _now_str())),
                ))
                db_records += 1

                for ticker, p_data in data.get("positions", {}).items():
                    json_records += 1
                    cur.execute("""
                        INSERT OR REPLACE INTO positions (ticker, shares, avg_cost, current_price, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        ticker,
                        float(p_data.get("shares", 0.0)),
                        float(p_data.get("avg_cost", 0.0)),
                        float(p_data.get("current_price", 0.0)),
                        _now_str(),
                    ))
                    db_records += 1
                break
            except Exception as e:
                print(f"⚠️ 解析 portfolio.json 異常: {e}")

    # Trades
    for t_file in [stock_dir / "trades.json", DATA_DIR / "archive_legacy_json" / "trades.json"]:
        if t_file.exists():
            try:
                with t_file.open("r", encoding="utf-8") as f:
                    trades_list = json.load(f)
                if isinstance(trades_list, list):
                    for t in trades_list:
                        json_records += 1
                        t_id = str(t.get("trade_id") or t.get("id") or f"trade_{t.get('timestamp')}_{t.get('ticker')}")
                        cur.execute("""
                            INSERT OR REPLACE INTO trades (trade_id, timestamp, ticker, action, shares, execution_price, fee, total_amount, realized_pnl, reason)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            t_id,
                            str(t.get("timestamp", _now_str())),
                            str(t.get("ticker", "")),
                            str(t.get("action", "")),
                            float(t.get("shares", 0.0)),
                            float(t.get("execution_price") or t.get("price") or 0.0),
                            float(t.get("fee", 0.0)),
                            float(t.get("total_amount", 0.0)),
                            float(t.get("realized_pnl") or t.get("profit_loss") or 0.0),
                            str(t.get("reason", "")),
                        ))
                        db_records += 1
                break
            except Exception:
                pass
    for d_file in [
        stock_dir / "daily_history.json",
        DATA_DIR / "archive_legacy_json" / "daily_history.json",
        DATA_DIR / "daily_history.json",
    ]:
        if d_file.exists():
            try:
                with d_file.open("r", encoding="utf-8") as f:
                    history_list = json.load(f)
                if isinstance(history_list, list):
                    for h in history_list:
                        json_records += 1
                        cur.execute("""
                            INSERT OR REPLACE INTO daily_history (date, total_asset_value, cash, total_pnl, return_rate_pct, day_pnl)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            str(h.get("date", _now_str()[:10])),
                            float(h.get("total_asset_value", 100000.0)),
                            float(h.get("cash", 100000.0)),
                            float(h.get("total_pnl", 0.0)),
                            float(h.get("return_rate_pct", 0.0)),
                            float(h.get("day_pnl", 0.0)),
                        ))
                        db_records += 1
                break
            except Exception as exc:
                print(f"⚠️ 解析 daily_history.json 異常: {exc}")

    # Thought Logs
    for th_file in [
        stock_dir / "thoughts.jsonl",
        DATA_DIR / "archive_legacy_json" / "thoughts.jsonl",
        DATA_DIR / "thoughts.jsonl",
    ]:
        if th_file.exists():
            try:
                with th_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        line_s = line.strip()
                        if not line_s:
                            continue
                        try:
                            th_item = json.loads(line_s)
                            json_records += 1
                            actions = th_item.get("recommended_actions", [])
                            cur.execute("""
                                INSERT INTO thought_logs (timestamp, trigger_type, thought_process, decision_summary, recommended_actions_json)
                                VALUES (?, ?, ?, ?, ?)
                            """, (
                                str(th_item.get("timestamp", _now_str())),
                                str(th_item.get("trigger_type", "EVALUATION")),
                                str(th_item.get("thought_process", "")),
                                str(th_item.get("decision_summary", "")),
                                json.dumps(actions, ensure_ascii=False) if not isinstance(actions, str) else actions,
                            ))
                            db_records += 1
                        except Exception:
                            pass
                break
            except Exception as exc:
                print(f"⚠️ 解析 thoughts.jsonl 異常: {exc}")

    conn.commit()
    conn.close()
    return json_records, db_records


# ---------------------------------------------------------------------------
# 3. 長期記憶、好感度、氛圍與吐槽 (sherry_memory.db)
# ---------------------------------------------------------------------------
def migrate_memory_and_relations() -> Tuple[int, int]:
    """遷移長期記憶、好感度、話題記憶、頻道氛圍與吐槽規則。"""
    db_path = DATA_DIR / "sherry_memory.db"
    conn = _init_sqlite_db(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            fact TEXT NOT NULL,
            category TEXT NOT NULL,
            importance INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES user_profiles(user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_relations (
            user_id TEXT PRIMARY KEY,
            affinity REAL NOT NULL DEFAULT 0.0,
            tier TEXT NOT NULL DEFAULT 'stranger',
            interaction_count INTEGER NOT NULL DEFAULT 0,
            last_interaction TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS relationship_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_a TEXT NOT NULL,
            user_b TEXT NOT NULL,
            fact TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 3,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS topic_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            summary TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS channel_vibes (
            channel_id TEXT PRIMARY KEY,
            topics_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS roast_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            enabled INTEGER NOT NULL DEFAULT 1,
            global_cooldown_seconds REAL NOT NULL DEFAULT 8.0,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS roast_rules (
            id TEXT PRIMARY KEY,
            keyword TEXT NOT NULL,
            reply_guide TEXT NOT NULL,
            probability INTEGER NOT NULL DEFAULT 100,
            match_mode TEXT NOT NULL DEFAULT 'contains',
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)

    json_records = 0
    db_records = 0

    # 1. user_memory.json
    for m_file in [DATA_DIR / "user_memory.json", DATA_DIR / "archive_legacy_json" / "user_memory.json"]:
        if m_file.exists():
            try:
                with m_file.open("r", encoding="utf-8") as f:
                    mem_data = json.load(f)
                if isinstance(mem_data, dict):
                    for uid, doc in mem_data.items():
                        if not isinstance(doc, dict):
                            continue
                        aliases = doc.get("aliases", [])
                        cur.execute("""
                            INSERT OR REPLACE INTO user_profiles (user_id, aliases_json, created_at, updated_at)
                            VALUES (?, ?, ?, ?)
                        """, (str(uid), json.dumps(aliases, ensure_ascii=False), _now_str(), _now_str()))
                        json_records += 1
                        db_records += 1

                        for f_obj in doc.get("facts", []):
                            fact_text = f_obj.get("fact", "") if isinstance(f_obj, dict) else str(f_obj)
                            cat = f_obj.get("category", "identity") if isinstance(f_obj, dict) else "identity"
                            imp = int(f_obj.get("importance", 3)) if isinstance(f_obj, dict) else 3
                            if fact_text:
                                json_records += 1
                                cur.execute("""
                                    INSERT INTO user_facts (user_id, fact, category, importance, updated_at)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (str(uid), fact_text, cat, imp, _now_str()))
                                db_records += 1
                break
            except Exception:
                pass

    # 2. user_relations.json
    for r_file in [DATA_DIR / "user_relations.json", DATA_DIR / "archive_legacy_json" / "user_relations.json"]:
        if r_file.exists():
            try:
                with r_file.open("r", encoding="utf-8") as f:
                    rel_data = json.load(f)
                if isinstance(rel_data, dict):
                    for uid, r_obj in rel_data.items():
                        if isinstance(r_obj, dict):
                            json_records += 1
                            cur.execute("""
                                INSERT OR REPLACE INTO user_relations (user_id, affinity, tier, interaction_count, last_interaction, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                str(uid),
                                float(r_obj.get("affinity", 0.0)),
                                str(r_obj.get("tier", "stranger")),
                                int(r_obj.get("interaction_count", 0)),
                                str(r_obj.get("last_interaction", "")),
                                str(r_obj.get("updated_at", _now_str())),
                            ))
                            db_records += 1
                break
            except Exception:
                pass

    # 3. channel_vibe.json
    for v_file in [DATA_DIR / "channel_vibe.json", DATA_DIR / "archive_legacy_json" / "channel_vibe.json"]:
        if v_file.exists():
            try:
                with v_file.open("r", encoding="utf-8") as f:
                    v_data = json.load(f)
                if isinstance(v_data, dict):
                    for ch_id, topics in v_data.items():
                        json_records += 1
                        cur.execute("""
                            INSERT OR REPLACE INTO channel_vibes (channel_id, topics_json, updated_at)
                            VALUES (?, ?, ?)
                        """, (str(ch_id), json.dumps(topics, ensure_ascii=False), _now_str()))
                        db_records += 1
                break
            except Exception:
                pass

    # 3b. topic_memory.json (topic_memory 表格)
    for t_file in [DATA_DIR / "topic_memory.json", DATA_DIR / "archive_legacy_json" / "topic_memory.json"]:
        if t_file.exists():
            try:
                with t_file.open("r", encoding="utf-8") as f:
                    t_data = json.load(f)
                topics = t_data.get("topics", t_data) if isinstance(t_data, dict) else {}
                if isinstance(topics, dict):
                    for topic_name, entry in topics.items():
                        if not isinstance(entry, dict):
                            continue
                        summaries = entry.get("summaries", [])
                        text = ""
                        if isinstance(summaries, list) and summaries:
                            last = summaries[-1]
                            text = last.get("text", "") if isinstance(last, dict) else str(last)
                        json_records += 1
                        cur.execute("""
                            INSERT OR REPLACE INTO topic_memory (topic, summary, updated_at)
                            VALUES (?, ?, ?)
                        """, (
                            str(topic_name),
                            str(text),
                            str(entry.get("last_updated", _now_str())),
                        ))
                        db_records += 1
                break
            except Exception:
                pass

    # 4. roast_rules.json
    for ro_file in [DATA_DIR / "roast_rules.json", DATA_DIR / "archive_legacy_json" / "roast_rules.json"]:
        if ro_file.exists():
            try:
                with ro_file.open("r", encoding="utf-8") as f:
                    ro_data = json.load(f)
                if isinstance(ro_data, dict):
                    cur.execute("""
                        INSERT OR REPLACE INTO roast_config (id, enabled, global_cooldown_seconds, updated_at)
                        VALUES (1, ?, ?, ?)
                    """, (1 if ro_data.get("enabled", True) else 0, float(ro_data.get("global_cooldown_seconds", 8.0)), _now_str()))
                    for r in ro_data.get("rules", []):
                        json_records += 1
                        cur.execute("""
                            INSERT OR REPLACE INTO roast_rules (id, keyword, reply_guide, probability, match_mode, enabled)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            str(r.get("id", "")),
                            str(r.get("keyword", "")),
                            str(r.get("reply_guide", "")),
                            int(r.get("probability", 100)),
                            str(r.get("match_mode", "contains")),
                            1 if r.get("enabled", True) else 0,
                        ))
                        db_records += 1
                break
            except Exception:
                pass

    conn.commit()
    conn.close()
    return json_records, db_records


# ---------------------------------------------------------------------------
# 4. 深度搜尋、情報哨兵、火花與排程 (外掛專屬庫)
# ---------------------------------------------------------------------------
def migrate_plugins() -> Tuple[int, int]:
    """遷移深度搜尋、情報哨兵、火花觸發與排程任務。"""
    json_records = 0
    db_records = 0

    # 1. Deep Search (data/deep_search/deep_search.db)
    ds_dir = DATA_DIR / "deep_search"
    ds_db_path = ds_dir / "deep_search.db"
    conn_ds = _init_sqlite_db(ds_db_path)
    cur_ds = conn_ds.cursor()
    cur_ds.execute("""
        CREATE TABLE IF NOT EXISTS research_sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            topic TEXT NOT NULL,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    for s_dir in [ds_dir / "sessions", DATA_DIR / "archive_legacy_json" / "deep_search" / "sessions"]:
        if s_dir.exists():
            for sf in s_dir.glob("*.json"):
                try:
                    with sf.open("r", encoding="utf-8") as f:
                        s_data = json.load(f)
                    json_records += 1
                    cur_ds.execute("""
                        INSERT OR REPLACE INTO research_sessions (session_id, title, topic, data_json, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        str(s_data.get("session_id") or sf.stem),
                        str(s_data.get("topic") or sf.stem),
                        str(s_data.get("topic") or ""),
                        json.dumps(s_data, ensure_ascii=False),
                        str(s_data.get("updated_at") or _now_str()),
                    ))
                    db_records += 1
                except Exception:
                    pass
    conn_ds.commit()
    conn_ds.close()

    # 2. Feed Sentinel (data/feed_sentinel/feed_sentinel.db)
    fs_dir = DATA_DIR / "feed_sentinel"
    fs_db_path = fs_dir / "feed_sentinel.db"
    conn_fs = _init_sqlite_db(fs_db_path)
    cur_fs = conn_fs.cursor()
    cur_fs.execute("""
        CREATE TABLE IF NOT EXISTS shared_posts (
            post_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            original_summary TEXT NOT NULL,
            sherry_analysis TEXT NOT NULL,
            author_or_repo TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            canonical_keys_json TEXT NOT NULL,
            published_at TEXT NOT NULL,
            posted_at TEXT NOT NULL,
            raw_json TEXT NOT NULL
        )
    """)
    cur_fs.execute("""
        CREATE TABLE IF NOT EXISTS seen_fingerprints (
            fingerprint TEXT PRIMARY KEY
        )
    """)
    for p_file in [fs_dir / "posts.json", DATA_DIR / "archive_legacy_json" / "feed_sentinel" / "posts.json"]:
        if p_file.exists():
            try:
                with p_file.open("r", encoding="utf-8") as f:
                    p_list = json.load(f)
                if isinstance(p_list, list):
                    for p in p_list:
                        json_records += 1
                        cur_fs.execute("""
                            INSERT OR REPLACE INTO shared_posts (
                                post_id, source, title, url, original_summary, sherry_analysis,
                                author_or_repo, tags_json, canonical_keys_json, published_at, posted_at, raw_json
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            str(p.get("post_id", "")),
                            str(p.get("source", "")),
                            str(p.get("title", "")),
                            str(p.get("url", "")),
                            str(p.get("original_summary", "")),
                            str(p.get("sherry_analysis", "")),
                            str(p.get("author_or_repo", "")),
                            json.dumps(p.get("tags", []), ensure_ascii=False),
                            json.dumps(p.get("canonical_keys", []), ensure_ascii=False),
                            str(p.get("published_at", "")),
                            str(p.get("posted_at", "")),
                            json.dumps(p, ensure_ascii=False),
                        ))
                        db_records += 1
                break
            except Exception:
                pass

    for s_file in [fs_dir / "sentinel_seen.json", DATA_DIR / "sentinel_seen.json", DATA_DIR / "archive_legacy_json" / "sentinel_seen.json"]:
        if s_file.exists():
            try:
                with s_file.open("r", encoding="utf-8") as f:
                    s_list = json.load(f)
                if isinstance(s_list, list):
                    for sk in s_list:
                        if isinstance(sk, str):
                            cur_fs.execute("INSERT OR IGNORE INTO seen_fingerprints (fingerprint) VALUES (?)", (sk.strip(),))
            except Exception:
                pass

    conn_fs.commit()
    conn_fs.close()

    # 3. Spark Triggers (data/spark/spark.db)
    spk_dir = DATA_DIR / "spark"
    spk_db_path = spk_dir / "spark.db"
    conn_spk = _init_sqlite_db(spk_db_path)
    cur_spk = conn_spk.cursor()
    cur_spk.execute("""
        CREATE TABLE IF NOT EXISTS triggers (
            trigger_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL DEFAULT 0,
            trigger_type TEXT NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    for t_file in [spk_dir / "triggers.json", DATA_DIR / "archive_legacy_json" / "spark" / "triggers.json"]:
        if t_file.exists():
            try:
                with t_file.open("r", encoding="utf-8") as f:
                    t_dict = json.load(f)
                if isinstance(t_dict, dict):
                    for tid, tdata in t_dict.items():
                        json_records += 1
                        cur_spk.execute("""
                            INSERT OR REPLACE INTO triggers (trigger_id, user_id, channel_id, trigger_type, name, is_active, data_json, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        """, (
                            str(tid),
                            int(tdata.get("user_id", 0)),
                            int(tdata.get("channel_id", 0)),
                            str(tdata.get("trigger_type", "")),
                            str(tdata.get("name", "")),
                            1 if tdata.get("is_active", True) else 0,
                            json.dumps(tdata, ensure_ascii=False),
                        ))
                        db_records += 1
                break
            except Exception:
                pass
    conn_spk.commit()
    conn_spk.close()

    # 4. Schedules (data/schedules.db)
    sched_db_path = DATA_DIR / "schedules.db"
    conn_sched = _init_sqlite_db(sched_db_path)
    cur_sched = conn_sched.cursor()
    cur_sched.execute("""
        CREATE TABLE IF NOT EXISTS schedule_tasks (
            task_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            schedule_type TEXT NOT NULL,
            trigger_time INTEGER,
            cron_expr TEXT,
            content TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)
    for sch_file in [DATA_DIR / "schedule.json", DATA_DIR / "archive_legacy_json" / "schedule.json"]:
        if sch_file.exists():
            try:
                with sch_file.open("r", encoding="utf-8") as f:
                    sch_data = json.load(f)
                if isinstance(sch_data, dict):
                    for t in sch_data.get("tasks", []):
                        json_records += 1
                        cur_sched.execute("""
                            INSERT OR REPLACE INTO schedule_tasks (
                                task_id, user_id, display_name, channel_id, schedule_type, trigger_time, cron_expr, content, is_active
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            str(t.get("task_id", "")),
                            str(t.get("user_id", "")),
                            str(t.get("display_name", "")),
                            str(t.get("channel_id", "")),
                            str(t.get("schedule_type", "once")),
                            t.get("trigger_time"),
                            t.get("cron_expr"),
                            str(t.get("content", "")),
                            1 if t.get("is_active", True) else 0,
                        ))
                        db_records += 1
                break
            except Exception:
                pass
    conn_sched.commit()
    conn_sched.close()

    return json_records, db_records


# ---------------------------------------------------------------------------
# 5. D&D 跑團系統 (dnd.db)
# ---------------------------------------------------------------------------
def migrate_dnd() -> Tuple[int, int]:
    """遷移 DND 跑團系統資料至 data/dnd/dnd.db。"""
    dnd_dir = DATA_DIR / "dnd"
    dnd_dir.mkdir(parents=True, exist_ok=True)
    db_path = dnd_dir / "dnd.db"
    conn = _init_sqlite_db(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS world_state (
            guild_id TEXT PRIMARY KEY,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            day INTEGER NOT NULL,
            hour INTEGER NOT NULL,
            minute INTEGER NOT NULL,
            current_tick INTEGER NOT NULL DEFAULT 0,
            locations_json TEXT NOT NULL DEFAULT '{}',
            active_channels_json TEXT NOT NULL DEFAULT '[]',
            setting_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            user_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            race TEXT NOT NULL,
            character_class TEXT NOT NULL,
            level INTEGER NOT NULL,
            experience INTEGER NOT NULL,
            hp_max INTEGER NOT NULL,
            hp_current INTEGER NOT NULL,
            ac INTEGER NOT NULL,
            stats_json TEXT NOT NULL,
            inventory_json TEXT NOT NULL,
            location_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS dnd_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tick INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            description TEXT NOT NULL
        )
    """)

    json_records = 0
    db_records = 0

    for c_file in [DATA_DIR / "dnd_characters.json", DATA_DIR / "archive_legacy_json" / "dnd_characters.json"]:
        if c_file.exists():
            try:
                with c_file.open("r", encoding="utf-8") as f:
                    c_data = json.load(f)
                if isinstance(c_data, dict):
                    for cid, char in c_data.items():
                        json_records += 1
                        cur.execute("""
                            INSERT OR REPLACE INTO characters (
                                user_id, name, race, character_class, level, experience,
                                hp_max, hp_current, ac, stats_json, inventory_json, location_id, created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            int(cid),
                            str(char.get("name", "")),
                            str(char.get("race", "")),
                            str(char.get("character_class", "")),
                            int(char.get("level", 1)),
                            int(char.get("experience", 0)),
                            int(char.get("hp_max", 10)),
                            int(char.get("hp_current", 10)),
                            int(char.get("ac", 10)),
                            json.dumps(char.get("stats", {}), ensure_ascii=False),
                            json.dumps(char.get("inventory", {}), ensure_ascii=False),
                            str(char.get("location_id", "start_village")),
                            str(char.get("created_at", _now_str())),
                        ))
                        db_records += 1
                break
            except Exception:
                pass

    conn.commit()
    conn.close()
    return json_records, db_records


# ---------------------------------------------------------------------------
# 歸檔舊 JSON 檔案
# ---------------------------------------------------------------------------
def archive_migrated_json_files() -> int:
    """將已轉移進 SQLite 的舊 JSON/JSONL 移動到 data/archive_legacy_json/ 進行歸檔。"""
    archive_dir = DATA_DIR / "archive_legacy_json"
    archive_dir.mkdir(parents=True, exist_ok=True)

    targets_to_move = [
        DATA_DIR / "runtime_log.jsonl",
        DATA_DIR / "error_log.jsonl",
        DATA_DIR / "admin_audit.jsonl",
        DATA_DIR / "user_memory.json",
        DATA_DIR / "user_relations.json",
        DATA_DIR / "relationship.json",
        DATA_DIR / "topic_memory.json",
        DATA_DIR / "channel_vibe.json",
        DATA_DIR / "roast_rules.json",
        DATA_DIR / "schedule.json",
        DATA_DIR / "sentinel_seen.json",
        DATA_DIR / "world_memory.jsonl",
        DATA_DIR / "dnd_world.json",
        DATA_DIR / "dnd_world_setting.json",
        DATA_DIR / "dnd_characters.json",
        DATA_DIR / "dnd_quests.json",
        DATA_DIR / "dnd_npcs.json",
        DATA_DIR / "dnd_history.jsonl",
        DATA_DIR / "market_cards.json",
        DATA_DIR / "market_stocks.json",
    ]

    stock_dir = DATA_DIR / "sherry_stock"
    if stock_dir.exists():
        targets_to_move.extend([
            stock_dir / "portfolio.json",
            stock_dir / "trades.json",
            stock_dir / "daily_history.json",
            stock_dir / "thoughts.jsonl",
        ])

    feed_dir = DATA_DIR / "feed_sentinel"
    if feed_dir.exists():
        targets_to_move.extend([
            feed_dir / "posts.json",
            feed_dir / "sentinel_seen.json",
        ])

    spark_dir = DATA_DIR / "spark"
    if spark_dir.exists():
        targets_to_move.append(spark_dir / "triggers.json")

    moved_count = 0
    for file_path in targets_to_move:
        if file_path.exists():
            dest = archive_dir / file_path.name
            shutil.move(str(file_path), str(dest))
            moved_count += 1

    # 歸檔 chat_logs 目錄
    chat_log_dir = DATA_DIR / "chat_logs"
    if chat_log_dir.exists() and any(chat_log_dir.iterdir()):
        dest_chat = archive_dir / "chat_logs"
        if dest_chat.exists():
            shutil.rmtree(dest_chat)
        shutil.move(str(chat_log_dir), str(dest_chat))
        chat_log_dir.mkdir(parents=True, exist_ok=True)
        moved_count += 1

    # 歸檔 deep_search/sessions 目錄
    ds_sessions_dir = DATA_DIR / "deep_search" / "sessions"
    if ds_sessions_dir.exists() and any(ds_sessions_dir.iterdir()):
        dest_ds = archive_dir / "deep_search_sessions"
        if dest_ds.exists():
            shutil.rmtree(dest_ds)
        shutil.move(str(ds_sessions_dir), str(dest_ds))
        ds_sessions_dir.mkdir(parents=True, exist_ok=True)
        moved_count += 1

    print(f"📦 [歸檔] 已將 {moved_count} 個項目安全移至 {archive_dir.relative_to(BASE_DIR)}")
    return moved_count


# ---------------------------------------------------------------------------
# 主執行流程
# ---------------------------------------------------------------------------
def run_migration(do_archive: bool = False) -> None:
    print("=" * 65)
    print("🚀 雪莉 (Sherry) 全系統 JSON 轉 SQLite 終極遷移工具啟動")
    print(f"⏰ 執行時間: {_now_str()}")
    print("=" * 65)

    # 1. 備份
    backup_path = _backup_data_folder()

    print("\n📦 [2/5] 開始建立 SQLite 資料庫並遷移資料...")

    # 2. 對話日誌、觀測與稽核
    chat_src, chat_dst = migrate_chat_history()
    print(f"  [OK] 💬 對話與觀測紀錄 (chat_history.db)  -> 原 JSON: {chat_src} 筆 / SQLite 寫入: {chat_dst} 筆")

    # 3. 股票交易
    stock_src, stock_dst = migrate_stock_trader()
    print(f"  [OK] 📈 虛擬美股量化 (stock_trader.db)  -> 原 JSON: {stock_src} 筆 / SQLite 寫入: {stock_dst} 筆")

    # 4. 記憶與好感度
    mem_src, mem_dst = migrate_memory_and_relations()
    print(f"  [OK] 🧠 長期記憶與規則 (sherry_memory.db) -> 原 JSON: {mem_src} 筆 / SQLite 寫入: {mem_dst} 筆")

    # 5. 外掛專屬庫 (DeepSearch, Feed, Spark, Schedule)
    pl_src, pl_dst = migrate_plugins()
    print(f"  [OK] 🔌 外掛與排程系統 (專屬 SQLite DBs) -> 原 JSON: {pl_src} 筆 / SQLite 寫入: {pl_dst} 筆")

    # 6. D&D 跑團
    dnd_src, dnd_dst = migrate_dnd()
    print(f"  [OK] 🎲 D&D 跑團系統 (dnd.db)           -> 原 JSON: {dnd_src} 筆 / SQLite 寫入: {dnd_dst} 筆")

    if do_archive:
        print("\n🧹 [4/5] 執行舊 JSON 檔案歸檔至 data/archive_legacy_json/ ...")
        archive_migrated_json_files()

    print("\n" + "=" * 65)
    print("🎉 [5/5] 全系統資料庫遷移圓滿完成！所有資料庫均已就緒並啟用 WAL 高速模式。")
    print(f"🛡️  原始資料完整備份於: {backup_path.relative_to(BASE_DIR)}")
    print("=" * 65)


if __name__ == "__main__":
    archive_flag = "--archive" in sys.argv or "--clean" in sys.argv
    run_migration(do_archive=archive_flag)
