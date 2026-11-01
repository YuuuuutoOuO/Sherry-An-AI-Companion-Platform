"""
tests/unit/test_livestream.py
=============================
Unit tests for the livestream module domain models, services, chat features, and web endpoints.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from mod.livestream import config
from mod.livestream.clients.broadcaster import LivestreamBroadcaster
from mod.livestream.cog import LivestreamCog
from mod.livestream.domain import (
    ChatMessage,
    PlaybackStatus,
    StreamEvent,
    StreamPlaybackState,
)
from mod.livestream.service import LivestreamService
from mod.livestream.web import livestream_app


def test_chat_message_creation_and_history(tmp_path: Path) -> None:
    """Tests ChatMessage creation, max history trimming, and service getters."""
    async def _run() -> None:
        broadcaster = LivestreamBroadcaster()
        service = LivestreamService(broadcaster=broadcaster, video_dir=tmp_path)

        assert service.get_chat_history() == []

        msg1 = await service.post_chat_message(
            author_name="Alice",
            author_avatar="https://example.com/avatar1.png",
            content="Hello Sherry!",
            is_owner=False,
            badge="💬 觀眾",
            channel_id="1519967448982487201",
        )

        assert msg1.author_name == "Alice"
        assert msg1.content == "Hello Sherry!"
        assert msg1.badge == "💬 觀眾"
        assert len(service.get_chat_history()) == 1

        msg2 = await service.post_chat_message(
            author_name="Sherry",
            content="Welcome to the stream!",
            is_owner=True,
            badge="👑 實況主",
        )

        assert msg2.is_owner is True
        assert len(service.get_chat_history()) == 2
        assert service.get_chat_history()[1].content == "Welcome to the stream!"

    asyncio.run(_run())


def test_broadcaster_dispatches_chat_event() -> None:
    """Tests that LivestreamBroadcaster delivers chat_message event to subscriber queues."""
    async def _run() -> None:
        broadcaster = LivestreamBroadcaster()
        queue = await broadcaster.subscribe()

        chat_msg = ChatMessage(
            id="test_msg_1",
            author_name="Bob",
            content="Testing SSE broadcast",
            timestamp=1234567.0,
            badge="⭐ VIP",
        )

        event = StreamEvent(
            event_type="chat_message",
            state=StreamPlaybackState(status=PlaybackStatus.PLAYING),
            timestamp=1234567.0,
            message="Testing SSE broadcast",
            chat_message=chat_msg,
        )

        await broadcaster.broadcast(event)

        received_event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert received_event.event_type == "chat_message"
        assert received_event.chat_message is not None
        assert received_event.chat_message.author_name == "Bob"
        assert received_event.chat_message.badge == "⭐ VIP"

        await broadcaster.unsubscribe(queue)

    asyncio.run(_run())


def test_livestream_web_chat_api() -> None:
    """Tests the GET and POST /api/livestream/chat endpoints on livestream_app."""
    async def _run() -> None:
        client = livestream_app.test_client()

        # GET initial chat history
        get_res = await client.get("/api/livestream/chat")
        assert get_res.status_code == 200
        get_json = await get_res.get_json()
        assert get_json["status"] == "success"
        assert isinstance(get_json["data"], list)
        assert get_json["channel_id"] == str(config.DISCORD_CHAT_CHANNEL_ID)

        # POST new chat message
        post_res = await client.post(
            "/api/livestream/chat",
            json={
                "author_name": "WebUser",
                "content": "Nice stream!",
                "badge": "💬 網頁",
            },
        )
        assert post_res.status_code == 200
        post_json = await post_res.get_json()
        assert post_json["status"] == "success"
        assert post_json["data"]["author_name"] == "WebUser"
        assert post_json["data"]["content"] == "Nice stream!"

        # POST empty content should fail
        empty_res = await client.post("/api/livestream/chat", json={"content": "  "})
        assert empty_res.status_code == 400

    asyncio.run(_run())


def test_livestream_cog_on_message_forwarding() -> None:
    """Tests LivestreamCog on_message event forwarding for the configured channel ID."""
    async def _run() -> None:
        bot = MagicMock()
        bot.owner_id = 9999
        service = MagicMock()
        service.post_chat_message = AsyncMock()

        cog = LivestreamCog(bot=bot, service=service)

        # 1. Message from another channel -> Ignored
        msg_other = MagicMock()
        msg_other.guild = MagicMock()
        msg_other.channel.id = 12345
        msg_other.author.id = 1111
        msg_other.author.display_name = "User1"
        msg_other.clean_content = "Hello in general"

        await cog.on_message(msg_other)
        service.post_chat_message.assert_not_called()

        # 2. Message from config.DISCORD_CHAT_CHANNEL_ID -> Forwarded
        msg_chat = MagicMock()
        msg_chat.guild = MagicMock()
        msg_chat.channel.id = config.DISCORD_CHAT_CHANNEL_ID
        msg_chat.author.id = 9999
        msg_chat.author.display_name = "Owner"
        msg_chat.author.display_avatar.url = "https://cdn.discordapp.com/owner.png"
        msg_chat.author.guild_permissions.administrator = True
        msg_chat.author.bot = False
        msg_chat.clean_content = "Stream is live!"

        await cog.on_message(msg_chat)
        service.post_chat_message.assert_called_once_with(
            author_name="Owner",
            author_avatar="https://cdn.discordapp.com/owner.png",
            content="Stream is live!",
            is_owner=True,
            badge="👑 實況主",
            channel_id=str(config.DISCORD_CHAT_CHANNEL_ID),
        )

    asyncio.run(_run())


def test_bilibili_and_youtube_url_extraction() -> None:
    """Tests URL extraction for Bilibili (BV, av, p-index, medialist) and YouTube (video, list, offset)."""
    broadcaster = LivestreamBroadcaster()
    service = LivestreamService(broadcaster=broadcaster)

    # 1. Bilibili BV plain & URL with page
    bvid1, p1, list1 = service.extract_bilibili_info("BV1xx411c7X-")
    assert bvid1 == "BV1xx411c7X-"
    assert p1 == 1
    assert list1 == ""

    bvid2, p2, list2 = service.extract_bilibili_info("https://www.bilibili.com/video/BV1xx411c7X-?p=3")
    assert bvid2 == "BV1xx411c7X-"
    assert p2 == 3
    assert list2 == ""

    # 2. Bilibili av & medialist
    bvid3, p3, list3 = service.extract_bilibili_info("https://www.bilibili.com/video/av170001")
    assert bvid3 == "av170001"
    assert p3 == 1

    bvid4, p4, list4 = service.extract_bilibili_info("https://www.bilibili.com/medialist/play/ml12345/BV1xx411c7X-?p=2")
    assert bvid4 == "BV1xx411c7X-"
    assert p4 == 2
    assert list4 == "ml12345"

    # 3. YouTube extraction with playlist and timestamps
    y_vid1, y_start1, y_list1 = service.extract_youtube_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL12345&t=90s")
    assert y_vid1 == "dQw4w9WgXcQ"
    assert y_start1 == 90
    assert y_list1 == "PL12345"

    y_vid2, y_start2, y_list2 = service.extract_youtube_info("https://www.youtube.com/playlist?list=PLABCXYZ")
    assert y_vid2 == "videoseries"
    assert y_list2 == "PLABCXYZ"


def test_play_video_bilibili_and_modes(tmp_path: Path) -> None:
    """Tests service play_video for Bilibili, YouTube playlists, and PlayMode options."""
    async def _run() -> None:
        broadcaster = LivestreamBroadcaster()
        service = LivestreamService(broadcaster=broadcaster, video_dir=tmp_path)

        from mod.livestream.domain import PlayAction, PlayMode

        # 1. Play Bilibili with LOOP mode
        state_bili = await service.play_video(
            action=PlayAction.BILIBILI,
            context="https://www.bilibili.com/video/BV1xx411c7X-?p=2",
            play_mode=PlayMode.LOOP,
            requester_name="Detective",
        )
        assert state_bili.status == PlaybackStatus.PLAYING
        assert state_bili.current_media is not None
        assert state_bili.current_media.action == PlayAction.BILIBILI
        assert state_bili.current_media.video_id == "BV1xx411c7X-"
        assert state_bili.current_media.page_index == 2
        assert state_bili.current_media.play_mode == PlayMode.LOOP

        # 2. Next episode on Bilibili in AUTOPLAY mode
        state_bili_auto = await service.play_video(
            action=PlayAction.BILIBILI,
            context="BV1xx411c7X-",
            play_mode=PlayMode.AUTOPLAY,
            episode=1,
        )
        assert state_bili_auto.current_media is not None
        assert state_bili_auto.current_media.page_index == 1

        next_bili_state = await service.next_episode()
        assert next_bili_state.current_media is not None
        assert next_bili_state.current_media.page_index == 2

        # 3. Next episode on SINGLE mode -> stops
        await service.play_video(
            action=PlayAction.BILIBILI,
            context="BV1xx411c7X-",
            play_mode=PlayMode.SINGLE,
            episode=1,
        )
        stopped_state = await service.next_episode()
        assert stopped_state.status == PlaybackStatus.IDLE
        assert stopped_state.current_media is None

    asyncio.run(_run())


def test_cog_play_video_url_auto_detection() -> None:
    """Tests LivestreamCog _handle_play correctly routes YouTube URLs containing params without confusing with Bilibili."""
    async def _run() -> None:
        bot = MagicMock()
        service = MagicMock()
        service.get_available_series.return_value = ["海綿寶寶"]
        service.play_video = AsyncMock()

        from discord import app_commands
        from mod.livestream.domain import PlayAction, PlayMode

        cog = LivestreamCog(bot=bot, service=service)

        interaction = MagicMock()
        interaction.user.name = "TestUser"
        interaction.user.display_name = "TestUser"
        interaction.user.display_avatar = None
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # 1. YouTube URL with parameters (like user reported)
        yt_url = "https://www.youtube.com/watch?v=VurEzKAVqlg&pp=ugUEEgJ6aA%3D%3D"
        await cog._handle_play(
            interaction=interaction,
            action=app_commands.Choice(name="play", value="file"),
            video_name=yt_url,
        )

        service.play_video.assert_called_once_with(
            action=PlayAction.YOUTUBE,
            context=yt_url,
            play_mode=PlayMode.AUTOPLAY,
            episode=1,
            requester_name="TestUser",
            requester_avatar="",
        )

        # 2. Bilibili URL with parameters
        service.play_video.reset_mock()
        bili_url = "https://www.bilibili.com/video/BV1xx411c7X-?p=2"
        await cog._handle_play(
            interaction=interaction,
            action=app_commands.Choice(name="play", value="file"),
            video_name=bili_url,
        )

        service.play_video.assert_called_once_with(
            action=PlayAction.BILIBILI,
            context=bili_url,
            play_mode=PlayMode.AUTOPLAY,
            episode=1,
            requester_name="TestUser",
            requester_avatar="",
        )

    asyncio.run(_run())


# ===========================================================================
# R2：Livestream 綁定與簡易認證（報告 §5.3.9 / §5.4.2）
# ===========================================================================
def test_livestream_default_host_is_loopback() -> None:
    """預設監聽位址必須是 127.0.0.1（不得預設對外開放）。"""
    assert config.DEFAULT_HOST == "127.0.0.1"
    assert config.HOST == os.getenv("LIVESTREAM_HOST", "127.0.0.1")


def test_livestream_open_when_token_not_configured() -> None:
    """未設定 LIVESTREAM_ACCESS_TOKEN 時，維持既有行為（依賴 loopback 邊界）。"""
    async def _run() -> None:
        client = livestream_app.test_client()
        with patch.object(config, "ACCESS_TOKEN", ""):
            res = await client.get("/api/livestream/chat")
            assert res.status_code == 200

    asyncio.run(_run())


def test_livestream_token_required_and_cookie_issued() -> None:
    """設定 ACCESS_TOKEN 後：無憑證 401、錯誤 token 401、標頭/查詢 token 放行並核發 cookie。"""
    async def _run() -> None:
        token = "test-livestream-token-abc123"
        client = livestream_app.test_client()
        with patch.object(config, "ACCESS_TOKEN", token):
            # 1. 無憑證 → 401
            res = await client.get("/api/livestream/chat")
            assert res.status_code == 401

            # 2. 錯誤 token → 401
            res = await client.get("/api/livestream/chat?token=wrong-token")
            assert res.status_code == 401

            # 3. 標頭 token → 200
            res = await client.get("/api/livestream/chat", headers={"X-Livestream-Token": token})
            assert res.status_code == 200

            # 4. 查詢 token（頁面載入路徑）→ 200 並核發 HttpOnly cookie
            res = await client.get(f"/api/livestream/chat?token={token}")
            assert res.status_code == 200
            set_cookie = res.headers.get("Set-Cookie", "")
            assert "livestream_access" in set_cookie
            assert "HttpOnly" in set_cookie

            # 5. 僅帶已核發 cookie、無 token → 仍可讀取（SSE/API 後續請求）
            res = await client.get("/api/livestream/chat")
            assert res.status_code == 200

            # 6. 靜態資源豁免（不存在 → 404，但不得是 401）
            res = await client.get("/static/__r2_not_exists__.css")
            assert res.status_code != 401

    asyncio.run(_run())


def test_livestream_control_error_hides_exception_details() -> None:
    """控制端點內部例外不得外洩細節，回應需改帶 error_id（報告 §5.3.6）。"""
    async def _run() -> None:
        client = livestream_app.test_client()
        marker = "LIVESTREAM-LEAK-MARKER-42"
        fake_service = MagicMock()
        fake_service.play_video = AsyncMock(side_effect=RuntimeError(marker))
        with patch("mod.livestream.web.get_service", return_value=fake_service):
            res = await client.post(
                "/api/control",
                json={
                    "action": "play",
                    "source": "youtube",
                    "context": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                },
            )
        assert res.status_code == 400
        text = await res.get_data(as_text=True)
        assert marker not in text
        data = await res.get_json()
        assert data.get("error_id")

    asyncio.run(_run())


