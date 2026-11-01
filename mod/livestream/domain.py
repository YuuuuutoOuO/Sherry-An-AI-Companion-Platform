"""
mod/livestream/domain.py
========================
Domain models and value objects for the livestream module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PlayAction(str, Enum):
    """Supported video playback source actions."""

    FILE = "file"
    LOCAL = "local"
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"
    TWITCH = "twitch"


class PlayMode(str, Enum):
    """Supported video playback modes."""

    AUTOPLAY = "autoplay"
    LOOP = "loop"
    SINGLE = "single"


class PlaybackStatus(str, Enum):
    """Current playback state of the livestream UI."""

    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass(frozen=True)
class MediaItem:
    """Represents a media item to be played on the livestream screen.

    Attributes:
        action: The source type of the media (e.g. YOUTUBE, BILIBILI).
        video_id: Extracted unique video identifier (e.g. YouTube video ID or Bilibili BV ID).
        original_url: The raw input URL or path provided by the user.
        title: Title of the video or media item.
        requester_name: Name of the Discord user who requested the video.
        requester_avatar: URL of the user's avatar.
        requested_at: Unix timestamp when requested.
        duration_sec: Estimated or known duration in seconds.
        start_seconds: Start offset in seconds (if specified in URL).
        play_mode: The playback mode (autoplay, loop, single).
        playlist_id: Optional YouTube/Bilibili playlist or collection ID.
        page_index: 1-based page / part index (for Bilibili multi-p).
    """

    action: PlayAction
    video_id: str
    original_url: str
    title: str = "Video"
    requester_name: str = "Unknown"
    requester_avatar: str = ""
    requested_at: float = 0.0
    duration_sec: float = 0.0
    start_seconds: int = 0
    series_name: str = ""
    episode_index: int = 1
    total_episodes: int = 1
    file_path: str = ""
    stream_url: str = ""
    play_mode: PlayMode = PlayMode.AUTOPLAY
    playlist_id: str = ""
    page_index: int = 1


@dataclass(frozen=True)
class StreamPlaybackState:
    """Represents the global real-time playback state of the livestream screen.

    Attributes:
        status: Current playback status (IDLE, PLAYING, etc.).
        current_media: Currently playing media item, or None if idle.
        started_at: Unix timestamp when current playback started.
        volume: Playback volume (0 - 100).
        is_muted: Whether audio is muted.
        queue: Sequence of queued media items waiting to be played.
    """

    status: PlaybackStatus = PlaybackStatus.IDLE
    current_media: Optional[MediaItem] = None
    started_at: float = 0.0
    volume: int = 100
    is_muted: bool = False
    queue: tuple[MediaItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ChatMessage:
    """Represents a chat comment displayed on the livestream screen overlay.

    Attributes:
        id: Unique identifier for the message.
        author_name: Display name of the commenter.
        author_avatar: Avatar image URL.
        content: Text content of the comment.
        timestamp: Unix timestamp.
        is_owner: Whether the author is a bot owner / admin.
        badge: Badge label (e.g. '👑 實況主', '⭐ VIP', '🤖 助手', '💬 觀眾').
        channel_id: Discord channel ID or source origin.
    """

    id: str
    author_name: str
    author_avatar: str = ""
    content: str = ""
    timestamp: float = 0.0
    is_owner: bool = False
    badge: str = "💬 觀眾"
    channel_id: str = ""


@dataclass(frozen=True)
class StreamEvent:
    """Payload sent to web clients via Server-Sent Events (SSE).

    Attributes:
        event_type: Type of event ('play', 'stop', 'pause', 'resume', 'state_sync', 'chat_message').
        state: The snapshot of the stream playback state.
        timestamp: Unix timestamp when the event was dispatched.
        message: Optional human-readable notification message.
        chat_message: Optional ChatMessage instance if event_type is 'chat_message'.
    """

    event_type: str
    state: StreamPlaybackState
    timestamp: float
    message: str = ""
    chat_message: Optional[ChatMessage] = None

