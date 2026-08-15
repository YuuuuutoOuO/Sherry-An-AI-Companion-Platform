"""
mod/video_clipper/contracts.py
==============================
Abstract interfaces for video clipper clients.
"""

from abc import ABC, abstractmethod

from mod.video_clipper.domain import VideoClipRequest, VideoClipResult


class AbstractVideoGifClient(ABC):
    """Abstract interface for downloading video clips and generating GIFs."""

    @abstractmethod
    async def create_gif_clip(self, request: VideoClipRequest) -> VideoClipResult:
        """Downloads a clip from a video URL and converts it into a GIF.

        Args:
            request: The clip configuration request.

        Returns:
            VideoClipResult containing binary bytes and file metadata.

        Raises:
            ValueError: If downloading or converting fails.
            TimeoutError: If execution exceeds allowed processing time.
        """
        pass
