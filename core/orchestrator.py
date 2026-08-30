"""
core/orchestrator.py
====================
雪莉中央調度微核心 (SherryCore Orchestrator)。

身為「樂團指揮家」，統籌：
1. 全生命週期請求分發 (SherryRequest -> SherryResponse)。
2. 即時心智快照 (SherryMindState) 維護。
3. 全域能力 (CapabilityRegistry) 雙層管理（精選日常工具 vs 專題外掛）。
4. 安全守衛與顯存協調 (CoreGuard)。
5. 智慧單一聚合輸出與 2000 字分段發送 (SmartMessageDispatcher)。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.contracts import CapabilityDescriptor, SherryRequest, SherryResponse, ThinkingStep
from core.dispatcher import SmartMessageDispatcher
from core.guard import CoreGuard
from core.registry import CapabilityRegistry
from core.state import SherryMindState
from handlers.social.observability import get_trace_id, log_event, set_trace_id

logger = logging.getLogger(__name__)


class SherryCore:
    """雪莉中央微核心指揮官。"""

    def __init__(self, bot: Any | None = None) -> None:
        self.bot = bot
        self.state: SherryMindState = SherryMindState()
        self.registry: CapabilityRegistry = CapabilityRegistry()
        self.guard: CoreGuard = CoreGuard()
        self.dispatcher: SmartMessageDispatcher = SmartMessageDispatcher()
        self._register_default_capabilities()

    def _register_default_capabilities(self) -> None:
        """註冊預設核心工具與專題外掛能力。"""
        # 1. 精選日常 AI 工具 (is_ai_callable=True)
        builtin_tools = [
            ("SEARCH", "搜尋引擎與網路即時資訊檢索", ["搜尋", "查詢", "GOOGLE"]),
            ("STOCK", "查詢台灣股市台股與美股即時行情", ["股票", "股價", "行情"]),
            ("DRAW", "使用畫布或二次元畫筆繪製自訂插圖", ["畫圖", "繪畫", "繪圖"]),
            ("MUSIC", "點播 YouTube 音樂留聲機與隊列控制", ["音樂", "點歌", "放歌"]),
            ("SCHEDULE", "設定定時提醒、鬧鐘與排程事項", ["提醒", "鬧鐘", "排程"]),
            ("WEATHER", "查詢全球與台灣各縣市即時天氣與降雨預報", ["天氣", "氣溫", "降雨"]),
            ("PIXIV", "搜尋二次元插畫圖庫美圖", ["PIXIV", "插畫", "美圖"]),
        ]
        for name, desc, aliases in builtin_tools:
            self.registry.register(
                CapabilityDescriptor(
                    name=name,
                    category="core_builtin",
                    description=desc,
                    aliases=aliases,
                    is_ai_callable=True,
                )
            )

        # 2. 獨立專題 Mod 外掛 (is_ai_callable=False，預設不污染日常 Prompt)
        mod_plugins = [
            ("DND", "龍與地下城跑團冒險與角色扮演模組", ["跑團", "TRPG"]),
            ("MARKET", "進階加密貨幣與 PTCG 卡牌行情分析", ["PTCG", "加密貨幣"]),
            ("SPARK", "條件觸發自動推播引擎", ["推播", "條件推播"]),
            ("DEEP_SEARCH", "多階段深度研究與長篇文獻報告", ["深度研究", "研究報告"]),
            ("PAINTER", "多畫布專業繪圖渲染引擎", ["專業繪圖"]),
            ("FEED_SENTINEL", "RSS 與資訊源自動巡邏哨兵", ["哨兵", "RSS"]),
        ]
        for name, desc, aliases in mod_plugins:
            self.registry.register(
                CapabilityDescriptor(
                    name=name,
                    category="mod_plugin",
                    description=desc,
                    aliases=aliases,
                    is_ai_callable=False,  # 保持雙層隔離
                )
            )

    async def handle_request(self, request: SherryRequest) -> SherryResponse:
        """處理進入微核心之標準請求。

        Args:
            request: SherryRequest 實例。

        Returns:
            SherryResponse: 包含回覆文字、Embed、附件與思維鏈之標準回應。
        """
        start_time = time.monotonic()
        actual_tid = request.trace_id or get_trace_id()
        set_trace_id(actual_tid)

        action_desc = f"{request.source_type}: {request.raw_content[:20]}"
        self.state.record_action_start(action_desc)

        logger.info(
            "SherryCore: [%s] 收到請求 source=%s user=%s content='%s'",
            actual_tid,
            request.source_type,
            request.user_name,
            request.raw_content[:50],
        )

        # 1. 頻率限制守衛檢查
        allowed, cooldown = self.guard.check_rate_limit(request.user_id)
        if not allowed:
            import settings
            msg_tmpl = getattr(settings, "RATE_LIMIT_REPLY_MESSAGE", "⚠️ 慢一點喔！請等 {cooldown} 秒後再試～")
            reply = msg_tmpl.format(cooldown=cooldown)
            resp = SherryResponse(
                trace_id=actual_tid,
                reply_text=reply,
                success=False,
                error_message="Rate limit exceeded",
            )
            if request.message_ref:
                await self.dispatcher.dispatch(request.message_ref.channel, resp, reference_msg=request.message_ref)
            self.state.record_action_finish(action_desc, success=False)
            return resp

        # 2. 路由分發與執行
        response = SherryResponse(trace_id=actual_tid, reply_text="")
        try:
            if request.source_type == "chat_message" and request.message_ref and self.bot:
                from handlers.social.router import route_and_execute_tag
                await route_and_execute_tag(self.bot, request.message_ref)
                response.reply_text = "(已透過社交大腦完成調度與回覆)"
                response.success = True
            else:
                response.reply_text = f"【SherryCore】已受理請求：{request.raw_content}"
                response.success = True

        except Exception as exc:
            logger.error("SherryCore: 請求執行異常: %s", exc, exc_info=True)
            response.success = False
            response.error_message = str(exc)
            self.state.record_action_finish(action_desc, success=False)
            return response

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        response.latency_ms = elapsed_ms
        self.state.record_action_finish(action_desc, success=response.success)

        return response
