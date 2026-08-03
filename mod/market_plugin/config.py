"""
config.py  (mod/market_plugin)
================================
模組專屬設定檔 — 股票分析 & PTCG 卡片追蹤插件。

所有與此模組有關的頻道 ID、API Key、資料路徑，
全部在這裡定義，完全不會干涉雪莉主程式的 settings.py。

使用方式：在 .env 中加入對應的環境變數即可覆寫預設值。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Module Root
# ---------------------------------------------------------------------------
MOD_DIR: Path = Path(__file__).resolve().parent

# 主程式根目錄（往上兩層：mod/market_plugin -> mod -> project root）
PROJECT_ROOT: Path = MOD_DIR.parent.parent

# ---------------------------------------------------------------------------
# Data Directory
# 模組的額外紀錄資訊（JSON 追蹤清單、SQLite）統一存放在主專案的 ./data/ 目錄
# ---------------------------------------------------------------------------
DATA_DIR: Path = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 股票追蹤清單
STOCKS_JSON_PATH: Path = DATA_DIR / "market_stocks.json"
# PTCG 卡片追蹤清單
CARDS_JSON_PATH: Path = DATA_DIR / "market_cards.json"
# AI 預測紀錄資料庫
DB_PATH: Path = DATA_DIR / "market_stock_bot.db"

# ---------------------------------------------------------------------------
# Discord Channel IDs
# 可透過 .env 覆寫，預設使用使用者指定的頻道 ID
# ---------------------------------------------------------------------------
STOCK_REPORT_CHANNEL_ID: int = int(
    os.getenv("STOCK_REPORT_CHANNEL_ID", "1454419136808747091")
)
PTCG_REPORT_CHANNEL_ID: int = int(
    os.getenv("PTCG_REPORT_CHANNEL_ID", "1500385625126473738")
)

# ---------------------------------------------------------------------------
# API Keys — 從 .env 讀取，不存在則為空字串（功能降級但不崩潰）
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

# ---------------------------------------------------------------------------
# Schedule Times（可依需求自行調整）
# ---------------------------------------------------------------------------
MORNING_VERIFY = True
MORNING_VERIFY_HOUR: int = 70
MORNING_VERIFY_MINUTE: int = 0

DAILY_RAW = True
DAILY_RAW_HOUR: int = 8
DAILY_RAW_MINUTE: int = 0

EVENING_AI = True
EVENING_AI_HOUR: int = 210
EVENING_AI_MINUTE: int = 30

CARD_REPORT = True
CARD_REPORT_HOUR: int = 10
CARD_REPORT_MINUTE: int = 0

ETF_MONITOR_INTERVAL = False
ETF_MONITOR_INTERVAL_MINUTES: int = 99999999999999999
