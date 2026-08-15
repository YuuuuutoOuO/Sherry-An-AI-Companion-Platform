"""
tests/unit/test_video_clipper.py
================================
Unit tests for mod/video_clipper module.
"""

from unittest.mock import AsyncMock

import pytest

from mod.video_clipper.contracts import AbstractVideoGifClient
from mod.video_clipper.domain import VideoClipRequest, VideoClipResult
from mod.video_clipper.service import VideoClipperService, parse_timestamp


def test_parse_timestamp_seconds() -> None:
    """Arrange, Act, Assert: Parse plain seconds."""
    # Arrange
    ts = "10.5"

    # Act
    res = parse_timestamp(ts)

    # Assert
    assert res == 10.5


def test_parse_timestamp_minutes_seconds() -> None:
    """Arrange, Act, Assert: Parse MM:SS format."""
    # Arrange
    ts = "01:30"

    # Act
    res = parse_timestamp(ts)

    # Assert
    assert res == 90.0


def test_parse_timestamp_single_digit_minutes() -> None:
    """Arrange, Act, Assert: Parse single-digit minute format M:SS (e.g., 1:30)."""
    # Arrange
    ts1 = "1:30"
    ts2 = "1:45.5"

    # Act
    res1 = parse_timestamp(ts1)
    res2 = parse_timestamp(ts2)

    # Assert
    assert res1 == 90.0
    assert res2 == 105.5


def test_parse_timestamp_hours_minutes_seconds() -> None:
    """Arrange, Act, Assert: Parse HH:MM:SS format."""
    # Arrange
    ts = "01:02:15"

    # Act
    res = parse_timestamp(ts)

    # Assert
    assert res == 3735.0


def test_parse_timestamp_invalid_format() -> None:
    """Arrange, Act, Assert: Raise ValueError on invalid string."""
    # Arrange
    ts = "abc:def"

    # Act & Assert
    with pytest.raises(ValueError, match="無法解析時間數字"):
        parse_timestamp(ts)


def test_parse_timestamp_negative() -> None:
    """Arrange, Act, Assert: Raise ValueError on negative timestamp."""
    # Arrange
    ts = "-10"

    # Act & Assert
    with pytest.raises(ValueError, match="時間戳不可為負數"):
        parse_timestamp(ts)


def test_parse_timestamp_empty() -> None:
    """Arrange, Act, Assert: Raise ValueError on empty string."""
    # Arrange
    ts = "   "

    # Act & Assert
    with pytest.raises(ValueError, match="時間戳不可為空白"):
        parse_timestamp(ts)


def test_validate_request_success() -> None:
    """Arrange, Act, Assert: Valid request construction."""
    # Arrange
    client_mock = AsyncMock(spec=AbstractVideoGifClient)
    service = VideoClipperService(client_mock)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    start_time = "00:10"
    end_time = "00:20"

    # Act
    req = service.validate_request(url, start_time, end_time)

    # Assert
    assert req.url == url
    assert req.time_range.start_seconds == 10.0
    assert req.time_range.end_seconds == 20.0
    assert req.time_range.duration == 10.0


def test_validate_request_invalid_url() -> None:
    """Arrange, Act, Assert: Reject non-YouTube URL."""
    # Arrange
    client_mock = AsyncMock(spec=AbstractVideoGifClient)
    service = VideoClipperService(client_mock)

    # Act & Assert
    with pytest.raises(ValueError, match="請輸入有效的 YouTube 網址"):
        service.validate_request("https://example.com/video", "0", "5")


def test_validate_request_end_before_start() -> None:
    """Arrange, Act, Assert: Reject end_time <= start_time."""
    # Arrange
    client_mock = AsyncMock(spec=AbstractVideoGifClient)
    service = VideoClipperService(client_mock)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # Act & Assert
    with pytest.raises(ValueError, match="必須大於開始時間"):
        service.validate_request(url, "10", "5")


def test_validate_request_duration_exceeded() -> None:
    """Arrange, Act, Assert: Reject clip longer than max duration limit."""
    # Arrange
    client_mock = AsyncMock(spec=AbstractVideoGifClient)
    service = VideoClipperService(client_mock)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # Act & Assert
    with pytest.raises(ValueError, match="超出限制"):
        service.validate_request(url, "0", "30")


def test_generate_gif_success() -> None:
    """Arrange, Act, Assert: Service delegates to client contract."""

    async def _test() -> None:
        client_mock = AsyncMock(spec=AbstractVideoGifClient)
        expected_result = VideoClipResult(
            gif_bytes=b"GIF89a_test_data",
            file_name="clip.gif",
            duration=5.0,
            file_size_bytes=15,
        )
        client_mock.create_gif_clip.return_value = expected_result
        service = VideoClipperService(client_mock)

        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        res = await service.generate_gif(url, "00:05", "00:10")

        assert res == expected_result
        client_mock.create_gif_clip.assert_called_once()

    import asyncio
    asyncio.run(_test())
