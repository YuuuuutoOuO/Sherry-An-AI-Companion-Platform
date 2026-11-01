from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from types import SimpleNamespace
from typing import Any

from .contracts import ChatEvent, RoomSnapshot
from .memory import MemoryConsolidator
from .model import PrimaryModelClient, resolve_primary_model
from .policy import ReplyPolicy
from .tools import CyberToolRegistry
from .worker import CyberRoomWorker

logger = logging.getLogger(__name__)


def event_from_message(message: Any, bot_user: Any = None, *, is_mentioned: bool = False) -> ChatEvent:
    """把 discord.Message 正規化；不讀取 content 以外的私有狀態。"""
    is_reply_to_bot = False
    reference = getattr(message, "reference", None)
    if reference is not None and bot_user is not None:
        resolved = getattr(reference, "resolved", None)
        if getattr(getattr(resolved, "author", None), "id", None) == getattr(bot_user, "id", None):
            is_reply_to_bot = True
        cached = getattr(reference, "cached_message", None)
        if getattr(getattr(cached, "author", None), "id", None) == getattr(bot_user, "id", None):
            is_reply_to_bot = True

    created_at = getattr(message, "created_at", None)
    try:
        ts = float(created_at.timestamp())
    except Exception:
        ts = time.time()

    attachments = tuple(
        str(getattr(item, "url", "") or "")
        for item in (getattr(message, "attachments", None) or [])
        if getattr(item, "url", None)
    )
    return ChatEvent(
        channel_id=str(getattr(getattr(message, "channel", None), "id", "") or ""),
        message_id=str(getattr(message, "id", "") or ""),
        author_id=str(getattr(getattr(message, "author", None), "id", "") or ""),
        author_name=str(getattr(getattr(message, "author", None), "display_name", "") or "群友"),
        content=str(getattr(message, "content", "") or ""),
        created_at=ts,
        guild_id=str(getattr(getattr(message, "guild", None), "id", "") or ""),
        is_reply_to_bot=is_reply_to_bot,
        is_mentioned=bool(is_mentioned),
        is_bot=bool(getattr(getattr(message, "author", None), "bot", False)),
        attachment_urls=attachments,
        raw=message,
    )


class CyberEngine:
    def __init__(self, bot: Any, care_engine: Any, *, mode: str | None = None) -> None:
        self.bot = bot
        self.care_engine = care_engine
        self.repository = getattr(care_engine, "repository", None)
        if mode is None:
            try:
                from .ui_config import get_cyber_ui_config

                mode = get_cyber_ui_config().engine_mode
            except Exception:
                import settings

                mode = str(getattr(settings, "CYBER_ENGINE_MODE", "shadow") or "shadow")
        self.mode = mode if mode in {"off", "shadow", "live"} else "shadow"

        self._worker: CyberRoomWorker | None = None
        self._memory: MemoryConsolidator | None = None
        self._proactive_task: asyncio.Task[None] | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=80)
        self._started = False
        # 由 CareCog 注入既有的 CareSurfer；v2 主動開話題時借它去網路上找素材。
        self.surfer: Any = None

    # ------------------------------------------------------------------
    # 生命週期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self.mode == "off" or self._started:
            return
        config = await self._config()
        channel_id = str(getattr(config, "sandbox_channel_id", "0") or "0").strip()
        if channel_id in {"", "0"}:
            logger.info("[CyberV2] 未設定測試頻道，引擎待命")
            return

        registry = CyberToolRegistry()
        client = PrimaryModelClient(self.bot, tool_registry=registry)
        self._memory = MemoryConsolidator(client=client)
        self._worker = CyberRoomWorker(
            channel_id=channel_id,
            bot=self.bot,
            config_provider=self._config,
            model=client,
            policy=ReplyPolicy(),
            memory=self._memory,
            shadow=self.mode == "shadow",
            event_sink=self._event_sink,
        )
        self._worker.start()
        try:
            await self._prime_runtime_from_history(channel_id)
        except Exception as exc:
            logger.debug("[CyberV2] 初始化頻道時間錨點失敗: %s", exc)
        if self.mode == "live":
            self._proactive_task = asyncio.create_task(
                self._proactive_loop(),
                name=f"cyber-v2-proactive-{channel_id}",
            )
        self._started = True
        logger.info("[CyberV2] 引擎啟動 mode=%s channel=%s", self.mode, channel_id)

    async def _prime_runtime_from_history(self, channel_id: str) -> None:
        """重啟後從頻道歷史補回 last_human/last_bot，安靜計時不會從零開始。"""
        if self._worker is None:
            return
        try:
            cid = int(channel_id)
        except (TypeError, ValueError):
            return
        channel = self.bot.get_channel(cid) if self.bot is not None else None
        if channel is None and self.bot is not None and hasattr(self.bot, "fetch_channel"):
            try:
                channel = await self.bot.fetch_channel(cid)
            except Exception:
                return
        if channel is None or not hasattr(channel, "history"):
            return
        bot_id = getattr(getattr(self.bot, "user", None), "id", None)
        latest_human = 0.0
        latest_bot = 0.0
        try:
            async for message in channel.history(limit=30):
                created_at = getattr(message, "created_at", None)
                try:
                    ts = float(created_at.timestamp())
                except Exception:
                    continue
                author = getattr(message, "author", None)
                if bot_id is not None and getattr(author, "id", None) == bot_id:
                    latest_bot = max(latest_bot, ts)
                elif not bool(getattr(author, "bot", False)):
                    latest_human = max(latest_human, ts)
        except Exception as exc:
            logger.debug("[CyberV2] 讀取頻道歷史失敗: %s", exc)
            return
        if latest_human:
            self._worker.runtime.last_human_at = max(
                float(self._worker.runtime.last_human_at or 0.0),
                latest_human,
            )
        if latest_bot:
            self._worker.runtime.last_bot_at = max(
                float(self._worker.runtime.last_bot_at or 0.0),
                latest_bot,
            )
            self._worker.runtime.opened_at = self._worker.runtime.last_bot_at

    async def stop(self) -> None:
        self._started = False
        if self._proactive_task is not None:
            self._proactive_task.cancel()
            try:
                await self._proactive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._proactive_task = None
        if self._worker is not None:
            await self._worker.stop()
            self._worker = None

    async def reset(self) -> None:
        await self.stop()
        await self.start()

    async def set_mode(self, mode: str) -> str:
        """WebUI 切換 off/shadow/live；會重建 worker 與 proactive loop。"""
        target = mode if mode in {"off", "shadow", "live"} else "shadow"
        if target == self.mode and self._started:
            return self.mode
        await self.stop()
        self.mode = target
        if target != "off":
            await self.start()
        return self.mode

    # ------------------------------------------------------------------
    # 事件入口
    # ------------------------------------------------------------------
    async def submit_human(self, event: ChatEvent) -> bool:
        if self.mode == "off" or event.is_bot:
            return False
        if not self._started:
            await self.start()
        if self._worker is None or event.channel_id != self._worker.channel_id:
            return False
        return await self._worker.submit_human(event)

    async def submit_turn(self, request: Any) -> bool:
        if self.mode == "off":
            return False
        if not self._started:
            await self.start()
        if self._worker is None:
            return False
        channel_id = str(getattr(request, "channel_id", "") or "")
        if channel_id and channel_id != self._worker.channel_id:
            return False
        return await self._worker.submit_turn(request)

    async def submit_proactive(
        self,
        *,
        reason: str = "proactive",
        motivation: str = "",
        mood: str = "",
    ) -> bool:
        if self.mode != "live":
            return False
        if not self._started:
            await self.start()
        if self._worker is None:
            return False
        await self._worker.submit_proactive(reason=reason, motivation=motivation, mood=mood)
        return True

    async def cancel_pending(self, channel_id: int | str, *, reason: str = "") -> int:
        """通知 worker 放棄尚未生成的同頻道 ambient 批次。"""
        if self._worker is None or str(channel_id) != self._worker.channel_id:
            return 0
        return self._worker.cancel_pending(reason=reason)

    async def note_external_bot_speak(
        self,
        channel_id: int | str,
        *,
        sent_at: float,
        target_author_id: str = "",
        message_id: str = "",
    ) -> None:
        if self._worker is None or str(channel_id) != self._worker.channel_id:
            return
        self._worker.runtime.note_external_bot_speak(
            sent_at=sent_at,
            target_author_id=target_author_id,
            message_id=message_id,
        )

    async def trigger_preview(self) -> dict[str, Any]:
        if self._worker is None:
            return {"decision": {"speak": False, "reason": "engine_not_started"}, "snapshot": self.snapshot().to_dict()}
        return await self._worker.preview()

    # ------------------------------------------------------------------
    # 主動排程
    # ------------------------------------------------------------------
    async def _proactive_loop(self) -> None:
        last_fire = 0.0
        while self._started and self.mode == "live":
            try:
                config = await self._config()
                if not bool(getattr(config, "enabled", False)):
                    await asyncio.sleep(60.0)
                    continue
                interval = self._proactive_interval(config)
                now = time.time()
                channel_id = str(getattr(config, "sandbox_channel_id", "0") or "0")
                ready = True
                motivation = "排程時間到"
                mood = ""
                try:
                    from handlers.care.repository import is_within_active_hours
                    from handlers.care.surfer import (
                        _gather_sherry_live_stats,
                        accumulate_mind_state,
                        get_mind_state,
                    )

                    stats = _gather_sherry_live_stats(self.bot, channel_id)
                    active = is_within_active_hours(
                        time.localtime(now).tm_hour,
                        int(getattr(config, "active_hour_start", 9)),
                        int(getattr(config, "active_hour_end", 23)),
                    )
                    accumulate_mind_state(
                        silence_minutes=float(stats.get("silence_minutes", 0.0) or 0.0),
                        pace=str(getattr(config, "mind_pace", "normal") or "normal"),
                        is_active_hours=active,
                    )
                    mind = get_mind_state(
                        int(getattr(config, "active_hour_start", 9)),
                        int(getattr(config, "active_hour_end", 23)),
                    )
                    boredom = float(mind.get("boredom_level", 0.0))
                    social = float(mind.get("social_satiety", 100.0))
                    boredom_th = float(mind.get("boredom_threshold", 80.0))
                    social_th = float(mind.get("social_threshold", 30.0))
                    ready = boredom >= boredom_th or social <= social_th

                    # 長時間完全沒人講話時，即使心智值還沒累積到門檻，
                    # 也讓雪莉自己開一個新話題。仍需通過 decide_proactive
                    # 的窗口、冷卻、活躍時段與額度檢查。
                    idle_seconds = float(getattr(config, "idle_proactive_seconds", 1800))
                    runtime = self._worker.runtime if self._worker is not None else None
                    last_human = float(getattr(runtime, "last_human_at", 0.0) or 0.0)
                    last_bot = float(getattr(runtime, "last_bot_at", 0.0) or 0.0)
                    last_activity = max(last_human, last_bot)
                    idle_ready = (
                        idle_seconds > 0
                        and last_activity > 0
                        and (now - last_activity) >= idle_seconds
                    )
                    ready = ready or idle_ready

                    mood = f"無聊 {boredom:.0f}/100、社交滿足 {social:.0f}/100"
                    motivation = self._describe_mind(
                        boredom=boredom,
                        boredom_th=boredom_th,
                        social=social,
                        social_th=social_th,
                        idle_ready=idle_ready,
                        silence_minutes=max(0.0, now - last_activity) / 60.0,
                    )
                except Exception as exc:
                    logger.debug("[CyberV2] 心智值讀取失敗，沿用時間排程: %s", exc)
                    ready = True

                if self._worker is not None:
                    next_allowed = max(last_fire + interval, self._worker.autonomy_next_at)
                else:
                    next_allowed = last_fire + interval
                if ready and now >= next_allowed and self._worker is not None:
                    web_topic = await self._fetch_web_topic()
                    await self._worker.submit_proactive(
                        reason="mind_pulse",
                        web_topic=web_topic,
                        motivation=motivation,
                        mood=mood,
                    )
                    last_fire = now
                await asyncio.sleep(60.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[CyberV2] 主動排程異常: %s", exc)
                await asyncio.sleep(60.0)

    def _proactive_interval(self, config: Any) -> float:
        pace = str(getattr(config, "mind_pace", "normal") or "normal").lower()
        base = {"fast": 12 * 60, "normal": 30 * 60, "chill": 75 * 60}.get(pace, 30 * 60)
        return float(max(300, base))

    @staticmethod
    def _describe_mind(
        *,
        boredom: float,
        boredom_th: float,
        social: float,
        social_th: float,
        idle_ready: bool,
        silence_minutes: float,
    ) -> str:
        """把心智數值轉成雪莉看得懂的一句動機。"""
        if boredom >= boredom_th:
            return f"無聊累積 {boredom:.0f}/{boredom_th:.0f}"
        if social <= social_th:
            return f"想聊天（社交滿足 {social:.0f}）"
        if idle_ready:
            return f"頻道安靜 {int(max(0.0, silence_minutes))} 分鐘"
        return "排程時間到"

    async def _fetch_web_topic(self) -> dict[str, Any] | None:
        """主動開話題前，先去網路抓一則雪莉還沒看過的情報。

        舊的 surf_once 排程在 live 模式被停用後，「自己上網找話題」就不見了。
        這裡只取回候選文章當素材，實際發送仍由 Cyber worker 單一出口處理，
        不會另外開第二條發送路徑。抓不到就回 None，主動開話題照常進行。
        """
        surfer = self.surfer
        if surfer is None or self.repository is None:
            return None
        try:
            base = await asyncio.to_thread(self.repository.get_surfer_config)
            if not bool(getattr(base, "enabled", False)):
                return None
            candidates = await asyncio.wait_for(surfer.fetch_candidates(), timeout=20)
        except (asyncio.TimeoutError, TimeoutError):
            logger.debug("[CyberV2] 上網找話題逾時，改用一般主動開話題")
            return None
        except Exception as exc:
            logger.debug("[CyberV2] 上網找話題失敗: %s", exc)
            return None
        if not candidates:
            return None
        candidate = random.choice(list(candidates)[:4])
        return {
            "title": str(getattr(candidate, "title", "") or "").strip(),
            "summary": str(getattr(candidate, "summary", "") or "").strip(),
            "source": str(getattr(candidate, "source", "") or "").strip(),
            "url": str(getattr(candidate, "url", "") or "").strip(),
        }

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------
    def snapshot(self) -> RoomSnapshot:
        if self._worker is not None:
            return self._worker.runtime.snapshot()
        return RoomSnapshot(channel_id="0", phase="idle", last_reason=self.mode)

    def status_payload(self) -> dict[str, Any]:
        snapshot = self.snapshot().to_dict()
        model_value = ""
        model_error = ""
        try:
            model_value = resolve_primary_model()
        except Exception as exc:
            model_error = str(exc)
        ui = None
        try:
            from .ui_config import get_cyber_ui_config

            ui = get_cyber_ui_config()
        except Exception:
            pass
        autonomy: dict[str, Any] = {
            "enabled": bool(getattr(ui, "autonomy_enabled", True)),
            "hourly_limit": int(getattr(ui, "autonomy_hourly", 2) or 0),
            "daily_limit": int(getattr(ui, "autonomy_daily", 12) or 0),
            "wait_max_minutes": int(getattr(ui, "autonomy_wait_max_minutes", 90) or 90),
            "act": "",
            "motivation": "",
            "mood": "",
            "thought": "",
            "decided_at": 0.0,
            "next_at": 0.0,
            "today_speaks": 0,
            "wait_minutes": 0,
            "reason": "",
        }
        if self._worker is not None:
            last = self._worker.last_autonomy or {}
            autonomy.update({
                "act": str(last.get("act", "") or ""),
                "motivation": str(last.get("motivation", "") or ""),
                "mood": str(last.get("mood", "") or ""),
                "thought": str(last.get("thought", "") or ""),
                "decided_at": float(last.get("at", 0.0) or 0.0),
                "next_at": self._worker.autonomy_next_at,
                "today_speaks": self._worker.proactive_budget.count_today(time.time()),
                "wait_minutes": int(last.get("wait_minutes", 0) or 0),
                "reason": str(last.get("reason", "") or ""),
            })
        return {
            "enabled": self.mode != "off",
            "version": "v2",
            "mode": self.mode,
            "gateway_enabled": bool(getattr(ui, "gateway_enabled", False)),
            "owns_mentions": bool(getattr(ui, "owns_mentions", False)),
            "mention_scope": str(getattr(ui, "mention_scope", "sandbox")),
            "autonomous_enabled": bool(getattr(ui, "autonomous_enabled", True)),
            "tool_policy": ",".join(getattr(ui, "allowed_tools", []) or []),
            "debounce_seconds": int(getattr(ui, "debounce_seconds", 10)),
            "reply_scoring_enabled": bool(getattr(ui, "reply_scoring_enabled", True)),
            "ambient_score_threshold": int(getattr(ui, "ambient_score_threshold", 6)),
            "idle_proactive_seconds": int(getattr(ui, "idle_proactive_seconds", 1800)),
            "reasoning_effort": str(getattr(ui, "reasoning_effort", "low")),
            "autonomy": autonomy,
            "primary_model": {
                "value": model_value,
                "source": "webui",
                "healthy": bool(model_value),
                "error": model_error,
            },
            **snapshot,
        }

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        count = max(1, min(80, int(limit)))
        return list(self._events)[-count:]

    def recent_autonomy(self, limit: int = 20) -> list[dict[str, Any]]:
        if self._worker is None:
            return []
        return self._worker.recent_autonomy(limit)

    def _event_sink(self, payload: dict[str, Any]) -> None:
        item = dict(payload)
        item.setdefault("ts", time.time())
        self._events.append(item)

    async def _config(self) -> Any:
        if self.repository is None:
            return None
        base = await asyncio.to_thread(self.repository.get_surfer_config)
        try:
            from dataclasses import replace

            from .ui_config import get_cyber_ui_config

            ui = get_cyber_ui_config()
            cfg = replace(
                base,
                enabled=bool(ui.engine_mode != "off"),
                active_chat_window_seconds=int(ui.window_seconds),
                interject_cooldown_seconds=int(ui.contact_cooldown),
                proactive_cooldown_seconds=max(int(ui.contact_cooldown), 60),
                max_replies_per_hour=int(ui.contact_hourly),
                max_replies_per_day=int(ui.contact_daily),
                active_hour_start=int(ui.active_hour_start),
                active_hour_end=int(ui.active_hour_end),
                mind_pace=str(ui.mind_pace),
                chat_reply_enabled=bool(ui.autonomous_enabled),
            )
            object.__setattr__(cfg, "cyber_ui", ui)
            object.__setattr__(cfg, "followup_window_seconds", int(ui.followup_window_seconds))
            object.__setattr__(cfg, "reply_scoring_enabled", bool(ui.reply_scoring_enabled))
            object.__setattr__(cfg, "ambient_score_threshold", int(ui.ambient_score_threshold))
            object.__setattr__(cfg, "idle_proactive_seconds", int(ui.idle_proactive_seconds))
            object.__setattr__(cfg, "reasoning_effort", str(ui.reasoning_effort))
            return cfg
        except Exception:
            return base


_ENGINE: CyberEngine | None = None


def get_cyber_engine() -> CyberEngine | None:
    return _ENGINE


def set_cyber_engine(engine: CyberEngine | None) -> None:
    global _ENGINE
    _ENGINE = engine
