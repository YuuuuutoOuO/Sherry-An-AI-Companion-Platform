"""
repository.py  (mod/market_plugin)
==================================
AI 股票預測紀錄的 SQLite 資料庫存取層 (Repository Layer)。
移植自 DC_bot_StockAnalysis-main/modules/database.py，
資料庫路徑由模組獨立的 config.py 決定（存放於主專案 ./data/ 目錄）。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any

from mod.market_plugin import config as cfg

logger = logging.getLogger(__name__)


class MarketRepository:
    """SQLite 資料存取儲存庫，管理股票與加密貨幣預測紀錄。"""

    def __init__(self) -> None:
        self.db_path = str(cfg.DB_PATH)
        cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._create_tables()
        logger.info("[market_plugin] 資料庫路徑: %s", self.db_path)

    def _create_tables(self) -> None:
        """建立預測紀錄資料表。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stock_predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT,
                    ticker TEXT,
                    company_name TEXT,
                    ai_score INTEGER,
                    reason TEXT,
                    open_price REAL,
                    predicted_price REAL,
                    actual_close REAL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crypto_predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT,
                    ticker TEXT,
                    ai_score INTEGER,
                    reason TEXT,
                    current_price REAL,
                    predicted_price REAL,
                    actual_price REAL
                )
            """)
            conn.commit()

    def save_prediction(self, table_type: str, data_list: list[dict[str, Any]]) -> None:
        """寫入新的預測紀錄（同日同標的先刪舊記錄）。

        Args:
            table_type: 資料表種類 ("stock" 或 "crypto")。
            data_list: 預測資料清單。
        """
        table = "stock_predictions" if table_type == "stock" else "crypto_predictions"
        price_col = "open_price" if table_type == "stock" else "current_price"
        today = datetime.now().strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            for item in data_list:
                ticker = item["ticker"]
                cur.execute(f"DELETE FROM {table} WHERE date = ? AND ticker = ?", (today, ticker))
                name = item.get("name", ticker)
                score = item.get("score", 0)
                reason = item.get("reason", "AI 未提供理由")
                if table_type == "stock":
                    cur.execute(
                        f"INSERT INTO {table} (date, ticker, {price_col}, predicted_price, ai_score, reason, company_name) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (today, ticker, item["price"], item["predicted_price"], score, reason, name),
                    )
                else:
                    cur.execute(
                        f"INSERT INTO {table} (date, ticker, {price_col}, predicted_price, ai_score, reason) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (today, ticker, item["price"], item["predicted_price"], score, reason),
                    )
            conn.commit()
        logger.info("[market_plugin] 資料庫更新完成 (%d 筆)", len(data_list))

    def update_actual_prices(self, table_type: str, price_map: dict[str, float]) -> int:
        """回填實際成交價（早晨驗收用）。

        Args:
            table_type: 資料表種類 ("stock" 或 "crypto")。
            price_map: 標的與實際價格之對應字典。

        Returns:
            int: 成功更新之筆數。
        """
        table = "stock_predictions" if table_type == "stock" else "crypto_predictions"
        actual_col = "actual_close" if table_type == "stock" else "actual_price"
        updated = 0
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT id, ticker FROM {table} WHERE {actual_col} IS NULL")
            rows = cur.fetchall()
            for row_id, ticker in rows:
                if ticker in price_map:
                    cur.execute(f"UPDATE {table} SET {actual_col} = ? WHERE id = ?", (price_map[ticker], row_id))
                    updated += 1
            conn.commit()
        return updated

    def get_ticker_history(self, ticker: str) -> list[tuple[Any, ...]]:
        """取得特定標的之歷史預測與實際價格紀錄。

        Args:
            ticker: 股票或加密貨幣代號。

        Returns:
            list[tuple]: 歷史預測紀錄。
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT date, predicted_price, actual_close FROM stock_predictions "
                "WHERE ticker = ? AND actual_close IS NOT NULL ORDER BY date ASC",
                (ticker,),
            )
            data = cur.fetchall()
            if not data:
                cur.execute(
                    "SELECT date, predicted_price, actual_price FROM crypto_predictions "
                    "WHERE ticker = ? AND actual_price IS NOT NULL ORDER BY date ASC",
                    (ticker,),
                )
                data = cur.fetchall()
        return data


# Backward compatibility alias
Database = MarketRepository
