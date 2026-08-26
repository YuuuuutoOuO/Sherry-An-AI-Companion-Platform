"""
mod/livestream/service.py
=========================
Core business logic service for the livestream module, supporting YouTube and Local Video Library.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import time
from typing import Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

from mod.livestream import config
from mod.livestream.contracts import ILivestreamBroadcaster, ILivestreamService
from mod.livestream.domain import (
    MediaItem,
    PlayAction,
    PlaybackStatus,
    StreamEvent,
    StreamPlaybackState,
)

logger = logging.getLogger(__name__)

# Standard YouTube 11-character video ID regex pattern
_YT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")

# Supported video file extensions for local library
_SUPPORTED_EXTENSIONS: tuple[str, ...] = config.SUPPORTED_VIDEO_EXTENSIONS


def _natural_sort_key(s: str) -> list[int | str]:
    """Helper for natural alphanumeric sorting (e.g. ep2 before ep10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]


class LivestreamService(ILivestreamService):
    """Core domain service orchestrating livestream video playback state and business rules."""

    def __init__(self, broadcaster: ILivestreamBroadcaster, video_dir: Optional[Path] = None) -> None:
        """Initializes LivestreamService with a broadcaster dependency.

        Args:
            broadcaster: Abstract broadcaster instance for event dispatching.
            video_dir: Optional custom Path to the video library directory.
        """
        self._broadcaster: ILivestreamBroadcaster = broadcaster
        self._video_dir: Path = video_dir if video_dir is not None else config.VIDEO_DIR

    # -----------------------------------------------------------------------
    # Local Video Library Scanning & Resolution
    # -----------------------------------------------------------------------

    def scan_video_library(self) -> dict[str, list[Path]]:
        """Scans the ./Video folder and discovers all single-level and two-level video series.

        Rules:
          - Single-level folder (e.g. ./Video/海綿寶寶/01.mp4) -> '海綿寶寶'
          - Two-level folder (e.g. ./Video/南方公園/第一季/01.mp4) -> '南方公園-第一季'

        Returns:
            Mapping of series name to naturally sorted list of video file paths.
        """
        library: dict[str, list[Path]] = {}
        if not self._video_dir.exists():
            return library

        for parent in sorted(self._video_dir.iterdir()):
            if not parent.is_dir() or parent.name.startswith("."):
                continue

            # Check for subdirectories (e.g. 南方公園/第一季, 南方公園/第二季)
            sub_dirs = [s for s in sorted(parent.iterdir()) if s.is_dir() and not s.name.startswith(".")]
            if sub_dirs:
                for sub in sub_dirs:
                    videos = [
                        f for f in sub.iterdir()
                        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
                    ]
                    if videos:
                        videos.sort(key=lambda p: _natural_sort_key(p.name))
                        series_name = f"{parent.name}-{sub.name}"
                        library[series_name] = videos

            # Also check if parent directly contains video files (e.g. 海綿寶寶/01.mp4)
            direct_videos = [
                f for f in parent.iterdir()
                if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
            ]
            if direct_videos:
                direct_videos.sort(key=lambda p: _natural_sort_key(p.name))
                library[parent.name] = direct_videos

        return library

    def get_available_series(self) -> list[str]:
        """Returns a list of all currently available series names.

        Returns:
            List of series names.
        """
        return sorted(list(self.scan_video_library().keys()))

    def get_video_file_path(self, series_name: str, episode: int) -> Optional[Path]:
        """Resolves the physical file Path for a given series name and 1-based episode index.

        Args:
            series_name: The series name.
            episode: 1-based episode index.

        Returns:
            The Path to the file, or None if not found.
        """
        library = self.scan_video_library()
        videos = library.get(series_name)
        if not videos:
            # Case-insensitive fallback
            for key, val in library.items():
                if key.lower() == series_name.lower():
                    videos = val
                    break

        if not videos:
            return None

        idx = max(1, min(episode, len(videos))) - 1
        return videos[idx]

    # -----------------------------------------------------------------------
    # YouTube Info Extraction
    # -----------------------------------------------------------------------

    def extract_youtube_info(self, url: str) -> Tuple[str, int]:
        """Extracts the YouTube video ID and optional start offset seconds from a URL.

        Args:
            url: YouTube URL or video ID string.

        Returns:
            A tuple of (video_id, start_seconds).

        Raises:
            ValueError: If no valid YouTube video ID could be extracted.
        """
        clean_url = url.strip()
        if not clean_url:
            raise ValueError("提供的影片網址不能為空。")

        # Direct 11-char ID check
        if _YT_ID_PATTERN.match(clean_url):
            return clean_url, 0

        # Ensure scheme is present for urlparse if starting with domain
        parse_target = clean_url
        if not parse_target.startswith(("http://", "https://")):
            parse_target = "https://" + parse_target

        try:
            parsed = urlparse(parse_target)
        except Exception as exc:
            raise ValueError(f"無效的 URL 格式: {clean_url}") from exc

        host = (parsed.hostname or "").lower()
        path = parsed.path
        query_params = parse_qs(parsed.query)

        video_id: Optional[str] = None
        start_seconds: int = 0

        # Parse start seconds if 't' or 'start' query param exists
        time_raw = ""
        if "t" in query_params:
            time_raw = query_params["t"][0]
        elif "start" in query_params:
            time_raw = query_params["start"][0]

        if time_raw:
            start_seconds = self._parse_time_param(time_raw)

        # 1. youtu.be / <id>
        if host in ("youtu.be", "www.youtu.be"):
            parts = [p for p in path.split("/") if p]
            if parts and _YT_ID_PATTERN.match(parts[0]):
                video_id = parts[0]

        # 2. youtube.com
        elif "youtube.com" in host or "youtube-nocookie.com" in host:
            if path == "/watch" and "v" in query_params:
                candidate = query_params["v"][0]
                if _YT_ID_PATTERN.match(candidate):
                    video_id = candidate
            elif path.startswith("/embed/"):
                parts = [p for p in path[len("/embed/"):].split("/") if p]
                if parts and _YT_ID_PATTERN.match(parts[0]):
                    video_id = parts[0]
            elif path.startswith("/shorts/"):
                parts = [p for p in path[len("/shorts/"):].split("/") if p]
                if parts and _YT_ID_PATTERN.match(parts[0]):
                    video_id = parts[0]
            elif path.startswith("/v/"):
                parts = [p for p in path[len("/v/"):].split("/") if p]
                if parts and _YT_ID_PATTERN.match(parts[0]):
                    video_id = parts[0]

        if not video_id:
            raise ValueError(f"無法從網址中解析出有效的 YouTube 影片 ID: {clean_url}")

        return video_id, start_seconds

    def _parse_time_param(self, raw_time: str) -> int:
        """Parses timestamp parameter into integer seconds (e.g. '120', '1m30s', '1h2m3s')."""
        raw = raw_time.strip().lower()
        if raw.isdigit():
            return int(raw)

        pattern = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?")
        match = pattern.fullmatch(raw)
        if match:
            hours = int(match.group(1) or 0)
            mins = int(match.group(2) or 0)
            secs = int(match.group(3) or 0)
            return hours * 3600 + mins * 60 + secs
        return 0

    # -----------------------------------------------------------------------
    # Playback Commands
    # -----------------------------------------------------------------------

    async def play_video(
        self,
        action: PlayAction,
        context: str,
        requester_name: str = "Unknown",
        requester_avatar: str = "",
    ) -> StreamPlaybackState:
        """Plays video based on action and context (YouTube or Local)."""
        if action == PlayAction.YOUTUBE:
            video_id, start_sec = self.extract_youtube_info(context)
            title = f"YouTube: {video_id}"
            media_item = MediaItem(
                action=action,
                video_id=video_id,
                original_url=context.strip(),
                title=title,
                requester_name=requester_name,
                requester_avatar=requester_avatar,
                requested_at=time.time(),
                start_seconds=start_sec,
            )
        elif action in (PlayAction.LOCAL, PlayAction.FILE):
            return await self.play_local_video(
                series_name=context,
                episode=1,
                requester_name=requester_name,
                requester_avatar=requester_avatar,
            )
        else:
            raise ValueError(f"尚未支援的播放來源類型: {action}")

        current = self._broadcaster.get_state()
        new_state = StreamPlaybackState(
            status=PlaybackStatus.PLAYING,
            current_media=media_item,
            started_at=time.time(),
            volume=current.volume,
            is_muted=current.is_muted,
            queue=(),
        )

        event = StreamEvent(
            event_type="play",
            state=new_state,
            timestamp=time.time(),
            message=f"開始播放 {media_item.title}",
        )

        await self._broadcaster.broadcast(event)
        logger.info(
            "Livestream playback updated to PLAYING for video_id=%s requested by %s",
            video_id,
            requester_name,
        )
        return new_state

    async def play_local_video(
        self,
        series_name: str,
        episode: int = 1,
        requester_name: str = "Unknown",
        requester_avatar: str = "",
    ) -> StreamPlaybackState:
        """Plays a local video file from the ./Video directory with series and episode index."""
        library = self.scan_video_library()
        target_series = series_name.strip()
        videos = library.get(target_series)

        if not videos:
            # Case-insensitive search
            for k, v in library.items():
                if k.lower() == target_series.lower():
                    target_series = k
                    videos = v
                    break

        if not videos:
            available = ", ".join(library.keys()) or "（目前目錄無影片）"
            raise ValueError(f"找不到影片系列 '{series_name}'。目前可用系列: {available}")

        total_episodes = len(videos)
        if episode < 1:
            episode = 1
        elif episode > total_episodes:
            episode = total_episodes

        selected_file = videos[episode - 1]
        stream_url = f"/api/video/stream?series={quote(target_series)}&episode={episode}"
        title = f"{target_series} - 第 {episode} 集 / 共 {total_episodes} 集 ({selected_file.name})"

        media_item = MediaItem(
            action=PlayAction.LOCAL,
            video_id=f"{target_series}_ep{episode}",
            original_url=stream_url,
            title=title,
            requester_name=requester_name,
            requester_avatar=requester_avatar,
            requested_at=time.time(),
            series_name=target_series,
            episode_index=episode,
            total_episodes=total_episodes,
            file_path=str(selected_file),
            stream_url=stream_url,
        )

        current = self._broadcaster.get_state()
        new_state = StreamPlaybackState(
            status=PlaybackStatus.PLAYING,
            current_media=media_item,
            started_at=time.time(),
            volume=current.volume,
            is_muted=current.is_muted,
            queue=(),
        )

        event = StreamEvent(
            event_type="play",
            state=new_state,
            timestamp=time.time(),
            message=f"開始播放本機影片: {title}",
        )

        await self._broadcaster.broadcast(event)
        logger.info(
            "Livestream local video started: %s (ep %d/%d) requested by %s",
            target_series,
            episode,
            total_episodes,
            requester_name,
        )
        return new_state

    async def next_episode(self) -> StreamPlaybackState:
        """Advances to the next episode in the current series, looping back to episode 1 if at the end."""
        current = self._broadcaster.get_state()
        if (
            current.current_media is None
            or current.current_media.action != PlayAction.LOCAL
            or not current.current_media.series_name
        ):
            logger.info("Cannot advance next episode: current media is not a local series.")
            return current

        series_name = current.current_media.series_name
        curr_ep = current.current_media.episode_index
        total_ep = current.current_media.total_episodes

        next_ep = (curr_ep % total_ep) + 1
        logger.info(
            "Continuous Autoplay: Advancing series '%s' from ep %d to ep %d (total %d)",
            series_name,
            curr_ep,
            next_ep,
            total_ep,
        )

        return await self.play_local_video(
            series_name=series_name,
            episode=next_ep,
            requester_name=current.current_media.requester_name,
            requester_avatar=current.current_media.requester_avatar,
        )

    async def stop(self) -> StreamPlaybackState:
        """Stops the current video playback and resets the livestream UI to IDLE."""
        current = self._broadcaster.get_state()
        new_state = StreamPlaybackState(
            status=PlaybackStatus.IDLE,
            current_media=None,
            started_at=0.0,
            volume=current.volume,
            is_muted=current.is_muted,
            queue=(),
        )

        event = StreamEvent(
            event_type="stop",
            state=new_state,
            timestamp=time.time(),
            message="直播畫面已停止播放並重置為待機畫面",
        )

        await self._broadcaster.broadcast(event)
        logger.info("Livestream playback stopped. Reset to IDLE.")
        return new_state

    async def pause(self) -> StreamPlaybackState:
        """Pauses current video playback."""
        current = self._broadcaster.get_state()
        new_state = StreamPlaybackState(
            status=PlaybackStatus.PAUSED,
            current_media=current.current_media,
            started_at=current.started_at,
            volume=current.volume,
            is_muted=current.is_muted,
            queue=current.queue,
        )

        event = StreamEvent(
            event_type="pause",
            state=new_state,
            timestamp=time.time(),
            message="直播畫面已暫停",
        )

        await self._broadcaster.broadcast(event)
        logger.info("Livestream playback paused.")
        return new_state

    async def resume(self) -> StreamPlaybackState:
        """Resumes paused video playback."""
        current = self._broadcaster.get_state()
        new_state = StreamPlaybackState(
            status=PlaybackStatus.PLAYING,
            current_media=current.current_media,
            started_at=current.started_at,
            volume=current.volume,
            is_muted=current.is_muted,
            queue=current.queue,
        )

        event = StreamEvent(
            event_type="resume",
            state=new_state,
            timestamp=time.time(),
            message="直播畫面繼續播放",
        )

        await self._broadcaster.broadcast(event)
        logger.info("Livestream playback resumed.")
        return new_state

    def get_current_state(self) -> StreamPlaybackState:
        """Returns the current playback state."""
        return self._broadcaster.get_state()
