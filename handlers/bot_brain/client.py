"""
handlers/bot_brain/client.py
============================
統一非同步推論客戶端（Async Inference Client）。

架構特點：
1. 支援前綴路由（Prefix-Based Routing）：
   - freetoken:  → FreeToken Edge-Native MoE 引擎 (OpenAI 相容協議, 預設端點 http://localhost:1919/v1)
   - ollama:     → Ollama 原生 API (/api/chat)
   - lms:        → LM Studio (OpenAI 相容協議, 向下相容至 LLM_BASE_URL)
   - vllm:       → vLLM 高效推理引擎 (OpenAI 相容協議)
   - openai:     → OpenAI 官方 API
   - deepseek:   → DeepSeek 官方 API
   - gemini:     → Google Gemini (OpenAI 相容端點)
   - openrouter: → OpenRouter 多模型閘道
   - claude:     → Anthropic Claude (Messages API)
2. 統一 OpenAI 相容協定轉發層：
   - 消除重複分支，所有 OpenAI 協議共用同一套 SSE 串流解析器。
3. 強制啟用串流 (stream=True) 與 MoE 分層逾時：
   - 自動解析 Server-Sent Events (SSE) 數據流，降低 TTFT 延遲並避免斷線。
4. 完整多模態（Vision）支援：
   - 自動將傳入之 base64 圖片編碼為 OpenAI 標準 image_url 格式。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiohttp

import settings
from core.compat import async_timeout

logger = logging.getLogger(__name__)


# Gemini 各思考強度對應的思考 Token 預算（thinking_level）。
# 官方文件：max_output_tokens 是「思考 + 輸出」的硬性總上限，因此額度必須
# 高於思考預算，否則思考會吃光額度導致空輸出或回覆被截斷。
_GEMINI_THINKING_BUDGET: dict[str, int] = {
    "low": 1024,
    "medium": 8192,
    "high": 24576,
}

# 思考之外額外保留給實際輸出的 Token 數。雪莉的日常對話需要足夠篇幅，
# 過小的輸出餘裕會讓回應被硬性截斷而顯得回話變少、內容單薄。
_GEMINI_OUTPUT_HEADROOM = 4096


def _normalized_finish_reason(finish_reason: Any) -> str | None:
    """正規化 finish_reason，移除 Gemini 附加的 ": PROHIBITED_CONTENT" 等原因後綴。

    Gemini OpenAI 相容端點會回傳 "content_filter: PROHIBITED_CONTENT" 這類
    帶原因說明的形式，需剝離後綴才能正確比對標準值。
    """
    if finish_reason is None:
        return None
    value = str(finish_reason).split(":", 1)[0].strip()
    return value or None


def _is_content_filtered(diagnostics: dict[str, Any]) -> bool:
    """判斷上次推論是否因內容安全過濾而被擋下（重試相同輸入無意義）。"""
    return _normalized_finish_reason(diagnostics.get("finish_reason")) == "content_filter"


class LLMError(aiohttp.ClientError):
    """推論鏈路結構化錯誤基底類別。

    繼承 ``aiohttp.ClientError`` 以維持既有呼叫端（含測試）的相容性，
    但攜帶結構化欄位讓重試策略不再依賴錯誤字串關鍵字比對。
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable


class LLMStreamError(LLMError):
    """串流協定層錯誤（SSE error frame、5xx、連線中斷等）。"""


class LLMAuthError(LLMStreamError):
    """憑證／權限錯誤：重試與換模型都不會成功。"""


class LLMRateLimitError(LLMStreamError):
    """配額或上游過載：可退避重試。"""

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class LLMContentFiltered(LLMStreamError):
    """供應商內容安全過濾：相同輸入重試必然失敗。"""


_CONTENT_FILTER_HINTS = (
    "content_filter",
    "content filter",
    "content policy",
    "safety",
    "prohibited",
    "blocked",
    "policy violation",
    "responsible ai",
    "harmful",
)
_AUTH_HINTS = (
    "invalid api key",
    "incorrect api key",
    "api key not valid",
    "unauthorized",
    "authentication",
    "permission denied",
    "invalid_api_key",
)


def _classify_llm_failure(
    detail: str,
    *,
    backend_label: str,
    status: int | None = None,
    code: int | str | None = None,
) -> LLMStreamError:
    """依 HTTP status／上游錯誤碼／訊息內容將失敗分類為結構化例外。

    Args:
        detail: 上游錯誤訊息（已截斷）。
        backend_label: 後端標籤，用於錯誤訊息。
        status: HTTP 狀態碼（SSE error frame 可能沒有）。
        code: 上游錯誤碼（SSE frame 的 ``error.code``）。

    Returns:
        LLMStreamError: 對應的結構化例外（尚未拋出）。
    """
    effective_status: int | None = status
    if effective_status is None and isinstance(code, int):
        effective_status = code
    elif effective_status is None and isinstance(code, str) and code.isdigit():
        effective_status = int(code)

    message = f"{backend_label} 推論失敗"
    if effective_status is not None:
        message += f" (HTTP {effective_status})"
    if detail:
        message += f": {detail[:200]}"

    lowered = f"{detail} {code or ''}".lower()
    if effective_status in (401, 403) or any(hint in lowered for hint in _AUTH_HINTS):
        return LLMAuthError(message, status=effective_status, code=code, retryable=False)
    if effective_status == 429:
        return LLMRateLimitError(message, status=effective_status, code=code)
    if any(hint in lowered for hint in _CONTENT_FILTER_HINTS):
        return LLMContentFiltered(message, status=effective_status, code=code, retryable=False)
    retryable = effective_status is None or effective_status >= 500 or effective_status in (408, 409)
    return LLMStreamError(
        message,
        status=effective_status,
        code=code,
        retryable=retryable,
    )


@asynccontextmanager
async def _deadline_post(
    session: aiohttp.ClientSession,
    url: str,
    *,
    deadline: float | None,
    **kwargs: Any,
) -> AsyncIterator[aiohttp.ClientResponse]:
    """在 ``asyncio.timeout`` 的整體 deadline 下發送請求並回傳回應。

    aiohttp 的 ``ClientTimeout(total=...)`` 會把「整個回應讀取（含 SSE 串流）」
    都算進同一個上限，導致長生成被硬切；因此改由外層 ``asyncio.timeout``
    掌握整體 deadline，aiohttp 只負責連線與單一 chunk 的讀取寬限。
    ``deadline`` 為 None 或非正數時不設整體上限。
    """
    effective_deadline = float(deadline) if deadline is not None and float(deadline) > 0 else None
    async with async_timeout(effective_deadline):
        async with session.post(url, **kwargs) as resp:
            yield resp


class OllamaClient:
    """Async HTTP client for local & cloud inference backends.

    Supports prefix-based routing across FreeToken, Ollama, LM Studio, vLLM,
    OpenAI, DeepSeek, Gemini, OpenRouter, and Claude.
    """

    def __init__(
        self,
        base_url: str | None = None,
        freetoken_base_url: str | None = None,
        lms_base_url: str | None = None,
        openai_base_url: str | None = None,
        openai_api_key: str | None = None,
        deepseek_base_url: str | None = None,
        deepseek_api_key: str | None = None,
        gemini_base_url: str | None = None,
        gemini_api_key: str | None = None,
        claude_base_url: str | None = None,
        claude_api_key: str | None = None,
        openrouter_base_url: str | None = None,
        openrouter_api_key: str | None = None,
        groq_base_url: str | None = None,
        groq_api_key: str | None = None,
        vllm_base_url: str | None = None,
    ) -> None:
        """Initialize the inference client with optional override parameters.

        Args:
            base_url: Custom Ollama base URL override.
            freetoken_base_url: Custom FreeToken base URL override.
            lms_base_url: Custom LM Studio base URL override.
            openai_base_url: Custom OpenAI base URL override.
            openai_api_key: Custom OpenAI API Key.
            deepseek_base_url: Custom DeepSeek base URL override.
            deepseek_api_key: Custom DeepSeek API Key.
            gemini_base_url: Custom Gemini base URL override.
            gemini_api_key: Custom Gemini API Key.
            claude_base_url: Custom Claude base URL override.
            claude_api_key: Custom Claude API Key.
            openrouter_base_url: Custom OpenRouter base URL override.
            openrouter_api_key: Custom OpenRouter API Key.
            groq_base_url: Custom Groq base URL override.
            groq_api_key: Custom Groq API Key.
            vllm_base_url: Custom vLLM base URL override.
        """
        self._custom_base_url = base_url
        self._custom_freetoken_base_url = freetoken_base_url
        self._custom_lms_base_url = lms_base_url
        self._custom_openai_base_url = openai_base_url
        self._custom_openai_api_key = openai_api_key
        self._custom_deepseek_base_url = deepseek_base_url
        self._custom_deepseek_api_key = deepseek_api_key
        self._custom_gemini_base_url = gemini_base_url
        self._custom_gemini_api_key = gemini_api_key
        self._custom_claude_base_url = claude_base_url
        self._custom_claude_api_key = claude_api_key
        self._custom_openrouter_base_url = openrouter_base_url
        self._custom_openrouter_api_key = openrouter_api_key
        self._custom_groq_base_url = groq_base_url
        self._custom_groq_api_key = groq_api_key
        self._custom_vllm_base_url = vllm_base_url

        self._session: aiohttp.ClientSession | None = None
        self.last_stream_diagnostics: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Base URL & API Key properties
    # ------------------------------------------------------------------

    @property
    def _base_url(self) -> str:
        url = str(self._custom_base_url or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434"))
        return url.rstrip("/")

    @property
    def _freetoken_base_url(self) -> str:
        url = str(
            self._custom_freetoken_base_url
            or getattr(settings, "LLM_BASE_URL", getattr(settings, "FREETOKEN_BASE_URL", "http://localhost:1919/v1"))
        )
        return url.rstrip("/")

    @property
    def _lms_base_url(self) -> str:
        url = str(
            self._custom_lms_base_url
            or getattr(settings, "LMS_BASE_URL", "http://localhost:1234/v1")
        )
        return url.rstrip("/")

    @property
    def _vllm_base_url(self) -> str:
        url = str(
            getattr(self, "_custom_vllm_base_url", None)
            or getattr(settings, "VLLM_BASE_URL", "http://localhost:8000/v1")
        )
        return url.rstrip("/")

    @property
    def _openai_base_url(self) -> str:
        url = str(self._custom_openai_base_url or getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1"))
        return url.rstrip("/")

    @property
    def _openai_api_key(self) -> str:
        return str(self._custom_openai_api_key or getattr(settings, "OPENAI_API_KEY", ""))

    @property
    def _deepseek_base_url(self) -> str:
        url = str(self._custom_deepseek_base_url or getattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
        return url.rstrip("/")

    @property
    def _deepseek_api_key(self) -> str:
        return str(self._custom_deepseek_api_key or getattr(settings, "DEEPSEEK_API_KEY", ""))

    @property
    def _gemini_base_url(self) -> str:
        url = str(self._custom_gemini_base_url or getattr(settings, "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"))
        return url.rstrip("/")

    @property
    def _gemini_api_key(self) -> str:
        return str(self._custom_gemini_api_key or getattr(settings, "GEMINI_API_KEY", ""))

    @property
    def _claude_base_url(self) -> str:
        url = str(self._custom_claude_base_url or getattr(settings, "CLAUDE_BASE_URL", "https://api.anthropic.com/v1"))
        return url.rstrip("/")

    @property
    def _claude_api_key(self) -> str:
        return str(self._custom_claude_api_key or getattr(settings, "CLAUDE_API_KEY", ""))

    @property
    def _openrouter_base_url(self) -> str:
        url = str(self._custom_openrouter_base_url or getattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
        return url.rstrip("/")

    @property
    def _openrouter_api_key(self) -> str:
        return str(self._custom_openrouter_api_key or getattr(settings, "OPENROUTER_API_KEY", ""))

    @property
    def _groq_base_url(self) -> str:
        url = str(self._custom_groq_base_url or getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
        return url.rstrip("/")

    @property
    def _groq_api_key(self) -> str:
        return str(self._custom_groq_api_key or getattr(settings, "GROQ_API_KEY", ""))

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or lazily initialize the shared aiohttp.ClientSession."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=getattr(settings, "LLM_TIMEOUT", 180),
                connect=30.0,
                sock_read=getattr(settings, "LLM_STREAM_CHUNK_TIMEOUT", 120.0),
            )
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    # ------------------------------------------------------------------
    # Model prefix parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_model(model: str) -> tuple[str, str]:
        """Parse model identifier into (backend_key, actual_model_name).

        支援標準前綴（gemini:, openai:, claude:, deepseek:, openrouter:, ollama:, lms:, vllm:, freetoken:），
        並具備無前綴自動辨識能力（如 gemini-3.5-flash-lite, gpt-4o, claude-3-5 等），
        防止使用者或上層模組省略前綴時誤入 localhost:1919 (FreeToken)。

        Args:
            model: Full model string with optional prefix (e.g. 'freetoken:mistralai/...').

        Returns:
            tuple[str, str]: Backend key and actual model name.
        """
        model_str = model.strip()
        PREFIX_MAP: dict[str, int] = {
            "freetoken:":  10,
            "openrouter:": 11,
            "groq:":       5,
            "claude:":     7,
            "deepseek:":   9,
            "gemini:":     7,
            "openai:":     7,
            "lms:":        4,
            "vllm:":       5,
            "ollama:":     7,
        }
        for prefix, length in PREFIX_MAP.items():
            if model_str.startswith(prefix):
                return prefix.rstrip(":"), model_str[length:].strip()

        # 自動偵測常見雲端模型，防止未帶前綴時誤入 localhost:1919
        lower_model = model_str.lower()
        if lower_model.startswith(("gemini-", "models/gemini")):
            return "gemini", model_str
        elif lower_model.startswith(("gpt-", "o1-", "o3-", "text-embedding-", "chatgpt-")):
            return "openai", model_str
        elif lower_model.startswith("claude-"):
            return "claude", model_str
        elif lower_model.startswith("deepseek-"):
            return "deepseek", model_str

        # Default fallback backend is freetoken
        return "freetoken", model_str

    # ------------------------------------------------------------------
    # OpenAI Backend Config Resolver
    # ------------------------------------------------------------------

    def _resolve_openai_backend(
        self, backend: str
    ) -> tuple[str, str, dict[str, str], str]:
        """Resolve base_url, api_key, headers, and label for an OpenAI-compatible backend.

        Args:
            backend: The backend key (e.g. 'freetoken', 'openai', etc.).

        Returns:
            tuple[str, str, dict[str, str], str]: (base_url, api_key, extra_headers, backend_label)
        """
        if backend == "freetoken":
            return self._freetoken_base_url, "", {}, "FreeToken"
        elif backend == "lms":
            return self._lms_base_url, "", {}, "LMS"
        elif backend == "vllm":
            return self._vllm_base_url, getattr(settings, "VLLM_API_KEY", ""), {}, "vLLM"
        elif backend == "openai":
            return self._openai_base_url, self._openai_api_key, {}, "OpenAI"
        elif backend == "deepseek":
            return self._deepseek_base_url, self._deepseek_api_key, {}, "DeepSeek"
        elif backend == "gemini":
            return self._gemini_base_url, self._gemini_api_key, {}, "Gemini"
        elif backend == "groq":
            return self._groq_base_url, self._groq_api_key, {}, "Groq"
        elif backend == "openrouter":
            extra_headers = {
                "HTTP-Referer": "https://github.com/YuuuuutoOuO/Sherry-An-AI-Companion-Platform",
                "X-Title": "Sherry AI Companion",
            }
            return self._openrouter_base_url, self._openrouter_api_key, extra_headers, "OpenRouter"
        else:
            return self._freetoken_base_url, "", {}, "FreeToken"

    # ------------------------------------------------------------------
    # Message Payload Builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_openai_messages(
        system_prompt: str,
        user_prompt: str,
        images: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Construct OpenAI chat messages with optional multimodal image data.

        Args:
            system_prompt: System instructions.
            user_prompt: User input.
            images: Optional list of base64 encoded image strings.

        Returns:
            list[dict[str, Any]]: Formatted OpenAI message list.
        """
        if images:
            user_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
            for img in images:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img}"},
                })
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    # ------------------------------------------------------------------
    # Unified OpenAI-Compatible SSE Streaming Handler
    # ------------------------------------------------------------------

    async def _stream_openai_compatible(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        api_key: str,
        actual_model: str,
        system_prompt: str,
        user_prompt: str,
        backend_label: str,
        extra_headers: dict[str, str] | None = None,
        images: list[str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        stream_diagnostics: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat completions from an OpenAI-compatible endpoint using SSE (stream=True).

        Args:
            session: Active aiohttp ClientSession.
            base_url: Target base URL.
            api_key: Bearer token (if any).
            actual_model: Model name/ID.
            system_prompt: System instructions.
            user_prompt: User input.
            backend_label: Label for structured logging.
            extra_headers: Optional additional request headers.
            images: Optional base64 encoded images.
            temperature: Sampling temperature override.
            max_tokens: Maximum completion tokens override.
            timeout: Request timeout override in seconds.
            stream_diagnostics: Optional sink populated with finish_reason, token usage,
                safety ratings and prompt feedback so callers can diagnose empty replies.

        Yields:
            str: Token delta strings as received.

        Raises:
            aiohttp.ClientResponseError: If server responds with HTTP error code.
            ValueError: If response format is invalid.
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if backend_label == "OpenRouter":
            headers["HTTP-Referer"] = "https://github.com/YuuuuutoOuO/Sherry-An-AI-Companion-Platform"
            headers["X-Title"] = "Sherry AI Companion"
        if extra_headers:
            headers.update(extra_headers)

        messages = self._build_openai_messages(system_prompt, user_prompt, images)
        target_temp = temperature if temperature is not None else getattr(settings, "LLM_TEMPERATURE", 0.85)
        target_max_tokens = max_tokens if max_tokens is not None else settings.LLM_MAX_OUTPUT_TOKENS

        # Google Gemini 官方 OpenAI 相容端點不支援 presence_penalty、frequency_penalty 與 stream_options，傳入會引發 HTTP 400
        is_gemini = backend_label == "Gemini" or "googleapis.com" in base_url
        gemini_reasoning_effort: str | None = None
        if is_gemini:
            # 自動升級/相容映射已下線之 Google Gemini 模型（防止舊設定或歷史快取導致 404）
            gemini_model_aliases: dict[str, str] = {
                "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
                "gemini-2.5-flash": "gemini-3.5-flash-lite",
                "gemini-2.5-pro": "gemini-3.8-pro",
                "gemini-2.0-flash": "gemini-3.5-flash-lite",
                "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
                "gemini-1.5-flash": "gemini-3.5-flash-lite",
                "gemini-1.5-flash-8b": "gemini-3.5-flash-lite",
                "gemini-1.5-pro": "gemini-3.8-pro",
                "models/gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
                "models/gemini-2.5-flash": "gemini-3.5-flash-lite",
                "models/gemini-2.0-flash": "gemini-3.5-flash-lite",
                "models/gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
                "models/gemini-1.5-flash": "gemini-3.5-flash-lite",
                "models/gemini-1.5-flash-8b": "gemini-3.5-flash-lite",
                "models/gemini-1.5-pro": "gemini-3.8-pro",
            }
            clean_model = actual_model
            if clean_model.startswith("models/"):
                clean_model = clean_model[len("models/"):]
            if clean_model in gemini_model_aliases or actual_model in gemini_model_aliases:
                target_alias = gemini_model_aliases.get(clean_model) or gemini_model_aliases[actual_model]
                logger.info(
                    "GeminiClient: 偵測到已下線模型 '%s'，自動映射為最新支援模型 '%s'",
                    actual_model,
                    target_alias,
                )
                actual_model = target_alias

            # Gemini 2.0 / 2.5 Flash 具備內部思考推理機制（Thinking Tokens）。
            # OpenAI 相容端點的 max_tokens 限制的是「思考 Token + 輸出 Token」總和，
            # 若維持預設 1024 會被思考 Token 耗盡導致回覆嚴重截斷 (finish_reason=length)。
            gemini_min_tokens = getattr(settings, "GEMINI_MIN_OUTPUT_TOKENS", 4096)
            if target_max_tokens < gemini_min_tokens:
                target_max_tokens = gemini_min_tokens

            # 官方文件：max_output_tokens 是包含思考 Token 的硬性總上限，且「不會改變
            # 思考預算配置」。Gemini 3.x Flash 預設 thinking_level=medium（8192 思考 Token），
            # 若 max_tokens 僅 4096，模型會在思考階段觸及硬性上限後回傳「空輸出」，
            # 且 OpenAI 相容層不會回報 finish_reason（觀測為 HTTP 200 + 僅 role 前導幀）。
            #
            # 這裡採取「保留思考品質、把額度拉高」的策略：依實際思考預算回推所需的
            # max_tokens，並額外保留 _GEMINI_OUTPUT_HEADROOM 給實際輸出，避免思考把
            # 輸出額度吃光而導致回覆被截斷。
            # 單一任務可覆寫推理強度：短對話回覆指定 low 可以把思考預算從
            # 8192 降到 1024，首字與整體延遲都會明顯下降；未指定時沿用全域設定。
            _effort = str(
                reasoning_effort or getattr(settings, "GEMINI_REASONING_EFFORT", "medium")
            ).strip().lower()
            if _effort in _GEMINI_THINKING_BUDGET:
                gemini_reasoning_effort = _effort
                _thinking_budget = _GEMINI_THINKING_BUDGET[_effort]
                if target_max_tokens <= _thinking_budget:
                    target_max_tokens = _thinking_budget + _GEMINI_OUTPUT_HEADROOM

        payload: dict[str, Any] = {
            "model": actual_model,
            "messages": messages,
            "stream": True,
            "max_tokens": target_max_tokens,
            "temperature": target_temp,
        }
        if gemini_reasoning_effort:
            payload["reasoning_effort"] = gemini_reasoning_effort
        if not is_gemini:
            payload["presence_penalty"] = getattr(settings, "LLM_PRESENCE_PENALTY", 0.6)
            payload["frequency_penalty"] = getattr(settings, "LLM_FREQUENCY_PENALTY", 0.6)
            if getattr(settings, "LLM_STREAM_INCLUDE_USAGE", True):
                payload["stream_options"] = {"include_usage": True}

        url = f"{base_url}/chat/completions"
        logger.debug(
            "%s stream request → model=%s url=%s temp=%.2f max_tokens=%d reasoning_effort=%s",
            backend_label,
            actual_model,
            url,
            target_temp,
            target_max_tokens,
            gemini_reasoning_effort or "n/a",
        )

        total_timeout = timeout if timeout is not None else getattr(settings, "LLM_TIMEOUT", 180)
        # 整體 deadline 由 _deadline_post 的 asyncio.timeout 掌控；aiohttp 的 total
        # 必須設為 None，否則長生成會在同一個 total 上被硬切。sock_read 僅約束
        # 「單一 chunk 之間的間隔」，與整體上限解耦。
        chunk_timeout = getattr(settings, "LLM_STREAM_CHUNK_TIMEOUT", 120.0)

        req_timeout = aiohttp.ClientTimeout(
            total=None,
            connect=30.0,
            sock_read=chunk_timeout,
        )

        reasoning_chunks: list[str] = []
        in_reasoning_mode = False
        has_yielded_content = False
        finish_reason: str | None = None
        usage_tokens: dict[str, int] | None = None
        stream_started = time.monotonic()
        first_token_latency: float | None = None
        last_chunk_ts = stream_started
        malformed_frames = 0
        safety_ratings: list[dict[str, Any]] = []
        prompt_feedback: dict[str, Any] | None = None
        empty_delta_frames = 0

        async with _deadline_post(
            session, url, deadline=total_timeout, json=payload, headers=headers, timeout=req_timeout
        ) as resp:
            if resp.status >= 400:
                err_body = await resp.text()
                logger.error("%s API 錯誤 (HTTP %s): %s", backend_label, resp.status, err_body)
                if stream_diagnostics is not None:
                    stream_diagnostics.clear()
                    stream_diagnostics["http_status"] = resp.status
                    stream_diagnostics["finish_reason"] = None
                raise _classify_llm_failure(
                    err_body,
                    backend_label=backend_label,
                    status=resp.status,
                )

            async for raw_line in resp.content:
                now = time.monotonic()
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue

                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                    if chunk.get("error"):
                        err_detail = str(chunk["error"])[:200]
                        err_obj = chunk["error"] if isinstance(chunk["error"], dict) else {}
                        logger.error("%s SSE 錯誤幀: %s", backend_label, err_detail)
                        raise _classify_llm_failure(
                            err_detail,
                            backend_label=f"{backend_label} SSE error frame",
                            code=err_obj.get("code") or err_obj.get("status"),
                        )
                    choices = chunk.get("choices", [])
                    usage = chunk.get("usage") or (choices[0].get("usage") if choices else None)
                    if usage and isinstance(usage, dict):
                        usage_tokens = {
                            str(k): int(v)
                            for k, v in usage.items()
                            if k in ("prompt_tokens", "completion_tokens", "total_tokens")
                        }
                    raw_safety = chunk.get("safety_ratings")
                    if isinstance(raw_safety, list) and raw_safety:
                        safety_ratings.extend(item for item in raw_safety if isinstance(item, dict))
                    raw_feedback = chunk.get("prompt_feedback")
                    if isinstance(raw_feedback, dict) and not prompt_feedback:
                        prompt_feedback = raw_feedback
                    if choices:
                        choice = choices[0]
                        delta = choice.get("delta", {})
                        content = delta.get("content") or choice.get("text", "")
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thought", "")
                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason = fr
                        if not content and not reasoning:
                            empty_delta_frames += 1

                        # 收到首個 Token（無論是 reasoning_content 或 content）立即標記 TTFT
                        if first_token_latency is None and (reasoning or content):
                            first_token_latency = (now - stream_started) * 1000.0
                            logger.info(
                                "%s stream 首token延遲 %.0fms (prompt=%.0f字), 後續chunk間隔 ≥%.0fms, model=%s",
                                backend_label, first_token_latency,
                                len(system_prompt) + len(user_prompt),
                                (now - last_chunk_ts) * 1000.0,
                                actual_model,
                            )

                        if reasoning:
                            reasoning_chunks.append(reasoning)
                            if not in_reasoning_mode:
                                in_reasoning_mode = True
                                yield "<thought>"
                            last_chunk_ts = now
                            # 注意：不在此設 has_yielded_content = True，也不直接 yield reasoning。
                            # reasoning 內容只收集進 reasoning_chunks，由串流結束後的 fallback
                            # (line 504) 統一輸出乾淨文字。這樣純推理模型（DeepSeek-R1 / Gemma-4
                            # 等只吐 reasoning_content 的模型）才能正確觸發 fallback，不會變成
                            # generic 訊息。<thought> 標籤保留給混合型模型（reasoning→content）使用。

                        if content:
                            if in_reasoning_mode:
                                in_reasoning_mode = False
                                yield "</thought>\n"
                            last_chunk_ts = now
                            has_yielded_content = True
                            yield content
                except json.JSONDecodeError:
                    malformed_frames += 1
                    logger.warning("%s SSE JSON 解析警告: %s", backend_label, data_str[:100])
                    continue

        if in_reasoning_mode:
            in_reasoning_mode = False
            yield "</thought>\n"

        if stream_diagnostics is not None:
            blocked_categories = sorted({
                str(rating.get("category"))
                for rating in safety_ratings
                if rating.get("blocked") and rating.get("category")
            })
            stream_diagnostics.update({
                "finish_reason": finish_reason,
                "usage_tokens": usage_tokens,
                "prompt_tokens": (usage_tokens or {}).get("prompt_tokens"),
                "completion_tokens": (usage_tokens or {}).get("completion_tokens"),
                "total_tokens": (usage_tokens or {}).get("total_tokens"),
                "blocked_safety_categories": blocked_categories,
                "prompt_feedback": prompt_feedback,
                "has_yielded_content": has_yielded_content,
                "reasoning_chars": sum(len(c) for c in reasoning_chunks),
                "empty_delta_frames": empty_delta_frames,
                "malformed_frames": malformed_frames,
                "max_tokens": target_max_tokens,
                "prompt_chars": len(system_prompt) + len(user_prompt),
            })

        if first_token_latency is not None:
            prompt_len = len(system_prompt) + len(user_prompt)
            usage_tokens = usage_tokens or {}
            usage_str = (
                f", usage={usage_tokens}"
                if usage_tokens
                else f", prompt估~{self._estimate_tokens(system_prompt, user_prompt)}tokens"
            )
            gen_ms = (time.monotonic() - stream_started) * 1000.0
            completion_tokens = int(usage_tokens.get("completion_tokens", 0)) if usage_tokens else 0
            if completion_tokens > 0 and gen_ms > first_token_latency:
                decode_ms = gen_ms - first_token_latency
                tok_s = completion_tokens / (decode_ms / 1000.0)
                usage_str = f"{usage_str}, 生成速度~{tok_s:.1f}tok/s"
            logger.info(
                "%s stream 總生成 %.0fms (首token %.0fms + %d chunks), prompt=%d字%s, finish_reason=%s, model=%s",
                backend_label,
                gen_ms,
                first_token_latency,
                has_yielded_content,
                prompt_len,
                usage_str,
                finish_reason,
                actual_model,
            )
            if finish_reason == "length":
                logger.warning(
                    "%s 生成被 engine 以 finish_reason=length 中斷 (prompt=%d字%s)！回覆可能不完整，且很可能是 prompt 逼近 context/max_tokens 上限所致。",
                    backend_label,
                    prompt_len,
                    usage_str,
                )
            if finish_reason == "content_filter":
                logger.warning(
                    "%s 回應被 provider 以 finish_reason=content_filter 擋下，判定為安全過濾。",
                    backend_label,
                )


        # 若模型為純思考/推理輸出 (例如 DeepSeek-R1 / Gemma-4 輸出僅含 reasoning_content)
        if not has_yielded_content and reasoning_chunks:
            full_reasoning = "".join(reasoning_chunks)
            clean_text = re.sub(r"</?think>", "", full_reasoning).strip()
            if clean_text:
                yield clean_text

    @staticmethod
    def _estimate_tokens(system_prompt: str, user_prompt: str) -> int:
        """估算混合中英文本的真實 token 數（引擎未送回 usage 時使用）。

        舊版用 1.5 tokens/字 虛胖估算（純中文約 0.75、英文約 4 字/token），
        差到 2 倍以上，會誤導對 context／KV 容量的判斷。
        """
        text = system_prompt + user_prompt
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff")
        non_cjk = len(text) - cjk
        return int(round(cjk * 0.75 + non_cjk / 3.5))

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
        extra_headers: dict[str, str] | None = None,
        images: list[str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Send a chat completion request to any OpenAI-compatible endpoint with enforced streaming.

        Args:
            session: Active aiohttp ClientSession.
            base_url: Target base URL.
            api_key: Bearer token (if any).
            actual_model: Model name/ID.
            system_prompt: System instructions.
            user_prompt: User input.
            backend_label: Label for structured logging.
            extra_headers: Optional additional request headers.
            images: Optional base64 encoded images.
            temperature: Sampling temperature override.
            max_tokens: Maximum completion tokens override.
            timeout: Request timeout override in seconds.

        Returns:
            str: Assembled full completion text.
        """
        chunks: list[str] = []
        stream_diagnostics: dict[str, Any] = {}
        self.last_stream_diagnostics = stream_diagnostics
        async for chunk in self._stream_openai_compatible(
            session,
            base_url=base_url,
            api_key=api_key,
            actual_model=actual_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            backend_label=backend_label,
            extra_headers=extra_headers,
            images=images,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            stream_diagnostics=stream_diagnostics,
            reasoning_effort=reasoning_effort,
        ):
            chunks.append(chunk)

        result = "".join(chunks).strip()
        if result:
            return result

        if chunks:
            logger.warning(
                "%s 回傳退化內容（僅標籤殘留，長度 %d）：%r (model: %s)",
                backend_label,
                len(result),
                result[:80],
                actual_model,
            )
            return result

        blocked = stream_diagnostics.get("blocked_safety_categories") or []
        logger.warning(
            "%s 回傳空白字串 (model: %s) | finish_reason=%s usage=%s max_tokens=%s prompt=%s字 "
            "reasoning=%s字 empty_frames=%s malformed=%s blocked=%s prompt_feedback=%s",
            backend_label,
            actual_model,
            stream_diagnostics.get("finish_reason"),
            stream_diagnostics.get("usage_tokens"),
            stream_diagnostics.get("max_tokens"),
            stream_diagnostics.get("prompt_chars"),
            stream_diagnostics.get("reasoning_chars"),
            stream_diagnostics.get("empty_delta_frames"),
            stream_diagnostics.get("malformed_frames"),
            blocked or None,
            stream_diagnostics.get("prompt_feedback"),
        )
        if blocked:
            logger.warning("%s 空回應主因為安全過濾，被封鎖類別: %s", backend_label, ", ".join(blocked))
        elif _normalized_finish_reason(stream_diagnostics.get("finish_reason")) == "content_filter":
            logger.warning(
                "%s 空回應主因為安全過濾：provider 以 finish_reason=content_filter 擋下此請求"
                "（reason=%s）。同一輸入重試結果相同，請改寫被觸發的內容或更換供應商模型。",
                backend_label,
                stream_diagnostics.get("finish_reason"),
            )
        elif stream_diagnostics.get("finish_reason") == "length" and not stream_diagnostics.get("completion_tokens"):
            logger.warning(
                "%s 空回應主因為思考 Token 耗盡 max_tokens=%s，請調高 GEMINI_MIN_OUTPUT_TOKENS。",
                backend_label,
                stream_diagnostics.get("max_tokens"),
            )
        elif stream_diagnostics.get("finish_reason") is None and (backend_label == "Gemini" or "googleapis.com" in base_url):
            # Gemini OpenAI 相容層在思考階段觸及 max_tokens 硬上限時，不會回報 finish_reason，
            # 僅會回傳一個 role 前導幀後結束串流（HTTP 200、無安全封鎖、無畸形幀）。
            logger.warning(
                "%s 空回應且無 finish_reason：Gemini 思考 Token 極可能已吃光 max_tokens=%s"
                "（思考預算 %s 對應 reasoning_effort=%s）。請調高 GEMINI_MIN_OUTPUT_TOKENS "
                "或降低 GEMINI_REASONING_EFFORT。",
                backend_label,
                stream_diagnostics.get("max_tokens"),
                _GEMINI_THINKING_BUDGET.get(
                    getattr(settings, "GEMINI_REASONING_EFFORT", "medium"), "medium"
                ),
                getattr(settings, "GEMINI_REASONING_EFFORT", "medium") or "model-default",
            )
        return result

    # ------------------------------------------------------------------
    # Claude Native API
    # ------------------------------------------------------------------

    async def _request_claude(
        self,
        session: aiohttp.ClientSession,
        *,
        actual_model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        """Send a request to Anthropic Claude Messages API."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "x-api-key": self._claude_api_key,
            "anthropic-version": "2023-06-01",
        }

        target_temp = temperature if temperature is not None else getattr(settings, "LLM_TEMPERATURE", 0.85)
        target_max_tokens = max_tokens if max_tokens is not None else settings.LLM_MAX_OUTPUT_TOKENS

        payload: dict[str, Any] = {
            "model": actual_model,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": target_max_tokens,
            "temperature": target_temp,
        }

        url = f"{self._claude_base_url}/messages"
        logger.debug("Claude request → model=%s url=%s temp=%.2f max_tokens=%d", actual_model, url, target_temp, target_max_tokens)

        req_timeout = aiohttp.ClientTimeout(
            total=timeout if timeout is not None else getattr(settings, "LLM_TIMEOUT", 180),
            connect=30.0,
        )

        async with session.post(url, json=payload, headers=headers, timeout=req_timeout) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()

        try:
            return str(data["content"][0]["text"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unexpected Claude response schema: {data}") from exc

    # ------------------------------------------------------------------
    # Public Chat & Streaming Interfaces
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        images: list[str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream token chunks asynchronously from the appropriate backend.

        Args:
            model: Full model name with optional prefix (e.g. 'freetoken:...').
            system_prompt: System persona/instructions.
            user_prompt: User instruction or input text.
            images: Optional base64 encoded images.
            temperature: Sampling temperature override.
            max_tokens: Maximum tokens override.
            timeout: Timeout in seconds override.

        Yields:
            str: Token delta chunks.
        """
        session = await self._get_session()
        backend, actual_model = self._parse_model(model)
        if not actual_model:
            fallback = str(getattr(settings, "PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1") or "").strip()
            backend, actual_model = self._parse_model(fallback)

        target_temp = temperature if temperature is not None else getattr(settings, "LLM_TEMPERATURE", 0.85)
        target_max_tokens = max_tokens if max_tokens is not None else settings.LLM_MAX_OUTPUT_TOKENS

        if backend == "ollama":
            # Ollama streaming
            user_msg: dict[str, Any] = {"role": "user", "content": user_prompt}
            if images:
                user_msg["images"] = images

            payload: dict[str, Any] = {
                "model": actual_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    user_msg,
                ],
                "stream": True,
                "keep_alive": getattr(settings, "OLLAMA_KEEP_ALIVE", "60m"),
                "options": {
                    "num_ctx": settings.LLM_MAX_CONTEXT_TOKENS,
                    "num_predict": target_max_tokens,
                    "num_batch": getattr(settings, "LLM_NUM_BATCH", 2048),
                    "temperature": target_temp,
                    "presence_penalty": getattr(settings, "LLM_PRESENCE_PENALTY", 0.6),
                    "frequency_penalty": getattr(settings, "LLM_FREQUENCY_PENALTY", 0.6),
                },
            }
            url = f"{self._base_url}/api/chat"
            total_ol = timeout if timeout is not None else getattr(settings, "LLM_TIMEOUT", 180)
            chunk_ol = getattr(settings, "LLM_STREAM_CHUNK_TIMEOUT", 120.0)
            req_timeout = aiohttp.ClientTimeout(
                total=None,
                connect=30.0,
                sock_read=chunk_ol,
            )
            async with _deadline_post(session, url, deadline=total_ol, json=payload, timeout=req_timeout) as resp:
                if resp.status >= 400:
                    err_body = await resp.text()
                    logger.error("Ollama API 錯誤 (HTTP %s): %s", resp.status, err_body)
                    raise _classify_llm_failure(
                        err_body,
                        backend_label="Ollama",
                        status=resp.status,
                    )
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            err_detail = str(chunk["error"])[:200]
                            err_obj = chunk["error"] if isinstance(chunk["error"], dict) else {}
                            logger.error("Ollama api/chat 錯誤幀: %s", err_detail)
                            raise _classify_llm_failure(
                                err_detail,
                                backend_label="Ollama api/chat error frame",
                                code=err_obj.get("code") or err_obj.get("status"),
                            )
                        msg = chunk.get("message", {})
                        content = msg.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
        elif backend == "claude":
            # Claude non-streaming wrapper for stream interface
            full_text = await self._request_claude(
                session,
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=target_temp,
                max_tokens=target_max_tokens,
                timeout=timeout,
            )
            yield full_text
        else:
            # Unified OpenAI-compatible backend streaming (FreeToken, LMS, vLLM, OpenAI, DeepSeek, Gemini, OpenRouter)
            base_url, api_key, extra_headers, label = self._resolve_openai_backend(backend)
            async for chunk in self._stream_openai_compatible(
                session,
                base_url=base_url,
                api_key=api_key,
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                backend_label=label,
                extra_headers=extra_headers,
                images=images,
                temperature=target_temp,
                max_tokens=target_max_tokens,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            ):
                yield chunk

    async def chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        images: list[str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Route a chat request to the correct backend and return full completion text.

        Args:
            model: Target model identifier.
            system_prompt: System persona/instructions.
            user_prompt: User instruction or input.
            images: Optional base64 encoded images.
            temperature: Sampling temperature override.
            max_tokens: Maximum completion tokens override.
            timeout: Timeout in seconds override.
            reasoning_effort: Optional per-request thinking level (low/medium/high).

        Returns:
            str: Generated completion response.
        """
        self.last_stream_diagnostics = {}
        session = await self._get_session()
        backend, actual_model = self._parse_model(model)
        if not actual_model:
            fallback = str(getattr(settings, "PRIMARY_MODEL", "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1") or "").strip()
            logger.warning(
                "OllamaClient: 模型名稱為空 ('%s')，自動回退至 PRIMARY_MODEL ('%s')",
                model, fallback,
            )
            backend, actual_model = self._parse_model(fallback)

        target_temp = temperature if temperature is not None else getattr(settings, "LLM_TEMPERATURE", 0.85)
        target_max_tokens = max_tokens if max_tokens is not None else settings.LLM_MAX_OUTPUT_TOKENS

        # --- Ollama native API ---
        if backend == "ollama":
            user_msg: dict[str, Any] = {"role": "user", "content": user_prompt}
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
                    "num_predict": target_max_tokens,
                    "num_batch": getattr(settings, "LLM_NUM_BATCH", 2048),
                    "temperature": target_temp,
                    "presence_penalty": getattr(settings, "LLM_PRESENCE_PENALTY", 0.6),
                    "frequency_penalty": getattr(settings, "LLM_FREQUENCY_PENALTY", 0.6),
                },
            }
            url = f"{self._base_url}/api/chat"
            logger.debug("Ollama request → model=%s url=%s temp=%.2f max_tokens=%d", actual_model, url, target_temp, target_max_tokens)

            req_timeout = aiohttp.ClientTimeout(
                total=timeout if timeout is not None else getattr(settings, "LLM_TIMEOUT", 180),
                connect=30.0,
            )

            async with session.post(url, json=payload, timeout=req_timeout) as resp:
                if resp.status >= 400:
                    err_body = await resp.text()
                    logger.error("Ollama API 錯誤 (HTTP %s): %s", resp.status, err_body)
                    if resp.status == 400 and "num_batch" in payload.get("options", {}):
                        logger.warning("Ollama 不支援或拒絕 options.num_batch，自動降級重試...")
                        payload["options"].pop("num_batch", None)
                        async with session.post(url, json=payload, timeout=req_timeout) as retry_resp:
                            if retry_resp.status >= 400:
                                retry_body = await retry_resp.text()
                                logger.error("Ollama 降級重試失敗 (HTTP %s): %s", retry_resp.status, retry_body)
                            retry_resp.raise_for_status()
                            retry_data = await retry_resp.json()
                            return str(retry_data["message"]["content"]).strip()
                resp.raise_for_status()
                resp_data: dict[str, Any] = await resp.json()

            try:
                return str(resp_data["message"]["content"]).strip()
            except (KeyError, TypeError) as exc:
                raise ValueError(f"Unexpected Ollama response schema: {resp_data}") from exc

        # --- Claude Native API ---
        if backend == "claude":
            return await self._request_claude(
                session,
                actual_model=actual_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=target_temp,
                max_tokens=target_max_tokens,
                timeout=timeout,
            )

        # --- Unified OpenAI-Compatible Backend (FreeToken, LMS, vLLM, OpenAI, DeepSeek, Gemini, OpenRouter) ---
        base_url, api_key, extra_headers, label = self._resolve_openai_backend(backend)
        return await self._request_openai_compatible(
            session,
            base_url=base_url,
            api_key=api_key,
            actual_model=actual_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            backend_label=label,
            extra_headers=extra_headers,
            images=images,
            temperature=target_temp,
            max_tokens=target_max_tokens,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
        )

    async def close(self) -> None:
        """安全釋放占用之 TCP 線路。"""
        if self._session and not self._session.closed:
            await self._session.close()