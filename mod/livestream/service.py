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
    ChatMessage,
    MediaItem,
    PlayAction,
    PlayMode,
    PlaybackStatus,
    StreamEvent,
    StreamPlaybackState,
)

logger = logging.getLogger(__name__)

# Standard YouTube 11-character video ID regex pattern
_YT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")

# Bilibili BV and av pattern matching
_BILI_BV_PATTERN = re.compile(r"BV[0-9a-zA-Z_-]{8,12}", re.IGNORECASE)
_BILI_AV_PATTERN = re.compile(r"av\d+", re.IGNORECASE)

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
        self._chat_history: list[ChatMessage] = []
        self._max_chat_history: int = config.MAX_CHAT_HISTORY

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
    # YouTube & Bilibili Info Extraction
    # -----------------------------------------------------------------------

    def extract_youtube_info(self, url: str) -> Tuple[str, int, str]:
        """Extracts the YouTube video ID, optional start offset seconds, and playlist ID from a URL.

        Args:
            url: YouTube URL or video ID string.

        Returns:
            A tuple of (video_id, start_seconds, playlist_id).

        Raises:
            ValueError: If no valid YouTube video ID or playlist ID could be extracted.
        """
        clean_url = url.strip()
        if not clean_url:
            raise ValueError("提供的影片網址不能為空。")

        # Direct 11-char ID check
        if _YT_ID_PATTERN.match(clean_url):
            return clean_url, 0, ""

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
        playlist_id: str = ""

        if "list" in query_params:
            playlist_id = query_params["list"][0]

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
            elif path == "/playlist" and playlist_id:
                video_id = "videoseries"
            elif path.startswith("/embed/"):
                parts = [p for p in path[len("/embed/"):].split("/") if p]
                if parts:
                    if parts[0] == "videoseries":
                        video_id = "videoseries"
                    elif _YT_ID_PATTERN.match(parts[0]):
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
            if playlist_id:
                video_id = "videoseries"
            else:
                raise ValueError(f"無法從網址中解析出有效的 YouTube 影片 ID: {clean_url}")

        return video_id, start_seconds, playlist_id

    def extract_bilibili_info(self, url: str) -> Tuple[str, int, str]:
        """Extracts the Bilibili video ID (BV/av), page index, and playlist/medialist ID from a URL.

        Args:
            url: Bilibili URL, BV id, or av id string.

        Returns:
            A tuple of (video_id, page_index, list_id).

        Raises:
            ValueError: If no valid Bilibili video ID or list ID could be extracted.
        """
        clean_url = url.strip()
        if not clean_url:
            raise ValueError("提供的 Bilibili 影片網址或 BV 號不能為空。")

        # Direct BV check (e.g. BV1xx411c7X-)
        bv_match = _BILI_BV_PATTERN.search(clean_url)
        av_match = _BILI_AV_PATTERN.search(clean_url)

        page_index = 1
        list_id = ""

        # Parse URL parameters if applicable
        parse_target = clean_url
        if not parse_target.startswith(("http://", "https://")):
            parse_target = "https://" + parse_target

        try:
            parsed = urlparse(parse_target)
            query_params = parse_qs(parsed.query)
            if "p" in query_params and query_params["p"][0].isdigit():
                page_index = max(1, int(query_params["p"][0]))
            elif "page" in query_params and query_params["page"][0].isdigit():
                page_index = max(1, int(query_params["page"][0]))

            # Check medialist or list
            if "medialist" in parsed.path or "list" in query_params:
                if "list" in query_params:
                    list_id = query_params["list"][0]
                else:
                    ml_match = re.search(r"ml\d+", parsed.path)
                    if ml_match:
                        list_id = ml_match.group(0)
        except Exception:
            pass

        video_id = ""
        if bv_match:
            video_id = bv_match.group(0)
        elif av_match:
            video_id = av_match.group(0)
        elif list_id:
            video_id = list_id

        if not video_id:
            raise ValueError(f"無法從網址中解析出有效的 Bilibili 影片 BV/av 號: {clean_url}")

        return video_id, page_index, list_id

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
        play_mode: PlayMode = PlayMode.AUTOPLAY,
        episode: int = 1,
        requester_name: str = "Unknown",
        requester_avatar: str = "",
    ) -> StreamPlaybackState:
        """Plays video based on action, context, play mode and episode (YouTube, Bilibili, or Local)."""
        if action == PlayAction.YOUTUBE:
            video_id, start_sec, playlist_id = self.extract_youtube_info(context)
            if video_id == "videoseries":
                title = f"YouTube 播放清單: {playlist_id}"
            else:
                title = f"YouTube: {video_id}"
                if playlist_id:
                    title += f" (清單: {playlist_id})"

            media_item = MediaItem(
                action=action,
                video_id=video_id,
                original_url=context.strip(),
                title=title,
                requester_name=requester_name,
                requester_avatar=requester_avatar,
                requested_at=time.time(),
                start_seconds=start_sec,
                play_mode=play_mode,
                playlist_id=playlist_id,
            )
        elif action == PlayAction.BILIBILI:
            bvid, p_idx, list_id = self.extract_bilibili_info(context)
            final_p = episode if episode > 1 else p_idx
            title = f"Bilibili: {bvid} (P{final_p})"
            if list_id:
                title += f" [清單: {list_id}]"

            media_item = MediaItem(
                action=action,
                video_id=bvid,
                original_url=context.strip(),
                title=title,
                requester_name=requester_name,
                requester_avatar=requester_avatar,
                requested_at=time.time(),
                page_index=final_p,
                play_mode=play_mode,
                playlist_id=list_id,
            )
        elif action in (PlayAction.LOCAL, PlayAction.FILE):
            return await self.play_local_video(
                series_name=context,
                episode=episode,
                play_mode=play_mode,
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
            "Livestream playback updated to PLAYING for video_id=%s [action=%s, mode=%s] requested by %s",
            media_item.video_id,
            action.value,
            play_mode.value,
            requester_name,
        )
        return new_state

    async def play_local_video(
        self,
        series_name: str,
        episode: int = 1,
        play_mode: PlayMode = PlayMode.AUTOPLAY,
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
            play_mode=play_mode,
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
            "Livestream local video started: %s (ep %d/%d) [mode=%s] requested by %s",
            target_series,
            episode,
            total_episodes,
            play_mode.value,
            requester_name,
        )
        return new_state

    async def next_episode(self) -> StreamPlaybackState:
        """Advances to the next episode or part in the current series/video."""
        current = self._broadcaster.get_state()
        if current.current_media is None:
            logger.info("Cannot advance next episode: current media is None.")
            return current

        media = current.current_media

        # 1. Local Video Series
        if media.action in (PlayAction.LOCAL, PlayAction.FILE) and media.series_name:
            series_name = media.series_name
            curr_ep = media.episode_index
            total_ep = media.total_episodes

            # In single mode, next episode stops
            if media.play_mode == PlayMode.SINGLE:
                return await self.stop()

            # In loop mode, repeat the current episode
            if media.play_mode == PlayMode.LOOP:
                next_ep = curr_ep
            else:
                next_ep = (curr_ep % total_ep) + 1

            logger.info(
                "Autoplay: Advancing series '%s' from ep %d to ep %d (total %d, mode=%s)",
                series_name,
                curr_ep,
                next_ep,
                total_ep,
                media.play_mode.value,
            )

            return await self.play_local_video(
                series_name=series_name,
                episode=next_ep,
                play_mode=media.play_mode,
                requester_name=media.requester_name,
                requester_avatar=media.requester_avatar,
            )

        # 2. Bilibili Multi-Part (分P)
        elif media.action == PlayAction.BILIBILI and media.video_id:
            if media.play_mode == PlayMode.SINGLE:
                return await self.stop()

            curr_p = media.page_index
            next_p = curr_p if media.play_mode == PlayMode.LOOP else curr_p + 1

            logger.info(
                "Autoplay: Advancing Bilibili '%s' from P%d to P%d (mode=%s)",
                media.video_id,
                curr_p,
                next_p,
                media.play_mode.value,
            )

            return await self.play_video(
                action=PlayAction.BILIBILI,
                context=media.original_url or media.video_id,
                play_mode=media.play_mode,
                episode=next_p,
                requester_name=media.requester_name,
                requester_avatar=media.requester_avatar,
            )

        # 3. YouTube or others
        elif media.action == PlayAction.YOUTUBE:
            if media.play_mode == PlayMode.SINGLE:
                return await self.stop()
            elif media.play_mode == PlayMode.LOOP:
                return await self.play_video(
                    action=PlayAction.YOUTUBE,
                    context=media.original_url or media.video_id,
                    play_mode=media.play_mode,
                    requester_name=media.requester_name,
                    requester_avatar=media.requester_avatar,
                )

        return current

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

    def get_chat_history(self) -> list[ChatMessage]:
        """Returns the recent in-memory chat message history."""
        return list(self._chat_history)

    async def post_chat_message(
        self,
        author_name: str,
        author_avatar: str = "",
        content: str = "",
        is_owner: bool = False,
        badge: str = "💬 觀眾",
        channel_id: str = "",
    ) -> ChatMessage:
        """Posts a new chat message and broadcasts it to all connected livestream clients."""
        msg_id = f"chat_{int(time.time() * 1000)}_{len(self._chat_history) + 1}"
        chat_msg = ChatMessage(
            id=msg_id,
            author_name=author_name or "匿名探員",
            author_avatar=author_avatar,
            content=content,
            timestamp=time.time(),
            is_owner=is_owner,
            badge=badge,
            channel_id=channel_id,
        )
        self._chat_history.append(chat_msg)
        if len(self._chat_history) > self._max_chat_history:
            self._chat_history = self._chat_history[-self._max_chat_history :]

        # Broadcast chat_message event via broadcaster
        event = StreamEvent(
            event_type="chat_message",
            state=self.get_current_state(),
            timestamp=time.time(),
            message=content,
            chat_message=chat_msg,
        )
        await self._broadcaster.broadcast(event)
        logger.debug("Livestream chat message broadcasted from '%s': %s", author_name, content[:30])
        return chat_msg

