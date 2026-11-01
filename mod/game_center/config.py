"""
config.py (mod/game_center)
===========================
雪莉小遊戲中心模組專屬設定檔。

包含猜拳、21點、踩地雷、海龜湯、Akinator、密室逃脫與井字棋之參數設定。
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
DATA_DIR: Path = PROJECT_ROOT / "data" / "game_center"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 模型與對話設定
# ---------------------------------------------------------------------------
def get_default_model() -> str:
    """取得小遊戲模組預設模型。"""
    try:
        import settings
        val = getattr(settings, "PRIMARY_MODEL", None)
        if val and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    return os.getenv(
        "GAME_CENTER_MODEL",
        os.getenv("PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1"),
    )

DEFAULT_MODEL: str = get_default_model()
