"""handlers/social/autonomy/modes.py
=================================
頻道模式解析（SOLO / SANDBOX_GROUP）。

安全預設：**沒有資料列就是 SOLO**。忘記設定不會意外開啟多角色。
沙盒要真正生效，必須同時滿足：
1. ``SOCIAL_SANDBOX_ENABLED=1``（全域）
2. ``social_channel_modes`` 有資料列、``enabled=1``、``mode=SANDBOX_GROUP``
3. 該資料列 ``allow_webhooks=1``
4. persona roster 至少有一個啟用中的 NPC

任一不符就降級 SOLO，並回傳可讀的 reason 供決策帳本使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import settings as default_settings

from .contracts import ChannelMode


@dataclass(frozen=True)
class ModeDecision:
    """頻道模式解析結果（含降級原因）。"""

    channel_id: str
    mode: ChannelMode = ChannelMode.SOLO
    enabled: bool = False
    allow_webhooks: bool = False
    reason: str = "default_solo"
    npc_personas: int = 0

    @property
    def is_group(self) -> bool:
        return self.mode is ChannelMode.SANDBOX_GROUP

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "mode": self.mode.value,
            "enabled": self.enabled,
            "allow_webhooks": self.allow_webhooks,
            "reason": self.reason,
            "npc_personas": self.npc_personas,
        }


class ChannelModeResolver:
    """依 DB 設定與全域開關決定頻道模式。"""

    def __init__(self, repository: Any, settings_module: Any | None = None) -> None:
        self.repository = repository
        self.settings = settings_module or default_settings

    def resolve(
        self,
        channel_id: int | str,
        *,
        guild_id: str | int = "",
        config: Any | None = None,
    ) -> ModeDecision:
        cid = str(channel_id)
        record = self.repository.get_channel_mode(cid)

        if record is None:
            # 相容舊設定：只有在明確開啟相容旗標時，才把 sandbox_channel_id 視為沙盒。
            compat_enabled = bool(
                getattr(self.settings, "SOCIAL_SANDBOX_COMPAT_MODE", False)
            )
            sandbox_id = str(getattr(config, "sandbox_channel_id", "0") or "0")
            if compat_enabled and sandbox_id not in ("", "0") and sandbox_id == cid:
                record = None
                candidate = ChannelMode.SANDBOX_GROUP
                allow_webhooks = True
                reason_prefix = "compat_sandbox_channel"
            else:
                return ModeDecision(channel_id=cid, mode=ChannelMode.SOLO, reason="default_solo")
        else:
            candidate = ChannelMode.parse(record.mode)
            allow_webhooks = bool(record.allow_webhooks)
            reason_prefix = "mode_record"

        if candidate is not ChannelMode.SANDBOX_GROUP:
            return ModeDecision(
                channel_id=cid,
                mode=ChannelMode.SOLO,
                enabled=bool(record.enabled) if record is not None else False,
                allow_webhooks=False,
                reason=f"{reason_prefix}:solo",
            )

        npc_personas = len(
            [
                p
                for p in self.repository.list_personas(cid, include_disabled=False)
                if not p.is_primary
            ]
        )

        if not bool(getattr(self.settings, "SOCIAL_SANDBOX_ENABLED", False)):
            return ModeDecision(
                channel_id=cid,
                mode=ChannelMode.SOLO,
                reason="sandbox_disabled",
                npc_personas=npc_personas,
            )

        if record is not None and not bool(record.enabled):
            return ModeDecision(
                channel_id=cid,
                mode=ChannelMode.SOLO,
                reason="sandbox_channel_disabled",
                npc_personas=npc_personas,
            )

        if not allow_webhooks:
            return ModeDecision(
                channel_id=cid,
                mode=ChannelMode.SOLO,
                reason="webhooks_not_allowed",
                npc_personas=npc_personas,
            )

        if npc_personas <= 0:
            return ModeDecision(
                channel_id=cid,
                mode=ChannelMode.SOLO,
                reason="no_enabled_npc_persona",
                allow_webhooks=allow_webhooks,
                npc_personas=npc_personas,
            )

        return ModeDecision(
            channel_id=cid,
            mode=ChannelMode.SANDBOX_GROUP,
            enabled=True,
            allow_webhooks=True,
            reason=f"{reason_prefix}:sandbox_group",
            npc_personas=npc_personas,
        )
