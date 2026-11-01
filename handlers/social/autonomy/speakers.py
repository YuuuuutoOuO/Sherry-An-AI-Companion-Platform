"""handlers/social/autonomy/speakers.py
====================================
把「一段文字」送到 Discord 的 adapter。

只有兩種 speaker：
- ``BotSpeaker``：用 Bot 身分發言（正式群唯一合法出口）。
- ``WebhookSpeaker``：用 Webhook 偽裝成群友（沙盒限定）。

**審查規則**：``create_webhook`` / ``webhook.send`` 只允許出現在本檔案。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import settings

from .contracts import Persona, SpeechIntent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    """一次發送的結果。"""

    ok: bool
    speaker: str
    message_ids: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "speaker": self.speaker,
            "message_ids": list(self.message_ids),
            "error": self.error,
        }


class BotSpeaker:
    """以 Bot 身分送出氣泡訊息。"""

    name = "bot"

    def __init__(self, channel: Any, *, prefer_reply: bool = True) -> None:
        self.channel = channel
        self.prefer_reply = prefer_reply

    async def send(self, intent: SpeechIntent) -> DeliveryResult:
        from handlers.care.surfer import clean_cyber_bubble, send_cyber_bubbles

        bubbles = list(intent.bubbles) or [intent.text]
        cleaned = [b for b in (clean_cyber_bubble(str(x)) for x in bubbles) if b]
        if not cleaned:
            return DeliveryResult(ok=False, speaker=self.name, error="empty_text")
        reply_to = intent.reply_to if self.prefer_reply else None
        try:
            sent = await send_cyber_bubbles(
                self.channel,
                cleaned,
                reply_to=reply_to,
                typing_delay=0.6,
            )
        except Exception as exc:  # pragma: no cover - 防禦性
            logger.warning("[Speaker] BotSpeaker 發送失敗: %s", exc)
            return DeliveryResult(ok=False, speaker=self.name, error=str(exc))
        ids = tuple(str(getattr(m, "id", "")) for m in sent if getattr(m, "id", None))
        return DeliveryResult(ok=bool(sent), speaker=self.name, message_ids=ids)


class WebhookSpeaker:
    """以 Webhook 送出 NPC 訊息。僅限沙盒頻道。"""

    name = "webhook"

    def __init__(self, channel: Any, persona: Persona) -> None:
        self.channel = channel
        self.persona = persona
        self._webhook: Any = None

    async def ensure_webhook(self) -> Any:
        """取得或建立此 persona 專屬 webhook。

        只存 webhook_id，不存 token；執行期用 ``channel.webhooks()`` 取回。
        """
        if self._webhook is not None:
            return self._webhook

        webhooks = await self.channel.webhooks()
        target = None
        for hook in webhooks:
            if self.persona.webhook_id and str(getattr(hook, "id", "")) == str(self.persona.webhook_id):
                target = hook
                break
        if target is None:
            name = f"sherry-npc-{self.persona.key}"[:80]
            for hook in webhooks:
                if getattr(hook, "name", "") == name:
                    target = hook
                    break

        avatar_bytes = self._load_avatar()
        if target is None:
            kwargs: dict[str, Any] = {"name": f"sherry-npc-{self.persona.key}"[:80]}
            if avatar_bytes:
                kwargs["avatar"] = avatar_bytes
            target = await self.channel.create_webhook(**kwargs)
        elif avatar_bytes:
            try:
                await target.edit(avatar=avatar_bytes, name=f"sherry-npc-{self.persona.key}"[:80])
            except Exception as exc:
                logger.debug("[Speaker] Webhook 頭像更新略過: %s", exc)

        self._webhook = target
        return target

    def _load_avatar(self) -> bytes | None:
        path = (self.persona.avatar_path or "").strip()
        if not path:
            return None
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(settings.BASE_DIR) / path
        try:
            if not candidate.is_file():
                return None
            if candidate.stat().st_size > int(getattr(settings, "SOCIAL_WEBHOOK_AVATAR_MAX_BYTES", 262144)):
                logger.warning("[Speaker] 頭像超過大小上限，忽略: %s", candidate)
                return None
            return candidate.read_bytes()
        except Exception as exc:
            logger.debug("[Speaker] 讀取頭像失敗: %s", exc)
            return None

    async def send(self, intent: SpeechIntent) -> DeliveryResult:
        try:
            webhook = await self.ensure_webhook()
        except Exception as exc:
            logger.warning("[Speaker] 取得 Webhook 失敗: %s", exc)
            return DeliveryResult(ok=False, speaker=self.name, error=str(exc))

        bubbles = list(intent.bubbles) or [intent.text]
        ids: list[str] = []
        for raw in bubbles:
            content = str(raw or "").strip()
            if not content:
                continue
            try:
                message = await webhook.send(
                    content=content,
                    username=self.persona.display_name or self.persona.key,
                    wait=True,
                    allowed_mentions=_no_mentions(),
                )
            except Exception as exc:
                logger.warning("[Speaker] Webhook 發送失敗: %s", exc)
                return DeliveryResult(
                    ok=bool(ids), speaker=self.name, message_ids=tuple(ids), error=str(exc)
                )
            if message is not None and getattr(message, "id", None):
                ids.append(str(message.id))
        return DeliveryResult(ok=bool(ids), speaker=self.name, message_ids=tuple(ids))


def _no_mentions() -> Any:
    """盡量使用 discord.AllowedMentions(parse=[])；匯入失敗時退回空 dict。"""
    try:
        import discord

        return discord.AllowedMentions(parse=[], everyone=False, roles=False, users=False)
    except Exception:  # pragma: no cover
        return {}
