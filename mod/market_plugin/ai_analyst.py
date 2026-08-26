"""
ai_analyst.py  (mod/market_plugin)
====================================
Gemini AI 分析模組。
使用 Google 官方最新 `google-genai` SDK。
API Key 對接模組獨立的 config.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from mod.market_plugin import config as cfg

try:
    from google import genai
    from google.genai.errors import APIError
    _GENAI_AVAILABLE = bool(cfg.GEMINI_API_KEY)
except ImportError:
    _GENAI_AVAILABLE = False
    APIError = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


class AIAnalyst:
    """Gemini AI market analysis and prediction report generator."""

    def __init__(self) -> None:
        self.model_name = "gemini-2.0-flash-lite"
        if _GENAI_AVAILABLE:
            try:
                self.client: genai.Client | None = genai.Client(api_key=cfg.GEMINI_API_KEY)
                logger.info("[market_plugin] Gemini AI 模型已初始化 (google-genai, %s)", self.model_name)
            except Exception as e:
                logger.warning("[market_plugin] Gemini 初始化失敗: %s", e)
                self.client = None
        else:
            self.client = None
            logger.warning("[market_plugin] GEMINI_API_KEY 未設定，AI 分析功能將受限")

    # ------------------------------------------------------------------
    # 工具函式
    # ------------------------------------------------------------------
    def _extract_json(self, text: str) -> list[dict[str, Any]]:
        try:
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                res = json.loads(match.group(0))
                return res if isinstance(res, list) else []
            clean = text.replace("```json", "").replace("```", "").strip()
            res = json.loads(clean)
            return res if isinstance(res, list) else []
        except (json.JSONDecodeError, Exception):
            logger.warning("[market_plugin] ❌ JSON 解析失敗，AI 回傳: %s...", text[:100])
            return []

    async def _call_gemini(self, prompt: str) -> str | None:
        if not self.client:
            return None
        for attempt in range(3):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                )
                return response.text
            except APIError as e:
                err_str = str(e).upper()
                if getattr(e, "code", None) == 429 or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait = 30 + attempt * 10
                    logger.warning("[market_plugin] ⚠️ API 額度額滿，冷卻 %ds... (%d/3)", wait, attempt + 1)
                    await asyncio.sleep(wait)
                else:
                    logger.error("[market_plugin] ❌ AI 呼叫異常 (APIError): %s", e)
                    await asyncio.sleep(5)
            except Exception as e:
                logger.error("[market_plugin] ❌ AI 呼叫異常: %s", e)
                await asyncio.sleep(5)
        return None

    # ------------------------------------------------------------------
    # 晚報 AI 分析
    # ------------------------------------------------------------------
    async def analyze_evening_review(self, market_data_str: str) -> list[dict[str, Any]]:
        prompt = f"""
你是一位精通技術分析與消息面的操盤手。現在是晚上 9:30。
請根據以下數據進行預測：
{market_data_str}
任務：
1. 針對清單中的每一檔標的進行分析。
2. 回傳 JSON 陣列：
[
    {{"ticker": "NVDA", "predicted_price": 145.2, "score": 88, "reason": "MA5支撐強勁..."}}
]
"""
        resp_text = await self._call_gemini(prompt)
        return self._extract_json(resp_text) if resp_text else []

    # ------------------------------------------------------------------
    # Discord 報告生成
    # ------------------------------------------------------------------
    async def generate_discord_report(self, predictions: list[Any], title_suffix: str = "") -> str:
        if not predictions:
            return "❌ 無可用預測數據"
        prompt = f"""
請根據以下預測數據寫一份 Discord 報告：
{predictions}
標題包含：{title_suffix}
重點：
1. 簡潔列出 Top 3。
2. 其餘用條列式帶過。
"""
        resp_text = await self._call_gemini(prompt)
        return resp_text if resp_text else "報告生成失敗 (AI 無回應)"
