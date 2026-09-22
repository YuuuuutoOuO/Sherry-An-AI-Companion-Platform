"""
tests/unit/test_architectural_edge_case_fixes.py
=================================================
驗證架構缺陷修復與邊界條件加固之單元測試：
1. PRIORITY_DRAW 優先級提升至 5 (高於背景任務)
2. SmartMessageDispatcher 容錯與 fail_if_not_exists
3. CoreGuard 震盪死循環 (A->B->A->B) 熔斷偵測
4. RelationshipManager 單日好感度防刷 (+10/day) 與千分封頂 (1000)
5. delete_user_memory 隱私軟刪除邏輯
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import discord

import settings
from core.dispatcher import SmartMessageDispatcher
from core.contracts import SherryResponse
from core.guard import CoreGuard
from handlers.memory.relationship import RelationshipManager
from handlers.memory.storage import delete_user_memory


def test_priority_draw_ordering() -> None:
    """測試 PRIORITY_DRAW 優先級高於背景任務 (心跳 20 與氛圍 50)。"""
    assert settings.PRIORITY_TAG_RESPONSE == 1
    assert settings.PRIORITY_DRAW == 5
    assert settings.PRIORITY_DRAW < settings.PRIORITY_INTERJECT
    assert settings.PRIORITY_DRAW < settings.PRIORITY_HEARTBEAT
    assert settings.PRIORITY_DRAW < settings.PRIORITY_VIBE_UPDATE
    assert settings.PRIORITY_DRAW < settings.PRIORITY_WEB_EXPLORER


def test_smart_dispatcher_http_exception_fallback() -> None:
    """測試當 reference_msg.reply 拋出 HTTPException (如 400/50035) 時，自動降級為 channel.send。"""
    async def _run() -> None:
        target = MagicMock()
        target.send = AsyncMock(return_value=MagicMock(spec=discord.Message))

        reference_msg = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.reason = "Bad Request"
        reference_msg.reply = AsyncMock(
            side_effect=discord.HTTPException(response=mock_response, message={"code": 50035, "message": "Invalid Form Body"})
        )

        response = SherryResponse(trace_id="test-trace-123", reply_text="這是一則測試回覆內容")
        sent = await SmartMessageDispatcher.dispatch(target, response, reference_msg=reference_msg)

        assert len(sent) == 1
        assert target.send.called
        assert "這是一則測試回覆內容" in target.send.call_args[1]["content"]

    asyncio.run(_run())


def test_core_guard_oscillating_loop_trip() -> None:
    """測試 CoreGuard 能夠精準偵測 A -> B -> A -> B 震盪死循環並觸發熔斷。"""
    guard = CoreGuard()
    user_id = 998877

    # 第一次調用 SearchTool
    assert not guard.check_tool_loop(user_id, "SearchTool", "query A")
    # 第二次調用 WeatherTool
    assert not guard.check_tool_loop(user_id, "WeatherTool", "query B")
    # 第三次再度調用 SearchTool
    assert not guard.check_tool_loop(user_id, "SearchTool", "query A")
    # 第四次再度調用 WeatherTool -> 觸發 A->B->A->B 震盪熔斷！
    assert guard.check_tool_loop(user_id, "WeatherTool", "query B")


def test_relationship_manager_daily_cap_and_ceiling() -> None:
    """測試好感度單日獲取上限 (+10/day) 與總量封頂 (1000)。"""
    mgr = RelationshipManager(debounce_seconds=999)
    user_id = 123456

    # 模擬當日連續多次互動
    for _ in range(6):
        mgr.update_affinity(user_id, 2.0)

    # 5 次 * 2 = 10，第 6 次應被單日上限攔截
    assert mgr.get_affinity(user_id) == 10.0

    # 測試千分封頂 (手動設置極限值)
    mgr.store[str(user_id)]["affinity"] = 995.0
    # 清空單日計次以測試封頂
    if hasattr(mgr, "_daily_gains"):
        mgr._daily_gains.clear()
    mgr.update_affinity(user_id, 10.0)
    assert mgr.get_affinity(user_id) == 1000.0


def test_delete_user_memory_execution() -> None:
    """測試 delete_user_memory 執行無異常。"""
    delete_user_memory(123456)
