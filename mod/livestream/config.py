"""
config.py (mod/livestream)
==========================
雪莉直播螢幕與本機影片串流模組專屬設定檔。

所有主機、連接埠、HTTPS、影片目錄與自動開啟設定皆在此模組內部定義，
不干涉主程式 settings.py，並支援透過 .env 環境變數進行個別覆寫。
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
VIDEO_DIR: Path = PROJECT_ROOT / "Video"
DATA_DIR: Path = PROJECT_ROOT / "data" / "livestream"
DATA_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 串流伺服器網路與連接埠設定
# ---------------------------------------------------------------------------
HOST: str = os.getenv("LIVESTREAM_HOST", "0.0.0.0")
PORT: int = int(os.getenv("LIVESTREAM_PORT", "8764"))

# ---------------------------------------------------------------------------
# HTTPS 與 SSL 憑證設定
# ---------------------------------------------------------------------------
HTTPS: bool = os.getenv("LIVESTREAM_HTTPS", "true").lower() in ("true", "1", "yes")
SSL_CERT: str | None = os.getenv("LIVESTREAM_SSL_CERT", None)
SSL_KEY: str | None = os.getenv("LIVESTREAM_SSL_KEY", None)
RADMIN_IP: str | None = os.getenv("WEB_UI_RADMIN_IP", "26.108.83.196")

# ---------------------------------------------------------------------------
# 瀏覽器與播放行為設定
# ---------------------------------------------------------------------------
AUTO_OPEN: bool = os.getenv("LIVESTREAM_AUTO_OPEN", "true").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# 本地影片支援副檔名
# ---------------------------------------------------------------------------
SUPPORTED_VIDEO_EXTENSIONS: tuple[str, ...] = (
    ".mp4",
    ".webm",
    ".mkv",
    ".mov",
    ".m4v",
    ".avi",
    ".flv",
    ".wmv",
)
