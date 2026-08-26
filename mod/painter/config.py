"""
config.py (mod/painter)
=======================
雪莉數位畫布繪圖模組專屬設定檔。

包含繪圖暫存目錄、畫布預設解析度、指令最大數量與超時設定。
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
DATA_DIR: Path = PROJECT_ROOT / "data"
PAINTING_DIR: Path = DATA_DIR / "painting"
PAINTING_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 畫布與指令限制設定
# ---------------------------------------------------------------------------
DEFAULT_CANVAS_WIDTH: int = int(os.getenv("PAINTER_DEFAULT_WIDTH", "800"))
DEFAULT_CANVAS_HEIGHT: int = int(os.getenv("PAINTER_DEFAULT_HEIGHT", "800"))
DEFAULT_CANVAS_SIZE: tuple[int, int] = (DEFAULT_CANVAS_WIDTH, DEFAULT_CANVAS_HEIGHT)

MAX_COMMANDS: int = int(os.getenv("PAINTER_MAX_COMMANDS", "150"))

# ---------------------------------------------------------------------------
# 推論與生成逾時
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT_SEC: float = float(os.getenv("PAINTER_TIMEOUT_SEC", "120.0"))
