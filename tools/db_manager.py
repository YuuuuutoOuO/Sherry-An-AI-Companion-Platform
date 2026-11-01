"""
tools/db_manager.py
===================
雪莉 (Sherry) 獨立資料庫管理、結構驗證與遷移工具。

核心設計原則：
1. 【零補丁原則】：核心業務程式碼 (handlers/core) 嚴禁夾雜 ALTER TABLE，所有結構更新一律由此工具手動執行。
2. 【軟刪除標準】：全系統資料表 100% 標配 is_deleted, deleted_at, created_at, updated_at 審計四本柱。
3. 【安全第一】：執行任何結構遷移前，自動在 data/backups/ 建立時間戳記備份。
4. 【冪等性保證】：重複執行多次 check / migrate 不會造成資料損毀或重複新增欄位。

支援指令：
  python tools/db_manager.py check      # 檢驗現有 SQLite 資料庫與標準 Schema 之差異
  python tools/db_manager.py migrate    # 自動備份並安全補齊缺漏欄位 (包含軟刪除欄位)
  python tools/db_manager.py stats      # 顯示全系統資料表筆數與軟刪除統計
  python tools/db_manager.py cleanup    # 物理清理超過 N 天 (預設 30 天) 的軟刪除資料
  python tools/db_manager.py backup     # 加密備份全資料庫並自動輪替保留 N 份 (R7)
  python tools/db_manager.py restore    # 還原（解密）指定備份目錄 (R7)
  python tools/db_manager.py vacuum     # 執行全資料庫 VACUUM 空間整理與重組
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Sequence

# Windows 控制台編碼支援
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
BACKUP_DIR: Path = DATA_DIR / "backups"

# R7：備份加密與輪替（報告 §5.5.1）
BACKUP_KEY_ENV: str = "SHERRY_BACKUP_KEY"  # Fernet 金鑰（base64）環境變數
BACKUP_KEY_FILENAME: str = "backup.key"  # 未提供環境變數時自動產生之金鑰檔（存於 BACKUP_DIR）
MAX_BACKUPS: int = 5  # 備份輪替保留份數

_TZ_TW = timezone(timedelta(hours=8))


def _now_str() -> str:
    """取得台灣時區當前時間字串。"""
    return datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")


# ===========================================================================
# 1. 標準 Schema 定義 (Canonical Schema Definition)
# ===========================================================================

@dataclass(frozen=True)
class ColumnDef:
    """欄位定義。"""
    name: str
    type_def: str
    default: str | None = None
    not_null: bool = False
    is_primary_key: bool = False


@dataclass(frozen=True)
class TableDef:
    """資料表定義。"""
    db_relative_path: str
    table_name: str
    columns: tuple[ColumnDef, ...]
    indexes: tuple[str, ...] = ()
    description: str = ""


# 全表格標準審計與軟刪除欄位 (審計四本柱)
AUDIT_COLUMNS: tuple[ColumnDef, ...] = (
    ColumnDef("is_deleted", "INTEGER", default="0", not_null=True),
    ColumnDef("deleted_at", "TEXT", default=None, not_null=False),
    ColumnDef("created_at", "TEXT", default="(datetime('now', 'localtime'))", not_null=True),
    ColumnDef("updated_at", "TEXT", default="(datetime('now', 'localtime'))", not_null=True),
)


CANONICAL_TABLES: tuple[TableDef, ...] = (
    # -----------------------------------------------------------------------
    # A. 對話、即時觀測與審計 (chat_history.db)
    # -----------------------------------------------------------------------
    TableDef(
        db_relative_path="chat_history.db",
        table_name="chat_messages",
        description="歷史對話紀錄",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("timestamp", "TEXT", not_null=True),
            ColumnDef("date_str", "TEXT", not_null=True),
            ColumnDef("user_id", "TEXT", not_null=True),
            ColumnDef("display_name", "TEXT", not_null=True),
            ColumnDef("content", "TEXT", not_null=True),
            ColumnDef("server_id", "TEXT", default="''"),
            ColumnDef("processed", "INTEGER", default="0"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_chat_date ON chat_messages(date_str);",
            "CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_messages(user_id, timestamp);",
            "CREATE INDEX IF NOT EXISTS idx_chat_server ON chat_messages(server_id, date_str);",
            "CREATE INDEX IF NOT EXISTS idx_chat_deleted ON chat_messages(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="chat_history.db",
        table_name="runtime_events",
        description="即時觀測與互動事件",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("timestamp", "TEXT", not_null=True),
            ColumnDef("trace_id", "TEXT"),
            ColumnDef("event", "TEXT", not_null=True),
            ColumnDef("user", "TEXT"),
            ColumnDef("channel_id", "TEXT"),
            ColumnDef("latency_ms", "INTEGER", default="0"),
            ColumnDef("data_json", "TEXT", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_runtime_time ON runtime_events(timestamp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_runtime_event ON runtime_events(event);",
            "CREATE INDEX IF NOT EXISTS idx_runtime_deleted ON runtime_events(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="chat_history.db",
        table_name="error_events",
        description="異常與降級事件",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("timestamp", "TEXT", not_null=True),
            ColumnDef("trace_id", "TEXT"),
            ColumnDef("event", "TEXT", not_null=True),
            ColumnDef("error_type", "TEXT"),
            ColumnDef("message", "TEXT"),
            ColumnDef("data_json", "TEXT", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_error_time ON error_events(timestamp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_error_deleted ON error_events(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="chat_history.db",
        table_name="audit_logs",
        description="指令與安全性稽核紀錄",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("timestamp", "TEXT", not_null=True),
            ColumnDef("level", "TEXT"),
            ColumnDef("event", "TEXT"),
            ColumnDef("user_id", "TEXT"),
            ColumnDef("channel_id", "TEXT"),
            ColumnDef("detail_json", "TEXT"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_audit_deleted ON audit_logs(is_deleted);",
        ),
    ),

    # -----------------------------------------------------------------------
    # B. 認知、記憶與好感度 (sherry_memory.db)
    # -----------------------------------------------------------------------
    TableDef(
        db_relative_path="sherry_memory.db",
        table_name="user_profiles",
        description="使用者認知主表與好感度",
        columns=(
            ColumnDef("user_id", "TEXT", is_primary_key=True),
            ColumnDef("display_name", "TEXT", not_null=True),
            ColumnDef("affinity", "INTEGER", default="0", not_null=True),
            ColumnDef("interaction_count", "INTEGER", default="0", not_null=True),
            ColumnDef("last_seen", "TEXT"),
            ColumnDef("notes", "TEXT"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_user_affinity ON user_profiles(affinity DESC);",
            "CREATE INDEX IF NOT EXISTS idx_user_deleted ON user_profiles(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="sherry_memory.db",
        table_name="user_facts",
        description="使用者個別事實與記憶條目",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("user_id", "TEXT", not_null=True),
            ColumnDef("fact", "TEXT", not_null=True),
            ColumnDef("category", "TEXT", default="'general'"),
            ColumnDef("importance", "INTEGER", default="5"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_fact_user ON user_facts(user_id);",
            "CREATE INDEX IF NOT EXISTS idx_fact_deleted ON user_facts(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="sherry_memory.db",
        table_name="user_aliases",
        description="使用者綽號與稱呼映射",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("user_id", "TEXT", not_null=True),
            ColumnDef("alias", "TEXT", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_alias_user ON user_aliases(user_id);",
            "CREATE INDEX IF NOT EXISTS idx_alias_name ON user_aliases(alias);",
            "CREATE INDEX IF NOT EXISTS idx_alias_deleted ON user_aliases(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="sherry_memory.db",
        table_name="user_relations",
        description="使用者好感度與社交關係",
        columns=(
            ColumnDef("user_id", "TEXT", is_primary_key=True),
            ColumnDef("affinity", "REAL", default="0.0", not_null=True),
            ColumnDef("tier", "TEXT", default="'stranger'", not_null=True),
            ColumnDef("interaction_count", "INTEGER", default="0", not_null=True),
            ColumnDef("last_interaction", "TEXT", default="''"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_rel_affinity ON user_relations(affinity DESC);",
            "CREATE INDEX IF NOT EXISTS idx_rel_deleted ON user_relations(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="world_memory.db",
        table_name="world_events",
        description="世界記憶與重大事件紀錄",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("event_id", "TEXT", not_null=True),
            ColumnDef("server_id", "TEXT", default="''"),
            ColumnDef("ts", "TEXT", not_null=True),
            ColumnDef("date_str", "TEXT", not_null=True),
            ColumnDef("event_type", "TEXT", not_null=True),
            ColumnDef("location", "TEXT", default="''"),
            ColumnDef("summary", "TEXT", default="''"),
            ColumnDef("participants_json", "TEXT", default="'[]'"),
            ColumnDef("importance", "INTEGER", default="3"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_world_events_date ON world_events(date_str);",
            "CREATE INDEX IF NOT EXISTS idx_world_events_ts ON world_events(ts);",
            "CREATE INDEX IF NOT EXISTS idx_world_events_server ON world_events(server_id);",
            "CREATE INDEX IF NOT EXISTS idx_world_events_deleted ON world_events(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="world_memory.db",
        table_name="world_summary",
        description="世界記憶每日摘要",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("event_id", "TEXT", not_null=True),
            ColumnDef("server_id", "TEXT", default="''"),
            ColumnDef("ts", "TEXT", not_null=True),
            ColumnDef("event_type", "TEXT", not_null=True),
            ColumnDef("location", "TEXT", default="''"),
            ColumnDef("summary", "TEXT", default="''"),
            ColumnDef("importance", "INTEGER", default="3"),
            ColumnDef("participants_json", "TEXT", default="'[]'"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_world_summary_ts ON world_summary(ts);",
            "CREATE INDEX IF NOT EXISTS idx_world_summary_server ON world_summary(server_id);",
            "CREATE INDEX IF NOT EXISTS idx_world_summary_deleted ON world_summary(is_deleted);",
        ),
    ),

    # -----------------------------------------------------------------------
    # C. 火花排程、條件觸發與排程任務 (spark/spark.db & schedules.db)
    # -----------------------------------------------------------------------
    TableDef(
        db_relative_path="spark/spark.db",
        table_name="triggers",
        description="SPARK 條件觸發規則表",
        columns=(
            ColumnDef("id", "TEXT", is_primary_key=True),
            ColumnDef("name", "TEXT", not_null=True),
            ColumnDef("condition_type", "TEXT", not_null=True),
            ColumnDef("condition_config", "TEXT", not_null=True),
            ColumnDef("action_type", "TEXT", not_null=True),
            ColumnDef("action_config", "TEXT", not_null=True),
            ColumnDef("is_active", "INTEGER", default="1", not_null=True),
            ColumnDef("last_triggered_at", "TEXT"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_trigger_active ON triggers(is_active);",
            "CREATE INDEX IF NOT EXISTS idx_trigger_deleted ON triggers(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="schedules.db",
        table_name="schedules",
        description="定時提醒與週期排程表",
        columns=(
            ColumnDef("id", "TEXT", is_primary_key=True),
            ColumnDef("guild_id", "TEXT", not_null=True),
            ColumnDef("channel_id", "TEXT", not_null=True),
            ColumnDef("user_id", "TEXT", not_null=True),
            ColumnDef("message", "TEXT", not_null=True),
            ColumnDef("cron_expression", "TEXT"),
            ColumnDef("run_at", "TEXT"),
            ColumnDef("is_recurring", "INTEGER", default="0", not_null=True),
            ColumnDef("is_active", "INTEGER", default="1", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_sched_run ON schedules(run_at);",
            "CREATE INDEX IF NOT EXISTS idx_sched_active ON schedules(is_active);",
            "CREATE INDEX IF NOT EXISTS idx_sched_deleted ON schedules(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="schedules.db",
        table_name="schedule_tasks",
        description="排程提醒主表（handlers/schedule_storage.py；R7 納入標準軟刪治理）",
        columns=(
            ColumnDef("task_id", "TEXT", is_primary_key=True),
            ColumnDef("user_id", "TEXT", not_null=True),
            ColumnDef("display_name", "TEXT", not_null=True),
            ColumnDef("channel_id", "TEXT", not_null=True),
            ColumnDef("schedule_type", "TEXT", not_null=True),
            ColumnDef("trigger_time", "INTEGER"),
            ColumnDef("cron_expr", "TEXT"),
            ColumnDef("content", "TEXT", not_null=True),
            ColumnDef("is_active", "INTEGER", default="1", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_sched_user ON schedule_tasks(user_id);",
            "CREATE INDEX IF NOT EXISTS idx_sched_trigger ON schedule_tasks(trigger_time);",
            "CREATE INDEX IF NOT EXISTS idx_sched_tasks_deleted ON schedule_tasks(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="chat_history.db",
        table_name="roast_rules",
        description="吐槽與自訂互動規則表",
        columns=(
            ColumnDef("id", "TEXT", is_primary_key=True),
            ColumnDef("guild_id", "TEXT", default="'global'"),
            ColumnDef("keyword", "TEXT", not_null=True),
            ColumnDef("reply_guide", "TEXT", not_null=True),
            ColumnDef("probability", "INTEGER", default="100"),
            ColumnDef("match_mode", "TEXT", default="'contains'"),
            ColumnDef("enabled", "INTEGER", default="1", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_roast_guild ON roast_rules(guild_id);",
            "CREATE INDEX IF NOT EXISTS idx_roast_deleted ON roast_rules(is_deleted);",
        ),
    ),

    # -----------------------------------------------------------------------
    # D. 外掛模組資料庫 (Stock Trader / DND / Feed Sentinel)
    # -----------------------------------------------------------------------
    TableDef(
        db_relative_path="sherry_stock/stock_trader.db",
        table_name="user_portfolios",
        description="美股量化虛擬帳戶與持股",
        columns=(
            ColumnDef("user_id", "TEXT", is_primary_key=True),
            ColumnDef("cash_balance", "REAL", default="100000.0", not_null=True),
            ColumnDef("total_realized_pnl", "REAL", default="0.0", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_portfolio_deleted ON user_portfolios(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="sherry_stock/stock_trader.db",
        table_name="stock_positions",
        description="美股持倉明細",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("user_id", "TEXT", not_null=True),
            ColumnDef("symbol", "TEXT", not_null=True),
            ColumnDef("shares", "REAL", not_null=True),
            ColumnDef("avg_cost", "REAL", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_pos_user_sym ON stock_positions(user_id, symbol);",
            "CREATE INDEX IF NOT EXISTS idx_pos_deleted ON stock_positions(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="feed_sentinel/feed_sentinel.db",
        table_name="subscriptions",
        description="社群與情報哨兵訂閱表",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("guild_id", "TEXT", not_null=True),
            ColumnDef("channel_id", "TEXT", not_null=True),
            ColumnDef("feed_url", "TEXT", not_null=True),
            ColumnDef("feed_type", "TEXT", not_null=True),
            ColumnDef("is_active", "INTEGER", default="1", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_feed_active ON subscriptions(is_active);",
            "CREATE INDEX IF NOT EXISTS idx_feed_deleted ON subscriptions(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="dnd/dnd.db",
        table_name="dnd_characters",
        description="DND 跑團角色卡",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("user_id", "TEXT", not_null=True),
            ColumnDef("name", "TEXT", not_null=True),
            ColumnDef("character_class", "TEXT", not_null=True),
            ColumnDef("level", "INTEGER", default="1", not_null=True),
            ColumnDef("hp", "INTEGER", default="10", not_null=True),
            ColumnDef("max_hp", "INTEGER", default="10", not_null=True),
            ColumnDef("stats_json", "TEXT", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_dnd_user ON dnd_characters(user_id);",
            "CREATE INDEX IF NOT EXISTS idx_dnd_deleted ON dnd_characters(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="market_stock_bot.db",
        table_name="stock_predictions",
        description="美股 AI 預測紀錄",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("date", "TEXT"),
            ColumnDef("ticker", "TEXT"),
            ColumnDef("company_name", "TEXT"),
            ColumnDef("ai_score", "INTEGER"),
            ColumnDef("reason", "TEXT"),
            ColumnDef("open_price", "REAL"),
            ColumnDef("predicted_price", "REAL"),
            ColumnDef("actual_close", "REAL"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_stock_ticker_date ON stock_predictions(ticker, date ASC);",
            "CREATE INDEX IF NOT EXISTS idx_stock_date_ticker ON stock_predictions(date, ticker);",
            "CREATE INDEX IF NOT EXISTS idx_stock_pred_deleted ON stock_predictions(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="market_stock_bot.db",
        table_name="crypto_predictions",
        description="加密貨幣 AI 預測紀錄",
        columns=(
            ColumnDef("id", "INTEGER", is_primary_key=True),
            ColumnDef("date", "TEXT"),
            ColumnDef("ticker", "TEXT"),
            ColumnDef("ai_score", "INTEGER"),
            ColumnDef("reason", "TEXT"),
            ColumnDef("current_price", "REAL"),
            ColumnDef("predicted_price", "REAL"),
            ColumnDef("actual_price", "REAL"),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_crypto_ticker_date ON crypto_predictions(ticker, date ASC);",
            "CREATE INDEX IF NOT EXISTS idx_crypto_date_ticker ON crypto_predictions(date, ticker);",
            "CREATE INDEX IF NOT EXISTS idx_crypto_pred_deleted ON crypto_predictions(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="market_stock_bot.db",
        table_name="stock_watchlists",
        description="股票與加密貨幣追蹤清單",
        columns=(
            ColumnDef("list_type", "TEXT", is_primary_key=True),
            ColumnDef("tickers_json", "TEXT", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_watchlists_deleted ON stock_watchlists(is_deleted);",
        ),
    ),
    TableDef(
        db_relative_path="market_stock_bot.db",
        table_name="card_watchlist",
        description="PTCG 卡牌行情追蹤清單",
        columns=(
            ColumnDef("name", "TEXT", is_primary_key=True),
            ColumnDef("url", "TEXT", not_null=True),
            ColumnDef("last_prices_json", "TEXT", default="'{}'", not_null=True),
        ) + AUDIT_COLUMNS,
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_cards_deleted ON card_watchlist(is_deleted);",
        ),
    ),
)


# ===========================================================================
# 2. 核心功能實作 (Inspection, Migration, Cleanup, Vacuum)
# ===========================================================================

def get_db_connection(db_path: Path) -> sqlite3.Connection:
    """建立資料庫連線並啟用 WAL 模式。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _get_backup_key() -> bytes | None:
    """取得備份加密金鑰（Fernet，base64）。

    優先序：
      1. 環境變數 `SHERRY_BACKUP_KEY`（正式部署建議以秘密管理器注入）。
      2. `data/backups/backup.key`（首次使用自動產生，權限盡可能收斂）。
      3. 若環境缺少 `cryptography` 套件則回傳 None（降級為明文備份並警告）。
    """
    env_key = os.environ.get(BACKUP_KEY_ENV, "").strip()
    if env_key:
        return env_key.encode("utf-8")

    key_file = BACKUP_DIR / BACKUP_KEY_FILENAME
    if key_file.exists():
        try:
            stored = key_file.read_bytes().strip()
            if stored:
                return stored
        except OSError:
            return None

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("⚠️  [備份] 缺少 cryptography 套件，本次備份將以明文保存（請安裝 cryptography）。")
        return None

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_file.write_bytes(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        print(f"🔑 [備份] 已產生備份加密金鑰: {key_file}")
        return key
    except OSError:
        return None


def _rotate_backups(keep: int = MAX_BACKUPS) -> int:
    """R7：備份輪替——僅保留最新 N 份 `manual_db_backup_*`（報告 §5.5.1）。"""
    if keep <= 0:
        return 0
    dirs = sorted(BACKUP_DIR.glob("manual_db_backup_*"), reverse=True)
    removed = 0
    for old_dir in dirs[keep:]:
        if not old_dir.is_dir():
            continue
        try:
            shutil.rmtree(old_dir)
            removed += 1
        except OSError as exc:
            print(f"⚠️  [備份] 輪替刪除失敗 ({old_dir}): {exc}")
    if removed:
        print(f"♻️  [備份] 輪替完成，移除 {removed} 份過期備份（保留上限 {keep} 份）。")
    return removed


def backup_all_databases(keep: int = MAX_BACKUPS) -> Path:
    """自動備份目前所有資料庫至 data/backups/（R7：加密＋輪替）。

    備份檔以 Fernet 加密為 `<db>.enc`（不落明文 DB）；完成後自動輪替，
    僅保留最新 `keep` 份備份。
    """
    timestamp = datetime.now(_TZ_TW).strftime("%Y%m%d_%H%M%S")
    target_backup_dir = BACKUP_DIR / f"manual_db_backup_{timestamp}"
    target_backup_dir.mkdir(parents=True, exist_ok=True)

    key = _get_backup_key()
    fernet = None
    if key is not None:
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            fernet = None
        else:
            fernet = Fernet(key)

    copied = 0
    unique_dbs = {t.db_relative_path for t in CANONICAL_TABLES}
    for rel_path in unique_dbs:
        src = DATA_DIR / rel_path
        if src.exists():
            dest = target_backup_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if fernet is not None:
                encrypted = fernet.encrypt(src.read_bytes())
                dest = dest.with_name(dest.name + ".enc")
                dest.write_bytes(encrypted)
            else:
                shutil.copy2(src, dest)
            copied += 1

    try:
        rel_display = target_backup_dir.relative_to(BASE_DIR)
    except ValueError:
        rel_display = target_backup_dir

    encrypted_note = "（已加密）" if fernet is not None else "（明文）"
    print(f"🛡️  [備份完成] 已備份 {copied} 個資料庫檔案至: {rel_display} {encrypted_note}")
    _rotate_backups(keep=keep)
    return target_backup_dir


def restore_backup(
    backup_dir: Path | str,
    target_dir: Path | str | None = None,
    key: bytes | None = None,
) -> int:
    """R7：還原（解密）一份備份至目標目錄（報告 §5.5.1）。

    Args:
        backup_dir: `backup_all_databases` 產生的備份目錄。
        target_dir: 還原目標（預設 DATA_DIR）。
        key: 覆寫金鑰（預設走 `SHERRY_BACKUP_KEY` / `backup.key`）。

    Returns:
        int: 還原的檔案數。
    """
    src_dir = Path(backup_dir)
    dest_root = Path(target_dir) if target_dir is not None else DATA_DIR

    fernet = None
    enc_files = list(src_dir.rglob("*.enc"))
    if enc_files:
        resolved_key = key or _get_backup_key()
        if resolved_key is None:
            raise RuntimeError("備份為加密檔但找不到金鑰（SHERRY_BACKUP_KEY 或 backup.key）")
        from cryptography.fernet import Fernet

        fernet = Fernet(resolved_key)

    restored = 0
    for src in src_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(src_dir)
        if src.suffix == ".enc" and fernet is not None:
            dest = dest_root / rel.with_suffix("")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(fernet.decrypt(src.read_bytes()))
            restored += 1
        elif src.suffix != ".enc":
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            restored += 1
    return restored


def check_schema() -> dict[str, list[str]]:
    """比對現有 SQLite 資料庫與標準 Schema 之差異。

    Returns:
        dict[str, list[str]]: 每個資料庫/表格的差異清單。
    """
    diff_report: dict[str, list[str]] = {}

    for t_def in CANONICAL_TABLES:
        db_path = DATA_DIR / t_def.db_relative_path
        key = f"[{t_def.db_relative_path}] -> {t_def.table_name}"
        diffs: list[str] = []

        if not db_path.exists():
            diffs.append("❌ 資料庫檔案不存在 (尚未建立)")
            diff_report[key] = diffs
            continue

        conn = get_db_connection(db_path)
        cur = conn.cursor()

        # 1. 檢查表格是否存在
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (t_def.table_name,),
        )
        if not cur.fetchone():
            diffs.append("❌ 資料表不存在")
            diff_report[key] = diffs
            conn.close()
            continue

        # 2. 檢查欄位清單
        cur.execute(f"PRAGMA table_info({t_def.table_name});")
        existing_cols = {row["name"]: row for row in cur.fetchall()}

        for col in t_def.columns:
            if col.name not in existing_cols:
                diffs.append(f"⚠️  缺少欄位: {col.name} ({col.type_def})")

        conn.close()
        if diffs:
            diff_report[key] = diffs

    return diff_report


def run_migration() -> int:
    """執行安全結構遷移，自動補齊缺少之表格與欄位。

    Returns:
        int: 成功遷移/更新的項目數。
    """
    print("\n" + "=" * 60)
    print("🚀 開始執行全系統 SQLite 結構升級與軟刪除遷移")
    print("=" * 60)

    # 1. 第一步先備份
    backup_all_databases()

    changes_count = 0

    for t_def in CANONICAL_TABLES:
        db_path = DATA_DIR / t_def.db_relative_path
        conn = get_db_connection(db_path)
        cur = conn.cursor()

        # 1. 檢查並建立不存在的表格
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (t_def.table_name,),
        )
        if not cur.fetchone():
            col_clauses: list[str] = []
            for col in t_def.columns:
                clause = f"{col.name} {col.type_def}"
                if col.is_primary_key:
                    if col.type_def.upper() == "INTEGER":
                        clause += " PRIMARY KEY AUTOINCREMENT"
                    else:
                        clause += " PRIMARY KEY"
                if col.not_null and not col.is_primary_key:
                    clause += " NOT NULL"
                if col.default is not None:
                    clause += f" DEFAULT {col.default}"
                col_clauses.append(clause)

            create_sql = f"CREATE TABLE {t_def.table_name} (\n  " + ",\n  ".join(col_clauses) + "\n);"
            cur.execute(create_sql)
            print(f"  ✨ [新建資料表] {t_def.db_relative_path} -> {t_def.table_name}")
            changes_count += 1
        else:
            # 2. 若表格已存在，安全補齊缺少欄位
            cur.execute(f"PRAGMA table_info({t_def.table_name});")
            existing_cols = {row["name"]: row for row in cur.fetchall()}

            for col in t_def.columns:
                if col.name not in existing_cols:
                    col_default = col.default
                    has_dynamic_expr = False
                    if col_default is not None and "(" in col_default:
                        col_default = None
                        has_dynamic_expr = True

                    alter_clause = f"ALTER TABLE {t_def.table_name} ADD COLUMN {col.name} {col.type_def}"
                    if col_default is not None:
                        alter_clause += f" DEFAULT {col_default}"
                    elif col.not_null and not has_dynamic_expr:
                        # SQLite ADD COLUMN NOT NULL 必須有固定預設值
                        alter_clause += " DEFAULT 0"

                    cur.execute(alter_clause)
                    if has_dynamic_expr:
                        cur.execute(f"UPDATE {t_def.table_name} SET {col.name} = datetime('now', 'localtime') WHERE {col.name} IS NULL;")
                    print(f"  ➕ [補齊欄位] {t_def.table_name}.{col.name} ({col.type_def})")
                    changes_count += 1

        # 3. 建立對應索引
        for idx_sql in t_def.indexes:
            try:
                cur.execute(idx_sql)
            except Exception as exc:
                print(f"  ⚠️  建立索引警告 ({t_def.table_name}): {exc}")

        conn.commit()
        conn.close()

    print("=" * 60)
    print(f"✅ 結構遷移完成！共執行 {changes_count} 項結構更新。")
    print("=" * 60 + "\n")
    return changes_count


def show_stats() -> None:
    """顯示全系統資料表之有效與軟刪除筆數統計。"""
    print("\n" + "=" * 70)
    print("📊 雪莉資料庫健康與軟刪除狀態報告")
    print("=" * 70)
    print(f"{'資料庫路徑 / 資料表':<35} | {'有效筆數':<10} | {'軟刪除筆數':<10} | {'狀態'}")
    print("-" * 70)

    for t_def in CANONICAL_TABLES:
        db_path = DATA_DIR / t_def.db_relative_path
        table_key = f"{t_def.table_name}"
        if not db_path.exists():
            print(f"{t_def.db_relative_path + ' (' + table_key + ')':<35} | {'--':<10} | {'--':<10} | ❌ 未建立")
            continue

        conn = get_db_connection(db_path)
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t_def.table_name} WHERE is_deleted = 0;")
            active_cnt = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {t_def.table_name} WHERE is_deleted = 1;")
            deleted_cnt = cur.fetchone()[0]
            status = "🟢 正常" if deleted_cnt == 0 else f"🟡 待清理 ({deleted_cnt})"
            display_name = f"{t_def.db_relative_path.split('/')[-1]}:{table_key}"
            print(f"{display_name:<35} | {active_cnt:<10} | {deleted_cnt:<10} | {status}")
        except sqlite3.OperationalError:
            print(f"{t_def.table_name:<35} | {'--':<10} | {'--':<10} | ⚠️  缺少軟刪除欄位")
        finally:
            conn.close()

    print("=" * 70 + "\n")


def cleanup_soft_deleted(days: int = 30) -> int:
    """手動物理清理超過指定天數的軟刪除資料。

    Args:
        days: 軟刪除保留天數 (預設 30 天)。

    Returns:
        int: 物理刪除的總筆數。
    """
    threshold_dt = datetime.now(_TZ_TW) - timedelta(days=days)
    threshold_str = threshold_dt.strftime("%Y-%m-%d %H:%M:%S")

    print(f"🧹 開始物理清理超過 {days} 天前 ({threshold_str} 之前) 標記軟刪除的資料...")
    backup_all_databases()

    total_purged = 0
    for t_def in CANONICAL_TABLES:
        db_path = DATA_DIR / t_def.db_relative_path
        if not db_path.exists():
            continue

        conn = get_db_connection(db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                f"DELETE FROM {t_def.table_name} WHERE is_deleted = 1 "
                f"AND (deleted_at IS NULL OR REPLACE(SUBSTR(deleted_at, 1, 19), 'T', ' ') <= ?);",
                (threshold_str,),
            )
            purged = cur.rowcount
            if purged > 0:
                print(f"  🗑️  已物理刪除 {t_def.table_name}: {purged} 筆過期資料")
                total_purged += purged
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    print(f"✅ 清理完成！共清除 {total_purged} 筆軟刪除過期資料。\n")
    return total_purged


def import_legacy_json() -> dict[str, int]:
    """將現有殘留之 user_memory.json、roast_rules.json 等舊 JSON 檔案安全匯入至對應 SQLite 資料表。

    Returns:
        dict[str, int]: 各項目成功匯入之筆數統計。
    """
    print("\n" + "=" * 60)
    print("📦 開始將舊有 JSON 檔案匯入至標準 SQLite 資料庫 (單向遷移)")
    print("=" * 60)

    # 1. 確保最新表格結構已就緒
    run_migration()

    imported_stats: dict[str, int] = {}

    # -----------------------------------------------------------------------
    # 1. 匯入 user_memory.json -> sherry_memory.db
    # -----------------------------------------------------------------------
    user_mem_path = DATA_DIR / "user_memory.json"
    if user_mem_path.exists():
        try:
            mem_data = json.loads(user_mem_path.read_text(encoding="utf-8"))
            conn = get_db_connection(DATA_DIR / "sherry_memory.db")
            cur = conn.cursor()

            p_count = 0
            f_count = 0
            a_count = 0

            for user_id_str, u_info in mem_data.items():
                if not isinstance(u_info, dict):
                    continue

                aliases = u_info.get("aliases", [])
                primary_name = aliases[0] if aliases else user_id_str

                # 插入/更新 user_profiles
                cur.execute("""
                    INSERT INTO user_profiles (user_id, display_name, affinity, is_deleted, created_at, updated_at)
                    VALUES (?, ?, 0, 0, datetime('now', 'localtime'), datetime('now', 'localtime'))
                    ON CONFLICT(user_id) DO UPDATE SET display_name = excluded.display_name, updated_at = datetime('now', 'localtime');
                """, (user_id_str, primary_name))
                p_count += 1

                # 插入 user_aliases
                for alias in aliases:
                    cur.execute("""
                        INSERT INTO user_aliases (user_id, alias, is_deleted, created_at, updated_at)
                        VALUES (?, ?, 0, datetime('now', 'localtime'), datetime('now', 'localtime'));
                    """, (user_id_str, str(alias)))
                    a_count += 1

                # 插入 user_facts
                facts = u_info.get("facts", [])
                for fact_item in facts:
                    if isinstance(fact_item, dict):
                        f_text = fact_item.get("fact", "")
                        cat = fact_item.get("category", "general")
                        imp = int(fact_item.get("importance", 5))
                    else:
                        f_text = str(fact_item)
                        cat = "general"
                        imp = 5

                    if f_text:
                        cur.execute("""
                            INSERT INTO user_facts (user_id, fact, category, importance, is_deleted, created_at, updated_at)
                            VALUES (?, ?, ?, ?, 0, datetime('now', 'localtime'), datetime('now', 'localtime'));
                        """, (user_id_str, f_text, cat, imp))
                        f_count += 1

            conn.commit()
            conn.close()
            imported_stats["user_profiles"] = p_count
            imported_stats["user_aliases"] = a_count
            imported_stats["user_facts"] = f_count
            print(f"  👤 [user_memory.json] 匯入成功：{p_count} 位用戶畫像、{a_count} 筆暱稱、{f_count} 條記憶事實")
        except Exception as exc:
            print(f"  ❌ 匯入 user_memory.json 失敗: {exc}")

    # -----------------------------------------------------------------------
    # 2. 匯入 roast_rules.json -> chat_history.db (roast_rules)
    # -----------------------------------------------------------------------
    roast_path = DATA_DIR / "roast_rules.json"
    if roast_path.exists():
        try:
            r_data = json.loads(roast_path.read_text(encoding="utf-8"))
            rules_list = r_data.get("rules", []) if isinstance(r_data, dict) else []
            conn = get_db_connection(DATA_DIR / "chat_history.db")
            cur = conn.cursor()

            r_count = 0
            for r in rules_list:
                r_id = str(r.get("id") or f"roast_{r_count}")
                kw = r.get("keyword") or r.get("pattern", "")
                reply = r.get("reply_guide") or r.get("reply", "")
                prob = int(r.get("probability", 100))
                mode = str(r.get("match_mode", "contains"))
                enabled = 1 if r.get("enabled", True) else 0

                if kw and reply:
                    cur.execute("""
                        INSERT INTO roast_rules (id, guild_id, keyword, reply_guide, probability, match_mode, enabled, is_deleted, created_at, updated_at)
                        VALUES (?, 'global', ?, ?, ?, ?, ?, 0, datetime('now', 'localtime'), datetime('now', 'localtime'))
                        ON CONFLICT(id) DO UPDATE SET
                            keyword = excluded.keyword,
                            reply_guide = excluded.reply_guide,
                            probability = excluded.probability,
                            match_mode = excluded.match_mode,
                            enabled = excluded.enabled,
                            updated_at = datetime('now', 'localtime');
                    """, (r_id, kw, reply, prob, mode, enabled))
                    r_count += 1

            conn.commit()
            conn.close()
            imported_stats["roast_rules"] = r_count
            print(f"  🔥 [roast_rules.json] 匯入成功：{r_count} 條自訂吐槽規則")
        except Exception as exc:
            print(f"  ❌ 匯入 roast_rules.json 失敗: {exc}")

    # -----------------------------------------------------------------------
    # 3. 匯入 triggers.json -> spark/spark.db (triggers)
    # -----------------------------------------------------------------------
    trigger_paths = [DATA_DIR / "spark" / "triggers.json", DATA_DIR / "triggers.json"]
    for t_path in trigger_paths:
        if t_path.exists():
            try:
                t_data = json.loads(t_path.read_text(encoding="utf-8"))
                if isinstance(t_data, list):
                    conn = get_db_connection(DATA_DIR / "spark/spark.db")
                    cur = conn.cursor()
                    t_count = 0
                    for item in t_data:
                        t_id = str(item.get("id", f"trg_{t_count}"))
                        name = str(item.get("name", t_id))
                        c_type = str(item.get("condition_type", "cron"))
                        c_cfg = json.dumps(item.get("condition_config", {}), ensure_ascii=False)
                        a_type = str(item.get("action_type", "message"))
                        a_cfg = json.dumps(item.get("action_config", {}), ensure_ascii=False)
                        active = 1 if item.get("is_active", True) else 0

                        cur.execute("""
                            INSERT INTO triggers (id, name, condition_type, condition_config, action_type, action_config, is_active, is_deleted)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                            ON CONFLICT(id) DO NOTHING;
                        """, (t_id, name, c_type, c_cfg, a_type, a_cfg, active))
                        t_count += 1

                    conn.commit()
                    conn.close()
                    imported_stats["spark_triggers"] = t_count
                    print(f"  ⚡ [{t_path.name}] 匯入成功：{t_count} 條 SPARK 觸發規則")
            except Exception as exc:
                print(f"  ❌ 匯入 {t_path.name} 失敗: {exc}")

    # -----------------------------------------------------------------------
    # 4. 匯入 user_relations.json -> sherry_memory.db (user_relations)
    # -----------------------------------------------------------------------
    rel_path = DATA_DIR / "user_relations.json"
    if rel_path.exists():
        try:
            rel_data = json.loads(rel_path.read_text(encoding="utf-8"))
            if isinstance(rel_data, dict):
                conn = get_db_connection(DATA_DIR / "sherry_memory.db")
                cur = conn.cursor()
                rel_count = 0
                for uid, r_obj in rel_data.items():
                    if isinstance(r_obj, dict):
                        aff = float(r_obj.get("affinity", 0.0))
                        tier = str(r_obj.get("tier", "stranger"))
                        ic = int(r_obj.get("interaction_count", 0))
                        last_i = str(r_obj.get("last_interaction", ""))
                        upd = str(r_obj.get("updated_at", ""))

                        cur.execute("""
                            INSERT INTO user_relations (user_id, affinity, tier, interaction_count, last_interaction, updated_at, is_deleted, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now', 'localtime'))
                            ON CONFLICT(user_id) DO UPDATE SET
                                affinity = excluded.affinity,
                                tier = excluded.tier,
                                interaction_count = excluded.interaction_count,
                                last_interaction = excluded.last_interaction,
                                updated_at = excluded.updated_at,
                                is_deleted = 0;
                        """, (str(uid), aff, tier, ic, last_i, upd))
                        rel_count += 1

                conn.commit()
                conn.close()
                imported_stats["user_relations"] = rel_count
                print(f"  ❤️ [user_relations.json] 匯入成功：{rel_count} 筆好感度與社交關係")
        except Exception as exc:
            print(f"  ❌ 匯入 user_relations.json 失敗: {exc}")

    print("=" * 60)
    print("🎉 舊 JSON 資料單向匯入完畢！已全部寫入標準 SQLite 資料庫。")
    print("=" * 60 + "\n")
    return imported_stats


def vacuum_all() -> None:
    """對全系統所有 SQLite 資料庫執行 VACUUM 壓縮與重組。"""
    print("📦 正在對全系統 SQLite 資料庫執行 VACUUM 重組與空間壓縮...")
    unique_dbs = {t.db_relative_path for t in CANONICAL_TABLES}
    for rel_path in sorted(unique_dbs):
        db_path = DATA_DIR / rel_path
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            conn.execute("VACUUM;")
            conn.close()
            print(f"  ✨ VACUUM 完成: {rel_path}")
    print("✅ 全系統資料庫重組完畢！\n")


# ===========================================================================
# 3. CLI 主進入點
# ===========================================================================

def main(args: Sequence[str] | None = None) -> int:
    """CLI 進入點。"""
    parser = argparse.ArgumentParser(
        description="雪莉 (Sherry) 獨立資料庫管理與結構遷移工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="欲執行的維護指令")

    # check
    subparsers.add_parser("check", help="檢驗現有資料庫結構與缺少欄位")

    # migrate
    subparsers.add_parser("migrate", help="自動備份並安全升級補齊缺少欄位 (包含軟刪除)")

    # stats
    subparsers.add_parser("stats", help="顯示各資料表筆數與軟刪除狀態")

    # import-legacy
    subparsers.add_parser("import-legacy", help="一次性將 user_memory.json / roast_rules.json 等舊檔洗入 SQL")

    # cleanup
    cleanup_parser = subparsers.add_parser("cleanup", help="物理清理已軟刪除之過期資料")
    cleanup_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="保留天數，超過此天數的軟刪除記錄將被永久抹除 (預設: 30)",
    )

    # backup（R7：加密＋輪替）
    backup_parser = subparsers.add_parser("backup", help="加密備份全資料庫並自動輪替")
    backup_parser.add_argument(
        "--keep",
        type=int,
        default=MAX_BACKUPS,
        help=f"備份輪替保留份數 (預設: {MAX_BACKUPS})",
    )

    # restore（R7：解密還原）
    restore_parser = subparsers.add_parser("restore", help="還原（解密）指定備份目錄")
    restore_parser.add_argument("backup_dir", help="備份目錄路徑 (例如 data/backups/manual_db_backup_*)")
    restore_parser.add_argument(
        "--target",
        default=None,
        help="還原目標目錄 (預設: data/)",
    )

    # vacuum
    subparsers.add_parser("vacuum", help="重組與壓縮所有資料庫檔案")

    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        return 0

    if parsed.command == "check":
        print("\n🔍 正在執行全系統資料庫結構體檢...")
        diffs = check_schema()
        if not diffs:
            print("🎉 恭喜！全系統 SQLite 結構與標準 Schema 100% 吻合，所有軟刪除欄位已就緒！\n")
        else:
            print("⚠️  發現以下結構差異，建議執行 `python tools/db_manager.py migrate` 進行修正：")
            for table_key, issues in diffs.items():
                print(f"\n📁 {table_key}:")
                for issue in issues:
                    print(f"   {issue}")
            print()
    elif parsed.command == "migrate":
        run_migration()
    elif parsed.command == "import-legacy":
        import_legacy_json()
    elif parsed.command == "stats":
        show_stats()
    elif parsed.command == "cleanup":
        cleanup_soft_deleted(days=parsed.days)
    elif parsed.command == "backup":
        backup_all_databases(keep=parsed.keep)
    elif parsed.command == "restore":
        restored = restore_backup(parsed.backup_dir, target_dir=parsed.target)
        print(f"✅ [還原完成] 已還原 {restored} 個檔案至: {parsed.target or DATA_DIR}")
    elif parsed.command == "vacuum":
        vacuum_all()

    return 0


if __name__ == "__main__":
    sys.exit(main())
