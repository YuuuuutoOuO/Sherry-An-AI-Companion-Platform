from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from handlers.interaction.contracts import TurnMode
from handlers.interaction.gateway import TurnGateway


def make_message(*, content: str, mentions: list | None = None) -> SimpleNamespace:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 123
    return SimpleNamespace(
        id=456,
        content=content,
        author=SimpleNamespace(id=1, bot=False, display_name="Yuuto"),
        channel=channel,
        guild=SimpleNamespace(id=9),
        mentions=list(mentions or []),
        reference=None,
        created_at=datetime.now(timezone.utc),
        attachments=[],
    )


class FakeRepository:
    def get_surfer_config(self) -> SimpleNamespace:
        return SimpleNamespace(sandbox_channel_id="123", enabled=True)


class FakeEngine:
    mode = "live"

    def __init__(self) -> None:
        self.requests: list[SimpleNamespace] = []

    async def submit_turn(self, request: SimpleNamespace) -> bool:
        self.requests.append(request)
        return True

    async def cancel_pending(self, channel_id: int | str, *, reason: str = "") -> int:
        return 0


@pytest.fixture
def gateway_setup(monkeypatch: pytest.MonkeyPatch):
    import handlers.care.cyber as cyber_pkg
    import handlers.care.cyber.ui_config as ui_config

    engine = FakeEngine()
    monkeypatch.setattr(cyber_pkg, "get_cyber_engine", lambda: engine)
    monkeypatch.setattr(
        ui_config,
        "get_cyber_ui_config",
        lambda: SimpleNamespace(
            gateway_enabled=True,
            owns_mentions=True,
            mention_scope="sandbox",
            allowed_tools=["SEARCH", "STOCK", "SUMMARY"],
            tool_max_rounds=2,
        ),
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    gateway = TurnGateway(bot, repository=FakeRepository())
    return gateway, engine


@pytest.mark.asyncio
async def test_name_only_message_in_sandbox_uses_v2_ambient(
    gateway_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只叫名字、沒有真的 Tag 時，應走 v2 ambient，而不是舊 router。"""
    gateway, engine = gateway_setup
    called = False

    async def fake_route(*args, **kwargs):
        nonlocal called
        called = True

    import handlers.social.router as router_module

    monkeypatch.setattr(router_module, "route_and_execute_tag", fake_route)

    message = make_message(content="雪莉最喜歡什麼點心啊？")
    assert await gateway.handle_message(message) is True
    assert called is False
    assert len(engine.requests) == 1
    assert engine.requests[0].mode == TurnMode.AMBIENT


@pytest.mark.asyncio
async def test_real_mention_still_uses_passive_router(
    gateway_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真正 Tag 雪莉時，仍走被動 lane 保留完整工具。"""
    gateway, engine = gateway_setup
    calls = 0

    async def fake_route(*args, **kwargs):
        nonlocal calls
        calls += 1

    import handlers.social.router as router_module

    monkeypatch.setattr(router_module, "route_and_execute_tag", fake_route)

    message = make_message(content="@雪莉 你在嗎", mentions=[gateway.bot.user])
    assert await gateway.handle_message(message) is True
    assert calls == 1
    assert engine.requests == []
