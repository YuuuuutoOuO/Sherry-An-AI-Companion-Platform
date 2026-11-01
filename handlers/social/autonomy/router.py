"""handlers/social/autonomy/router.py
==================================
SpeakerRouter：正式群隔離的**唯一**執行點。

Invariant：
- 非 ``SANDBOX_GROUP`` 頻道，只能解析出 ``BotSpeaker`` 與 primary persona。
- 任何非 primary persona 在非沙盒頻道都會丟 ``SoloModeViolation``。
- ``WebhookSpeaker`` 只在沙盒分支被建構。

UI 隱藏只是第二道；這一層才是硬阻擋。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import settings as default_settings

from .contracts import ChannelMode, Persona, SpeechIntent
from .speakers import BotSpeaker, DeliveryResult, WebhookSpeaker

logger = logging.getLogger(__name__)


class SoloModeViolation(RuntimeError):
    """正式群不允許第二個 AI 身分。"""


class SpeakerRouter:
    """依頻道模式與 persona 決定實際的發言 adapter。"""

    def __init__(
        self,
        settings_module: Any | None = None,
        *,
        bot_speaker_factory: Callable[[Any], Any] = BotSpeaker,
        webhook_speaker_factory: Callable[[Any, Persona], Any] = WebhookSpeaker,
    ) -> None:
        self.settings = settings_module or default_settings
        self.bot_speaker_factory = bot_speaker_factory
        self.webhook_speaker_factory = webhook_speaker_factory

    def resolve(
        self,
        *,
        channel: Any,
        persona: Persona,
        mode: ChannelMode | str,
    ) -> Any:
        """回傳可用的 speaker；違反隔離規則時丟 SoloModeViolation。"""
        if channel is None:
            raise SoloModeViolation("channel 不可為 None")
        if persona is None:
            raise SoloModeViolation("persona 不可為 None")

        channel_mode = ChannelMode.parse(mode)

        if channel_mode is not ChannelMode.SANDBOX_GROUP:
            if not persona.is_primary:
                raise SoloModeViolation(
                    f"channel={getattr(channel, 'id', '?')} mode=SOLO "
                    f"拒絕非 primary persona={persona.key}"
                )
            return self.bot_speaker_factory(channel)

        if persona.is_primary:
            return self.bot_speaker_factory(channel)

        if not bool(getattr(self.settings, "SOCIAL_SANDBOX_ENABLED", False)):
            raise SoloModeViolation("sandbox 總開關未開啟，拒絕建立 WebhookSpeaker")

        return self.webhook_speaker_factory(channel, persona)

    async def send(
        self,
        *,
        channel: Any,
        persona: Persona,
        mode: ChannelMode | str,
        text: str,
        reply_to: Any = None,
        bubbles: tuple[str, ...] = (),
    ) -> DeliveryResult:
        """解析 speaker 並送出訊息。"""
        speaker = self.resolve(channel=channel, persona=persona, mode=mode)
        intent = SpeechIntent(
            persona=persona,
            channel=channel,
            text=text,
            mode=ChannelMode.parse(mode),
            reply_to=reply_to,
            bubbles=bubbles,
        )
        return await speaker.send(intent)
