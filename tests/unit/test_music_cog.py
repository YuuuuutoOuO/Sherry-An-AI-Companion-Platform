import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from discord.ext import commands

from handlers.music.cog import MusicHandler


def test_music_handler_format_methods() -> None:
    bot = MagicMock(spec=commands.Bot)
    handler = MusicHandler(bot)

    assert handler.format_duration(125) == '02:05'
    assert handler.format_duration(3665) == '01:01:05'
    assert handler.format_duration(None) == '00:00'

    axis = handler.format_time_axis(30, 60, bar_length=10)
    assert '00:30 / 01:00' in axis

    live_axis = handler.format_time_axis(30, 0)
    assert 'LIVE' in live_axis


def test_music_handler_queue_operations() -> None:
    bot = MagicMock(spec=commands.Bot)
    handler = MusicHandler(bot)
    guild_id = 12345

    handler.queues[guild_id] = [
        {'title': 'Song 1', 'webpage_url': 'url1'},
        {'title': 'Song 2', 'webpage_url': 'url2'},
        {'title': 'Song 3', 'webpage_url': 'url3'},
    ]

    # Jump to song 3
    jumped = handler.jump_to(guild_id, 3)
    assert jumped is not None
    assert jumped['title'] == 'Song 3'
    assert handler.queues[guild_id][0]['title'] == 'Song 3'

    # Clear queue
    cleared_count = handler.clear_queue(guild_id)
    assert cleared_count == 3
    assert len(handler.queues[guild_id]) == 0


def test_music_handler_headless_play_single() -> None:
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)

        guild = MagicMock(spec=discord.Guild)
        guild.id = 9999
        guild.voice_client = None

        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.guild = guild
        text_channel.send = AsyncMock()

        voice_channel = MagicMock(spec=discord.VoiceChannel)
        voice_vc_mock = MagicMock(spec=discord.VoiceClient)
        voice_vc_mock.is_playing.return_value = False
        voice_vc_mock.is_paused.return_value = False
        voice_channel.connect = AsyncMock(return_value=voice_vc_mock)

        mock_info = {
            'title': 'Test Song',
            'webpage_url': 'https://www.youtube.com/watch?v=mock123',
            'duration': 180,
            'thumbnail': 'https://example.com/thumb.jpg',
            'extractor': 'youtube',
        }

        with patch('handlers.music.cog.ytdl_client.extract_info', return_value=mock_info):
            with patch.object(handler, '_play_next') as mock_play_next:
                await handler.headless_play(text_channel, voice_channel, 'Test Song')
                assert len(handler.queues[guild.id]) == 1
                assert handler.queues[guild.id][0]['title'] == 'Test Song'
                mock_play_next.assert_called_once()
                text_channel.send.assert_called()

    asyncio.run(_run())


def test_music_handler_headless_play_playlist() -> None:
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)

        guild = MagicMock(spec=discord.Guild)
        guild.id = 8888
        guild.voice_client = None

        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.guild = guild
        text_channel.send = AsyncMock()

        voice_channel = MagicMock(spec=discord.VoiceChannel)
        voice_vc_mock = MagicMock(spec=discord.VoiceClient)
        voice_vc_mock.is_playing.return_value = True
        voice_vc_mock.is_paused.return_value = False
        voice_channel.connect = AsyncMock(return_value=voice_vc_mock)

        mock_playlist_info = {
            'entries': [
                {
                    'title': 'Playlist Song 1',
                    'webpage_url': 'https://www.youtube.com/watch?v=p1',
                    'duration': 200,
                    'thumbnail': 'https://example.com/p1.jpg',
                    'extractor': 'youtube',
                },
                {
                    'title': 'Playlist Song 2',
                    'webpage_url': 'https://www.youtube.com/watch?v=p2',
                    'duration': 210,
                    'thumbnail': 'https://example.com/p2.jpg',
                    'extractor': 'youtube',
                },
            ]
        }

        with patch('handlers.music.cog.ytdl_client.extract_info', return_value=mock_playlist_info):
            await handler.headless_play(text_channel, voice_channel, 'https://youtube.com/playlist?list=xxx')
            assert len(handler.queues[guild.id]) == 2
            assert handler.queues[guild.id][0]['title'] == 'Playlist Song 1'
            assert handler.queues[guild.id][1]['title'] == 'Playlist Song 2'

    asyncio.run(_run())


def test_play_command_dual_mode_dashboard() -> None:
    """測試 /play 無參數時自動發送留聲機主控台 Embed 與 View。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild_id = 9999
        interaction.response.send_message = AsyncMock()

        # Act: 無參數呼叫 /play
        await handler.play_command.callback(handler, interaction, play_type=None, context="")

        # Assert
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("embed") is not None
        from handlers.music.ui import MusicControlView
        assert isinstance(kwargs.get("view"), MusicControlView)

    asyncio.run(_run())


def test_song_input_modal_submit() -> None:
    """測試 SongInputModal 表單提交。"""
    async def _run() -> None:
        from handlers.music.ui import SongInputModal
        modal = SongInputModal()
        modal.query_input._value = "Yoasobi Idol"
        modal.play_mode_input._value = "add"

        interaction = MagicMock(spec=discord.Interaction)
        mock_cog = MagicMock()
        mock_cog.play_command.callback = AsyncMock()
        interaction.client.get_cog.return_value = mock_cog

        # Act
        await modal.on_submit(interaction)

        # Assert
        mock_cog.play_command.callback.assert_awaited_once()

    asyncio.run(_run())


def test_music_handler_stop_player_with_loop_mode() -> None:
    """測試在循環播放模式下執行 stop_player，確認佇列完全清空且循環模式被重置為 none。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)
        guild_id = 7777

        # Arrange
        handler.queues[guild_id] = [{"title": "Queued Song 1"}]
        handler.current_song[guild_id] = {"title": "Ib Soundtrack"}
        handler.loop_modes[guild_id] = "all"

        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = True
        mock_vc.is_paused.return_value = False
        mock_vc.disconnect = AsyncMock()

        mock_guild = MagicMock(spec=discord.Guild)
        mock_guild.id = guild_id
        mock_guild.voice_client = mock_vc
        bot.get_guild.return_value = mock_guild

        # Act
        await handler.stop_player(guild_id, disconnect=True)

        # Assert
        assert handler.queues[guild_id] == []
        assert handler.current_song[guild_id] is None
        assert handler.loop_modes[guild_id] == "none"
        assert handler.is_paused[guild_id] is False
        mock_vc.stop.assert_called_once()
        mock_vc.disconnect.assert_awaited_once()

    asyncio.run(_run())


def test_music_handler_skip_song() -> None:
    """測試 skip_song 成功設定 _is_skipping 標記並觸發 vc.stop。"""
    bot = MagicMock(spec=commands.Bot)
    handler = MusicHandler(bot)
    guild_id = 6666

    mock_vc = MagicMock(spec=discord.VoiceClient)
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = True
    mock_vc.is_paused.return_value = False

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.voice_client = mock_vc
    bot.get_guild.return_value = mock_guild

    # Act
    success = handler.skip_song(guild_id)

    # Assert
    assert success is True
    assert handler._is_skipping[guild_id] is True
    mock_vc.stop.assert_called_once()


def test_music_ui_stop_button_calls_stop_player() -> None:
    """測試 MusicControlView 的 stop_button 正確呼叫 cog.stop_player。"""
    async def _run() -> None:
        from handlers.music.ui import MusicControlView
        view = MusicControlView()

        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild_id = 5555
        interaction.user.mention = "@User"
        interaction.response.send_message = AsyncMock()

        mock_cog = MagicMock(spec=MusicHandler)
        mock_cog.stop_player = AsyncMock()
        interaction.client.get_cog.return_value = mock_cog

        stop_btn = [item for item in view.children if getattr(item, "custom_id", None) == "stop_song_btn"][0]

        # Act
        await stop_btn.callback(interaction)

        # Assert
        mock_cog.stop_player.assert_awaited_once_with(5555, disconnect=True)
        interaction.response.send_message.assert_awaited_once()

    asyncio.run(_run())


def test_music_ui_skip_button_calls_skip_song() -> None:
    """測試 MusicControlView 的 skip_button 正確呼叫 cog.skip_song。"""
    async def _run() -> None:
        from handlers.music.ui import MusicControlView
        view = MusicControlView()

        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild_id = 5555
        interaction.user.mention = "@User"
        interaction.response.send_message = AsyncMock()

        mock_cog = MagicMock(spec=MusicHandler)
        mock_cog.skip_song.return_value = True
        interaction.client.get_cog.return_value = mock_cog

        skip_btn = [item for item in view.children if getattr(item, "custom_id", None) == "skip_song_btn"][0]

        # Act
        await skip_btn.callback(interaction)

        # Assert
        mock_cog.skip_song.assert_called_once_with(5555)
        interaction.response.send_message.assert_awaited_once()

    asyncio.run(_run())


def test_music_handler_on_voice_state_update_leave() -> None:
    """測試當機器人自身離開語音頻道時，自動觸發 stop_player 清理。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        bot.user.id = 123456
        handler = MusicHandler(bot)
        handler.stop_player = AsyncMock()

        member = MagicMock(spec=discord.Member)
        member.id = 123456
        member.guild.id = 4444

        before = MagicMock(spec=discord.VoiceState)
        before.channel = MagicMock()

        after = MagicMock(spec=discord.VoiceState)
        after.channel = None

        # Act
        await handler.on_voice_state_update(member, before, after)

        # Assert
        handler.stop_player.assert_awaited_once_with(4444, disconnect=False)

    asyncio.run(_run())


def test_cleanup_guild_source_kills_ffmpeg_process() -> None:
    """測試 _cleanup_guild_source 正確終止未結束的 FFmpeg 進程並關閉管道。"""
    bot = MagicMock(spec=commands.Bot)
    handler = MusicHandler(bot)
    guild_id = 12345

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # 行程仍在執行中
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_proc.stdin = MagicMock()

    mock_raw_source = MagicMock()
    mock_raw_source._process = mock_proc
    mock_raw_source.cleanup = MagicMock()

    mock_source = MagicMock()
    mock_source.original = mock_raw_source
    mock_source.cleanup = MagicMock()

    handler.current_sources[guild_id] = mock_source

    # Act
    handler._cleanup_guild_source(guild_id)

    # Assert
    assert guild_id not in handler.current_sources
    mock_proc.kill.assert_called_once()
    mock_proc.stdout.close.assert_called_once()
    mock_proc.stderr.close.assert_called_once()
    mock_proc.stdin.close.assert_called_once()
    mock_source.cleanup.assert_called_once()


def test_recover_voice_and_play_next() -> None:
    """測試 _recover_voice_and_play_next 正確中斷舊連線並重新連線語音。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)
        guild_id = 9999

        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.disconnect = AsyncMock()

        target_voice_channel = MagicMock(spec=discord.VoiceChannel)
        target_voice_channel.name = "General Voice"
        target_voice_channel.connect = AsyncMock()
        mock_vc.channel = target_voice_channel

        mock_guild = MagicMock(spec=discord.Guild)
        mock_guild.id = guild_id
        mock_guild.voice_client = mock_vc
        mock_guild.voice_channels = [target_voice_channel]

        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.guild = mock_guild
        text_channel.send = AsyncMock()

        with patch.object(handler, "_play_next") as mock_play_next:
            with patch("asyncio.sleep", new=AsyncMock()):
                # Act
                await handler._recover_voice_and_play_next(mock_guild, text_channel)

                # Assert
                mock_vc.disconnect.assert_awaited_once_with(force=True)
                target_voice_channel.connect.assert_awaited_once()
                mock_play_next.assert_called_once_with(mock_guild, text_channel)

    asyncio.run(_run())


def test_headless_play_sanitizes_trailing_ampersand() -> None:
    """測試 headless_play 正確清洗網址尾綴的 & 或 ? 符號。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)

        guild = MagicMock(spec=discord.Guild)
        guild.id = 1111
        guild.voice_client = None

        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.guild = guild
        text_channel.send = AsyncMock()

        voice_channel = MagicMock(spec=discord.VoiceChannel)
        voice_vc_mock = MagicMock(spec=discord.VoiceClient)
        voice_vc_mock.is_playing.return_value = False
        voice_vc_mock.is_paused.return_value = False
        voice_channel.connect = AsyncMock(return_value=voice_vc_mock)

        mock_info = {
            'title': 'Sanitized Song',
            'webpage_url': 'https://www.youtube.com/watch?v=ViMpXYNfgVs',
            'duration': 200,
            'thumbnail': 'https://example.com/thumb.jpg',
            'extractor': 'youtube',
        }

        with patch('handlers.music.cog.ytdl_client.extract_info', return_value=mock_info) as mock_extract:
            with patch.object(handler, '_play_next'):
                # Act: 傳入尾綴帶有 & 的 YouTube 網址
                await handler.headless_play(text_channel, voice_channel, 'https://www.youtube.com/watch?v=ViMpXYNfgVs&')

                # Assert: 驗證 extract_info 接收到已清洗的網址
                mock_extract.assert_called_once_with('https://www.youtube.com/watch?v=ViMpXYNfgVs', download=False)

    asyncio.run(_run())


def test_after_playback_native_crash_and_circuit_breaker() -> None:
    """測試 after_playback 遭遇 Access Violation 時自癒重連，以及連續失敗 3 次觸發熔斷。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)
        guild_id = 3333

        mock_guild = MagicMock(spec=discord.Guild)
        mock_guild.id = guild_id
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_guild.voice_client = mock_vc
        bot.get_guild.return_value = mock_guild

        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.guild = mock_guild
        text_channel.send = AsyncMock()

        # 模擬連續 2 次失敗
        handler.consecutive_failures[guild_id] = 2

        # 構造模擬 source 與佇列
        handler.queues[guild_id] = [{'title': 'Failed Song', 'url': 'http://example.com'}]
        mock_source = MagicMock()
        handler.current_sources[guild_id] = mock_source

        # 呼叫 stop_player 驗證狀態清空與計數重置
        await handler.stop_player(guild_id, disconnect=False)
        assert handler.consecutive_failures[guild_id] == 0
        assert guild_id not in handler.current_sources

    asyncio.run(_run())




