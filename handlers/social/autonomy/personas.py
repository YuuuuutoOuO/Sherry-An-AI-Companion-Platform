"""handlers/social/autonomy/personas.py
====================================
Persona roster：雪莉（primary）與沙盒 NPC 的身分管理。

- 雪莉的 soul 由 ``settings.CURRENT_SOUL_NAME`` 決定，不可被沙盒設定覆蓋。
- NPC soul 只允許放在 ``SOUL_DIR/sandbox/`` 底下（防止路徑穿越）。
- 停用 NPC 不刪 webhook，避免歷史訊息 attribution 消失。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import settings as default_settings

from .contracts import Persona

logger = logging.getLogger(__name__)

_KEY_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")
_SOUL_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+\.(md|txt)$")

PRIMARY_PERSONA_KEY = "sherry"


class PersonaValidationError(ValueError):
    """persona 設定不合法。"""


class PersonaRoster:
    """persona 的建立、查詢與安全驗證。"""

    def __init__(self, repository: Any, settings_module: Any | None = None) -> None:
        self.repository = repository
        self.settings = settings_module or default_settings

    @property
    def soul_dir(self) -> Path:
        return Path(getattr(self.settings, "SOUL_DIR", Path(getattr(self.settings, "BASE_DIR", ".")) / "file" / "soul"))

    @property
    def sandbox_soul_dir(self) -> Path:
        return self.soul_dir / "sandbox"

    def primary_persona(self, channel_id: int | str = 0, guild_id: str | int = "") -> Persona:
        """取得（必要時建立）雪莉的 primary persona。"""
        soul_name = str(getattr(self.settings, "CURRENT_SOUL_NAME", "manosaba_sherry_default.md"))
        display_name = str(getattr(self.settings, "BOT_NAME", "雪莉") or "雪莉")
        record = self.repository.get_persona(PRIMARY_PERSONA_KEY)
        if record is None:
            record = self.repository.upsert_persona(
                persona_key=PRIMARY_PERSONA_KEY,
                channel_id=str(channel_id or 0),
                guild_id=str(guild_id or ""),
                soul_name=soul_name,
                display_name=display_name,
                role="primary",
                is_primary=True,
                enabled=True,
            )
        return Persona.from_record(record)

    def ensure_primary(self, channel_id: int | str = 0, guild_id: str | int = "") -> Persona:
        return self.primary_persona(channel_id, guild_id)

    def get(self, persona_key: str) -> Persona | None:
        record = self.repository.get_persona(persona_key)
        return Persona.from_record(record) if record is not None else None

    def list(self, channel_id: int | str, *, include_disabled: bool = True) -> list[Persona]:
        records = self.repository.list_personas(channel_id, include_disabled=include_disabled)
        return [Persona.from_record(r) for r in records]

    def group_personas(self, channel_id: int | str) -> list[Persona]:
        """沙盒可發言的 NPC（不含雪莉）。"""
        return [
            p
            for p in self.list(channel_id, include_disabled=False)
            if not p.is_primary and p.enabled
        ]

    def validate_key(self, persona_key: str) -> str:
        key = str(persona_key or "").strip().lower()
        if key == PRIMARY_PERSONA_KEY:
            raise PersonaValidationError("sherry 是保留的 primary persona key")
        if not _KEY_PATTERN.match(key):
            raise PersonaValidationError("persona_key 只允許小寫英數、底線與減號（1~32 字元）")
        return key

    def validate_soul_name(self, soul_name: str) -> str:
        """只接受 SOUL_DIR/sandbox 底下的 .md/.txt，禁止路徑穿越。"""
        name = str(soul_name or "").strip()
        if "/" in name or "\\" in name:
            raise PersonaValidationError("soul_name 不可包含路徑分隔符號")
        if not _SOUL_PATTERN.match(name):
            raise PersonaValidationError("soul_name 必須是 .md 或 .txt 檔名")
        candidate = (self.sandbox_soul_dir / name).resolve()
        try:
            if not candidate.is_relative_to(self.sandbox_soul_dir.resolve()):
                raise PersonaValidationError("soul_name 必須位於 sandbox 目錄內")
        except AttributeError:  # Python < 3.9
            if str(self.sandbox_soul_dir.resolve()) not in str(candidate):
                raise PersonaValidationError("soul_name 必須位於 sandbox 目錄內")
        return name

    def upsert_npc(
        self,
        *,
        persona_key: str,
        channel_id: int | str,
        soul_name: str,
        display_name: str,
        guild_id: str | int = "",
        avatar_path: str = "",
        enabled: bool = True,
        speech_style: str = "",
    ) -> Persona:
        key = self.validate_key(persona_key)
        soul = self.validate_soul_name(soul_name)
        if not str(display_name or "").strip():
            raise PersonaValidationError("display_name 不可為空")

        limit = int(getattr(self.settings, "SOCIAL_SANDBOX_MAX_NPCS", 3))
        existing = self.repository.get_persona(key)
        if existing is None and len(self.list(channel_id, include_disabled=True)) >= limit + 1:
            raise PersonaValidationError(f"此頻道 NPC 數量已達上限（{limit}）")

        record = self.repository.upsert_persona(
            persona_key=key,
            channel_id=str(channel_id),
            guild_id=str(guild_id or ""),
            soul_name=soul,
            display_name=str(display_name).strip(),
            avatar_path=str(avatar_path or ""),
            role="npc",
            is_primary=False,
            enabled=bool(enabled),
            speech_style=str(speech_style or ""),
        )
        return Persona.from_record(record)

    def disable(self, persona_key: str) -> bool:
        if str(persona_key).strip().lower() == PRIMARY_PERSONA_KEY:
            raise PersonaValidationError("不可停用 primary persona")
        return self.repository.set_persona_enabled(persona_key, False)

    def soul_exists(self, soul_name: str) -> bool:
        try:
            name = self.validate_soul_name(soul_name)
        except PersonaValidationError:
            return False
        return (self.sandbox_soul_dir / name).is_file()

    def ensure_sandbox_dir(self) -> Path:
        self.sandbox_soul_dir.mkdir(parents=True, exist_ok=True)
        return self.sandbox_soul_dir
