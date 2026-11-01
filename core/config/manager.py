"""
core/config/manager.py
======================
動態設定覆寫 (Config Overrides) 載入、儲存與熱更新管理器。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.config.paths import CONFIG_OVERRIDE_PATH

logger = logging.getLogger(__name__)


def load_config_overrides(override_path: Path | str | None = None) -> dict[str, Any]:
    """從 JSON 檔案載入設定覆寫。

    Args:
        override_path: 覆寫檔案路徑 (預設為 CONFIG_OVERRIDE_PATH)。

    Returns:
        dict[str, Any]: 覆寫字典。
    """
    path = Path(override_path) if override_path else CONFIG_OVERRIDE_PATH
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return dict(data)
            return {}
    except Exception as exc:
        logger.warning("[ConfigManager] 載入設定覆寫失敗 (%s): %s", path, exc)
        return {}


def save_config_overrides(overrides: dict[str, Any], override_path: Path | str | None = None) -> bool:
    """儲存設定覆寫至 JSON 檔案。

    Args:
        overrides: 欲持久化之設定覆寫字典。
        override_path: 檔案路徑。

    Returns:
        bool: 是否成功寫入。
    """
    path = Path(override_path) if override_path else CONFIG_OVERRIDE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = dict(overrides)
        data.pop("CORE_PERSONA", None)
        data.pop("SOUL_CONTENT", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as exc:
        logger.error("[ConfigManager] 儲存設定覆寫失敗 (%s): %s", path, exc)
        return False


# 向後相容別名與語意化別名
load_runtime_overrides = load_config_overrides
save_runtime_override = save_config_overrides
save_runtime_overrides = save_config_overrides

