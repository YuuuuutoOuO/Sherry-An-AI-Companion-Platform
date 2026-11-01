from __future__ import annotations

from handlers.interaction.contracts import Lane, TurnMode, TurnRequest
from handlers.memory.context_service import MemoryContextService


class FakeMemoryManager:
    def get_user_memory_snippet(self, user_id, max_facts=8, query_text=""):
        return "- 喜歡喝黑咖啡"

    class relations:
        @staticmethod
        def get_relationship_snippet(user_id):
            return "好感度：信任"


class FakeBot:
    def get_cog(self, name):
        return FakeMemoryManager()


def test_lane_mapping() -> None:
    mention = TurnRequest(channel_id="1", mode=TurnMode.MENTION, must_reply=True)
    ambient = TurnRequest(channel_id="1", mode=TurnMode.AMBIENT)
    assert mention.lane == Lane.PASSIVE
    assert ambient.lane == Lane.AUTONOMOUS
    assert mention.must_reply is True


def test_memory_context_includes_user_facts() -> None:
    bundle = MemoryContextService.build(
        bot=FakeBot(),
        user_id=123,
        user_name="A",
        query="今天喝什麼",
        channel_id=1,
        guild_id=1,
        mode="mention",
    )
    text = bundle.render()
    assert "黑咖啡" in text
    assert "<user_facts>" in text
    assert "user_facts" in bundle.sources


def test_memory_context_empty_user_id() -> None:
    bundle = MemoryContextService.build(
        bot=FakeBot(),
        user_id="",
        user_name="",
        query="",
        channel_id=1,
        guild_id=1,
        mode="proactive",
    )
    assert "user_facts" not in bundle.sources
    assert isinstance(bundle.render(), str)


def test_memory_context_budget() -> None:
    bundle = MemoryContextService.build(
        bot=FakeBot(),
        user_id=123,
        user_name="A",
        query="x",
        channel_id=1,
        guild_id=1,
        mode="mention",
    )
    text = bundle.render(max_chars=10)
    assert len(text) < 400
