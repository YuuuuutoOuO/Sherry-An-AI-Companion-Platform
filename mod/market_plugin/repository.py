"""
repository.py  (mod/market_plugin)
==================================
AI 股票預測紀錄的 SQLite 資料庫存取層 (Repository Layer)。
移植自 DC_bot_StockAnalysis-main/modules/database.py，
資料庫路徑由模組獨立的 config.py 決定（存放於主專案 ./data/ 目錄）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
import time
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stock_watchlists (
                    list_type TEXT PRIMARY KEY,
                    tickers_json TEXT NOT NULL,
                    updated_at REAL NOT NULL DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS card_watchlist (
                    name TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    last_prices_json TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL DEFAULT 0
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_stock_ticker_date ON stock_predictions(ticker, date ASC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_ticker_date ON crypto_predictions(ticker, date ASC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_stock_date_ticker ON stock_predictions(date, ticker);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_crypto_date_ticker ON crypto_predictions(date, ticker);")
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
            update_params = [(price_map[ticker], row_id) for row_id, ticker in rows if ticker in price_map]
            if update_params:
                cur.executemany(f"UPDATE {table} SET {actual_col} = ? WHERE id = ?", update_params)
                updated = len(update_params)
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

    def load_watchlists(self) -> dict[str, list[str]]:
        """自 DB 讀取 watchlist；DB 空白則一次性自 JSON 或預設值種入。"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT list_type, tickers_json FROM stock_watchlists")
                rows = cur.fetchall()

            if rows:
                data: dict[str, list[str]] = {"ai_list": [], "raw_list": [], "etf_list": []}
                for row in rows:
                    list_type = str(row["list_type"])
                    tickers_json = row["tickers_json"]
                    try:
                        tickers = json.loads(tickers_json)
                    except Exception:
                        tickers = []
                    data[list_type] = [
                        t.replace(".B", "-B").replace(".A", "-A")
                        for t in tickers if isinstance(t, str)
                    ]
                return data
        except Exception as e:
            logger.error("[market_plugin] 讀取 watchlists 自 DB 失敗: %s", e)

        return self._migrate_watchlists_from_json()

    def _migrate_watchlists_from_json(self) -> dict[str, list[str]]:
        """一次性：DB 空白時，自既有 market_stocks.json 讀入並種入 DB。"""
        data: dict[str, list[str]] = {
            "ai_list": ["NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA", "TSM", "AVGO", "AMD"],
            "raw_list": ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "GOOG", "META", "AMZN", "TSM", "AVGO"],
            "etf_list": ["0050.TW", "0056.TW", "00878.TW"],
        }
        path: Path = cfg.STOCKS_JSON_PATH
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for key in ("ai_list", "raw_list", "etf_list"):
                        data[key] = [
                            t.replace(".B", "-B").replace(".A", "-A")
                            for t in raw.get(key, []) if isinstance(t, str)
                        ]
            except (OSError, json.JSONDecodeError, ValueError) as e:
                logger.error("[market_plugin] 讀取 stocks.json 舊檔失敗: %s", e)
        try:
            self.save_watchlists(data)
        except Exception as e:
            logger.error("[market_plugin] 初始 watchlists 寫入 DB 失敗: %s", e)
        return data

    def save_watchlists(self, data: dict[str, list[str]]) -> bool:
        """將 watchlist 寫入 DB。"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                for key in ("ai_list", "raw_list", "etf_list"):
                    cur.execute(
                        "INSERT OR REPLACE INTO stock_watchlists (list_type, tickers_json, updated_at) VALUES (?, ?, ?)",
                        (key, json.dumps(data.get(key, []), ensure_ascii=False), time.time()),
                    )
                conn.commit()
            return True
        except Exception as e:
            logger.error("[market_plugin] 儲存 watchlists 至 DB 失敗: %s", e)
            return False

    def load_cards(self) -> dict[str, Any]:
        """自 DB 讀取卡片追蹤清單；DB 空白則一次性自 JSON 種入。"""
        data: dict[str, Any] = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT name, url, last_prices_json FROM card_watchlist")
                rows = cur.fetchall()

            for row in rows:
                name = str(row["name"])
                url = str(row["url"])
                last_prices_json = row["last_prices_json"]
                try:
                    last_prices = json.loads(last_prices_json)
                except Exception:
                    last_prices = {}
                if not isinstance(last_prices, dict):
                    last_prices = {}
                data[name] = {"url": url, "last_prices": last_prices}
            if data:
                return data
        except Exception as e:
            logger.error("[market_plugin] 讀取 cards 自 DB 失敗: %s", e)

        return self._migrate_cards_from_json()

    def _migrate_cards_from_json(self) -> dict[str, Any]:
        """一次性：DB 空白且既有 market_cards.json 有資料時，自 JSON 種入 DB。"""
        data: dict[str, Any] = {}
        path: Path = cfg.CARDS_JSON_PATH
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            except (OSError, json.JSONDecodeError, ValueError) as e:
                logger.error("[market_plugin] 讀取 cards.json 舊檔失敗: %s", e)
        try:
            self.save_cards(data)
        except Exception as e:
            logger.error("[market_plugin] 初始 cards 寫入 DB 失敗: %s", e)
        return data

    def save_cards(self, data: dict[str, Any]) -> bool:
        """將卡片清單寫入 DB。"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                for name, info in data.items():
                    url = ""
                    last_prices: dict[str, Any] = {}
                    if isinstance(info, dict):
                        url = str(info.get("url", ""))
                        lp = info.get("last_prices", {})
                        last_prices = lp if isinstance(lp, dict) else {}
                    cur.execute(
                        "INSERT OR REPLACE INTO card_watchlist (name, url, last_prices_json, updated_at) VALUES (?, ?, ?, ?)",
                        (str(name), url, json.dumps(last_prices, ensure_ascii=False), time.time()),
                    )
                conn.commit()
            return True
        except Exception as e:
            logger.error("[market_plugin] 儲存 cards 至 DB 失敗: %s", e)
            return False


# Backward compatibility alias
Database = MarketRepository
