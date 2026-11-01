"""
core/config/env.py
==================
環境變數載入與型別安全解析工具。
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv


def load_env_file(dotenv_path: Path | str | None = None) -> bool:
    """載入 .env 檔案。

    Args:
        dotenv_path: 指定 .env 檔案路徑。

    Returns:
        bool: 是否成功載入。
    """
    try:
        if dotenv_path:
            return load_dotenv(dotenv_path=dotenv_path, override=True, encoding="utf-8")
        return load_dotenv(override=True, encoding="utf-8")
    except Exception:
        return False


def parse_int_list_env(env_name: str, default: list[int] | None = None) -> list[int]:
    """自環境變數安全解析整數列表（支援逗號分隔、中括號與引號格式）。

    Args:
        env_name: 環境變數名稱。
        default: 預設整數列表。

    Returns:
        list[int]: 解析後的整數列表。
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return list(default) if default is not None else []
    raw = raw.strip("[]")
    parsed: list[int] = []
    for token in raw.split(","):
        token = token.strip().strip("'\"")
        if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
            parsed.append(int(token))
    return parsed


def parse_str_list_env(env_name: str, default: list[str] | None = None) -> list[str]:
    """自環境變數安全解析字串列表。"""
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return list(default) if default is not None else []
    raw = raw.strip("[]")
    parsed: list[str] = []
    for token in raw.split(","):
        token = token.strip().strip("'\"")
        if token:
            parsed.append(token)
    return parsed


def parse_bool_env(env_name: str, default: bool = False) -> bool:
    """自環境變數安全解析布林值。"""
    raw = os.getenv(env_name, "").strip().lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes", "y", "on")


def parse_float_env(env_name: str, default: float = 0.0) -> float:
    """自環境變數安全解析浮點數。"""
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_int_env(env_name: str, default: int = 0) -> int:
    """自環境變數安全解析整數。"""
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
