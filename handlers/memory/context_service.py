from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryBundle:
    user_facts: str = ""
    relationship: str = ""
    retrieval: str = ""
    self_memory: str = ""
    world_memory: str = ""
    action_memory: str = ""
    interests: str = ""
    sources: tuple[str, ...] = ()
    chars: int = 0

    def render(self, *, max_chars: int = 3600) -> str:
        sections: list[str] = []
        if self.user_facts:
            sections.append(f"<user_facts>\n{self.user_facts.strip()}\n</user_facts>")
        if self.relationship:
            sections.append(f"<relationship>\n{self.relationship.strip()}\n</relationship>")
        if self.retrieval:
            sections.append(f"<retrieval>\n{self.retrieval.strip()}\n</retrieval>")
        if self.self_memory:
            sections.append(f"<self_memory>\n{self.self_memory.strip()}\n</self_memory>")
        if self.world_memory:
            sections.append(f"<world_memory>\n{self.world_memory.strip()}\n</world_memory>")
        if self.action_memory:
            sections.append(f"<action_memory>\n{self.action_memory.strip()}\n</action_memory>")
        if self.interests:
            sections.append(f"<interests>\n{self.interests.strip()}\n</interests>")
        if not sections:
            return ""

        body = "\n".join(sections)
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n…"

        self.chars = len(body)
        return (
            "<memory_context>\n"
            "以下是雪莉的記憶與關係資料，只用來讓語氣與事實一致；"
            "不要逐條背誦、不要說出標籤名稱、不要提到系統。\n"
            f"{body}\n"
            "</memory_context>"
        )


class MemoryContextService:
    @staticmethod
    def build(
        *,
        bot: Any,
        user_id: int | str,
        user_name: str,
        query: str,
        channel_id: int | str,
        guild_id: int | str = "",
        mode: str = "mention",
        max_chars: int = 3600,
    ) -> MemoryBundle:
        mode_value = getattr(mode, "value", mode)
        limits = (
            {"facts": 8, "retrieval": 4, "self": 4, "world": 2, "action": 4, "interests": 5}
            if mode_value in {"mention", "reply"}
            else {"facts": 4, "retrieval": 2, "self": 3, "world": 1, "action": 2, "interests": 3}
        )
        bundle = MemoryBundle()
        sources: list[str] = []
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            uid = 0

        mem_mgr = None
        try:
            mem_mgr = bot.get_cog("MemoryManager") if bot is not None else None
        except Exception:
            mem_mgr = None

        if mem_mgr is not None and uid > 0:
            try:
                facts = mem_mgr.get_user_memory_snippet(
                    uid,
                    max_facts=limits["facts"],
                    query_text=query,
                )
                if isinstance(facts, str) and facts.strip():
                    bundle.user_facts = facts.strip()
                    sources.append("user_facts")
            except Exception as exc:
                logger.debug("[MemoryContext] user facts 失敗: %s", exc)

            try:
                rel = mem_mgr.relations.get_relationship_snippet(uid)
                if isinstance(rel, str) and rel.strip():
                    bundle.relationship = rel.strip()
                    sources.append("relationship")
            except Exception as exc:
                logger.debug("[MemoryContext] relationship 失敗: %s", exc)

        exclude = "\n".join(part for part in (bundle.user_facts, bundle.relationship) if part)
        if uid > 0:
            try:
                from handlers.social.dispatch.prompts import build_retrieved_memory_tail

                retrieval = build_retrieved_memory_tail(
                    query,
                    uid,
                    exclude_text=exclude,
                    limit=limits["retrieval"],
                )
                if retrieval and retrieval.strip():
                    bundle.retrieval = retrieval.strip()
                    sources.append("retrieval")
            except Exception as exc:
                logger.debug("[MemoryContext] retrieval 失敗: %s", exc)

        try:
            from handlers.memory.self_memory import get_self_memory_snippet

            self_text = get_self_memory_snippet(query_text=query, limit=limits["self"])
            if self_text and self_text.strip():
                bundle.self_memory = self_text.strip()
                sources.append("self_memory")
        except Exception as exc:
            logger.debug("[MemoryContext] self memory 失敗: %s", exc)

        try:
            from handlers.memory.world_memory import get_world_memory_snippet

            world = get_world_memory_snippet(
                days=2,
                min_importance=3,
                server_id=str(guild_id or ""),
                limit=limits["world"],
            )
            if world and world.strip():
                bundle.world_memory = world.strip()
                sources.append("world_memory")
        except Exception as exc:
            logger.debug("[MemoryContext] world memory 失敗: %s", exc)

        try:
            from handlers.memory.self_memory import get_action_memory_snippet

            action = get_action_memory_snippet(
                query_text=query,
                channel_id=channel_id,
                limit=limits["action"],
            )
            if action and action.strip():
                bundle.action_memory = action.strip()
                sources.append("action_memory")
        except Exception as exc:
            logger.debug("[MemoryContext] action memory 失敗: %s", exc)

        try:
            from handlers.memory.self_memory import get_active_interests

            interests = get_active_interests(limit=limits["interests"])
            if interests:
                bundle.interests = "、".join(str(item) for item in interests if str(item).strip())
                sources.append("interests")
        except Exception as exc:
            logger.debug("[MemoryContext] interests 失敗: %s", exc)

        bundle.sources = tuple(sources)
        bundle.chars = sum(
            len(part)
            for part in (
                bundle.user_facts,
                bundle.relationship,
                bundle.retrieval,
                bundle.self_memory,
                bundle.world_memory,
                bundle.action_memory,
                bundle.interests,
            )
        )
        return bundle
