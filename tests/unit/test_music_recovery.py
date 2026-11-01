"""
tests/unit/test_music_recovery.py
=================================
Unit tests for the Music Recovery Provider (Phase 2).
"""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.recovery.base import SnapshotMetadata
from handlers.music.recovery_provider import MusicRecoveryProvider


class DummyMember:
    def __init__(self, bot: bool = False, name: str = "User") -> None:
        self.bot = bot
        self.name = name


class DummyVoiceChannel:
    def __init__(self, channel_id: int = 1001, name: str = "Music Room", members: list | None = None) -> None:
        self.id = channel_id
        self.name = name
        self.members = members if members is not None else [DummyMember(bot=False)]
        self.connect = AsyncMock()


class DummyTextChannel:
    def __init__(self, channel_id: int = 2001) -> None:
        self.id = channel_id
        self.send = AsyncMock()
        self.fetch_message = AsyncMock()


class DummyGuild:
    def __init__(self, guild_id: int = 123456789) -> None:
        self.id = guild_id
        self.name = "Test Guild"
        self.voice_client = None
        self._channels: dict[int, Any] = {}

    def get_channel(self, channel_id: int) -> Any:
        return self._channels.get(channel_id)


class DummyBot:
    def __init__(self) -> None:
        self.guilds: list[DummyGuild] = []
        self._guild_map: dict[int, DummyGuild] = {}
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = MagicMock()
        self.is_closed = lambda: False

    def get_guild(self, guild_id: int) -> DummyGuild | None:
        return self._guild_map.get(guild_id)

    def get_channel(self, channel_id: int) -> Any:
        for g in self.guilds:
            ch = g.get_channel(channel_id)
            if ch:
                return ch
        return None


@pytest.fixture
def mock_music_handler() -> MagicMock:
    handler = MagicMock()
    handler.bot = DummyBot()
    handler.queues = {}
    handler.current_song = {}
    handler.loop_modes = {}
    handler.current_message = {}
    handler.playback_start_time = {}
    handler.playback_accumulated_time = {}
    handler.is_paused = {}
    handler.current_sources = {}
    handler._is_stopping = {}
    handler._is_skipping = {}

    handler.get_elapsed_time.side_effect = lambda gid: 45
    handler.format_duration.side_effect = lambda sec: f"{int(sec)//60:02d}:{int(sec)%60:02d}"
    handler.build_playing_embed.return_value = MagicMock()
    handler._delete_song_message = AsyncMock()
    handler._start_progress_updater = MagicMock()
    handler._cleanup_guild_source = MagicMock()
    handler._play_next = MagicMock()

    return handler


@pytest.mark.asyncio
async def test_capture_snapshot_empty(mock_music_handler: MagicMock) -> None:
    provider = MusicRecoveryProvider(mock_music_handler)
    snapshot = await provider.capture_recovery_snapshot()
    assert snapshot is None


@pytest.mark.asyncio
async def test_capture_snapshot_with_active_song(mock_music_handler: MagicMock) -> None:
    provider = MusicRecoveryProvider(mock_music_handler)
    guild_id = 999
    guild = DummyGuild(guild_id)
    vc = MagicMock()
    vc.channel = DummyVoiceChannel(channel_id=888)
    guild.voice_client = vc

    mock_music_handler.bot.guilds.append(guild)
    mock_music_handler.bot._guild_map[guild_id] = guild

    mock_music_handler.current_song[guild_id] = {
        "title": "Night Dancer",
        "webpage_url": "https://youtube.com/watch?v=123",
        "duration": 210,
    }
    mock_music_handler.queues[guild_id] = [
        {"title": "Song 2", "webpage_url": "https://youtube.com/watch?v=456", "duration": 180}
    ]
    mock_music_handler.loop_modes[guild_id] = "all"
    mock_music_handler.is_paused[guild_id] = False

    msg = MagicMock()
    msg.id = 777
    msg.channel.id = 666
    mock_music_handler.current_message[guild_id] = msg

    snapshot = await provider.capture_recovery_snapshot()
    assert snapshot is not None
    assert "guilds" in snapshot
    assert str(guild_id) in snapshot["guilds"]
    g_info = snapshot["guilds"][str(guild_id)]
    assert g_info["guild_id"] == guild_id
    assert g_info["voice_channel_id"] == 888
    assert g_info["text_channel_id"] == 666
    assert g_info["current_message_id"] == 777
    assert g_info["playback_offset_seconds"] == 45.0
    assert len(g_info["queue"]) == 1
    assert g_info["loop_mode"] == "all"


@pytest.mark.asyncio
async def test_restore_expired_snapshot(mock_music_handler: MagicMock) -> None:
    provider = MusicRecoveryProvider(mock_music_handler)
    metadata = SnapshotMetadata(
        provider_name="music_player",
        snapshot_time=time.time() - 300.0,  # 300 seconds ago (> 180 TTL)
        ttl_seconds=180.0,
    )
    snapshot_data = {
        "guilds": {
            "999": {
                "guild_id": 999,
                "voice_channel_id": 888,
                "current_song": {"title": "Expired Song"},
            }
        }
    }

    # Should silently return and do nothing
    await provider.restore_from_snapshot(snapshot_data, metadata)
    assert len(mock_music_handler.queues) == 0


@pytest.mark.asyncio
async def test_restore_empty_channel_ignored(mock_music_handler: MagicMock) -> None:
    provider = MusicRecoveryProvider(mock_music_handler)
    metadata = SnapshotMetadata(
        provider_name="music_player",
        snapshot_time=time.time() - 10.0,
        ttl_seconds=180.0,
    )

    guild_id = 999
    guild = DummyGuild(guild_id)
    # Voice channel with only a bot
    v_chan = DummyVoiceChannel(channel_id=888, members=[DummyMember(bot=True)])
    t_chan = DummyTextChannel(channel_id=666)
    guild._channels[888] = v_chan
    guild._channels[666] = t_chan

    mock_music_handler.bot.guilds.append(guild)
    mock_music_handler.bot._guild_map[guild_id] = guild

    snapshot_data = {
        "guilds": {
            str(guild_id): {
                "guild_id": guild_id,
                "voice_channel_id": 888,
                "text_channel_id": 666,
                "current_song": {"title": "Test Song"},
            }
        }
    }

    await provider.restore_from_snapshot(snapshot_data, metadata)
    # v_chan.connect should not be called because all members are bots
    assert not v_chan.connect.called


@pytest.mark.asyncio
async def test_restore_success_flow(mock_music_handler: MagicMock) -> None:
    provider = MusicRecoveryProvider(mock_music_handler)
    metadata = SnapshotMetadata(
        provider_name="music_player",
        snapshot_time=time.time() - 5.0,
        ttl_seconds=180.0,
    )

    guild_id = 999
    guild = DummyGuild(guild_id)
    v_chan = DummyVoiceChannel(channel_id=888, members=[DummyMember(bot=False)])
    t_chan = DummyTextChannel(channel_id=666)
    old_msg = MagicMock()
    old_msg.delete = AsyncMock()
    t_chan.fetch_message.return_value = old_msg

    guild._channels[888] = v_chan
    guild._channels[666] = t_chan

    # Mock voice client connected upon v_chan.connect()
    vc = MagicMock()
    vc.channel = v_chan
    vc.is_connected.return_value = True
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    v_chan.connect.return_value = vc
    guild.voice_client = None

    mock_music_handler.bot.guilds.append(guild)
    mock_music_handler.bot._guild_map[guild_id] = guild

    song_info = {
        "title": "群青",
        "webpage_url": "https://www.youtube.com/watch?v=abc",
        "duration": 240,
    }
    snapshot_data = {
        "guilds": {
            str(guild_id): {
                "guild_id": guild_id,
                "voice_channel_id": 888,
                "text_channel_id": 666,
                "current_message_id": 777,
                "playback_offset_seconds": 65.0,
                "is_paused": False,
                "current_song": song_info,
                "queue": [{"title": "Next Song"}],
                "loop_mode": "none",
            }
        }
    }

    with patch.object(provider, "resume_playback", new_callable=AsyncMock) as mock_resume:
        await provider.restore_from_snapshot(snapshot_data, metadata)

        # 1. Old card was deleted
        assert t_chan.fetch_message.called
        assert old_msg.delete.called

        # 2. Voice channel connected
        assert v_chan.connect.called

        # 3. Queue restored
        assert len(mock_music_handler.queues[guild_id]) == 1

        # 4. resume_playback called with -ss offset 65.0
        assert mock_resume.called
        call_kwargs = mock_resume.call_args.kwargs
        assert call_kwargs["offset_seconds"] == 65.0
        assert call_kwargs["song"] == song_info
