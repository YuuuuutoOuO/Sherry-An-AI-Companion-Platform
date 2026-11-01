"""mod/spark/checkers/web_monitor_checker.py - 智慧網頁與新聞巡檢守門員策略。

純業務邏輯層：透過 WebMonitorClientProtocol 協定注入搜尋/爬蟲客戶端。
具備內容指紋去重 (content_hash) 與 AI 語意條件判定。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from ..models import Trigger, TriggerResult

logger = logging.getLogger(__name__)


class WebMonitorClientProtocol(Protocol):
    """網頁監控檢索客戶端協定介面。"""

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """執行即時網路檢索並回傳條目列表。"""
        ...


class WebMonitorChecker:
    """智慧網頁巡檢守門員檢查器。"""

    trigger_type: str = "web_monitor"

    def __init__(
        self,
        web_client: WebMonitorClientProtocol | None = None,
        ai_evaluator: Any | None = None,
    ) -> None:
        """初始化 WebMonitorChecker。

        Args:
            web_client: 實作 WebMonitorClientProtocol 的網路檢索客戶端。
            ai_evaluator: 選填之語意過濾判定器。
        """
        self._client = web_client
        self._evaluator = ai_evaluator

    async def should_fire(
        self,
        trigger: Trigger,
        now_dt: datetime | None = None,
    ) -> TriggerResult:
        """評估是否有最新符合條件之網路消息。

        Args:
            trigger: 欲評估的 Trigger 實例。
            now_dt: 當前時間（選填，方便單元測試注入）。

        Returns:
            TriggerResult: 判定結果與最新內容摘要。
        """
        cond = trigger.condition
        query = str(cond.get("query") or trigger.message or "").strip()
        if not query:
            return TriggerResult(should_fire=False, current_value="未指定搜尋監控關鍵字 (query)")

        # 間隔檢查 (若有指定 interval_minutes)
        now = now_dt if now_dt is not None else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        if trigger.last_triggered_at:
            try:
                last_dt = datetime.fromisoformat(trigger.last_triggered_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                interval_minutes = int(cond.get("interval_minutes", 0))
                if interval_minutes > 0:
                    elapsed = (now - last_dt).total_seconds()
                    if elapsed < interval_minutes * 60:
                        remaining = max(0, int((interval_minutes * 60 - elapsed) / 60))
                        return TriggerResult(
                            should_fire=False,
                            current_value=f"網頁巡檢間隔冷卻中，預計 {remaining} 分鐘後再次檢查",
                        )
            except Exception:
                pass

        if self._client is None:
            return TriggerResult(should_fire=False, current_value="未配置 WebMonitor 客戶端")

        try:
            results = await self._client.search(query, limit=5)
        except Exception as exc:
            logger.warning("[WebMonitorChecker] 檢索失敗 (%s): %s", query, exc)
            return TriggerResult(should_fire=False, current_value=f"網路檢索失敗: {exc}")

        if not results:
            return TriggerResult(
                should_fire=False,
                current_value=f"近期未檢索到與 '{query}' 相關的情報",
            )

        # 計算內容指紋 (去重)
        fingerprint_src = "||".join(
            f"{r.get('title', '')}:{r.get('snippet', '') or r.get('summary', '') or r.get('url', '')}"
            for r in results[:5]
        )
        content_hash = hashlib.sha256(fingerprint_src.encode("utf-8")).hexdigest()[:16]

        last_hash = str(
            cond.get("last_content_hash")
            or trigger.extra_data.get("last_content_hash")
            or ""
        ).strip()

        if last_hash and content_hash == last_hash:
            return TriggerResult(
                should_fire=False,
                current_value=f"內容無新變化（指紋 {content_hash} 一致）",
            )

        # AI 語意條件判定 (若有設定 ai_condition 且 evaluator 可用)
        ai_cond = str(cond.get("ai_condition") or "").strip()
        if ai_cond and self._evaluator is not None:
            try:
                matched, reason = await self._evaluator.evaluate(condition=ai_cond, content=fingerprint_src)
                if not matched:
                    return TriggerResult(
                        should_fire=False,
                        current_value=f"未滿足語意過濾條件: {reason}",
                    )
            except Exception as exc:
                logger.warning("[WebMonitorChecker] 語意評估異常: %s", exc)

        top_item = results[0]
        top_title = str(top_item.get("title") or "最新情報")
        top_url = str(top_item.get("url") or top_item.get("link") or "")
        top_snippet = str(top_item.get("snippet") or top_item.get("summary") or "")

        summary_text = f"🔎 發現最新情報更新！\n【{top_title}】\n{top_snippet[:120]}\n{top_url}".strip()

        return TriggerResult(
            should_fire=True,
            current_value=summary_text,
            detail_data={
                "last_content_hash": content_hash,
                "query": query,
                "title": top_title,
                "url": top_url,
                "results": results[:5],
            },
        )
