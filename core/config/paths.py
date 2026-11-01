"""
core/config/paths.py
====================
專案檔案目錄與持久化檔案路徑常數定義。
"""

from __future__ import annotations

from pathlib import Path

# 根目錄與基礎子目錄
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
DATA_DIR: Path = BASE_DIR / "data"
FILE_DIR: Path = BASE_DIR / "file"
SOUL_DIR: Path = FILE_DIR / "soul"
VIDEO_DIR: Path = BASE_DIR / "Video"

# 確保基礎資料目錄存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILE_DIR.mkdir(parents=True, exist_ok=True)
SOUL_DIR.mkdir(parents=True, exist_ok=True)

# 靈魂人設與規則路徑
CURRENT_SOUL_NAME: str = "manosaba_sherry_default.md"
DEFAULT_SOUL_PATH: Path = SOUL_DIR / CURRENT_SOUL_NAME
COMMON_RULES_PATH: Path = SOUL_DIR / "common_rules.md"

# 狀態與日誌資料路徑
HEARTBEAT_PATH: Path = DATA_DIR / "heartbeat.md"
CHANNEL_VIBE_PATH: Path = DATA_DIR / "channel_vibe.json"
USER_MEMORY_PATH: Path = DATA_DIR / "user_memory.json"
USER_RELATIONS_PATH: Path = DATA_DIR / "user_relations.json"
ROAST_RULES_PATH: Path = DATA_DIR / "roast_rules.json"
RUNTIME_LOG_PATH: Path = DATA_DIR / "runtime_log.jsonl"
ERROR_LOG_PATH: Path = DATA_DIR / "error_log.jsonl"
SCHEDULE_PATH: Path = DATA_DIR / "schedule.json"
CONFIG_OVERRIDE_PATH: Path = DATA_DIR / "config_override.json"
OVERRIDE_FILE: Path = CONFIG_OVERRIDE_PATH

# 外部二進位檔案
FFMPEG: str = str(BASE_DIR / "ffmpeg")
