"""mod/spark/config.py - Spark 推播系統常數與組態設定。"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import settings as _settings
    DATA_DIR = getattr(_settings, "DATA_DIR", Path("data"))
except ImportError:
    _settings = None  # type: ignore[assignment]
    DATA_DIR = Path("data")

# 模組專屬儲存目錄與資料檔案路徑
SPARK_DATA_DIR: Path = DATA_DIR / "spark"
TRIGGERS_FILE_PATH: Path = SPARK_DATA_DIR / "triggers.json"

# 外部 HTTP 請求預設逾時時間 (秒) (符合 AGENTS.md §5)
DEFAULT_TIMEOUT_SEC: int = int(os.getenv("SPARK_TIMEOUT_SEC", "8"))

# 觸發器預設冷卻時間 (秒) - 1 小時
DEFAULT_COOLDOWN_SECONDS: int = 3600

# 預設巡檢迴圈執行頻率 (分鐘)
DEFAULT_CHECK_INTERVAL_MINUTES: int = 1

# 由 Web UI 設定覆寫 (config_override.json)
if _settings is not None:
    DEFAULT_COOLDOWN_SECONDS = int(getattr(_settings, "SPARK_DEFAULT_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS))
    DEFAULT_CHECK_INTERVAL_MINUTES = int(getattr(_settings, "SPARK_CHECK_INTERVAL_MINUTES", DEFAULT_CHECK_INTERVAL_MINUTES))

# 天氣 API 預設端點
OPENWEATHERMAP_API_URL: str = os.getenv(
    "OPENWEATHERMAP_API_URL",
    "https://api.openweathermap.org/data/2.5/weather",
)
OPENWEATHERMAP_API_KEY: str = os.getenv("OPENWEATHERMAP_API_KEY", "")

__all__ = [
    "SPARK_DATA_DIR",
    "TRIGGERS_FILE_PATH",
    "DEFAULT_TIMEOUT_SEC",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_CHECK_INTERVAL_MINUTES",
    "OPENWEATHERMAP_API_URL",
    "OPENWEATHERMAP_API_KEY",
]
