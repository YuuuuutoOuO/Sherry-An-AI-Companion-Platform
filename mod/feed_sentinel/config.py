"""
config.py  (mod/feed_sentinel)
==============================
AI 社群情報哨兵模組專屬設定檔。

所有監聽時段、間隔、頻道與資料路徑皆在此定義，不干涉雪莉主程式 settings.py。
可透過 .env 環境變數進行個別覆寫。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Module Root & Project Paths
# ---------------------------------------------------------------------------
MOD_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = MOD_DIR.parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data" / "feed_sentinel"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 模組功能開關
# ---------------------------------------------------------------------------
ENABLED: bool = os.getenv("SENTINEL_ENABLED", "True").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# 推播時段與頻率控制
# ---------------------------------------------------------------------------
# 執行時段：每日 08:00 ~ 23:00 (台灣時間 UTC+8)
START_HOUR: int = int(os.getenv("SENTINEL_START_HOUR", "8"))
END_HOUR: int = int(os.getenv("SENTINEL_END_HOUR", "23"))

# 每次情報推播之間隔限制：至少 1 小時 (3600 秒)
MIN_GAP_SECONDS: int = int(os.getenv("SENTINEL_MIN_GAP_SECONDS", "3600"))

# 背景檢查輪詢間隔 (分鐘)
CHECK_INTERVAL_MINUTES: int = int(os.getenv("SENTINEL_CHECK_INTERVAL_MINUTES", "15"))

# ---------------------------------------------------------------------------
# 推播目標頻道
# ---------------------------------------------------------------------------
TARGET_CHANNEL_ID: int = int(
    os.getenv("SENTINEL_TARGET_CHANNEL_ID", "1519967448982487201")
)

# ---------------------------------------------------------------------------
# 持久化紀錄路徑 (情報文章歷史與去重快取)
# ---------------------------------------------------------------------------
POSTS_PATH: Path = DATA_DIR / "posts.json"
SEEN_PATH: Path = DATA_DIR / "sentinel_seen.json"
LEGACY_SEEN_PATH: Path = PROJECT_ROOT / "data" / "sentinel_seen.json"

# ---------------------------------------------------------------------------
# 網路請求預設逾時 (秒)
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT_SEC: int = 8
