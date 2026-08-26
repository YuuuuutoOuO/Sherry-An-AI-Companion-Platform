"""
tests/unit/test_livestream.py
=============================
Unit tests for the livestream module (Local Video Library, YouTube, Broadcaster, Web, and Cog).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from mod.livestream.clients.broadcaster import LivestreamBroadcaster
from mod.livestream.cog import LivestreamCog
from mod.livestream.domain import (
    MediaItem,
    PlayAction,
    PlaybackStatus,
    StreamEvent,
    StreamPlaybackState,
)
from mod.livestream.service import LivestreamService, _natural_sort_key
from mod.livestream.web import _serialize_event, _serialize_state


# ===========================================================================
# 1. Local Video Library & Directory Parsing Tests
# ===========================================================================

def test_natural_sort_key() -> None:
    """Test natural sorting key orders ep2 before ep10."""
    files = ["ep10.mp4", "ep1.mp4", "ep2.mp4", "ep20.mp4"]
    sorted_files = sorted(files, key=_natural_sort_key)
    assert sorted_files == ["ep1.mp4", "ep2.mp4", "ep10.mp4", "ep20.mp4"]


def test_scan_video_library_single_and_two_level(tmp_path: Path) -> None:
    """Test directory rule: single level -> '海綿寶寶', two level -> '南方公園-第一季'."""
    # 1. Single level: ./Video/海綿寶寶/
    spongebob = tmp_path / "海綿寶寶"
    spongebob.mkdir()
    (spongebob / "02.mp4").touch()
    (spongebob / "01.mp4").touch()
    (spongebob / "10.mp4").touch()

    # 2. Two level: ./Video/南方公園/第一季/ and ./Video/南方公園/第二季/
    south_park = tmp_path / "南方公園"
    south_park.mkdir()
    sp_s1 = south_park / "第一季"
    sp_s1.mkdir()
    (sp_s1 / "ep01.mp4").touch()
    (sp_s1 / "ep02.mp4").touch()

    sp_s2 = south_park / "第二季"
    sp_s2.mkdir()
    (sp_s2 / "ep01.mp4").touch()

    # Arrange service with temp video dir
    broadcaster = LivestreamBroadcaster()
    service = LivestreamService(broadcaster, video_dir=tmp_path)

    # Act
    library = service.scan_video_library()
    available = service.get_available_series()

    # Assert
    assert "海綿寶寶" in library
    assert len(library["海綿寶寶"]) == 3
    # Check natural sort
    assert [p.name for p in library["海綿寶寶"]] == ["01.mp4", "02.mp4", "10.mp4"]

    assert "南方公園-第一季" in library
    assert len(library["南方公園-第一季"]) == 2
    assert [p.name for p in library["南方公園-第一季"]] == ["ep01.mp4", "ep02.mp4"]

    assert "南方公園-第二季" in library
    assert len(library["南方公園-第二季"]) == 1

    assert available == ["南方公園-第一季", "南方公園-第二季", "海綿寶寶"]


def test_play_local_video_and_next_episode_loop(tmp_path: Path) -> None:
    """Test local video playback and continuous loop from ep 1 to 2 then back to 1."""
    async def _test() -> None:
        # Arrange
        spongebob = tmp_path / "海綿寶寶"
        spongebob.mkdir()
        (spongebob / "01.mp4").touch()
        (spongebob / "02.mp4").touch()

        broadcaster = LivestreamBroadcaster()
        service = LivestreamService(broadcaster, video_dir=tmp_path)

        # Act: Play Episode 1
        state1 = await service.play_local_video(
            series_name="海綿寶寶",
            episode=1,
            requester_name="SherryAgent",
        )
        assert state1.status == PlaybackStatus.PLAYING
        assert state1.current_media is not None
        assert state1.current_media.series_name == "海綿寶寶"
        assert state1.current_media.episode_index == 1
        assert state1.current_media.total_episodes == 2

        # Act: Next Episode -> should advance to Episode 2
        state2 = await service.next_episode()
        assert state2.current_media is not None
        assert state2.current_media.episode_index == 2

        # Act: Next Episode at end -> should loop back to Episode 1
        state3 = await service.next_episode()
        assert state3.current_media is not None
        assert state3.current_media.episode_index == 1

    asyncio.run(_test())


# ===========================================================================
# 2. YouTube Info Extraction Tests
# ===========================================================================

def test_extract_youtube_info_standard_url() -> None:
    """Test extracting video ID from standard youtube.com/watch?v= URLs."""
    broadcaster = LivestreamBroadcaster()
    service = LivestreamService(broadcaster)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    video_id, start_sec = service.extract_youtube_info(url)
    assert video_id == "dQw4w9WgXcQ"
    assert start_sec == 0


def test_extract_youtube_info_short_url_with_timestamps() -> None:
    """Test extracting video ID and timestamp offset from youtu.be short URLs."""
    broadcaster = LivestreamBroadcaster()
    service = LivestreamService(broadcaster)
    url_secs = "https://youtu.be/dQw4w9WgXcQ?t=90"
    url_compound = "https://youtu.be/dQw4w9WgXcQ?t=1m30s"

    vid1, time1 = service.extract_youtube_info(url_secs)
    vid2, time2 = service.extract_youtube_info(url_compound)

    assert vid1 == "dQw4w9WgXcQ"
    assert time1 == 90
    assert vid2 == "dQw4w9WgXcQ"
    assert time2 == 90


def test_extract_youtube_info_embed_and_shorts() -> None:
    """Test extracting video ID from embed and shorts URL paths."""
    broadcaster = LivestreamBroadcaster()
    service = LivestreamService(broadcaster)
    embed_url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
    shorts_url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"

    vid_embed, _ = service.extract_youtube_info(embed_url)
    vid_shorts, _ = service.extract_youtube_info(shorts_url)

    assert vid_embed == "dQw4w9WgXcQ"
    assert vid_shorts == "dQw4w9WgXcQ"


def test_extract_youtube_info_direct_id() -> None:
    """Test passing raw 11-character YouTube video ID directly."""
    broadcaster = LivestreamBroadcaster()
    service = LivestreamService(broadcaster)
    raw_id = "dQw4w9WgXcQ"

    video_id, start_sec = service.extract_youtube_info(raw_id)
    assert video_id == "dQw4w9WgXcQ"
    assert start_sec == 0


def test_extract_youtube_info_invalid_urls() -> None:
    """Test error handling for empty or invalid video URLs."""
    broadcaster = LivestreamBroadcaster()
    service = LivestreamService(broadcaster)

    with pytest.raises(ValueError, match="不能為空"):
        service.extract_youtube_info("   ")

    with pytest.raises(ValueError, match="無法從網址中解析"):
        service.extract_youtube_info("https://example.com/video.mp4")


# ===========================================================================
# 3. Service Playback Control Tests
# ===========================================================================

def test_service_play_video_success() -> None:
    """Test playing a valid YouTube video and state transitions."""
    async def _test() -> None:
        broadcaster = LivestreamBroadcaster()
        service = LivestreamService(broadcaster)
        url = "https://youtu.be/dQw4w9WgXcQ"

        state = await service.play_video(
            action=PlayAction.YOUTUBE,
            context=url,
            requester_name="SherryAgent",
            requester_avatar="http://avatar.jpg",
        )

        assert state.status == PlaybackStatus.PLAYING
        assert state.current_media is not None
        assert state.current_media.video_id == "dQw4w9WgXcQ"
        assert state.current_media.requester_name == "SherryAgent"
        assert broadcaster.get_state().status == PlaybackStatus.PLAYING

    asyncio.run(_test())


def test_service_stop_pause_resume() -> None:
    """Test stop, pause, and resume workflows."""
    async def _test() -> None:
        broadcaster = LivestreamBroadcaster()
        service = LivestreamService(broadcaster)
        await service.play_video(
            action=PlayAction.YOUTUBE,
            context="dQw4w9WgXcQ",
            requester_name="SherryAgent",
        )

        paused_state = await service.pause()
        assert paused_state.status == PlaybackStatus.PAUSED

        resumed_state = await service.resume()
        assert resumed_state.status == PlaybackStatus.PLAYING

        stopped_state = await service.stop()
        assert stopped_state.status == PlaybackStatus.IDLE
        assert stopped_state.current_media is None

    asyncio.run(_test())


# ===========================================================================
# 4. Broadcaster Subscription Tests
# ===========================================================================

def test_broadcaster_subscription_and_dispatch() -> None:
    """Test subscribing queues and receiving broadcast events."""
    async def _test() -> None:
        broadcaster = LivestreamBroadcaster()
        queue = await broadcaster.subscribe()

        event = StreamEvent(
            event_type="test_event",
            state=StreamPlaybackState(status=PlaybackStatus.PLAYING),
            timestamp=123456.78,
            message="Testing broadcast",
        )

        await broadcaster.broadcast(event)
        received = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert received.event_type == "test_event"
        assert received.message == "Testing broadcast"
        assert broadcaster.get_state().status == PlaybackStatus.PLAYING

        await broadcaster.unsubscribe(queue)

    asyncio.run(_test())


# ===========================================================================
# 5. Web Serialization Tests
# ===========================================================================

def test_web_serialization_helpers() -> None:
    """Test serializing StreamPlaybackState and StreamEvent into JSON-friendly dicts."""
    media = MediaItem(
        action=PlayAction.LOCAL,
        video_id="海綿寶寶_ep1",
        original_url="/api/video/stream?series=海綿寶寶&episode=1",
        title="海綿寶寶 - 第 1 集",
        requester_name="SherryAgent",
        series_name="海綿寶寶",
        episode_index=1,
        total_episodes=10,
    )
    state = StreamPlaybackState(
        status=PlaybackStatus.PLAYING,
        current_media=media,
        volume=80,
    )
    event = StreamEvent(
        event_type="play",
        state=state,
        timestamp=100.0,
        message="Now playing",
    )

    state_dict = _serialize_state(state)
    event_dict = _serialize_event(event)

    assert state_dict["status"] == "playing"
    assert state_dict["volume"] == 80
    assert state_dict["current_media"]["series_name"] == "海綿寶寶"  # type: ignore[index]
    assert event_dict["event_type"] == "play"
    assert event_dict["message"] == "Now playing"


# ===========================================================================
# 6. Discord Cog Slash Command & Autocomplete Tests
# ===========================================================================

def test_cog_video_name_autocomplete() -> None:
    """Test autocomplete filters available series properly."""
    async def _test() -> None:
        mock_bot = MagicMock()
        mock_service = MagicMock(spec=LivestreamService)
        mock_service.get_available_series.return_value = ["海綿寶寶", "南方公園-第一季", "南方公園-第二季"]

        cog = LivestreamCog(bot=mock_bot, service=mock_service)
        mock_interaction = AsyncMock()

        # Act 1: Query '南方'
        choices = await cog.video_name_autocomplete(mock_interaction, "南方")
        assert len(choices) == 2
        assert choices[0].name == "南方公園-第一季"

        # Act 2: Query empty -> returns all up to 25
        choices_all = await cog.video_name_autocomplete(mock_interaction, "")
        assert len(choices_all) == 3

    asyncio.run(_test())


def test_cog_play_video_local_command() -> None:
    """Test /play_video with local series name and episode."""
    async def _test() -> None:
        mock_bot = MagicMock()
        mock_service = AsyncMock(spec=LivestreamService)
        media = MediaItem(
            action=PlayAction.LOCAL,
            video_id="海綿寶寶_ep5",
            original_url="/api/video/stream?series=海綿寶寶&episode=5",
            title="海綿寶寶 - 第 5 集",
            series_name="海綿寶寶",
            episode_index=5,
            total_episodes=20,
        )
        mock_service.play_local_video.return_value = StreamPlaybackState(
            status=PlaybackStatus.PLAYING,
            current_media=media,
        )

        cog = LivestreamCog(bot=mock_bot, service=mock_service)

        mock_interaction = AsyncMock()
        mock_interaction.user.name = "TestUser"
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.user.id = 12345
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.user.display_avatar.url = "http://avatar.png"

        mock_action = MagicMock()
        mock_action.name = "本機影片 (file)"
        mock_action.value = "file"

        # Act
        await cog.play_video.callback(  # type: ignore[misc]
            cog,
            mock_interaction,
            mock_action,
            "海綿寶寶",
            5,
        )

        # Assert
        mock_interaction.response.defer.assert_awaited_once_with(thinking=True)
        mock_service.play_local_video.assert_awaited_once_with(
            series_name="海綿寶寶",
            episode=5,
            requester_name="TestUser",
            requester_avatar="http://avatar.png",
        )
        mock_interaction.followup.send.assert_awaited_once()

    asyncio.run(_test())


def test_cog_play_video_youtube_command() -> None:
    """Test /play_video with YouTube action choice and URL."""
    async def _test() -> None:
        mock_bot = MagicMock()
        mock_service = AsyncMock(spec=LivestreamService)
        media = MediaItem(
            action=PlayAction.YOUTUBE,
            video_id="dQw4w9WgXcQ",
            original_url="https://youtu.be/dQw4w9WgXcQ",
            title="Rick Astley",
        )
        mock_service.play_video.return_value = StreamPlaybackState(
            status=PlaybackStatus.PLAYING,
            current_media=media,
        )

        cog = LivestreamCog(bot=mock_bot, service=mock_service)

        mock_interaction = AsyncMock()
        mock_interaction.user.name = "TestUser"
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.user.id = 12345
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.user.display_avatar.url = "http://avatar.png"

        mock_action = MagicMock()
        mock_action.name = "YouTube 串流 (youtube)"
        mock_action.value = "youtube"

        # Act
        await cog.play_video.callback(  # type: ignore[misc]
            cog,
            mock_interaction,
            mock_action,
            "https://youtu.be/dQw4w9WgXcQ",
            1,
        )

        # Assert
        mock_interaction.response.defer.assert_awaited_once_with(thinking=True)
        mock_service.play_video.assert_awaited_once_with(
            action=PlayAction.YOUTUBE,
            context="https://youtu.be/dQw4w9WgXcQ",
            requester_name="TestUser",
            requester_avatar="http://avatar.png",
        )
        mock_interaction.followup.send.assert_awaited_once()

    asyncio.run(_test())


def test_cog_stream_stop_command() -> None:
    """Test the /stream_stop slash command flow."""
    async def _test() -> None:
        mock_bot = MagicMock()
        mock_service = AsyncMock(spec=LivestreamService)
        cog = LivestreamCog(bot=mock_bot, service=mock_service)

        mock_interaction = AsyncMock()
        mock_interaction.user.name = "TestUser"

        # Act
        await cog.stream_stop.callback(cog, mock_interaction)  # type: ignore[misc]

        # Assert
        mock_interaction.response.defer.assert_awaited_once_with(thinking=True)
        mock_service.stop.assert_awaited_once()
        mock_interaction.followup.send.assert_awaited_once()

    asyncio.run(_test())


def test_web_stream_video_endpoint(tmp_path: Path) -> None:
    """Test streaming local video endpoint with send_file Byte-Range."""
    async def _test() -> None:
        from urllib.parse import quote
        # Create test dummy video
        spongebob = tmp_path / "海綿寶寶"
        spongebob.mkdir()
        video_file = spongebob / "01.mp4"
        video_file.write_bytes(b"dummy video byte content 1234567890")

        from mod.livestream.web import get_service, livestream_app
        service = get_service()
        service._video_dir = tmp_path

        client = livestream_app.test_client()

        # Act
        encoded_series = quote("海綿寶寶")
        response = await client.get(f"/api/video/stream?series={encoded_series}&episode=1")

        # Assert
        assert response.status_code == 200
        data = await response.get_data()
        assert data == b"dummy video byte content 1234567890"

    asyncio.run(_test())


def test_web_events_stream_endpoint() -> None:
    """Test SSE events stream endpoint initialization and generator yield."""
    async def _test() -> None:
        from mod.livestream.web import _handle_events_stream
        response = await _handle_events_stream()
        assert response.mimetype == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"

        # Verify initial sync payload from generator
        gen = response.response
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
            break
        assert len(chunks) == 1
        assert chunks[0].startswith("data: ")
        assert "Initial state sync" in chunks[0]

    asyncio.run(_test())



