"""
tests.unit.test_roast_manager
=============================
Unit tests for handlers.roast.manager and handlers.roast.models.
"""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from handlers.roast.manager import RoastManager
from handlers.roast.models import RoastRule


@pytest.fixture
def temp_roast_file(tmp_path: Path) -> Path:
    """Fixture providing a temporary JSON file path for testing."""
    return tmp_path / "data" / "roast_rules.json"


def test_roast_manager_initialization(temp_roast_file: Path) -> None:
    """Test RoastManager initializes default rules on first run."""
    # Arrange & Act
    manager = RoastManager(file_path=temp_roast_file)
    config = manager.get_config()

    # Assert
    assert manager.db_path.exists()
    assert config.enabled is True
    assert config.global_cooldown_seconds == 8
    assert len(config.rules) >= 2
    assert any(r.keyword == "==" for r in config.rules)


def test_roast_manager_add_rule(temp_roast_file: Path) -> None:
    """Test adding a new roast keyword rule."""
    # Arrange
    manager = RoastManager(file_path=temp_roast_file)

    # Act
    new_rule = manager.add_rule(
        keyword="笨蛋",
        reply_guide="你才是笨蛋！",
        probability=75,
        match_mode="contains",
        enabled=True,
    )

    # Assert
    assert new_rule.keyword == "笨蛋"
    assert new_rule.reply_guide == "你才是笨蛋！"
    assert new_rule.probability == 75
    assert new_rule.match_mode == "contains"
    assert new_rule.enabled is True

    # Verify persisted in file
    fresh_manager = RoastManager(file_path=temp_roast_file)
    rules = fresh_manager.get_rules()
    assert any(r.id == new_rule.id for r in rules)


def test_roast_manager_update_rule(temp_roast_file: Path) -> None:
    """Test modifying an existing roast rule."""
    # Arrange
    manager = RoastManager(file_path=temp_roast_file)
    rule = manager.add_rule(keyword="測試", reply_guide="測試回覆", probability=50)

    # Act
    updated = manager.update_rule(
        rule_id=rule.id,
        keyword="測試更新",
        reply_guide="更新後導引",
        probability=90,
        match_mode="exact",
        enabled=False,
    )

    # Assert
    assert updated is not None
    assert updated.keyword == "測試更新"
    assert updated.reply_guide == "更新後導引"
    assert updated.probability == 90
    assert updated.match_mode == "exact"
    assert updated.enabled is False


def test_roast_manager_delete_rule(temp_roast_file: Path) -> None:
    """Test deleting a roast rule."""
    # Arrange
    manager = RoastManager(file_path=temp_roast_file)
    rule = manager.add_rule(keyword="待刪除", reply_guide="導引")

    # Act
    deleted = manager.delete_rule(rule.id)
    non_existent = manager.delete_rule("fake_id_12345")

    # Assert
    assert deleted is True
    assert non_existent is False
    assert not any(r.id == rule.id for r in manager.get_rules())


def test_roast_manager_keyword_matching(temp_roast_file: Path) -> None:
    """Test matching messages against contains, exact, and regex modes."""
    # Arrange
    manager = RoastManager(file_path=temp_roast_file)
    manager.add_rule(keyword="==", reply_guide="空格", match_mode="contains")
    manager.add_rule(keyword="笑死", reply_guide="有這麼好笑嗎", match_mode="exact")
    manager.add_rule(keyword=r"打[0-9]+折", reply_guide="貪小便宜", match_mode="regex")

    # Act & Assert (contains)
    matched_contains = manager.find_matching_rules("你是笨蛋==")
    assert any(r.keyword == "==" for r in matched_contains)

    # Act & Assert (exact)
    matched_exact_yes = manager.find_matching_rules("笑死")
    assert any(r.keyword == "笑死" for r in matched_exact_yes)

    matched_exact_no = manager.find_matching_rules("笑死我了哈哈哈")
    assert not any(r.keyword == "笑死" for r in matched_exact_no)

    # Act & Assert (regex)
    matched_regex = manager.find_matching_rules("今天全店打5折耶！")
    assert any(r.keyword == r"打[0-9]+折" for r in matched_regex)


def test_roast_manager_probability_roll(temp_roast_file: Path) -> None:
    """Test probability roll boundaries."""
    # Arrange
    rule_0 = RoastRule(keyword="k0", reply_guide="g", probability=0)
    rule_100 = RoastRule(keyword="k100", reply_guide="g", probability=100)

    # Act & Assert
    assert RoastManager.roll_probability(rule_0) is False
    assert RoastManager.roll_probability(rule_100) is True


def test_roast_manager_master_switch(temp_roast_file: Path) -> None:
    """Test master toggle enables and disables keyword matching."""
    # Arrange
    manager = RoastManager(file_path=temp_roast_file)

    # Act - Disable
    manager.set_enabled(False)
    assert manager.is_enabled() is False
    assert len(manager.find_matching_rules("你是笨蛋==")) == 0

    # Act - Re-enable
    manager.set_enabled(True)
    assert manager.is_enabled() is True
    assert len(manager.find_matching_rules("你是笨蛋==")) > 0


def test_roast_cog_is_targeting_bot() -> None:
    """Test RoastCog._is_targeting_bot filters out messages directed at the bot."""
    from unittest.mock import MagicMock
    import discord
    from handlers.roast.cog import RoastCog

    # Arrange
    mock_bot = MagicMock()
    mock_bot_user = MagicMock(spec=discord.ClientUser)
    mock_bot_user.id = 123456789
    mock_bot.user = mock_bot_user

    cog = RoastCog(bot=mock_bot)

    # Case 1: Mentioned in message.mentions
    msg_mention = MagicMock(spec=discord.Message)
    msg_mention.content = "麻麻我愛你"
    msg_mention.mentions = [mock_bot_user]
    msg_mention.reference = None
    msg_mention.guild = None
    assert cog._is_targeting_bot(msg_mention) is True

    # Case 2: Textual @雪莉
    msg_text_tag = MagicMock(spec=discord.Message)
    msg_text_tag.content = "@雪莉 你是笨蛋=="
    msg_text_tag.mentions = []
    msg_text_tag.reference = None
    msg_text_tag.guild = None
    assert cog._is_targeting_bot(msg_text_tag) is True

    # Case 3: Textual @Sherry
    msg_sherry_tag = MagicMock(spec=discord.Message)
    msg_sherry_tag.content = "hello @sherry =="
    msg_sherry_tag.mentions = []
    msg_sherry_tag.reference = None
    msg_sherry_tag.guild = None
    assert cog._is_targeting_bot(msg_sherry_tag) is True

    # Case 4: Normal user message with no bot targeting -> Should be False
    msg_normal = MagicMock(spec=discord.Message)
    msg_normal.content = "露露卡麻麻在幫我煮晚餐"
    msg_normal.mentions = []
    msg_normal.reference = None
    msg_normal.guild = None
    assert cog._is_targeting_bot(msg_normal) is False


def test_clean_roast_reply() -> None:
    """Test cleaning thoughts, prefixes, and quotation marks from roast reply."""
    from handlers.roast.utils import clean_roast_reply

    # Case 1: Thoughts and quotes
    raw_1 = '<thought>分析中...</thought>「說誰是笨蛋呢！本名偵探才不理你！」'
    assert clean_roast_reply(raw_1) == "說誰是笨蛋呢！本名偵探才不理你！"

    # Case 2: Character name prefix
    raw_2 = '橘雪莉: 那是我麻麻，才不是你的！'
    assert clean_roast_reply(raw_2) == "那是我麻麻，才不是你的！"

    # Case 3: Empty string
    assert clean_roast_reply("") == ""


def test_extract_fallback_roast_conditional() -> None:
    """Test safe extraction of fallback roast replies with condition brackets."""
    from handlers.roast.utils import extract_fallback_roast

    guide = "那是我麻麻 不是你麻麻 [若提及雪莉:我才不是你麻麻]"

    # Case 1: Message does not mention Sherry -> Strips brackets and returns main line
    fallback_normal = extract_fallback_roast(guide, user_content="小望麻麻最喜歡我了")
    assert fallback_normal == "那是我麻麻 不是你麻麻"
    assert "[" not in fallback_normal and "]" not in fallback_normal

    # Case 2: Message mentions Sherry -> Selects matching condition branch
    fallback_sherry = extract_fallback_roast(guide, user_content="雪莉你麻麻最喜歡我了")
    assert fallback_sherry == "我才不是你麻麻"
    assert "[" not in fallback_sherry and "]" not in fallback_sherry


def test_extract_fallback_roast_pure_instruction() -> None:
    """Test fallback extraction replaces pure prompt instructions with default persona replies."""
    from handlers.roast.utils import extract_fallback_roast, DEFAULT_FALLBACK_ROASTS

    # Case 1: Bracketed pure instruction
    pure_instruction_1 = "[請用極度傲嬌的語氣狠狠吐槽對方]"
    fallback_1 = extract_fallback_roast(pure_instruction_1)
    assert fallback_1 in DEFAULT_FALLBACK_ROASTS
    assert "[" not in fallback_1

    # Case 2: Plain text prompt directive
    pure_instruction_2 = "請用名偵探自信滿滿且得意的語氣吐槽"
    fallback_2 = extract_fallback_roast(pure_instruction_2)
    assert fallback_2 in DEFAULT_FALLBACK_ROASTS


def test_roast_cog_execute_roast_timeout_fallback() -> None:
    """Test that RoastCog._execute_roast cleanly falls back without prompt leakage on timeout."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    import discord
    from handlers.roast.cog import RoastCog
    from handlers.roast.models import RoastRule

    async def _run() -> None:
        # Arrange
        mock_bot = MagicMock()
        mock_bot.get_cog.return_value = None  # No BotBrain available -> forces fallback

        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.id = 987654321
        mock_channel.name = "general"

        mock_author = MagicMock(spec=discord.Member)
        mock_author.display_name = "歐潤吉"
        mock_author.bot = False

        mock_message = MagicMock(spec=discord.Message)
        mock_message.channel = mock_channel
        mock_message.author = mock_author
        mock_message.content = "小望麻麻最喜歡我了"

        rule = RoastRule(
            keyword="麻麻",
            reply_guide="那是我麻麻 不是你麻麻 [若提及雪莉:我才不是你麻麻]",
            probability=100,
        )

        cog = RoastCog(bot=mock_bot)

        # Act & Assert: When BotBrain is None (fallback mode)
        with patch("handlers.roast.cog.simulate_typing_and_send_to_channel", new_callable=AsyncMock) as mock_send:
            await cog._execute_roast(mock_message, rule)
            mock_send.assert_called_once()
            sent_text = mock_send.call_args[0][1]
            assert sent_text == "那是我麻麻 不是你麻麻"
            assert "[" not in sent_text and "]" not in sent_text

    asyncio.run(_run())
