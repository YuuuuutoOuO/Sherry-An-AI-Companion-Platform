from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import discord

import settings
from handlers.interaction.contracts import (
    AUTONOMOUS_POLICY,
    FULL_MENTION_POLICY,
    ToolPolicy,
    TurnMode,
    TurnRequest,
)

logger = logging.getLogger(__name__)


class TurnGateway:
    """單一入口：把 Discord 訊息分類成 Passive / Autonomous lane。

    Passive lane 目前仍委派給既有的 `route_and_execute_tag()`，以保留完整工具；
    Autonomous lane 交由 Cyber worker。兩條 lane 共用 Cyber 的 ChannelRuntime、
    MemoryContextService 與 BotBrain/PRIMARY_MODEL。
    """

    def __init__(self, bot: Any, *, repository: Any = None) -> None:
        self.bot = bot
        self.repository = repository
        self._last_config: Any = None
        self._last_config_at = 0.0
        self._name_tokens = tuple(
            token.lower()
            for token in (
                "雪莉",
                "sherry",
                str(getattr(settings, "BOT_NAME", "") or ""),
            )
            if token
        )

    async def _config(self) -> Any:
        if self.repository is None:
            try:
                from handlers.care.repository import get_care_repository

                self.repository = get_care_repository()
            except Exception:
                return None
        now = time.time()
        if self._last_config is not None and (now - self._last_config_at) < 5.0:
            return self._last_config
        try:
            self._last_config = await asyncio.to_thread(self.repository.get_surfer_config)
            self._last_config_at = now
        except Exception as exc:
            logger.debug("[TurnGateway] 讀取設定失敗: %s", exc)
            return None
        return self._last_config

    async def handle_message(self, message: discord.Message) -> bool:
        """回傳 True 表示 gateway 已接管，呼叫端不要再走舊 router。"""
        if message.author.bot:
            return False
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return False

        try:
            from handlers.care.cyber.ui_config import get_cyber_ui_config

            ui_config = get_cyber_ui_config()
        except Exception:
            ui_config = None
        if ui_config is None or not bool(ui_config.gateway_enabled):
            return False

        from handlers.care.cyber import event_from_message, get_cyber_engine

        engine = get_cyber_engine()
        if engine is None or engine.mode == "off":
            return False

        config = await self._config()
        if config is None:
            return False
        if not bool(getattr(config, "enabled", False)) and engine.mode not in {"shadow", "live"}:
            return False

        channel_id = str(message.channel.id)
        sandbox_id = str(getattr(config, "sandbox_channel_id", "0") or "0").strip()
        is_sandbox = sandbox_id not in {"", "0"} and channel_id == sandbox_id

        mentioned = bool(self.bot.user and self.bot.user in message.mentions)
        event = event_from_message(message, self.bot.user, is_mentioned=mentioned)
        name_hit = bool(getattr(settings, "INTERACTION_NAME_MENTION_ENABLED", True)) and self._name_hit(event.content)

        if event.is_mentioned:
            mode = TurnMode.MENTION
        elif event.is_reply_to_bot:
            mode = TurnMode.REPLY
        elif is_sandbox:
            # 沙盒內「只叫名字、沒有真的 Tag」的一般訊息一律走 v2 ambient：
            # 1. 可以和前後句一起進 10 秒 debounce，不會一問兩答；
            # 2. 由 v2 輸出管線發送，不會被舊 router 加上 🔄📌🗑️ 互動圖示。
            # 真正的 @Tag／引用回覆仍走被動 lane，保留完整工具。
            mode = TurnMode.AMBIENT
        elif name_hit:
            mode = TurnMode.MENTION
        else:
            return False

        must_reply = mode in (TurnMode.MENTION, TurnMode.REPLY)
        if must_reply:
            scope = str(getattr(ui_config, "mention_scope", "sandbox") or "sandbox").lower()
            if scope == "sandbox" and not is_sandbox:
                return False

            # 這一輪由被動 lane／既有 router 回答，先取消同頻道還沒生成的
            # ambient debounce，避免「前一顆 10 秒計時器」稍後又回一次。
            cancel_pending = getattr(engine, "cancel_pending", None)
            if cancel_pending is not None:
                try:
                    await cancel_pending(channel_id, reason="mention_lane")
                except Exception as exc:
                    logger.debug("[TurnGateway] 取消 ambient 批次失敗: %s", exc)

            if not bool(getattr(ui_config, "owns_mentions", False)):
                # 預設仍由 SocialChat 既有的 mention router 處理；
                # 設為 True 才由 gateway 接管並在重新導向 router 後同步狀態。
                return False

            # Passive lane：保留既有完整工具與 DispatchAgent，不另開 LLM 路徑。
            from handlers.social.router import route_and_execute_tag

            await route_and_execute_tag(self.bot, message)
            await self.note_external_bot_spoke(
                channel_id,
                sent_at=time.time(),
                target_author_id=event.author_id,
            )
            return True

        priority = int(getattr(settings, "PRIORITY_INTERJECT", 2))
        allowed_tools = frozenset(
            str(item).strip().upper()
            for item in (getattr(ui_config, "allowed_tools", None) or [])
            if str(item).strip()
        )
        autonomous_policy = ToolPolicy(
            backend="readonly",
            allowed=allowed_tools or AUTONOMOUS_POLICY.allowed,
            max_calls=max(1, min(3, int(getattr(ui_config, "tool_max_rounds", 2) or 2))),
            allow_direct_ui=False,
        )
        request = TurnRequest(
            channel_id=channel_id,
            mode=mode,
            events=(event,),
            guild_id=event.guild_id,
            target_author_id=event.author_id,
            target_author_name=event.author_name,
            message=message,
            reply_to=message,
            priority=priority,
            must_reply=False,
            tool_policy=autonomous_policy,
            source="gateway",
        )
        go = getattr(engine, "submit_turn", None)
        if go is None:
            logger.warning("[TurnGateway] CyberEngine 尚未支援 submit_turn")
            return False
        accepted = await go(request)
        return bool(accepted)

    async def note_external_bot_spoke(
        self,
        channel_id: int | str,
        *,
        sent_at: float | None = None,
        target_author_id: int | str = "",
        message_id: str = "",
    ) -> None:
        from handlers.care.cyber import get_cyber_engine

        engine = get_cyber_engine()
        if engine is None:
            return
        note = getattr(engine, "note_external_bot_speak", None)
        if note is None:
            return
        await note(
            channel_id,
            sent_at=time.time() if sent_at is None else float(sent_at),
            target_author_id=str(target_author_id or ""),
            message_id=message_id,
        )

    def _name_hit(self, content: str) -> bool:
        lowered = str(content or "").lower()
        return any(token in lowered for token in self._name_tokens)


_GATEWAY: TurnGateway | None = None


def get_turn_gateway() -> TurnGateway | None:
    return _GATEWAY


def set_turn_gateway(gateway: TurnGateway | None) -> None:
    global _GATEWAY
    _GATEWAY = gateway
