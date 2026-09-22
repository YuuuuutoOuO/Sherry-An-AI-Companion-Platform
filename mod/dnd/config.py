"""
config.py (mod/dnd)
===================
雪莉 D&D 跑團與文字冒險模組專屬設定檔。

定義世界觀、角色、任務、NPC 與歷史日誌的持久化路徑與設定。
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
DATA_DIR.mkdir(parents=True, exist_ok=True)
DND_DATA_DIR: Path = DATA_DIR / "dnd"
DND_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 持久化檔案路徑
# ---------------------------------------------------------------------------
DND_DB_PATH: Path = DND_DATA_DIR / "dnd.db"
WORLD_FILE: Path = DND_DATA_DIR / "dnd_world.json"
CHARACTERS_FILE: Path = DND_DATA_DIR / "dnd_characters.json"
QUESTS_FILE: Path = DND_DATA_DIR / "dnd_quests.json"
NPCS_FILE: Path = DND_DATA_DIR / "dnd_npcs.json"
HISTORY_FILE: Path = DND_DATA_DIR / "dnd_history.jsonl"
WORLD_SETTING_FILE: Path = DND_DATA_DIR / "dnd_world_setting.json"

# ---------------------------------------------------------------------------
# 模型與推理優先級
# ---------------------------------------------------------------------------
def get_default_model() -> str:
    """取得跑團模組預設模型。"""
    try:
        import settings
        val = getattr(settings, "PRIMARY_MODEL", None)
        if val and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    return os.getenv(
        "DND_MODEL",
        os.getenv("PRIMARY_MODEL", f"freetoken:{os.getenv('LLM_MODEL_NAME', 'mistralai/Mixtral-8x7B-Instruct-v0.1')}"),
    )

DEFAULT_MODEL: str = get_default_model()
DEFAULT_PRIORITY: int = int(os.getenv("DND_PRIORITY", "1"))
