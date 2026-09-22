"""
mod/video_clipper/clients/yt_gif_client.py
=========================================
External client implementation using yt-dlp and FFmpeg to generate GIF clips.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

try:
    import imageio_ffmpeg  # type: ignore[import-untyped]
except ImportError:
    imageio_ffmpeg = None

try:
    import yt_dlp  # type: ignore[import-untyped]
except ImportError:
    yt_dlp = None

from mod.video_clipper import config
from mod.video_clipper.contracts import AbstractVideoGifClient
from mod.video_clipper.domain import VideoClipRequest, VideoClipResult

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

DEFAULT_CLIP_TIMEOUT_SEC: int = config.DEFAULT_CLIP_TIMEOUT_SEC

# 專屬獨立線程池，隔離重度 CPU/IO 運算，保護預設線程池與資料庫查詢
_VIDEO_CLIPPER_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-clipper-worker")


class YoutubeGifClient(AbstractVideoGifClient):
    """Client for fetching YouTube video clips and converting them to GIFs."""

    def __init__(self, timeout_seconds: int = DEFAULT_CLIP_TIMEOUT_SEC) -> None:
        """Initializes YoutubeGifClient with processing timeout.

        Args:
            timeout_seconds: Maximum seconds allowed for download and conversion.
        """
        self._timeout_seconds: int = timeout_seconds

    async def create_gif_clip(self, request: VideoClipRequest) -> VideoClipResult:
        """Downloads a YouTube segment and produces a GIF binary.

        Args:
            request: The configured video clip request.

        Returns:
            VideoClipResult containing raw GIF bytes and metadata.

        Raises:
            ValueError: If downloading or converting fails.
            TimeoutError: If processing time exceeds timeout limits.
        """
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_VIDEO_CLIPPER_EXECUTOR, self._process_sync, request),
                timeout=float(self._timeout_seconds),
            )
        except asyncio.TimeoutError:
            logger.error("Timed out after %d seconds creating GIF clip", self._timeout_seconds)
            raise TimeoutError(f"影片處理超時（上限 {self._timeout_seconds} 秒）")

    def _process_sync(self, request: VideoClipRequest) -> VideoClipResult:
        """Synchronous implementation executed in isolated thread pool.

        Args:
            request: Video clip request parameters.

        Returns:
            VideoClipResult with binary content.
        """
        if imageio_ffmpeg is not None:
            try:
                ffmpeg_exe = str(imageio_ffmpeg.get_ffmpeg_exe())
            except Exception:
                ffmpeg_exe = shutil.which("ffmpeg") or ""
        else:
            ffmpeg_exe = shutil.which("ffmpeg") or ""

        if not ffmpeg_exe:
            raise RuntimeError("系統未安裝 FFmpeg 或缺少 imageio-ffmpeg 套件，無法進行影片轉檔")

        ffmpeg_dir = str(Path(ffmpeg_exe).parent)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            video_out = tmp_path / "clip.mp4"
            gif_out = tmp_path / "output.gif"

            def download_range_func(info_dict: Dict[str, Any], ydl: Any) -> List[Dict[str, float]]:
                return [{
                    "start_time": request.time_range.start_seconds,
                    "end_time": request.time_range.end_seconds,
                }]

            ydl_opts: Dict[str, Any] = {
                "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                "outtmpl": str(video_out),
                "download_ranges": download_range_func,
                "ffmpeg_location": ffmpeg_dir,
                "quiet": True,
                "no_warnings": True,
            }

            logger.info(
                "Starting yt-dlp download for URL=%s [%.2fs - %.2fs]",
                request.url,
                request.time_range.start_seconds,
                request.time_range.end_seconds,
            )

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([request.url])

            # Find downloaded video file in temp directory
            downloaded_files: List[Path] = [
                f for f in tmp_path.glob("*")
                if f.is_file() and f.name != "output.gif"
            ]

            if not downloaded_files:
                logger.error("yt-dlp completed but output file not found in %s", tmpdir)
                raise ValueError("下載影片片段失敗，無法取得音視訊檔")

            target_video: Path = downloaded_files[0]
            logger.info("Video segment saved to %s (size=%d bytes)", target_video, target_video.stat().st_size)

            # Convert video clip to GIF using FFmpeg palette filters
            vf_filter = (
                f"fps={request.fps},scale={request.scale_width}:-1:flags=lanczos,"
                f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
            )
            ffmpeg_cmd: List[str] = [
                ffmpeg_exe,
                "-y",
                "-i", str(target_video),
                "-vf", vf_filter,
                str(gif_out),
            ]

            logger.info("Executing FFmpeg command: %s", " ".join(ffmpeg_cmd))
            res = subprocess.run(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )

            if res.returncode != 0 or not gif_out.exists():
                logger.error("FFmpeg GIF generation failed code=%d, stderr=%s", res.returncode, res.stderr)
                raise ValueError("轉換 GIF 失敗，影片編碼格式可能不受支援")

            gif_bytes: bytes = gif_out.read_bytes()
            file_size: int = len(gif_bytes)
            logger.info("GIF generated successfully size=%d bytes", file_size)

            return VideoClipResult(
                gif_bytes=gif_bytes,
                file_name="clip.gif",
                duration=request.time_range.duration,
                file_size_bytes=file_size,
            )
