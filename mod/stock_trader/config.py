"""
config.py (mod/stock_trader)
=============================
雪莉美股虛擬量化交易模組獨立設定與常數宣告。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Module Root & Project Root
# ---------------------------------------------------------------------------
MOD_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = MOD_DIR.parent.parent

# ---------------------------------------------------------------------------
# Data Directory & File Paths
# 所有儲存檔案統一放置於 ./data/sherry_stock/
# ---------------------------------------------------------------------------
STOCK_DATA_DIR: Path = PROJECT_ROOT / "data" / "sherry_stock"
STOCK_DATA_DIR.mkdir(parents=True, exist_ok=True)

PORTFOLIO_JSON_PATH: Path = STOCK_DATA_DIR / "portfolio.json"
DAILY_HISTORY_JSON_PATH: Path = STOCK_DATA_DIR / "daily_history.json"
TRADES_JSON_PATH: Path = STOCK_DATA_DIR / "trades.json"
THOUGHTS_JSONL_PATH: Path = STOCK_DATA_DIR / "thoughts.jsonl"

# ---------------------------------------------------------------------------
# Trading Engine Constants
# ---------------------------------------------------------------------------
INITIAL_CAPITAL_USD: float = 10000.0   # 初始 10,000 美金
FEE_RATE: float = 0.001                 # 常規手續費 0.1%
MIN_SHARES: float = 0.01                # 至少交易 0.01 股

# 美股盤中交易時段（台灣時間 23:00 ~ 04:00）
TRADING_START_HOUR: int = 23
TRADING_END_HOUR: int = 4

# 臨關盤強制看盤時間 (03:50 AM)
FORCE_EVAL_HOUR: int = 3
FORCE_EVAL_MINUTE: int = 50

# ---------------------------------------------------------------------------
# Persona & Strategy Mode
# ---------------------------------------------------------------------------
# 是否導入雪莉個人特色與名偵探語氣進行交易推理
# True  = 融入橘雪莉名偵探女高中生人設風格
# False = 純客觀專業量化交易策略 AI 風格
ENABLE_SHERRY_PERSONA: bool = os.getenv("ENABLE_SHERRY_PERSONA", "True").lower() in ("true", "1", "t")

# ---------------------------------------------------------------------------
# Discord Announcement Channel
# ---------------------------------------------------------------------------
# Discord 交易紀錄推送頻道 ID (已固定寫死為指定頻道: 1468666011904512201)
# 雪莉每筆成交的交易紀錄將自動即時發送至該頻道
TRADE_ANNOUNCE_CHANNEL_ID: int | None = 1534924821844983858

# ---------------------------------------------------------------------------
# 策略推理模型設定
# ---------------------------------------------------------------------------
def get_default_model() -> str:
    """取得美股交易模組預設模型（即時動態同步全域 PRIMARY_MODEL 設定）。"""
    stock_env = os.getenv("STOCK_TRADER_MODEL", "").strip()
    if stock_env:
        return stock_env

    try:
        import settings
        val = getattr(settings, "PRIMARY_MODEL", None)
        if val and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    return os.getenv(
        "PRIMARY_MODEL",
        f"freetoken:{os.getenv('LLM_MODEL_NAME', 'mistralai/Mixtral-8x7B-Instruct-v0.1')}",
    )


def __getattr__(name: str) -> object:
    """動態屬性存取，確保 DEFAULT_MODEL 永遠即時取得當前最新的主力模型。"""
    if name == "DEFAULT_MODEL":
        return get_default_model()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
