"""
tests/unit/test_config_and_concurrency.py
=========================================
設定治理（settings/core.config）與並發控制（BotBrain Queue Task Done）單元測試套件。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

import core.config as config
from core.config.paths import (
    BASE_DIR,
    CONFIG_OVERRIDE_PATH,
    DATA_DIR,
    FILE_DIR,
    OVERRIDE_FILE,
    SOUL_DIR,
)
from handlers.bot_brain.cog import BotBrain
from handlers.bot_brain.models import InferenceTask
import settings


class TestUpdateSettingBooleanParsing:
    """測試 settings.update_setting 函式對於布林值與字串之安全轉換。"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, tmp_path: Path) -> Any:
        # Arrange: 使用臨時檔案避免汙染真實設定檔
        temp_override = tmp_path / "temp_override.json"
        with patch.object(settings, "CONFIG_OVERRIDE_PATH", temp_override):
            original_setting = getattr(settings, "ACTION_EARLY_TERMINATION_ENABLED", True)
            try:
                yield
            finally:
                settings.ACTION_EARLY_TERMINATION_ENABLED = original_setting

    @pytest.mark.parametrize(
        "raw_value, expected_bool",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            (" 1 ", True),
            ("yes", True),
            ("YES", True),
            ("on", True),
            ("ON", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            (" 0 ", False),
            ("no", False),
            ("NO", False),
            ("off", False),
            ("OFF", False),
            (True, True),
            (False, False),
            (1, True),
            (0, False),
        ],
    )
    def test_update_setting_bool_conversion(self, raw_value: Any, expected_bool: bool) -> None:
        """驗證布林類型設定項傳入各種字串/數值表示法皆能正確解析。"""
        # Act
        success = settings.update_setting("ACTION_EARLY_TERMINATION_ENABLED", raw_value)

        # Assert
        assert success is True
        assert settings.ACTION_EARLY_TERMINATION_ENABLED is expected_bool

    def test_live2d_enabled_setting_and_schema(self) -> None:
        """驗證 Web UI Live 2D 開關可停用並出現在設定 schema。"""
        from web_ui.settings_schema import get_editable_settings

        import os
        original_setting = getattr(settings, "WEB_UI_LIVE2D_ENABLED", True)
        try:
            with patch.dict(os.environ, {}, clear=False):
                success = settings.update_setting("WEB_UI_LIVE2D_ENABLED", "off")

            assert success is True
            assert settings.WEB_UI_LIVE2D_ENABLED is False
            live2d_setting = get_editable_settings()["WEB_UI_LIVE2D_ENABLED"]
            assert live2d_setting["type"] == "bool"
            assert live2d_setting["value"] is False
        finally:
            settings.WEB_UI_LIVE2D_ENABLED = original_setting

    def test_update_setting_invalid_key(self) -> None:
        """驗證傳入無效或非全大寫鍵值時回傳 False。"""
        # Act & Assert
        assert settings.update_setting("non_existent_key", True) is False
        assert settings.update_setting("enable_ai_chat", True) is False
        assert settings.update_setting("123INVALID", True) is False

    def test_health_alert_user_ids_schema_and_update(self) -> None:
        """驗證 HEALTH_ALERT_USER_IDS 之 Schema 定義正確且支援清單與字串更新。"""
        # Arrange
        from web_ui.settings_schema import SETTINGS_META, get_editable_settings

        # Assert Schema
        assert SETTINGS_META["HEALTH_ALERT_USER_IDS"]["type"] == "list"
        assert SETTINGS_META["HEALTH_ALERT_USER_IDS"]["item_type"] == "int"

        editable = get_editable_settings()
        assert editable["HEALTH_ALERT_USER_IDS"]["type"] == "list"
        assert editable["HEALTH_ALERT_USER_IDS"]["item_type"] == "int"

        # Act & Assert Update with list
        orig = getattr(settings, "HEALTH_ALERT_USER_IDS", [])
        try:
            assert settings.update_setting("HEALTH_ALERT_USER_IDS", [123456, 789012]) is True
            assert settings.HEALTH_ALERT_USER_IDS == [123456, 789012]

            # Update with JSON string representation
            assert settings.update_setting("HEALTH_ALERT_USER_IDS", "[]") is True
            assert settings.HEALTH_ALERT_USER_IDS == []

            # Update with comma-separated string
            assert settings.update_setting("HEALTH_ALERT_USER_IDS", "111, 222") is True
            assert settings.HEALTH_ALERT_USER_IDS == [111, 222]
        finally:
            settings.HEALTH_ALERT_USER_IDS = orig

    def test_new_tunables_registered_in_settings_meta(self) -> None:
        """Verify newly added system tunables are registered in SETTINGS_META and editable via Web UI."""
        from web_ui.settings_schema import SETTINGS_META, is_web_editable_key

        expected_keys = [
            "BOT_BRAIN_MAX_TIMEOUT_RETRIES",
            "BOT_BRAIN_RETRY_MAX_DELAY",
            "BOT_BRAIN_CLOUD_MAX_CONCURRENCY",
            "BOT_BRAIN_CLOUD_PER_USER_CONCURRENCY",
            "BOT_BRAIN_WORKER_MAX_FAILURES",
            "BOT_BRAIN_WORKER_BACKOFF_CAP",
            "OBSERVABILITY_PII_REDACTION",
            "OBSERVABILITY_WRITE_QUEUE_MAXSIZE",
            "VOICE_TTS_DEVICE",
            "SPARK_MAX_TRIGGERS_PER_USER",
            "SPARK_MAX_PUSHES_PER_DAY",
            "SPARK_MAX_CONCURRENT_ACTIONS",
            "SPARK_ACTION_TIMEOUT_SECONDS",
            "CARE_WEEKLY_LIMIT_PER_USER",
            "CARE_DAILY_LIMIT_GLOBAL",
            "CARE_MIN_AFFINITY",
            "CARE_INACTIVE_DAYS",
            "CARE_ADAPTIVE_BUDGET_ENABLED",
            "CARE_FEEDBACK_WINDOW_DAYS",
            "CARE_MIN_FEEDBACK_SAMPLES",
            "CARE_SILENCE_DOWNGRADE_THRESHOLD",
            "CARE_SILENCE_STOP_THRESHOLD",
            "CARE_SILENCE_SETTLE_HOURS",
            "EMOTION_ENABLED",
            "EMOTION_HALF_LIFE_HOURS",
            "PERSONA_GUARD_ENABLED",
            "VISION_REACTION_ENABLED",
            "VISION_REACTION_COOLDOWN_SECONDS",
            "VISION_MAX_IMAGES",
            "VISION_MAX_FILE_BYTES",
            "PRIORITY_VISION_REACTION",
        ]
        for key in expected_keys:
            assert key in SETTINGS_META, f"{key} should be registered in SETTINGS_META"
            assert is_web_editable_key(key) is True, f"{key} should be web editable"

    def test_list_setting_ignores_blank_items(self, tmp_path: Path) -> None:
        """Web UI 清空清單欄位會送出空白字串，應忽略而非讓整個 key 儲存失敗。"""
        override = tmp_path / "config_override.json"
        orig = getattr(settings, "HEALTH_ALERT_USER_IDS", [])
        try:
            with patch.object(settings, "CONFIG_OVERRIDE_PATH", override):
                assert settings.update_setting("HEALTH_ALERT_USER_IDS", [""]) is True
                assert settings.HEALTH_ALERT_USER_IDS == []

                assert settings.update_setting("HEALTH_ALERT_USER_IDS", ["", "333", "  "]) is True
                assert settings.HEALTH_ALERT_USER_IDS == [333]

                assert settings.update_setting("HEALTH_ALERT_USER_IDS", [111, ""]) is True
                assert settings.HEALTH_ALERT_USER_IDS == [111]
        finally:
            settings.HEALTH_ALERT_USER_IDS = orig

    def test_web_ui_saves_health_alert_user_ids_with_blank_row(self, tmp_path: Path) -> None:
        """回歸測試：帶空白列的 HEALTH_ALERT_USER_IDS 不應回報儲存失敗。"""
        import time

        import web_ui.server as server

        token = "test-admin-token-blank-list"
        server._admin_sessions[token] = time.time()
        orig = getattr(settings, "HEALTH_ALERT_USER_IDS", [])
        try:
            with patch.object(settings, "CONFIG_OVERRIDE_PATH", tmp_path / "config_override.json"):
                async def scenario() -> tuple[int, dict[str, Any]]:
                    async with server.app.test_client() as client:
                        resp = await client.post(
                            "/api/settings",
                            json={"HEALTH_ALERT_USER_IDS": ["", "123456"]},
                            headers={"X-Admin-Token": token},
                        )
                        return resp.status_code, await resp.get_json()

                status, body = asyncio.run(scenario())
                assert status == 200, body
                assert body == {"status": "success"}
                assert settings.HEALTH_ALERT_USER_IDS == [123456]
        finally:
            server._admin_sessions.pop(token, None)
            settings.HEALTH_ALERT_USER_IDS = orig

    def test_discord_snowflake_64bit_precision_preservation(self) -> None:
        """驗證 64-bit Discord Snowflake ID（超過 JS 安全上限）無損精度保存與序列化。"""
        # Arrange
        from web_ui.settings_schema import get_editable_settings
        snowflake_id = 425311733503229972  # 18 位數，超過 2^53-1 (9007199254740991)
        orig_admin = getattr(settings, "ADMIN_USER_IDS", [])
        orig_channel = getattr(settings, "HEALTH_ALERT_CHANNEL_ID", 0)

        try:
            # Act 1: List setting with string snowflake
            assert settings.update_setting("ADMIN_USER_IDS", [str(snowflake_id)]) is True
            assert settings.ADMIN_USER_IDS == [snowflake_id]
            assert settings.ADMIN_USER_IDS[0] == snowflake_id
            assert settings.ADMIN_USER_IDS[0] != 425311733503229950

            # Schema export should serialize as string to protect browser JSON.parse
            editable = get_editable_settings()
            assert editable["ADMIN_USER_IDS"]["value"] == [str(snowflake_id)]

            # Act 2: Scalar setting with snowflake
            assert settings.update_setting("HEALTH_ALERT_CHANNEL_ID", str(snowflake_id)) is True
            assert settings.HEALTH_ALERT_CHANNEL_ID == snowflake_id
            assert settings.HEALTH_ALERT_CHANNEL_ID != 425311733503229950

            editable = get_editable_settings()
            assert editable["HEALTH_ALERT_CHANNEL_ID"]["value"] == str(snowflake_id)
        finally:
            settings.ADMIN_USER_IDS = orig_admin
            settings.HEALTH_ALERT_CHANNEL_ID = orig_channel


class TestCoreConfigExportsAndPaths:
    """測試 core.config 模組之公開常數、別名導出與設定管理器。"""

    def test_paths_and_aliases(self) -> None:
        """驗證路徑常數與向後相容別名一致性。"""
        # Assert
        assert BASE_DIR.exists()
        assert DATA_DIR.exists()
        assert FILE_DIR.exists()
        assert SOUL_DIR.exists()
        assert CONFIG_OVERRIDE_PATH == DATA_DIR / "config_override.json"
        assert OVERRIDE_FILE == CONFIG_OVERRIDE_PATH

    def test_core_config_module_exports(self) -> None:
        """驗證 core.config 頂層套件是否完整導出所需常數與函式。"""
        # Assert
        assert hasattr(config, "BASE_DIR")
        assert hasattr(config, "DATA_DIR")
        assert hasattr(config, "CONFIG_OVERRIDE_PATH")
        assert hasattr(config, "OVERRIDE_FILE")
        assert hasattr(config, "load_runtime_overrides")
        assert hasattr(config, "save_runtime_override")
        assert hasattr(config, "save_runtime_overrides")
        assert hasattr(config, "load_config_overrides")
        assert hasattr(config, "save_config_overrides")
        assert hasattr(config, "load_env_file")
        assert hasattr(config, "parse_bool_env")
        assert hasattr(config, "get_available_soul_names")

    def test_runtime_overrides_save_and_load(self, tmp_path: Path) -> None:
        """驗證 load_runtime_overrides 與 save_runtime_override 的持久化流程。"""
        # Arrange
        temp_file = tmp_path / "runtime_override.json"
        payload = {"PRIMARY_MODEL": "openai:gpt-4o", "LLM_TIMEOUT": 90}

        # Act
        save_success = config.save_runtime_override(payload, override_path=temp_file)
        loaded_data = config.load_runtime_overrides(override_path=temp_file)

        # Assert
        assert save_success is True
        assert loaded_data.get("PRIMARY_MODEL") == "openai:gpt-4o"
        assert loaded_data.get("LLM_TIMEOUT") == 90


class TestBotBrainConcurrencyAndTaskDone:
    """測試 BotBrain 推論隊列任務取消與 task_done 保證機制。"""

    @pytest.fixture
    def bot_mock(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def brain(self, bot_mock: MagicMock) -> BotBrain:
        b = BotBrain(bot_mock)
        if hasattr(b, "_worker_task") and b._worker_task:
            b._worker_task.cancel()
        return b

    def test_process_task_cancelled_guarantees_task_done(self, brain: BotBrain) -> None:
        """驗證當任務在 _process_task 內被判定取消時，finally: self._queue.task_done() 必定執行。"""
        async def _run() -> None:
            # Arrange
            cb = AsyncMock()
            task = InferenceTask(
                priority=1,
                system_prompt="s",
                user_prompt="u",
                callback=cb,
                task_id="cancelled_in_process",
                model="local:qwen",
            )
            await brain._queue.put(task)
            task.is_cancelled = True

            # Act: 直接呼叫 _process_task
            await brain._process_task(task, max_retries=1, retry_delay=0.1)

            # Assert: queue.join() 應立即返回（若未呼叫 task_done 則會超時掛起）
            await asyncio.wait_for(brain._queue.join(), timeout=1.0)
            cb.assert_not_called()

        asyncio.run(_run())

    def test_process_task_exception_guarantees_task_done(self, brain: BotBrain) -> None:
        """驗證當 _process_task 內部拋出非預期異常時，finally: self._queue.task_done() 仍百分之百被呼叫。"""
        async def _run() -> None:
            # Arrange
            cb = AsyncMock()
            task = InferenceTask(
                priority=1,
                system_prompt="s",
                user_prompt="u",
                callback=cb,
                task_id="exception_task",
                model="unknown_model",
            )
            await brain._queue.put(task)

            # 模擬 chat 呼叫引發異常
            with patch.object(brain._ollama, "chat", side_effect=RuntimeError("Simulated Ollama Crash")):
                await brain._process_task(task, max_retries=1, retry_delay=0.01)

            # Assert: queue.join() 能正常結束
            await asyncio.wait_for(brain._queue.join(), timeout=1.0)
            # 發生例外時 callback 應被以空字串調用降級
            cb.assert_awaited_once_with("")

        asyncio.run(_run())

    def test_worker_mixed_cancellation_and_normal_tasks(self, brain: BotBrain) -> None:
        """驗證多個混合取消與正常任務在 Worker 併發處理下，所有隊列計數正確釋放且 join() 順暢。"""
        async def _run() -> None:
            # Arrange
            results: list[str] = []

            async def fake_chat(*args: Any, **kwargs: Any) -> str:
                return "AI Response"

            brain._ollama.chat = fake_chat  # type: ignore

            t1 = InferenceTask(
                priority=1,
                system_prompt="s",
                user_prompt="u1",
                callback=AsyncMock(side_effect=lambda r: results.append(f"t1:{r}")),
                task_id="t1",
                model="local:test",
            )
            t2 = InferenceTask(
                priority=2,
                system_prompt="s",
                user_prompt="u2",
                callback=AsyncMock(side_effect=lambda r: results.append(f"t2:{r}")),
                task_id="t2",
                model="local:test",
            )
            t3 = InferenceTask(
                priority=0,
                system_prompt="s",
                user_prompt="u3",
                callback=AsyncMock(side_effect=lambda r: results.append(f"t3:{r}")),
                task_id="t3",
                model="local:test",
            )

            # 將 t2 標記為已取消
            t2.is_cancelled = True

            await brain.enqueue_task(t1)
            await brain.enqueue_task(t2)
            await brain.enqueue_task(t3)

            # Act
            worker = asyncio.create_task(brain._inference_worker())
            await asyncio.wait_for(brain._queue.join(), timeout=2.0)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

            # Assert: t3 (Priority 0) 與 t1 (Priority 1) 執行成功，t2 (取消) 被跳過
            assert "t3:AI Response" in results
            assert "t1:AI Response" in results
            assert not any("t2" in r for r in results)

        asyncio.run(_run())
