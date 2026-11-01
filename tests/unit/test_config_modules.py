"""
tests/unit/test_config_modules.py
=================================
針對 core.config 子模組 (paths, env, models, soul, manager) 與 settings 向後相容性之單元測試。
"""

from __future__ import annotations

from pathlib import Path
import pytest

from core.config.env import (
    parse_bool_env,
    parse_float_env,
    parse_int_env,
    parse_int_list_env,
    parse_str_list_env,
)
from core.config.manager import (
    load_config_overrides,
    save_config_overrides,
)
from core.config.paths import (
    BASE_DIR,
    DATA_DIR,
    FILE_DIR,
    SOUL_DIR,
)
from core.config.soul import (
    get_available_soul_names,
)
import settings


def test_config_paths() -> None:
    # Assert
    assert BASE_DIR.exists()
    assert DATA_DIR.exists()
    assert FILE_DIR.exists()
    assert SOUL_DIR.exists()


def test_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("TEST_INT_LIST", "[100, 200, 300]")
    monkeypatch.setenv("TEST_STR_LIST", "apple, banana, cherry")
    monkeypatch.setenv("TEST_BOOL", "true")
    monkeypatch.setenv("TEST_FLOAT", "3.1415")
    monkeypatch.setenv("TEST_INT", "42")

    # Act & Assert
    assert parse_int_list_env("TEST_INT_LIST") == [100, 200, 300]
    assert parse_str_list_env("TEST_STR_LIST") == ["apple", "banana", "cherry"]
    assert parse_bool_env("TEST_BOOL") is True
    assert parse_float_env("TEST_FLOAT") == 3.1415
    assert parse_int_env("TEST_INT") == 42


def test_config_manager_overrides(tmp_path: Path) -> None:
    # Arrange
    override_file = tmp_path / "test_override.json"
    data = {"PRIMARY_MODEL": "gemini:gemini-2.0-flash", "LLM_TIMEOUT": 120}

    # Act - Save
    saved = save_config_overrides(data, override_path=override_file)
    assert saved is True

    # Act - Load
    loaded = load_config_overrides(override_path=override_file)

    # Assert
    assert loaded["PRIMARY_MODEL"] == "gemini:gemini-2.0-flash"
    assert loaded["LLM_TIMEOUT"] == 120


def test_soul_names_discovery() -> None:
    # Act
    names = get_available_soul_names()

    # Assert
    assert isinstance(names, list)
    assert len(names) > 0
    assert any("sherry" in n.lower() for n in names)


def test_settings_backward_compatibility() -> None:
    # Assert key settings are exposed directly via settings module
    assert hasattr(settings, "BASE_DIR")
    assert hasattr(settings, "PRIMARY_MODEL")
    assert hasattr(settings, "ADMIN_USER_IDS")
    assert hasattr(settings, "update_setting")
    assert hasattr(settings, "get_available_soul_names")
