"""
mod/video_clipper/service.py
============================
Service layer for validating video clip requests and coordinating GIF creation.
Strictly follows clean architecture: no imports of discord, sqlite3, or requests.
"""

from __future__ import annotations

import logging

from mod.video_clipper import config
from mod.video_clipper.contracts import AbstractVideoGifClient
from mod.video_clipper.domain import TimeRange, VideoClipRequest, VideoClipResult

logger = logging.getLogger(__name__)

MAX_CLIP_DURATION_SEC: float = config.MAX_CLIP_DURATION_SEC
YOUTUBE_URL_REGEX = config.YOUTUBE_URL_REGEX


def parse_timestamp(timestamp_str: str) -> float:
    """Parses timestamp strings into seconds.

    Supports formats:
    - Seconds: "10", "12.5"
    - MM:SS: "01:30", "1:15"
    - HH:MM:SS: "00:01:30", "1:02:15"

    Args:
        timestamp_str: Timestamp formatted string.

    Returns:
        Time value in seconds.

    Raises:
        ValueError: If timestamp format is invalid or negative.
    """
    cleaned = timestamp_str.strip()
    if not cleaned:
        raise ValueError("時間戳不可為空白")

    parts = cleaned.split(":")
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            minutes = float(parts[0])
            seconds = float(parts[1]) + minutes * 60.0
        elif len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2]) + hours * 3600.0 + minutes * 60.0
        else:
            raise ValueError(f"無效的時間戳格式: '{timestamp_str}'")
    except ValueError as exc:
        if "無效的時間戳格式" in str(exc):
            raise
        raise ValueError(f"無法解析時間數字: '{timestamp_str}'") from exc

    if seconds < 0:
        raise ValueError("時間戳不可為負數")

    return seconds


class VideoClipperService:
    """Service providing core business logic for YouTube clip creation."""

    def __init__(self, client: AbstractVideoGifClient) -> None:
        """Initializes service with an abstract client contract.

        Args:
            client: Concrete implementation of AbstractVideoGifClient.
        """
        self._client: AbstractVideoGifClient = client

    def validate_request(
        self,
        url: str,
        start_time_str: str,
        end_time_str: str,
    ) -> VideoClipRequest:
        """Validates parameters and builds a VideoClipRequest.

        Args:
            url: YouTube URL string.
            start_time_str: Start timestamp string.
            end_time_str: End timestamp string.

        Returns:
            Validated VideoClipRequest entity.

        Raises:
            ValueError: If URL format, timestamps, or duration limits are violated.
        """
        cleaned_url = url.strip()
        if not YOUTUBE_URL_REGEX.match(cleaned_url):
            raise ValueError("請輸入有效的 YouTube 網址 (如 https://www.youtube.com/watch?v=...)")

        start_sec = parse_timestamp(start_time_str)
        end_sec = parse_timestamp(end_time_str)

        if end_sec <= start_sec:
            raise ValueError(
                f"結束時間 ({end_time_str} -> {end_sec}s) 必須大於開始時間 ({start_time_str} -> {start_sec}s)"
            )

        duration = end_sec - start_sec
        if duration > MAX_CLIP_DURATION_SEC:
            raise ValueError(
                f"片段長度 ({duration:.1f} 秒) 超出限制！目前最大允許片段長度為 {MAX_CLIP_DURATION_SEC} 秒"
            )

        time_range = TimeRange(start_seconds=start_sec, end_seconds=end_sec)
        return VideoClipRequest(url=cleaned_url, time_range=time_range)

    async def generate_gif(
        self,
        url: str,
        start_time: str,
        end_time: str,
    ) -> VideoClipResult:
        """Validates request and generates GIF clip binary.

        Args:
            url: YouTube video URL.
            start_time: Start timestamp string.
            end_time: End timestamp string.

        Returns:
            VideoClipResult containing GIF bytes and metadata.

        Raises:
            ValueError: On validation failure or processing failure.
        """
        request = self.validate_request(url, start_time, end_time)
        logger.info(
            "Generating GIF for URL=%s from %.2fs to %.2fs",
            request.url,
            request.time_range.start_seconds,
            request.time_range.end_seconds,
        )
        return await self._client.create_gif_clip(request)
