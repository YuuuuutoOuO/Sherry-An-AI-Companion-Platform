"""
mod/livestream/contracts.py
===========================
Abstract interface contracts for livestream broadcasters and services.
"""

from __future__ import annotations

import abc
import asyncio
from pathlib import Path
from typing import Optional, Tuple

from mod.livestream.domain import (
    ChatMessage,
    PlayAction,
    PlayMode,
    StreamEvent,
    StreamPlaybackState,
)


class ILivestreamBroadcaster(abc.ABC):
    """Abstract interface for broadcasting livestream events to connected Web UI clients."""

    @abc.abstractmethod
    async def broadcast(self, event: StreamEvent) -> None:
        """Broadcasts a stream event to all active subscriber queues.

        Args:
            event: The stream event to broadcast.
        """

    @abc.abstractmethod
    async def subscribe(self) -> asyncio.Queue[StreamEvent]:
        """Subscribes a new Web UI client queue for receiving real-time SSE stream events.

        Returns:
            An asyncio.Queue yielding StreamEvent instances.
        """

    @abc.abstractmethod
    async def unsubscribe(self, queue: asyncio.Queue[StreamEvent]) -> None:
        """Unsubscribes a client queue when disconnected.

        Args:
            queue: The subscriber queue to remove.
        """

    @abc.abstractmethod
    def get_state(self) -> StreamPlaybackState:
        """Retrieves the current cached playback state.

        Returns:
            The current StreamPlaybackState.
        """

    @abc.abstractmethod
    def set_state(self, state: StreamPlaybackState) -> None:
        """Updates the current cached playback state.

        Args:
            state: The new StreamPlaybackState.
        """


class ILivestreamService(abc.ABC):
    """Abstract interface defining the livestream business service."""

    @abc.abstractmethod
    async def play_video(
        self,
        action: PlayAction,
        context: str,
        play_mode: PlayMode = PlayMode.AUTOPLAY,
        episode: int = 1,
        requester_name: str = "Unknown",
        requester_avatar: str = "",
    ) -> StreamPlaybackState:
        """Validates the input, prepares the media item, and updates the livestream state to PLAYING.

        Args:
            action: The playback source type (e.g. YOUTUBE, BILIBILI, or LOCAL).
            context: The video URL, BV id, or identifier.
            play_mode: Playback mode (AUTOPLAY, LOOP, SINGLE).
            episode: 1-based start episode or part index.
            requester_name: Name of the Discord requester.
            requester_avatar: Avatar URL of the Discord requester.

        Returns:
            The updated StreamPlaybackState.

        Raises:
            ValueError: If the context or action is invalid or unsupported.
        """

    @abc.abstractmethod
    async def play_local_video(
        self,
        series_name: str,
        episode: int = 1,
        play_mode: PlayMode = PlayMode.AUTOPLAY,
        requester_name: str = "Unknown",
        requester_avatar: str = "",
    ) -> StreamPlaybackState:
        """Plays a local video file from the ./Video directory with series and episode index.

        Args:
            series_name: The discovered series name (e.g. '海綿寶寶' or '南方公園-第一季').
            episode: 1-based episode index.
            play_mode: Playback mode (AUTOPLAY, LOOP, SINGLE).
            requester_name: Name of the Discord requester.
            requester_avatar: Avatar URL of the Discord requester.

        Returns:
            The updated StreamPlaybackState.

        Raises:
            ValueError: If the series is not found or has no videos.
        """

    @abc.abstractmethod
    async def next_episode(self) -> StreamPlaybackState:
        """Advances to the next episode in the current series, looping back to episode 1 if at the end.

        Returns:
            The updated StreamPlaybackState.
        """

    @abc.abstractmethod
    def scan_video_library(self) -> dict[str, list[Path]]:
        """Scans the ./Video folder and discovers all single-level and two-level video series.

        Returns:
            Mapping of series name to naturally sorted list of video file paths.
        """

    @abc.abstractmethod
    def get_available_series(self) -> list[str]:
        """Returns a list of all currently available series names.

        Returns:
            List of series names.
        """

    @abc.abstractmethod
    def get_video_file_path(self, series_name: str, episode: int) -> Optional[Path]:
        """Resolves the physical file Path for a given series name and 1-based episode index.

        Args:
            series_name: The series name.
            episode: 1-based episode index.

        Returns:
            The Path to the file, or None if not found.
        """

    @abc.abstractmethod
    async def stop(self) -> StreamPlaybackState:
        """Stops the current video playback and resets the livestream UI to IDLE.

        Returns:
            The updated StreamPlaybackState.
        """

    @abc.abstractmethod
    async def pause(self) -> StreamPlaybackState:
        """Pauses current video playback.

        Returns:
            The updated StreamPlaybackState.
        """

    @abc.abstractmethod
    async def resume(self) -> StreamPlaybackState:
        """Resumes paused video playback.

        Returns:
            The updated StreamPlaybackState.
        """

    @abc.abstractmethod
    def get_current_state(self) -> StreamPlaybackState:
        """Returns the current playback state.

        Returns:
            Current StreamPlaybackState.
        """

    @abc.abstractmethod
    def extract_youtube_info(self, url: str) -> Tuple[str, int, str]:
        """Extracts the YouTube video ID, optional start offset seconds, and playlist ID from a URL.

        Args:
            url: YouTube URL or video ID string.

        Returns:
            A tuple of (video_id, start_seconds, playlist_id).

        Raises:
            ValueError: If no valid YouTube video ID or playlist ID could be extracted.
        """

    @abc.abstractmethod
    def extract_bilibili_info(self, url: str) -> Tuple[str, int, str]:
        """Extracts the Bilibili video ID (BV/av), page index, and playlist/medialist ID from a URL.

        Args:
            url: Bilibili URL, BV id, or av id string.

        Returns:
            A tuple of (video_id, page_index, list_id).

        Raises:
            ValueError: If no valid Bilibili video ID or list ID could be extracted.
        """

    @abc.abstractmethod
    def get_chat_history(self) -> list[ChatMessage]:
        """Returns the recent in-memory chat message history.

        Returns:
            List of ChatMessage objects.
        """

    @abc.abstractmethod
    async def post_chat_message(
        self,
        author_name: str,
        author_avatar: str = "",
        content: str = "",
        is_owner: bool = False,
        badge: str = "💬 觀眾",
        channel_id: str = "",
    ) -> ChatMessage:
        """Posts a new chat message and broadcasts it to all connected livestream clients.

        Args:
            author_name: Display name of the commenter.
            author_avatar: Avatar URL.
            content: Comment text.
            is_owner: Whether author is owner/admin.
            badge: Role or badge label.
            channel_id: Channel ID or source.

        Returns:
            The created ChatMessage object.
        """

