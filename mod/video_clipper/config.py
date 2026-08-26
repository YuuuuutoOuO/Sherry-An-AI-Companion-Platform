"""
config.py (mod/video_clipper)
=============================
YouTube 精華剪輯與 GIF 產生模組專屬設定檔。

定義最長剪輯時長、FFmpeg 轉換逾時限制與正規表示式。
不干涉主程式 settings.py，並支援透過 .env 環境變數進行個別覆寫。
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 剪輯限制與逾時設定
# ---------------------------------------------------------------------------
MAX_CLIP_DURATION_SEC: float = float(os.getenv("CLIPPER_MAX_DURATION_SEC", "15.0"))
DEFAULT_CLIP_TIMEOUT_SEC: int = int(os.getenv("CLIPPER_TIMEOUT_SEC", "90"))

# ---------------------------------------------------------------------------
# YouTube 網址匹配正則
# ---------------------------------------------------------------------------
YOUTUBE_URL_REGEX = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$",
    re.IGNORECASE,
)
