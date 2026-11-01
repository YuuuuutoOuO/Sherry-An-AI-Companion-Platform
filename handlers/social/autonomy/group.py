"""handlers/social/autonomy/group.py
=================================
沙盒群像的最小可行 runner（雪莉 + 1~3 個 Webhook NPC）。

流程：
1. 人類在 ``SANDBOX_GROUP`` 頻道發言。
2. 先取消所有待送 AI turn（人類優先），更新 room，確保 scene。
3. 記一筆 human turn。
4. 依規則挑一個 NPC，延遲 2~8 秒後回覆。
5. 若規則允許，雪莉再補一句（最多一輪 2 個 AI）。
6. 所有成功／失敗都寫進 social_decisions。

正式群永遠不會走到這裡：``ChannelModeResolver`` 會先降級成 SOLO。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import settings as default_settings

from .actions.prompts import (
    build_npc_prompt,
    build_primary_group_prompt,
    extract_reply,
    format_roster_block,
    format_scene_turns,
    parse_json_reply,
)
from .contracts import ChannelMode, DecisionAction, Persona, SocialEvent, TriggerType
from .director import SocialDirector
from .generation import generate_text
from .personas import PersonaRoster
from .router import SpeakerRouter, SoloModeViolation
from .scene import SceneManager
from .turns import TurnScheduler

logger = logging.getLogger(__name__)


class SandboxGroupRunner:
    """把「人類訊息」變成「一輪有節制的群像對話」。"""

    def __init__(
        self,
        bot: Any,
        repository: Any,
        *,
        director: SocialDirector | None = None,
        roster: PersonaRoster | None = None,
        scenes: SceneManager | None = None,
        turns: TurnScheduler | None = None,
        router: SpeakerRouter | None = None,
        settings_module: Any | None = None,
        broadcaster: Any | None = None,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.settings = settings_module or default_settings
        self.director = director or SocialDirector(repository, self.settings)
        self.roster = roster or self.director.roster
        self.scenes = scenes or SceneManager(repository, self.settings)
        self.turns = turns or TurnScheduler(repository)
        self.router = router or SpeakerRouter(self.settings)
        self.broadcaster = broadcaster
        self._seen_messages: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 對外入口
    # ------------------------------------------------------------------
    async def handle_human_message(self, message: Any, config: Any) -> dict[str, Any]:
        """處理沙盒頻道的人類訊息；非沙盒頻道直接回 solo。"""
        channel = getattr(message, "channel", None)
        if channel is None:
            return {"status": "ignored", "reason": "no_channel"}
        cid = str(getattr(channel, "id", ""))
        guild = getattr(message, "guild", None)
        guild_id = str(getattr(guild, "id", "") or "")
        author = getattr(message, "author", None)
        if author is None or bool(getattr(author, "bot", False)):
            return {"status": "ignored", "reason": "author_is_bot"}

        mode = self.director.mode_for(cid, guild_id=guild_id, config=config)
        if not mode.is_group:
            return {"status": "solo", "reason": mode.reason}

        message_id = str(getattr(message, "id", "") or "")
        if message_id and self._is_duplicate(message_id):
            return {"status": "ignored", "reason": "duplicate"}

        event = SocialEvent(
            kind="sandbox_human",
            channel_id=cid,
            guild_id=guild_id,
            trigger_type=TriggerType.HUMAN_MESSAGE,
            message_id=message_id,
            content=str(getattr(message, "content", "") or ""),
            author_id=str(getattr(author, "id", "") or ""),
            author_name=str(getattr(author, "display_name", "") or getattr(author, "name", "") or "群友"),
            is_human=True,
            created_at=time.time(),
        )

        # 人類優先：作廢所有待送 AI turn，再更新房間與 scene。
        self.turns.cancel(cid)
        self.director.observe_human(event)

        scene = self.scenes.ensure_scene(
            cid,
            guild_id=guild_id,
            topic=str(getattr(channel, "name", "") or ""),
            now=event.created_at,
        )
        event = SocialEvent(
            kind=event.kind,
            channel_id=event.channel_id,
            guild_id=event.guild_id,
            trigger_type=event.trigger_type,
            message_id=event.message_id,
            content=event.content,
            author_id=event.author_id,
            author_name=event.author_name,
            is_human=True,
            scene_id=int(scene.scene_id),
            created_at=event.created_at,
        )
        self.scenes.record_human(
            scene,
            content=event.content,
            message_id=message_id,
            author_name=event.author_name,
            now=event.created_at,
        )

        npcs = [p for p in self.roster.group_personas(cid) if p.enabled]
        if not npcs:
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                reasons=["no_enabled_npc_persona"],
            )
            return {"status": "solo", "reason": "no_enabled_npc_persona"}

        last_speaker = self.scenes.inspect(int(scene.scene_id)).last_speaker
        candidates = [p for p in npcs if p.key != last_speaker] or npcs
        persona = candidates[0]

        decision = self.director.decide(
            event,
            config,
            room=self.repository.get_room(cid),
            ai_consecutive=self.scenes.consecutive_ai(int(scene.scene_id)),
            persona_key=persona.key,
        )
        if not decision.allowed:
            return {"status": "silent", "reasons": list(decision.gate.reasons)}

        generation = self.turns.generation(cid)
        self.turns.schedule(
            cid,
            self._initial_delay(),
            lambda: self._run_round(
                message=message,
                event=event,
                config=config,
                scene_id=int(scene.scene_id),
                persona=persona,
                generation=generation,
            ),
            generation=generation,
        )
        return {
            "status": "scheduled",
            "scene_id": int(scene.scene_id),
            "persona_key": persona.key,
            "mode": mode.mode.value,
        }

    def kill(self, channel_id: int | str) -> dict[str, Any]:
        """L2 kill switch：降回 SOLO、關 scene、取消所有待送 turn。"""
        result = self.repository.kill_channel_sandbox(channel_id)
        self.turns.cancel(channel_id)
        self.director.record_silent(
            SocialEvent(
                kind="kill_switch",
                channel_id=str(channel_id),
                trigger_type=TriggerType.TIMER,
                is_human=False,
            ),
            mode=ChannelMode.SOLO,
            reasons=["kill_switch"],
        )
        if self.broadcaster is not None:
            try:
                asyncio.create_task(self.broadcaster.broadcast("sandbox_killed", {"channel_id": str(channel_id)}))
            except Exception:
                pass
        return result

    def snapshot(self, channel_id: int | str, *, config: Any | None = None) -> dict[str, Any]:
        """給 WebUI 的沙盒狀態快照。"""
        cid = str(channel_id)
        mode = self.director.mode_for(cid, config=config)
        scene = self.repository.get_open_scene(cid)
        personas = self.roster.list(cid, include_disabled=True)
        return {
            "mode": mode.to_dict(),
            "scene": scene.to_dict() if scene is not None else None,
            "personas": [p.to_dict() for p in personas],
            "room": self.repository.get_room(cid).to_dict(),
            "mind": self.repository.get_social_mind(cid).to_dict(),
            "pending_turns": 1 if cid in getattr(self.turns, "_pending", {}) else 0,
        }

    # ------------------------------------------------------------------
    # 一輪群像
    # ------------------------------------------------------------------
    async def _run_round(
        self,
        *,
        message: Any,
        event: SocialEvent,
        config: Any,
        scene_id: int,
        persona: Persona,
        generation: int,
    ) -> None:
        cid = event.channel_id
        if self.turns.is_stale(cid, generation):
            return
        scene = self.repository.get_scene(scene_id)
        if scene is None or scene.status != "open":
            return

        npc_spoke = await self._run_npc_turn(
            message=message,
            event=event,
            config=config,
            scene=scene,
            persona=persona,
            generation=generation,
        )
        if not npc_spoke or self.turns.is_stale(cid, generation):
            return

        delay = self.turns.rng.uniform(2.5, 6.0)
        await asyncio.sleep(delay)
        if self.turns.is_stale(cid, generation):
            return
        scene = self.repository.get_scene(scene_id)
        if scene is None or scene.status != "open":
            return
        await self._run_primary_turn(
            message=message,
            event=event,
            config=config,
            scene=scene,
            generation=generation,
        )

    async def _run_npc_turn(
        self,
        *,
        message: Any,
        event: SocialEvent,
        config: Any,
        scene: Any,
        persona: Persona,
        generation: int,
    ) -> bool:
        cid = event.channel_id
        decision = self.director.decide(
            event,
            config,
            room=self.repository.get_room(cid),
            ai_consecutive=self.scenes.consecutive_ai(int(scene.scene_id)),
            persona_key=persona.key,
        )
        if not decision.allowed:
            return False

        last_ai_text = self._last_ai_text(int(scene.scene_id))
        turn_decision = self.turns.evaluate(
            scene=scene,
            persona_key=persona.key,
            role="npc",
            last_ai_text="",
            now=time.time(),
        )
        if not turn_decision.allowed:
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=list(turn_decision.reasons),
                gate=decision.gate,
            )
            return False

        script, context_block = await self._context(message, event)
        roster_block = format_roster_block(self.roster.list(cid, include_disabled=False), current_key=persona.key)
        memory_block = await self._memory_block(event)
        head_persona = await self._persona_text(persona, script, event, config)
        system_prompt = build_npc_prompt(
            persona,
            head_persona=head_persona,
            script=script,
            context_block=context_block,
            roster_block=roster_block,
            memory_block=memory_block,
        )

        raw = await generate_text(
            self.bot,
            system_prompt=system_prompt,
            user_prompt=script or event.content,
            model=str(getattr(self.settings, "LIGHT_MODEL", "") or getattr(self.settings, "PRIMARY_MODEL", "")),
            max_tokens=180,
            timeout=25.0,
            task_id=f"social-npc-{persona.key}-{int(time.time())}",
        )
        thought, reply = extract_reply(parse_json_reply(raw), keys=("reply", "post_text"))
        if not reply:
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=["generation_failed"],
                gate=decision.gate,
            )
            return False

        if self.turns.is_too_similar(reply, last_ai_text):
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=["duplicate_opening"],
                gate=decision.gate,
            )
            return False

        try:
            delivery = await self.router.send(
                channel=message.channel,
                persona=persona,
                mode=ChannelMode.SANDBOX_GROUP,
                text=reply,
                reply_to=None,
            )
        except SoloModeViolation as exc:
            logger.warning("[SandboxGroup] 隔離規則阻擋: %s", exc)
            return False
        except Exception as exc:
            logger.warning("[SandboxGroup] NPC 發送失敗: %s", exc)
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=[f"send_failed:{type(exc).__name__}"],
                gate=decision.gate,
            )
            return False

        if not delivery.ok:
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=[f"send_failed:{delivery.error or 'unknown'}"],
                gate=decision.gate,
            )
            return False

        decision_id = self.director.record_speak(
            event,
            mode=ChannelMode.SANDBOX_GROUP,
            action=DecisionAction.SPEAK,
            persona_key=persona.key,
            score=decision.gate.score,
            gate=decision.gate,
            model=str(getattr(self.settings, "LIGHT_MODEL", "")),
            tokens=max(1, len(reply) // 2),
            scene_id=int(scene.scene_id),
            text=reply,
        )
        self.scenes.record_ai(
            scene,
            role="npc",
            persona_key=persona.key,
            content=reply,
            message_id=delivery.message_ids[0] if delivery.message_ids else "",
            decision_id=decision_id,
        )
        self.director.observe_ai(event, role="npc", persona_key=persona.key)
        self.director.apply_cooldown(cid, config)
        await self._broadcast_turn(scene, persona, reply, thought, role="npc", decision_id=decision_id)
        return True

    async def _run_primary_turn(
        self,
        *,
        message: Any,
        event: SocialEvent,
        config: Any,
        scene: Any,
        generation: int,
    ) -> bool:
        cid = event.channel_id
        persona = self.director.primary_persona(cid, event.guild_id)
        decision = self.director.decide(
            event,
            config,
            room=self.repository.get_room(cid),
            ai_consecutive=self.scenes.consecutive_ai(int(scene.scene_id)),
            persona_key=persona.key,
        )
        if not decision.allowed:
            return False

        turn_decision = self.turns.evaluate(
            scene=scene,
            persona_key=persona.key,
            role="bot",
            last_ai_text="",
            now=time.time(),
        )
        if not turn_decision.allowed:
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=list(turn_decision.reasons),
                gate=decision.gate,
            )
            return False

        script, context_block = await self._context(message, event)
        roster_block = format_roster_block(self.roster.list(cid, include_disabled=False), current_key=persona.key)
        memory_block = await self._memory_block(event)
        head_persona = await self._persona_text(persona, script, event, config, primary=True)
        system_prompt = build_primary_group_prompt(
            persona,
            head_persona=head_persona,
            script=script,
            context_block=context_block,
            roster_block=roster_block,
            memory_block=memory_block,
        )

        raw = await generate_text(
            self.bot,
            system_prompt=system_prompt,
            user_prompt=script or event.content,
            max_tokens=200,
            timeout=25.0,
            task_id=f"social-primary-group-{int(time.time())}",
        )
        thought, reply = extract_reply(parse_json_reply(raw), keys=("reply", "post_text"))

        last_ai_text = self._last_ai_text(int(scene.scene_id))
        if not reply or self.turns.is_too_similar(reply, last_ai_text):
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=["generation_failed"] if not reply else ["duplicate_opening"],
                gate=decision.gate,
            )
            return False

        try:
            delivery = await self.router.send(
                channel=message.channel,
                persona=persona,
                mode=ChannelMode.SANDBOX_GROUP,
                text=reply,
                reply_to=None,
            )
        except SoloModeViolation as exc:
            logger.warning("[SandboxGroup] 隔離規則阻擋（primary）: %s", exc)
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=["solo_mode_violation"],
                gate=decision.gate,
            )
            return False
        except Exception as exc:
            logger.warning("[SandboxGroup] primary 發送失敗: %s", exc)
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=[f"send_failed:{type(exc).__name__}"],
                gate=decision.gate,
            )
            return False
        if not delivery.ok:
            self.director.record_silent(
                event,
                mode=ChannelMode.SANDBOX_GROUP,
                persona_key=persona.key,
                reasons=[f"send_failed:{delivery.error or 'unknown'}"],
                gate=decision.gate,
            )
            return False

        decision_id = self.director.record_speak(
            event,
            mode=ChannelMode.SANDBOX_GROUP,
            action=DecisionAction.SPEAK,
            persona_key=persona.key,
            score=decision.gate.score,
            gate=decision.gate,
            model=str(getattr(self.settings, "PRIMARY_MODEL", "")),
            tokens=max(1, len(reply) // 2),
            scene_id=int(scene.scene_id),
            text=reply,
        )
        self.scenes.record_ai(
            scene,
            role="bot",
            persona_key=persona.key,
            content=reply,
            message_id=delivery.message_ids[0] if delivery.message_ids else "",
            decision_id=decision_id,
        )
        self.director.observe_ai(event, role="bot", persona_key=persona.key)
        self.director.apply_cooldown(cid, config)
        await self._broadcast_turn(scene, persona, reply, thought, role="bot", decision_id=decision_id)
        return True

    # ------------------------------------------------------------------
    # 輔助
    # ------------------------------------------------------------------
    def _initial_delay(self) -> float:
        low = float(getattr(self.settings, "SOCIAL_NPC_DELAY_MIN", 2.0))
        high = float(getattr(self.settings, "SOCIAL_NPC_DELAY_MAX", 8.0))
        return self.turns.rng.uniform(low, max(low, high))

    def _is_duplicate(self, message_id: str) -> bool:
        now = time.time()
        for key in [k for k, ts in self._seen_messages.items() if now - ts > 600]:
            self._seen_messages.pop(key, None)
        if message_id in self._seen_messages:
            return True
        self._seen_messages[message_id] = now
        return False

    async def _context(self, message: Any, event: SocialEvent) -> tuple[str, str]:
        script = ""
        try:
            from handlers.social.utils import fetch_recent_script

            script = await fetch_recent_script(message.channel, 10)
        except Exception as exc:
            logger.debug("[SandboxGroup] 讀取近期對話失敗: %s", exc)
        silence = 0
        room = self.repository.get_room(event.channel_id)
        if room.last_human_at:
            silence = max(0, int((time.time() - room.last_human_at) / 60))
        context_block = (
            f"（距上一則訊息已冷場安靜了 {silence} 分鐘）"
            if silence
            else "（頻道現在正在熱聊）"
        )
        return script, context_block

    async def _memory_block(self, event: SocialEvent) -> str:
        if not event.author_id.isdigit():
            return ""
        try:
            from handlers.social.memory_builder import build_user_memory_block

            return await asyncio.to_thread(
                build_user_memory_block,
                self.bot,
                int(event.author_id),
                event.author_name,
                [],
                event.content,
            )
        except Exception as exc:
            logger.debug("[SandboxGroup] 讀取使用者記憶失敗: %s", exc)
            return ""

    async def _persona_text(self, persona: Persona, script: str, event: SocialEvent, config: Any, *, primary: bool = False) -> str:
        from handlers.social.persona import build_dynamic_persona

        try:
            guild_id = int(event.guild_id) if str(event.guild_id).isdigit() else None
            head, _tail = await asyncio.to_thread(
                build_dynamic_persona,
                script,
                "CHAT",
                int(event.channel_id) if str(event.channel_id).isdigit() else None,
                "",
                event.content,
                self.bot,
                guild_id,
                None if primary else persona.soul_name,
            )
            return head
        except Exception as exc:
            logger.debug("[SandboxGroup] 組裝 persona 失敗: %s", exc)
            return ""

    def _last_ai_text(self, scene_id: int) -> str:
        for turn in reversed(self.repository.list_scene_turns(scene_id, limit=50)):
            if str(getattr(turn, "role", "")) in ("npc", "bot"):
                return str(getattr(turn, "content", ""))
        return ""

    async def _broadcast_turn(
        self,
        scene: Any,
        persona: Persona,
        reply: str,
        thought: str,
        *,
        role: str,
        decision_id: int,
    ) -> None:
        if self.broadcaster is None:
            return
        try:
            await self.broadcaster.broadcast(
                "scene_turn",
                {
                    "scene_id": int(scene.scene_id),
                    "channel_id": str(scene.channel_id),
                    "role": role,
                    "persona_key": persona.key,
                    "display_name": persona.display_name,
                    "content": reply,
                    "thought": thought,
                    "decision_id": decision_id,
                },
            )
        except Exception as exc:
            logger.debug("[SandboxGroup] SSE 廣播失敗: %s", exc)
