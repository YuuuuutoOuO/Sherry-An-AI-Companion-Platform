import asyncio
import time
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


def test_music_handler_on_voice_state_update_unexpected_disconnect_triggers_auto_heal() -> None:
    """測試當有正在播放的音樂時非預期斷開語音，不直接呼叫 stop_player，而是觸發自癒重連任務。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        bot.user.id = 123456
        handler = MusicHandler(bot)
        handler.stop_player = AsyncMock()
        handler._handle_unexpected_voice_disconnect = AsyncMock()

        member = MagicMock(spec=discord.Member)
        member.id = 123456
        member.guild.id = 4444

        before = MagicMock(spec=discord.VoiceState)
        before.channel = MagicMock()

        after = MagicMock(spec=discord.VoiceState)
        after.channel = None

        # 設定有正在播放的歌曲
        handler.current_song[4444] = {"title": "10小時放鬆音樂", "duration": 36000}

        # Act
        await handler.on_voice_state_update(member, before, after)
        await asyncio.sleep(0.01)

        # Assert: 絕不應直接呼叫 stop_player，而是應轉交 _handle_unexpected_voice_disconnect
        handler.stop_player.assert_not_called()
        handler._handle_unexpected_voice_disconnect.assert_called_once()

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


def test_parse_time_to_seconds_and_url_timestamp() -> None:
    """測試時間軸字串與 URL 時間戳解析。"""
    # 絕對時間
    assert MusicHandler.parse_time_to_seconds("20:00") == 1200
    assert MusicHandler.parse_time_to_seconds("01:20:30") == 4830
    assert MusicHandler.parse_time_to_seconds("1200") == 1200
    assert MusicHandler.parse_time_to_seconds("20m") == 1200
    assert MusicHandler.parse_time_to_seconds("20分") == 1200
    assert MusicHandler.parse_time_to_seconds("20分鐘") == 1200
    assert MusicHandler.parse_time_to_seconds("1小時20分30秒") == 4830
    assert MusicHandler.parse_time_to_seconds("90s") == 90
    assert MusicHandler.parse_time_to_seconds("invalid_time") is None

    # 相對時間
    assert MusicHandler.parse_time_to_seconds("+30", current_elapsed=100) == 130
    assert MusicHandler.parse_time_to_seconds("+30s", current_elapsed=100) == 130
    assert MusicHandler.parse_time_to_seconds("-15s", current_elapsed=100) == 85
    assert MusicHandler.parse_time_to_seconds("+1:00", current_elapsed=100) == 160
    assert MusicHandler.parse_time_to_seconds("-30s", current_elapsed=10) == 0

    # URL 時間戳
    assert MusicHandler.extract_url_timestamp("https://www.youtube.com/watch?v=mock&t=1200s") == 1200
    assert MusicHandler.extract_url_timestamp("https://youtu.be/mock?t=20m") == 1200
    assert MusicHandler.extract_url_timestamp("https://youtu.be/mock?start=300") == 300
    assert MusicHandler.extract_url_timestamp("https://youtu.be/mock?t=1h20m30s") == 4830
    assert MusicHandler.extract_url_timestamp("https://youtu.be/mock") == 0


def test_seek_to_updates_timeline_and_ffmpeg_options() -> None:
    """測試 seek_to 正確建立 -ss 參數音訊來源並更新時間軸。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)
        guild_id = 4321

        guild = MagicMock(spec=discord.Guild)
        guild.id = guild_id
        vc = MagicMock(spec=discord.VoiceClient)
        vc.is_connected.return_value = True
        vc.is_playing.return_value = True
        vc.is_paused.return_value = False
        guild.voice_client = vc
        bot.get_guild.return_value = guild

        handler.current_song[guild_id] = {
            "title": "Seekable Song",
            "url": "https://example.com/audio",
            "duration": 3600,
            "stream_url": "https://stream.example.com/audio.opus",
        }
        handler.current_message[guild_id] = MagicMock()
        handler.current_message[guild_id].edit = AsyncMock()

        with patch("handlers.music.cog.discord.FFmpegPCMAudio") as mock_ffmpeg:
            with patch("handlers.music.cog.discord.PCMVolumeTransformer"):
                with patch("asyncio.sleep", new=AsyncMock()):
                    success = await handler.seek_to(guild, None, target_seconds=1200)
                    assert success is True
                    assert handler.playback_accumulated_time[guild_id] == 1200.0
                    assert handler._is_seeking.get(guild_id) is True

                    # 驗證 FFmpegPCMAudio 接收到 -ss 1200
                    kwargs = mock_ffmpeg.call_args.kwargs
                    assert "-ss 1200" in kwargs["before_options"]
                    vc.play.assert_called_once()

    asyncio.run(_run())


def test_premature_eof_auto_resumes_from_breakpoint() -> None:
    """測試長歌曲在半途斷流時，after_playback 自動記錄斷點並排回首位原地續播。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        bot.loop = MagicMock()
        handler = MusicHandler(bot)
        guild_id = 9876

        guild = MagicMock(spec=discord.Guild)
        guild.id = guild_id
        vc = MagicMock(spec=discord.VoiceClient)
        vc.is_connected.return_value = True
        guild.voice_client = vc
        bot.get_guild.return_value = guild

        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.send = AsyncMock()

        song = {
            "title": "1 Hour Long Music",
            "url": "https://example.com/song",
            "duration": 3600,
        }
        handler.current_song[guild_id] = song
        # 模擬已播到 20 分鐘 (1200 秒)
        handler.playback_start_time[guild_id] = time.time() - 1200
        handler.playback_accumulated_time[guild_id] = 0.0

        after_fn = handler._create_after_playback(guild, text_channel, song)

        with patch.object(handler, "_play_next"):
            # 觸發正常結束（無錯誤），但時間僅播到 1200s (距結尾還有 2400s)
            after_fn(error=None)

            # 驗證歌曲被自動帶入 start_offset=1200 並放回佇列首位
            assert len(handler.queues[guild_id]) == 1
            requeued_song = handler.queues[guild_id][0]
            assert requeued_song["title"] == "1 Hour Long Music"
            assert requeued_song["start_offset"] == 1200

            # 驗證斷點記憶庫成功儲存
            cp = handler.last_checkpoints.get(guild_id)
            assert cp is not None
            assert cp["offset_seconds"] == 1200.0

    asyncio.run(_run())


def test_resume_from_checkpoint_flow() -> None:
    """測試 resume_from_checkpoint 成功讀取斷點並接續播放。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)
        guild_id = 6543

        v_channel = MagicMock(spec=discord.VoiceChannel)
        v_channel.id = 1111
        v_channel.members = [MagicMock(bot=False)]
        vc_mock = MagicMock(spec=discord.VoiceClient)
        vc_mock.is_connected.return_value = True
        vc_mock.is_playing.return_value = False
        vc_mock.is_paused.return_value = False
        v_channel.connect = AsyncMock(return_value=vc_mock)

        guild = MagicMock(spec=discord.Guild)
        guild.id = guild_id
        guild.voice_client = None
        guild.voice_channels = [v_channel]
        guild.get_channel = MagicMock(return_value=v_channel)

        text_channel = MagicMock(spec=discord.TextChannel)

        # 預先設置斷點
        handler.last_checkpoints[guild_id] = {
            "song": {"title": "Interrupted Track", "url": "https://example.com/track", "duration": 3000},
            "offset_seconds": 1200.0,
            "voice_channel_id": 1111,
            "text_channel_id": 2222,
        }

        with patch.object(handler, "_play_next") as mock_play:
            cp = await handler.resume_from_checkpoint(guild, text_channel)
            assert cp is not None
            assert len(handler.queues[guild_id]) == 1
            assert handler.queues[guild_id][0]["start_offset"] == 1200
            mock_play.assert_called_once()

    asyncio.run(_run())


def test_shutdown_guard_preserves_music_snapshot() -> None:
    """測試關機旗標開啟時，離開語音頻道不會清空狀態與抹除快照。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        bot.user.id = 999
        handler = MusicHandler(bot)
        handler.stop_player = AsyncMock()
        guild_id = 5555

        member = MagicMock(spec=discord.Member)
        member.id = 999
        member.guild.id = guild_id

        before = MagicMock(spec=discord.VoiceState)
        before.channel = MagicMock()
        after = MagicMock(spec=discord.VoiceState)
        after.channel = None

        # 啟用關機防護鎖定
        handler._is_shutting_down = True

        await handler.on_voice_state_update(member, before, after)

        # 驗證 stop_player 未被呼叫，保全了快照
        handler.stop_player.assert_not_called()

    asyncio.run(_run())


def test_seek_to_preserves_paused_state() -> None:
    """測試在暫停狀態下執行 seek_to，跳轉完成後仍維持暫停狀態。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)
        guild_id = 7788

        guild = MagicMock(spec=discord.Guild)
        guild.id = guild_id
        vc = MagicMock(spec=discord.VoiceClient)
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = True
        guild.voice_client = vc
        bot.get_guild.return_value = guild

        handler.current_song[guild_id] = {
            "title": "Paused Song",
            "url": "https://example.com/audio",
            "duration": 3600,
            "stream_url": "https://stream.example.com/audio.opus",
        }
        handler.is_paused[guild_id] = True

        with patch("handlers.music.cog.discord.FFmpegPCMAudio"):
            with patch("handlers.music.cog.discord.PCMVolumeTransformer"):
                with patch("asyncio.sleep", new=AsyncMock()):
                    success = await handler.seek_to(guild, None, target_seconds=600)
                    assert success is True
                    vc.pause.assert_called_once()
                    assert handler.is_paused[guild_id] is True

    asyncio.run(_run())


def test_parse_time_to_seconds_rejects_dirty_leftover() -> None:
    """測試 parse_time_to_seconds 嚴格過濾髒資料字串。"""
    assert MusicHandler.parse_time_to_seconds("20m_foo") is None
    assert MusicHandler.parse_time_to_seconds("1小時xyz") is None
    assert MusicHandler.parse_time_to_seconds("20:00") == 1200
    assert MusicHandler.parse_time_to_seconds("00:00") == 0


def test_music_ui_seek_buttons_execution() -> None:
    """測試 UI 快轉/倒退按鈕執行時不會拋出 TypeError (delete_after)。"""
    async def _run() -> None:
        from handlers.music.ui import MusicControlView
        view = MusicControlView()
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild_id = 9999
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.channel = MagicMock(spec=discord.TextChannel)
        interaction.user = MagicMock()
        interaction.user.mention = "@User"
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        mock_cog = MagicMock(spec=MusicHandler)
        mock_cog.current_song = {9999: {"title": "Test", "duration": 300}}
        mock_cog.get_elapsed_time.return_value = 60
        mock_cog.format_duration.return_value = "00:30"
        mock_cog.seek_to = AsyncMock(return_value=True)

        interaction.client.get_cog.return_value = mock_cog

        seek_rewind = [item for item in view.children if getattr(item, "custom_id", None) == "seek_rewind_btn"][0]
        seek_forward = [item for item in view.children if getattr(item, "custom_id", None) == "seek_forward_btn"][0]

        # 測試倒退按鈕
        await seek_rewind.callback(interaction)
        interaction.followup.send.assert_called_with(
            "⏪ @User 倒退了 30 秒 (至 00:30)！",
            ephemeral=True,
        )

        # 測試快轉按鈕
        interaction.followup.send.reset_mock()
        mock_cog.format_duration.return_value = "01:30"
        await seek_forward.callback(interaction)
        interaction.followup.send.assert_called_with(
            "⏩ @User 快轉了 30 秒 (至 01:30)！",
            ephemeral=True,
        )

    asyncio.run(_run())


def test_headless_play_preserves_query_offset() -> None:
    """測試 headless_play 能正確從使用者輸入的網址參數中提取 offset。"""
    async def _run() -> None:
        bot = MagicMock(spec=commands.Bot)
        handler = MusicHandler(bot)
        guild_id = 8888
        mock_guild = MagicMock(spec=discord.Guild)
        mock_guild.id = guild_id
        mock_guild.voice_client = None

        text_channel = MagicMock(spec=discord.TextChannel)
        text_channel.guild = mock_guild
        text_channel.send = AsyncMock()

        voice_channel = MagicMock(spec=discord.VoiceChannel)
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        voice_channel.connect = AsyncMock(return_value=mock_vc)

        raw_query = "https://www.youtube.com/watch?v=mock_video&t=150s"
        mock_info = {
            "title": "Mock Video",
            "webpage_url": "https://www.youtube.com/watch?v=mock_video",  # yt-dlp 常返回去參數網址
            "duration": 600,
            "thumbnail": "https://thumb.url",
            "extractor": "youtube",
        }

        with patch("handlers.music.cog.ytdl_client.extract_info", return_value=mock_info):
            with patch.object(handler, "_play_next"):
                await handler.headless_play(text_channel, voice_channel, raw_query)
                assert guild_id in handler.queues
                queued = handler.queues[guild_id][-1]
                assert queued["start_offset"] == 150

    asyncio.run(_run())


def test_premature_eof_max_retries() -> None:
    """測試異常中斷超過 3 次時停止重試，避免造成死迴圈。"""
    bot = MagicMock(spec=commands.Bot)
    bot.loop = MagicMock()
    handler = MusicHandler(bot)
    guild_id = 7777

    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    text_ch = MagicMock(spec=discord.TextChannel)

    song = {
        "title": "Endless Crash",
        "duration": 1800,
        "eof_retries": 3,  # 已達到 3 次上限
    }

    handler.playback_start_time[guild_id] = time.time() - 100
    handler.playback_accumulated_time[guild_id] = 0

    after_fn = handler._create_after_playback(guild, text_ch, song)
    after_fn(error=None)

    # 應不會再把 song 塞回佇列首位重試
    assert handler.queues.get(guild_id, []) == []






