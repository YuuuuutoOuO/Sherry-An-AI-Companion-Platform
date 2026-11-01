"""
core/config
===========
雪莉平台模組化設定與環境治理套件。
"""

from __future__ import annotations

from core.config.env import (
    load_env_file,
    parse_bool_env,
    parse_float_env,
    parse_int_env,
    parse_int_list_env,
    parse_str_list_env,
)
from core.config.manager import (
    load_config_overrides,
    load_runtime_overrides,
    save_config_overrides,
    save_runtime_override,
    save_runtime_overrides,
)
from core.config.paths import (
    BASE_DIR,
    CHANNEL_VIBE_PATH,
    COMMON_RULES_PATH,
    CONFIG_OVERRIDE_PATH,
    CURRENT_SOUL_NAME,
    DATA_DIR,
    DEFAULT_SOUL_PATH,
    ERROR_LOG_PATH,
    FFMPEG,
    FILE_DIR,
    HEARTBEAT_PATH,
    OVERRIDE_FILE,
    ROAST_RULES_PATH,
    RUNTIME_LOG_PATH,
    SCHEDULE_PATH,
    SOUL_DIR,
    USER_MEMORY_PATH,
    USER_RELATIONS_PATH,
    VIDEO_DIR,
)
from core.config.soul import (
    get_available_soul_names,
    load_soul_file,
)

__all__ = [
    # 路徑常數
    "BASE_DIR",
    "DATA_DIR",
    "FILE_DIR",
    "SOUL_DIR",
    "VIDEO_DIR",
    "CONFIG_OVERRIDE_PATH",
    "OVERRIDE_FILE",
    "CURRENT_SOUL_NAME",
    "DEFAULT_SOUL_PATH",
    "COMMON_RULES_PATH",
    "HEARTBEAT_PATH",
    "CHANNEL_VIBE_PATH",
    "USER_MEMORY_PATH",
    "USER_RELATIONS_PATH",
    "ROAST_RULES_PATH",
    "RUNTIME_LOG_PATH",
    "ERROR_LOG_PATH",
    "SCHEDULE_PATH",
    "FFMPEG",
    # 環境變數解析器
    "load_env_file",
    "parse_bool_env",
    "parse_float_env",
    "parse_int_env",
    "parse_int_list_env",
    "parse_str_list_env",
    # 動態覆寫管理器
    "load_config_overrides",
    "load_runtime_overrides",
    "save_config_overrides",
    "save_runtime_override",
    "save_runtime_overrides",
    # 靈魂人設
    "get_available_soul_names",
    "load_soul_file",
]
