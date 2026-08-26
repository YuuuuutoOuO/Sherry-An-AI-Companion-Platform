"""
llm_client.py (mod/deep_search/repositories)
============================================
地端/雲端 LLM 介接客戶端實作。
封裝 handlers.bot_brain.client.OllamaClient，提供統一的學術/推論生成介面。
"""

from __future__ import annotations

import logging

from handlers.bot_brain.client import OllamaClient
from mod.deep_search import config
from mod.deep_search.domain.interfaces import ILLMClient

logger = logging.getLogger(__name__)


class OllamaResearchClient(ILLMClient):
    """基於 OllamaClient 的推論客戶端，支援 LM Studio / Ollama 及各種主流模型後端。"""

    def __init__(self, ollama_client: OllamaClient | None = None) -> None:
        self._client = ollama_client or OllamaClient()

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        """發起 LLM 推論請求。

        Args:
            model: 模型名稱 (如: lms:... 或 ollama:...)。
            system_prompt: 系統提示詞。
            user_prompt: 使用者輸入/任務指令。
            temperature: 採樣溫度。
            max_tokens: 最大生成 Token 上限 override。
            timeout: 推論逾時秒數 override。

        Returns:
            str: 生成之回覆文字，若失敗則返回空字串。
        """
        target_model = model or config.get_default_model()
        try:
            logger.debug(
                "[deep_search.llm] 發送推論請求 -> model: %s, prompt_len: %d, max_tokens: %s, timeout: %s",
                target_model,
                len(user_prompt),
                str(max_tokens),
                str(timeout),
            )
            response = await self._client.chat(
                model=target_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return response.strip() if response else ""
        except Exception as exc:
            logger.error(
                "[deep_search.llm] 推論發生異常 (model: %s): %s",
                target_model,
                exc,
            )
            return ""
