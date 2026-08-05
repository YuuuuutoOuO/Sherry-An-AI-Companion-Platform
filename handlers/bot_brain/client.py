import asyncio
import logging
from typing import Any

import aiohttp

import settings

logger = logging.getLogger(__name__)


class OllamaClient:
    """Async HTTP client for local & cloud inference backends.

    Supports prefix-based routing:
      ollama:   → Ollama native API
      lms:      → LM Studio (OpenAI-compatible)
      openai:   → OpenAI ChatGPT
      deepseek: → DeepSeek
      gemini:   → Google Gemini (OpenAI-compatible endpoint)
      claude:   → Anthropic Claude (Messages API)

    Falls back to ``ollama:`` when no prefix is given.
    """

    def __init__(
        self,
        base_url: str | None = None,
        lms_base_url: str | None = None,
        openai_base_url: str | None = None,
        openai_api_key: str | None = None,
        deepseek_base_url: str | None = None,
        deepseek_api_key: str | None = None,
        gemini_base_url: str | None = None,
        gemini_api_key: str | None = None,
        claude_base_url: str | None = None,
        claude_api_key: str | None = None,
    ) -> None:
        # Custom overrides (if explicitly passed)
        self._custom_base_url = base_url
        self._custom_lms_base_url = lms_base_url
        self._custom_openai_base_url = openai_base_url
        self._custom_openai_api_key = openai_api_key
        self._custom_deepseek_base_url = deepseek_base_url
        self._custom_deepseek_api_key = deepseek_api_key
        self._custom_gemini_base_url = gemini_base_url
        self._custom_gemini_api_key = gemini_api_key
        self._custom_claude_base_url = claude_base_url
        self._custom_claude_api_key = claude_api_key

        self._session: aiohttp.ClientSession | None = None

    @property
    def _base_url(self) -> str:
        url = self._custom_base_url or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        return url.rstrip("/")

    @property
    def _lms_base_url(self) -> str:
        url = self._custom_lms_base_url or getattr(settings, "LMS_BASE_URL", "http://localhost:1234/v1")
        return url.rstrip("/")

    @property
    def _openai_base_url(self) -> str:
        url = self._custom_openai_base_url or getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
        return url.rstrip("/")

    @property
    def _openai_api_key(self) -> str:
        return self._custom_openai_api_key or getattr(settings, "OPENAI_API_KEY", "")

    @property
    def _deepseek_base_url(self) -> str:
        url = self._custom_deepseek_base_url or getattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        return url.rstrip("/")

    @property
    def _deepseek_api_key(self) -> str:
        return self._custom_deepseek_api_key or getattr(settings, "DEEPSEEK_API_KEY", "")

    @property
    def _gemini_base_url(self) -> str:
        url = self._custom_gemini_base_url or getattr(settings, "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
        return url.rstrip("/")

    @property
    def _gemini_api_key(self) -> str:
        return self._custom_gemini_api_key or getattr(settings, "GEMINI_API_KEY", "")

    @property
    def _claude_base_url(self) -> str:
        url = self._custom_claude_base_url or getattr(settings, "CLAUDE_BASE_URL", "https://api.anthropic.com/v1")
        return url.rstrip("/")

    @property
    def _claude_api_key(self) -> str:
        return self._custom_claude_api_key or getattr(settings, "CLAUDE_API_KEY", "")

        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=settings.OLLAMA_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    # ------------------------------------------------------------------
    # Helpers – extract prefix & decide routing backend
    # ------------------------------------------------------------------

    @property
    def _vllm_base_url(self) -> str:
        url = self._custom_vllm_base_url if hasattr(self, "_custom_vllm_base_url") and self._custom_vllm_base_url else getattr(settings, "VLLM_BASE_URL", "http://localhost:8000/v1")
        return url.rstrip("/")

    @staticmethod
    def _parse_model(model: str) -> tuple[str, str]:
        """Return (backend_key, actual_model_name)."""
        PREFIX_MAP: dict[str, int] = {
            "claude:":   7,
            "deepseek:": 9,
            "gemini:":   7,
            "openai:":   7,
            "lms:":      4,
            "vllm:":     5,
            "ollama:":   7,
        }
        for prefix, length in PREFIX_MAP.items():
            if model.startswith(prefix):
                return prefix.rstrip(":"), model[length:]
        return "ollama", model

    # ------------------------------------------------------------------
    # Backend-specific request helpers
    # ------------------------------------------------------------------

    async def _request_openai_compatible(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        api_key: str,
        actual_model: str,
        system_prompt: str,
        user_prompt: str,
        backend_label: str,
        images: list[str] | None = None,
    ) -> str:
        """Send a chat completion request to any OpenAI-compatible endpoint."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if images:
            user_content = [{"type": "text", "text": user_prompt}]
            for img in images:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img}"}
                })
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        payload: dict[str, Any] = {
            "model": actual_model,
            "messages": messages,
            "stream": False,
            "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
            "temperature": getattr(settings, "LLM_TEMPERATURE", 0.85),
            "presence_penalty": getattr(settings, "LLM_PRESENCE_PENALTY", 0.6),
            "frequency_penalty": getattr(settings, "LLM_FREQUENCY_PENALTY", 0.6),
        }

        url = f"{base_url}/chat/completions"
        logger.debug("%s request → model=%s url=%s", backend_label, actual_model, url)

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status >= 400:
                err_body = await resp.text()
                logger.error("%s API 錯誤 (HTTP %s): %s", backend_label, resp.status, err_body)
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                f"Unexpected {backend_label} response schema: {data}"
            ) from exc

    async def _request_claude(
        self,
        session: aiohttp.ClientSession,
        *,
        actual_model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Send a request to Anthropic Claude Messages API."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "x-api-key": self._claude_api_key,
            "anthropic-version": "2023-06-01",
        }

        payload: dict[str, Any] = {
            "model": actual_model,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
            "temperature": getattr(settings, "LLM_TEMPERATURE", 0.85),
        }

        url = f"{self._claude_base_url}/messages"
        logger.debug("Claude request → model=%s url=%s", actual_model, url)

        async with session.post(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()

        try:
            # Anthropic returns content as a list of blocks
            return data["content"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                f"Unexpected Claude response schema: {data}"
            ) from exc

    # ------------------------------------------------------------------
    # Main chat interface
    # ------------------------------------------------------------------

    async def chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        images: list[str] | None = None,
    ) -> str:
        """Route a chat request to the correct backend based on model prefix."""
        session = await self._get_session()

        backend, actual_model = self._parse_model(model)

        # --- Ollama native API ---
        if backend == "ollama":
            user_msg = {"role": "user", "content": user_prompt}
            if images:
                user_msg["images"] = images
                
            payload: dict[str, Any] = {
                "model": actual_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    user_msg,
                ],
                "stream": False,
                "keep_alive": getattr(settings, "OLLAMA_KEEP_ALIVE", "60m"),
                "options": {
                    "num_ctx": settings.LLM_MAX_CONTEXT_TOKENS,
                    "num_predict": settings.LLM_MAX_OUTPUT_TOKENS,
                    "num_batch": getattr(settings, "LLM_NUM_BATCH", 2048),
                    "temperature": getattr(settings, "LLM_TEMPERATURE", 0.85),
                    "presence_penalty": getattr(settings, "LLM_PRESENCE_PENALTY", 0.6),
                    "frequency_penalty": getattr(settings, "LLM_FREQUENCY_PENALTY", 0.6),
                },
            }
            url = f"{self._base_url}/api/chat"
            logger.debug("Ollama request → model=%s url=%s", actual_model, url)

            async with session.post(url, json=payload) as resp:
                if resp.status >= 400:
                    err_body = await resp.text()
                    logger.error("Ollama API 錯誤 (HTTP %s): %s", resp.status, err_body)
                    # 如果是因為 num_batch 導致 400 Bad Request，自動剔除 num_batch 重試一次
                    if resp.status == 400 and "num_batch" in payload.get("options", {}):
                        logger.warning("Ollama 不支援或拒絕 options.num_batch，自動降級重試...")
                        payload["options"].pop("num_batch", None)
                        async with session.post(url, json=payload) as retry_resp:
                            if retry_resp.status >= 400:
                                retry_body = await retry_resp.text()
                                logger.error("Ollama 降級重試失敗 (HTTP %s): %s", retry_resp.status, retry_body)
                            retry_resp.raise_for_status()
                            data = await retry_resp.json()
                            return data["message"]["content"].strip()
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()

            try:
                return data["message"]["content"].strip()
            except (KeyError, TypeError) as exc:
                raise ValueError(
                    f"Unexpected Ollama response schema: {data}"
                ) from exc

        # --- LM Studio (legacy local, OpenAI-compatible) ---
        if backend == "lms":
            return await self._request_openai_compatible(
                session,
                base_url=self._lms_base_url,
                api_key="",  # LM Studio has no auth
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                backend_label="LMS",
                images=images,
            )

        # --- vLLM (High-Performance Engine, OpenAI-compatible) ---
        if backend == "vllm":
            return await self._request_openai_compatible(
                session,
                base_url=self._vllm_base_url,
                api_key=getattr(settings, "VLLM_API_KEY", ""),
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                backend_label="vLLM",
                images=images,
            )

        # --- OpenAI ChatGPT ---
        if backend == "openai":
            return await self._request_openai_compatible(
                session,
                base_url=self._openai_base_url,
                api_key=self._openai_api_key,
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                backend_label="OpenAI",
                images=images,
            )

        # --- DeepSeek ---
        if backend == "deepseek":
            return await self._request_openai_compatible(
                session,
                base_url=self._deepseek_base_url,
                api_key=self._deepseek_api_key,
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                backend_label="DeepSeek",
                images=images,
            )

        # --- Google Gemini (OpenAI-compatible endpoint) ---
        if backend == "gemini":
            return await self._request_openai_compatible(
                session,
                base_url=self._gemini_base_url,
                api_key=self._gemini_api_key,
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                backend_label="Gemini",
                images=images,
            )

        # --- Anthropic Claude (Messages API) ---
        if backend == "claude":
            return await self._request_claude(
                session,
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

        # Should never reach here
        raise ValueError(f"Unknown backend '{backend}' for model '{model}'")

    async def close(self) -> None:
        """安全釋放占用之 TCP 線路"""
        if self._session and not self._session.closed:
            await self._session.close()