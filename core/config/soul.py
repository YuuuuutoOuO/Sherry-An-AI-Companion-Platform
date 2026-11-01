"""
core/config/soul.py
===================
靈魂人設 (Soul) 檔案掃描、載入與共用規則解析。
"""

from __future__ import annotations

import logging
from pathlib import Path
from core.config.paths import COMMON_RULES_PATH, DEFAULT_SOUL_PATH, SOUL_DIR

logger = logging.getLogger(__name__)


def get_available_soul_names() -> list[str]:
    """自動掃描 SOUL_DIR 獲取所有 *_default.md 人設檔案名稱清單。

    Returns:
        list[str]: 依檔名排序的人設檔名列表。
    """
    if not SOUL_DIR.is_dir():
        return ["manosaba_sherry_default.md"]
    names = sorted([f.name for f in SOUL_DIR.glob("*_default.md") if f.is_file()])
    return names if names else ["manosaba_sherry_default.md"]


def load_soul_file(soul_path: Path | str | None = None) -> str:
    """載入指定的 Soul 人設檔案內容。

    Args:
        soul_path: 人設檔案路徑 (預設為 DEFAULT_SOUL_PATH)。

    Returns:
        str: 檔案內容字串。
    """
    path = Path(soul_path) if soul_path else DEFAULT_SOUL_PATH
    if not path.is_file():
        logger.warning("[ConfigSoul] 找不到 Soul 檔案: %s", path)
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.error("[ConfigSoul] 讀取 Soul 檔案失敗 (%s): %s", path, exc)
        return ""


def load_common_rules(rules_path: Path | str | None = None) -> str:
    """載入共用規則 (common_rules.md)。

    Args:
        rules_path: 規則路徑 (預設為 COMMON_RULES_PATH)。

    Returns:
        str: 規則字串。
    """
    path = Path(rules_path) if rules_path else COMMON_RULES_PATH
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("[ConfigSoul] 讀取共用規則失敗 (%s): %s", path, exc)
        return ""
