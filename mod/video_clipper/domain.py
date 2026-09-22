"""
mod/video_clipper/domain.py
===========================
Domain entities and data models for video clip to GIF conversion.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeRange:
    """Represents a validated time range in seconds.

    Attributes:
        start_seconds: Start time in seconds.
        end_seconds: End time in seconds.
    """

    start_seconds: float
    end_seconds: float

    @property
    def duration(self) -> float:
        """Returns the duration of the time range in seconds."""
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class VideoClipRequest:
    """Data transfer object for requesting a video GIF clip.

    Attributes:
        url: The source YouTube URL.
        time_range: Validated time range.
        fps: Target frame rate for the GIF.
        scale_width: Target width for downscaling the GIF (-1 maintains aspect ratio).
    """

    url: str
    time_range: TimeRange
    fps: int = 12
    scale_width: int = 480


@dataclass(frozen=True)
class VideoClipResult:
    """Data transfer object containing the output GIF binary and metadata.

    Attributes:
        gif_bytes: Raw binary content of the generated GIF.
        file_name: Suggested file name for Discord attachment.
        duration: Duration of the clip in seconds.
        file_size_bytes: Size of the GIF in bytes.
    """

    gif_bytes: bytes
    file_name: str
    duration: float
    file_size_bytes: int
