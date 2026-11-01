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
# R2（報告 §5.3.9）：預設僅監聽 loopback，不再預設 0.0.0.0 對外開放；
# 如需區網／外部存取請顯式設定 LIVESTREAM_HOST。
DEFAULT_HOST: str = "127.0.0.1"
HOST: str = os.getenv("LIVESTREAM_HOST", DEFAULT_HOST)
PORT: int = int(os.getenv("LIVESTREAM_PORT", "8764"))

# ---------------------------------------------------------------------------
# 簡易存取權杖（R2；報告 §5.4.2）
# ---------------------------------------------------------------------------
# 設定後，8764 專用服務的頁面與 /api/* 皆需 X-Livestream-Token 標頭或 ?token= 查詢參數；
# 未設定時維持既有行為（依賴 loopback 綁定邊界）。
ACCESS_TOKEN: str = os.getenv("LIVESTREAM_ACCESS_TOKEN", "").strip()

# ---------------------------------------------------------------------------
# HTTPS 與 SSL 憑證設定
# ---------------------------------------------------------------------------
HTTPS: bool = os.getenv("LIVESTREAM_HTTPS", "true").lower() in ("true", "1", "yes")
SSL_CERT: str | None = os.getenv("LIVESTREAM_SSL_CERT", None)
SSL_KEY: str | None = os.getenv("LIVESTREAM_SSL_KEY", None)
# R2（報告 §5.3.9）：不再硬編特定管理員 IP；如需憑證 SAN 納入固定 IP，請以 WEB_UI_RADMIN_IP 覆寫。
RADMIN_IP: str | None = os.getenv("WEB_UI_RADMIN_IP") or None

# ---------------------------------------------------------------------------
# 瀏覽器與播放行為設定
# ---------------------------------------------------------------------------
AUTO_OPEN: bool = os.getenv("LIVESTREAM_AUTO_OPEN", "false").lower() in ("true", "1", "yes")

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

# ---------------------------------------------------------------------------
# 直播即時聊天室與 Discord 頻道連動設定
# ---------------------------------------------------------------------------
DISCORD_CHAT_CHANNEL_ID: int = int(
    os.getenv("LIVESTREAM_CHAT_CHANNEL_ID", "1519967448982487201")
)
MAX_CHAT_HISTORY: int = int(os.getenv("LIVESTREAM_MAX_CHAT_HISTORY", "100"))

