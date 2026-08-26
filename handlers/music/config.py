import os
import logging
import yt_dlp
import settings

logger = logging.getLogger(__name__)

import shutil

# Cross-platform FFmpeg path resolution (Windows ffmpeg.exe vs Linux ffmpeg)
_system_ffmpeg = shutil.which("ffmpeg")
if os.path.exists(os.path.join(settings.FFMPEG, "ffmpeg.exe")):
    FFMPEG_EXE_PATH = os.path.join(settings.FFMPEG, "ffmpeg.exe")
elif os.path.exists(os.path.join(settings.FFMPEG, "bin", "ffmpeg.exe")):
    FFMPEG_EXE_PATH = os.path.join(settings.FFMPEG, "bin", "ffmpeg.exe")
elif os.path.exists(os.path.join(settings.FFMPEG, "ffmpeg")):
    FFMPEG_EXE_PATH = os.path.join(settings.FFMPEG, "ffmpeg")
elif _system_ffmpeg:
    FFMPEG_EXE_PATH = _system_ffmpeg
else:
    # 支援常見本機與套件路徑
    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]
        FFMPEG_EXE_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        FFMPEG_EXE_PATH = "ffmpeg"

# Removed user_agent string that crashed Windows ffmpeg
# Enabled loudnorm for volume normalization
FFMPEG_OPTIONS = {
    'before_options': getattr(settings, "MUSIC_FFMPEG_BEFORE_OPTIONS", '-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx -reconnect_delay_max 5 -loglevel error'),
    'options': getattr(settings, "MUSIC_FFMPEG_OPTIONS", '-vn -af "loudnorm=I=-9:LRA=11:TP=-1.0"')
}

# YouTube cookies 檔案路徑（需手動匯出瀏覽器 cookies 至此）
# 參見: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
_COOKIE_PATH = getattr(settings, "YOUTUBE_COOKIES_PATH", os.path.join(settings.DATA_DIR, "cookies.txt"))

# Force IPv4 to counter YouTube 403 blocks & use android player client
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': False,
    'playlistend': getattr(settings, "MUSIC_PLAYLIST_MAX_ITEMS", 100),
    'nocheckcertificate': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': 'in_playlist',
    'ignoreerrors': True,
    'age_limit': 99,
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web'],
        }
    },
}

if os.path.exists(_COOKIE_PATH):
    YTDL_OPTIONS['cookiefile'] = _COOKIE_PATH
    logger.info("已載入 YouTube cookies: %s", _COOKIE_PATH)
else:
    logger.warning("未找到 YouTube cookies 檔案 (%s)，播放可能被 YouTube 阻擋", _COOKIE_PATH)

ytdl_client = yt_dlp.YoutubeDL(YTDL_OPTIONS)