"""Unit tests for GeminiImageClient (mod/clients/gemini_image_client.py)."""

import asyncio
from unittest.mock import MagicMock, patch

from mod.clients.gemini_image_client import GeminiImageClient


def _make_mock_response(raw_bytes: bytes) -> MagicMock:
    """Build a mock Gemini API response containing image inline_data."""
    mock_inline = MagicMock()
    mock_inline.data = raw_bytes

    mock_part = MagicMock()
    mock_part.inline_data = mock_inline

    mock_content = MagicMock()
    mock_content.parts = [mock_part]

    mock_candidate = MagicMock()
    mock_candidate.content = mock_content

    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    return mock_response


def test_generate_image_no_api_key() -> None:
    """Test generate_image returns None when API key is missing."""
    async def run() -> None:
        client = GeminiImageClient(api_key="")
        result = await client.generate_image("a cute anime girl")
        assert result is None

    asyncio.run(run())


def test_generate_image_success() -> None:
    """Test successful image generation with mocked google-genai SDK."""
    raw_bytes = b"fake_png_image_data"

    async def run() -> None:
        client = GeminiImageClient(api_key="test_api_key")
        mock_response = _make_mock_response(raw_bytes)

        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client_instance):
            result = await client.generate_image("draw an anime girl")
            assert result == raw_bytes

    asyncio.run(run())


def test_generate_image_no_image_in_response() -> None:
    """Test returns None when response contains no inline image data."""
    async def run() -> None:
        client = GeminiImageClient(api_key="test_api_key")

        mock_part = MagicMock()
        mock_part.inline_data = None  # 沒有圖片

        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        mock_candidate = MagicMock()
        mock_candidate.content = mock_content

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client_instance):
            result = await client.generate_image("some prompt")
            assert result is None

    asyncio.run(run())


def test_generate_image_exception_handling() -> None:
    """Test that exceptions from the SDK are caught and return None."""
    async def run() -> None:
        client = GeminiImageClient(api_key="test_api_key")
        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.side_effect = RuntimeError("API error")

        with patch("google.genai.Client", return_value=mock_client_instance):
            result = await client.generate_image("some prompt")
            assert result is None

    asyncio.run(run())
