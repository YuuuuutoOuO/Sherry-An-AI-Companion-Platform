"""
ai_analyst.py  (mod/market_plugin)
====================================
Gemini AI 分析模組。
移植自 DC_bot_StockAnalysis-main/modules/ai_analyst.py，
API Key 改為對接模組獨立的 config.py。
"""

from __future__ import annotations

import asyncio
import json
import re

from mod.market_plugin import config as cfg

try:
    import google.generativeai as genai
    from google.api_core.exceptions import ResourceExhausted
    _GENAI_AVAILABLE = bool(cfg.GEMINI_API_KEY)
    if _GENAI_AVAILABLE:
        genai.configure(api_key=cfg.GEMINI_API_KEY)
except ImportError:
    _GENAI_AVAILABLE = False
    ResourceExhausted = Exception


class AIAnalyst:
    def __init__(self):
        if _GENAI_AVAILABLE:
            try:
                self.model = genai.GenerativeModel("gemini-2.0-flash-lite")
                print("✅ [market_plugin] Gemini AI 模型已初始化")
            except Exception as e:
                print(f"⚠️ [market_plugin] Gemini 初始化失敗: {e}")
                self.model = None
        else:
            self.model = None
            print("⚠️ [market_plugin] GEMINI_API_KEY 未設定，AI 分析功能將受限")

    # ------------------------------------------------------------------
    # 工具函式
    # ------------------------------------------------------------------
    def _extract_json(self, text: str) -> list:
        try:
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            clean = text.replace("```json", "").replace("```", "").strip()
            return json.loads(clean)
        except json.JSONDecodeError:
            print(f"[market_plugin] ❌ JSON 解析失敗，AI 回傳: {text[:100]}...")
            return []

    async def _call_gemini(self, prompt: str):
        if not self.model:
            return None
        for attempt in range(3):
            try:
                response = await self.model.generate_content_async(prompt)
                return response
            except ResourceExhausted:
                wait = 30 + attempt * 10
                print(f"[market_plugin] ⚠️ API 額度額滿，冷卻 {wait}s... ({attempt+1}/3)")
                await asyncio.sleep(wait)
            except Exception as e:
                print(f"[market_plugin] ❌ AI 呼叫異常: {e}")
                await asyncio.sleep(5)
        return None

    # ------------------------------------------------------------------
    # 晚報 AI 分析
    # ------------------------------------------------------------------
    async def analyze_evening_review(self, market_data_str: str) -> list:
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
        resp = await self._call_gemini(prompt)
        return self._extract_json(resp.text) if resp else []

    # ------------------------------------------------------------------
    # Discord 報告生成
    # ------------------------------------------------------------------
    async def generate_discord_report(self, predictions: list, title_suffix: str = "") -> str:
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
        resp = await self._call_gemini(prompt)
        return resp.text if resp else "報告生成失敗 (AI 無回應)"
