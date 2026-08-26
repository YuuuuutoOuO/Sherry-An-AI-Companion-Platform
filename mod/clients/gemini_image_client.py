"""Gemini Image Generation Client (Nano Banana).

This client encapsulates external API calls to Google Generative AI SDK
for image generation using the Nano Banana model (gemini-2.5-flash-image).
Uses the official `google-genai` SDK with `client.models.generate_content`.
"""

import asyncio
import io
import logging
from typing import Any

from google import genai
from google.genai import types

import settings

logger = logging.getLogger(__name__)


class GeminiImageClient:
    """External client for Google Gemini image generation (gemini-2.5-flash-image).

    Uses the official google-genai SDK with generate_content() API,
    which is the recommended approach after Imagen model deprecation.
    """

    def __init__(
        self,
        api_key: str = "",
        model_name: str = "",
        timeout_seconds: int = 30,
    ) -> None:
        """Initialize GeminiImageClient.

        Args:
            api_key: Google Gemini API key. Falls back to settings.GEMINI_API_KEY.
            model_name: Model name string (e.g., "gemini-2.5-flash-image").
            timeout_seconds: Timeout duration for API request in seconds.
        """
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._model_name = model_name or getattr(settings, "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
        self._timeout_seconds = timeout_seconds or getattr(settings, "GEMINI_IMAGE_TIMEOUT_SECONDS", 30)

    def _call_generate_content(self, prompt: str) -> bytes | None:
        """Synchronous call to Gemini generate_content (runs in thread executor).

        Args:
            prompt: Text prompt describing the image to generate.

        Returns:
            Raw image bytes from the first valid image part, or None.
        """
        client = genai.Client(api_key=self._api_key)
        response = client.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="1:1"),
            ),
        )

        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.data:
                            # part.inline_data.data 是 bytes 物件
                            data = part.inline_data.data
                            if isinstance(data, bytes):
                                return data
                            # 若為 BufferedReader / IO 物件則讀取
                            if hasattr(data, "read"):
                                read_data = data.read()
                                return read_data if isinstance(read_data, bytes) else bytes(read_data)

        logger.error("GeminiImageClient: API 回傳中找不到有效的圖片資料")
        return None

    async def generate_image(self, prompt: str, timeout_seconds: int | None = None) -> bytes | None:
        """Generate image via Nano Banana (gemini-2.5-flash-image).

        Runs the synchronous SDK call in a thread executor to avoid blocking
        the asyncio event loop.

        Args:
            prompt: Text prompt describing the image to generate.
            timeout_seconds: Optional custom timeout in seconds.

        Returns:
            Decoded image bytes if successful, None otherwise.
        """
        if not self._api_key:
            logger.info("GeminiImageClient: 未設定 GEMINI_API_KEY，跳過 Gemini 圖片生成。")
            return None

        effective_timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds

        loop = asyncio.get_running_loop()
        try:
            img_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, self._call_generate_content, prompt),
                timeout=float(effective_timeout),
            )
            if img_bytes:
                logger.info(
                    "GeminiImageClient: %s 圖片生成成功 (%d bytes)",
                    self._model_name,
                    len(img_bytes),
                )
            return img_bytes
        except TimeoutError:
            logger.warning("GeminiImageClient: 請求超時 (%ds)", effective_timeout)
            return None
        except Exception as e:  # noqa: BLE001
            logger.error("GeminiImageClient: 生成失敗: %s", e)
            return None
