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

import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any

from core.contracts import (
    CapabilityDescriptor,
    CloudInferenceProtocol,
    SherryRequest,
    SherryResponse,
)
from core.dispatcher import SmartMessageDispatcher
from core.guard import CoreGuard
from core.registry import CapabilityRegistry
from core.state import SherryMindState

logger = logging.getLogger(__name__)

# 相同輸入的雲端推論結果快取 TTL (秒)。短時間內對同一訊息重複摘要/翻譯直接命中快取，節省 LLM 開銷。
CONTEXT_RESULT_CACHE_TTL_SEC: float = 60.0
# 快取容量上限：採 LRU 逐出，避免記憶體無限增長。
CONTEXT_RESULT_CACHE_MAX_SIZE: int = 256


class SherryCore:
    """雪莉中央微核心指揮官。"""

    def __init__(
        self,
        bot: Any | None = None,
        cloud_client: CloudInferenceProtocol | None = None,
    ) -> None:
        self.bot = bot
        self.state: SherryMindState = SherryMindState()
        self.registry: CapabilityRegistry = CapabilityRegistry()
        self.guard: CoreGuard = CoreGuard()
        self.dispatcher: SmartMessageDispatcher = SmartMessageDispatcher()
        if cloud_client is not None:
            self.cloud_client: CloudInferenceProtocol = cloud_client
        else:
            self.cloud_client = self._create_default_cloud_client()
        self._context_result_cache: OrderedDict[tuple[Any, ...], tuple[float, str]] = OrderedDict()
        self._register_default_capabilities()

    @staticmethod
    def _create_default_cloud_client() -> CloudInferenceProtocol:
        """安全延遲載入預設雲端推論客戶端，避免微核心頂層直接靜態依賴 mod 層。"""
        from mod.clients.cloud_inference_client import CloudInferenceClient
        return CloudInferenceClient()

    def _register_default_capabilities(self) -> None:
        """註冊預設核心工具與專題外掛能力。"""
        # 1. 精選日常 AI 工具 (is_ai_callable=True)
        builtin_tools = [
            ("SEARCH", "搜尋引擎與網路即時資訊檢索（含時事、新聞與即時氣象天氣）", ["搜尋", "查詢", "GOOGLE", "天氣", "氣溫"]),
            ("STOCK", "查詢台灣股市台股與美股即時行情", ["股票", "股價", "行情"]),
            ("DRAW", "使用畫布或二次元畫筆繪製自訂插圖", ["畫圖", "繪畫", "繪圖"]),
            ("MUSIC", "點播 YouTube 音樂留聲機與隊列控制", ["音樂", "點歌", "放歌"]),
            ("SCHEDULE", "設定定時提醒、鬧鐘、條件推播與排程事項 (SPARK)", ["提醒", "鬧鐘", "排程", "推播", "條件推播", "SPARK"]),
            ("PIXIV", "搜尋二次元插畫圖庫美圖", ["PIXIV", "插畫", "美圖"]),
            ("SYSTEM_INSPECT", "主機硬體負載與雪莉運行狀態檢測", ["系統", "硬體", "監控", "CPU", "GPU", "負載"]),
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

        # 3. 雲端快捷分析能力 (Context Menu Capabilities)
        context_tools = [
            (
                "CONTEXT_SUMMARIZE",
                "多模態訊息速讀與智能摘要（文字/網址/圖片）",
                ["雪莉摘要", "摘要"],
                "summarize_text",
            ),
            (
                "CONTEXT_TRANSLATE",
                "訊息專業多國語言即時翻譯為繁體中文",
                ["翻譯這則訊息", "翻譯"],
                "translate_text",
            ),
            (
                "CONTEXT_VISION",
                "多模態圖片識別、圖表說明與程式報錯截圖分析",
                ["分析圖片", "看圖"],
                "analyze_image",
            ),
            (
                "CONTEXT_URL_SUMMARIZE",
                "網頁長文與 YouTube 影片大綱深度摘要",
                ["摘要網址", "網址摘要", "YT摘要"],
                "summarize_url",
            ),
            (
                "CONTEXT_EXTEND_ASK",
                "訊息延伸發問與深度追問",
                ["雪莉延伸發問", "延伸發問", "追問"],
                "extend_ask",
            ),
        ]
        for name, desc, aliases, method_name in context_tools:
            self.registry.register(
                CapabilityDescriptor(
                    name=name,
                    category="core_builtin",
                    description=desc,
                    aliases=aliases,
                    is_ai_callable=True,
                    execute_handler=self._make_method_handler(method_name),
                )
            )

    def _make_method_handler(self, method_name: str) -> Any:
        """建立動態解析的執行器，執行時才自目前 cloud_client 取得對應方法。

        讓替換 self.cloud_client (如測試注入 Mock) 後仍能正確派發。
        """

        async def handler(*args: Any, **kwargs: Any) -> Any:
            method = getattr(self.cloud_client, method_name)
            return await method(*args, **kwargs)

        return handler

    def _context_cache_lookup(self, cache_key: tuple[Any, ...]) -> str | None:
        """查詢快取並惰性檢查 TTL；命中時刷新 LRU 位置，逾期則移除。

        Returns:
            str | None: 快取的回覆文字，未命中或已逾期回傳 None。
        """
        cached = self._context_result_cache.get(cache_key)
        if cached is None:
            return None

        ts, cached_text = cached
        if time.monotonic() - ts > CONTEXT_RESULT_CACHE_TTL_SEC:
            self._context_result_cache.pop(cache_key, None)
            return None

        self._context_result_cache.move_to_end(cache_key)
        return cached_text

    def _context_cache_store(self, cache_key: tuple[Any, ...], reply_text: str) -> None:
        """寫入快取並執行 LRU 容量逐出 (O(1))。

        Args:
            cache_key: 快取鍵。
            reply_text: 要快取的雲端回覆文字 (尚未截斷之原始內容)。
        """
        self._context_result_cache[cache_key] = (time.monotonic(), reply_text)
        self._context_result_cache.move_to_end(cache_key)
        while len(self._context_result_cache) > CONTEXT_RESULT_CACHE_MAX_SIZE:
            self._context_result_cache.popitem(last=False)

    def _context_cache_key(
        self,
        action: str,
        raw_content: str,
        extra: dict[str, Any] | None = None,
    ) -> tuple[Any, ...]:
        """建立快取鍵 (action + 輸入內容 + 額外參數)，供去重使用。"""
        if extra:
            ordered = tuple(sorted((k, str(v)) for k, v in extra.items() if v not in (None, "")))
            return (action, raw_content) + ordered
        return (action, raw_content)

    async def handle_request(self, request: SherryRequest) -> SherryResponse:
        """處理進入微核心之標準請求。

        Args:
            request: SherryRequest 實例。

        Returns:
            SherryResponse: 包含回覆文字、Embed、附件與思維鏈之標準回應。
        """
        from handlers.social.observability import get_trace_id, set_trace_id

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
        #    context_menu 為高成本雲端推論，使用獨立限流窗口避免與本地 Handler 互相擠兌。
        if request.source_type == "context_menu":
            import settings
            ctx_max = int(getattr(settings, "CONTEXT_MENU_MAX_CALLS", 5))
            ctx_window = float(getattr(settings, "CONTEXT_MENU_WINDOW_SEC", 30.0))
            allowed, cooldown = self.guard.check_rate_limit(
                request.user_id, max_calls=ctx_max, window_sec=ctx_window, scope="context_menu"
            )
        else:
            allowed, cooldown = self.guard.check_rate_limit(request.user_id, scope="default")
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
            resp.latency_ms = int((time.monotonic() - start_time) * 1000)
            if request.message_ref and hasattr(request.message_ref, "channel"):
                await self.dispatcher.dispatch(request.message_ref.channel, resp, reference_msg=request.message_ref)
            self.state.record_action_finish(action_desc, success=False)
            return resp

        # 2. 路由分發與執行
        response = SherryResponse(trace_id=actual_tid, reply_text="")
        try:
            if request.source_type == "chat_message" and request.message_ref and self.bot:
                from handlers.social.router import route_and_execute_tag
                await route_and_execute_tag(self.bot, request.message_ref, from_core=True)
                response.reply_text = "(已透過社交大腦完成調度與回覆)"
                response.success = True
            elif request.source_type == "context_menu":
                action = request.options.get("action", "summarize").upper()
                cap = self.registry.get_capability(f"CONTEXT_{action}")

                # 未註冊之動作一律回退至摘要
                if cap is None or cap.execute_handler is None:
                    cap = self.registry.get_capability("CONTEXT_SUMMARIZE")
                    action = "SUMMARIZE"

                if cap is None or cap.execute_handler is None:
                    response.success = False
                    response.error_message = f"Capability CONTEXT_{action} not found or has no handler"
                    response.reply_text = "❌ 處理失敗：未找到對應的能力處理器。"
                    self.state.record_action_finish(action_desc, success=False)
                    return response

                handler: Any = cap.execute_handler
                extra: dict[str, Any] = {}
                if action == "SUMMARIZE":
                    img_bytes_sum: bytes = request.options.get("image_bytes", b"")
                    mime_type_sum: str = request.options.get("mime_type", "image/png")
                    img_url_sum: str = request.options.get("image_url", "")
                    if not img_url_sum and request.attachments:
                        img_url_sum = request.attachments[0]
                    if img_url_sum or img_bytes_sum:
                        extra = {
                            "mime_type": mime_type_sum,
                            "image_url": img_url_sum,
                        }
                        if img_bytes_sum:
                            extra["image_hash"] = hashlib.sha256(img_bytes_sum).hexdigest()
                elif action == "TRANSLATE":
                    target_lang = request.options.get("target_lang", "繁體中文")
                    extra["target_lang"] = target_lang
                elif action == "VISION":
                    img_bytes: bytes = request.options.get("image_bytes", b"")
                    mime_type: str = request.options.get("mime_type", "image/png")
                    prompt: str = request.options.get("prompt", request.raw_content)
                    img_url: str = request.options.get("image_url", "")
                    if not img_url and request.attachments:
                        img_url = request.attachments[0]
                    extra = {
                        "mime_type": mime_type,
                        "prompt": prompt,
                        "image_url": img_url,
                    }
                    # 以 bytes 直接提供畫像時，納入內容雜湊避免不同圖片誤命中同一快取
                    if img_bytes:
                        extra["image_hash"] = hashlib.sha256(img_bytes).hexdigest()
                elif action == "EXTEND_ASK":
                    question = request.options.get("question", "")
                    context_history = request.options.get("context_history", "")
                    user_name = request.options.get("user_name", request.user_name)
                    extra = {
                        "question": question,
                        "context_history": context_history,
                        "user_name": user_name,
                    }

                # 相同輸入短時間內去重，避免重複呼叫雲端 LLM 消耗配額
                cache_key = self._context_cache_key(action, request.raw_content, extra or None)
                cache_hit = False
                cached_text = self._context_cache_lookup(cache_key)
                if cached_text is not None:
                    response.reply_text = cached_text
                    cache_hit = True
                else:
                    if action == "SUMMARIZE":
                        if img_bytes_sum or img_url_sum:
                            response.reply_text = await handler(
                                text=request.raw_content,
                                image_bytes=img_bytes_sum,
                                mime_type=mime_type_sum,
                                image_url=img_url_sum,
                            )
                        else:
                            response.reply_text = await handler(request.raw_content)
                    elif action == "TRANSLATE":
                        response.reply_text = await handler(request.raw_content, target_lang=target_lang)
                    elif action == "VISION":
                        response.reply_text = await handler(
                            image_bytes=img_bytes, mime_type=mime_type, user_prompt=prompt, image_url=img_url
                        )
                    elif action == "EXTEND_ASK":
                        response.reply_text = await handler(
                            target_text=request.raw_content,
                            question=question,
                            context_history=context_history,
                            user_name=user_name,
                        )
                    else:
                        response.reply_text = await handler(request.raw_content)
                    self._context_cache_store(cache_key, response.reply_text)

                # Discord 2000 字元長度防禦截斷
                if response.reply_text and len(response.reply_text) > 1950:
                    response.reply_text = response.reply_text[:1920] + "\n\n...（內容已達 Discord 上限，已截斷）"

                response.capabilities_used = [f"CONTEXT_{action}"]
                if cache_hit:
                    response.capabilities_used.append("CACHE_HIT")
                response.success = True
            else:
                response.reply_text = f"【SherryCore】已受理請求：{request.raw_content}"
                response.success = True

        except Exception as exc:
            logger.error("SherryCore: 請求執行異常: %s", exc, exc_info=True)
            response.success = False
            response.error_message = str(exc)
            response.reply_text = f"❌ 處理失敗：{exc}"
            self.state.record_action_finish(action_desc, success=False)
            return response

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        response.latency_ms = elapsed_ms
        self.state.record_action_finish(action_desc, success=response.success)

        return response
