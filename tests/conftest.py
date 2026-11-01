import asyncio
import inspect
import sys
from unittest.mock import MagicMock
import pytest

if "yt_dlp" not in sys.modules:
    try:
        import yt_dlp
    except ImportError:
        sys.modules["yt_dlp"] = MagicMock()


def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        asyncio.run(pyfuncitem.obj(**{arg: pyfuncitem.funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames if arg in pyfuncitem.funcargs}))
        return True
    return None


@pytest.fixture(autouse=True)
def _reset_social_dialogue_state():
    """對話鏈是全域記憶體狀態，測試之間必須清空，避免順序相依。"""
    try:
        from handlers.social.autonomy import reset_dialogue_tracker

        reset_dialogue_tracker()
    except Exception:
        pass
    yield
    try:
        from handlers.social.autonomy import reset_dialogue_tracker

        reset_dialogue_tracker()
    except Exception:
        pass
