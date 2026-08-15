"""
tests/unit/test_memory_optimization.py
======================================
單元測試：驗證記憶系統（Memory 2.0）的別名消毒、語意模糊去重、類別多樣性選取與向下相容性。
"""

import unittest
from pathlib import Path
import sys

# 將專案根目錄加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from handlers.memory.storage import (
    sanitize_aliases,
    sanitize_and_merge_facts,
    calculate_fact_similarity,
    get_diverse_facts_for_context,
    make_fact,
    _migrate_fact,
    UserFact,
)


class TestAliasSanitization(unittest.TestCase):
    """驗證別名消毒邏輯，防止遊戲代號、指令與群組泛稱被誤記為暱稱。"""

    def test_user_reported_aliases(self) -> None:
        """測試使用者回報的案例：Yuuto 與 歐潤吉 應保留，賽爾號 m/n 與 群友 應剔除。"""
        # Arrange
        raw_aliases = ["Yuuto", "賽爾號 m", "賽爾號 n", "群友", "歐潤吉"]

        # Act
        cleaned = sanitize_aliases(raw_aliases)

        # Assert
        self.assertEqual(cleaned, ["Yuuto", "歐潤吉"])

    def test_blacklist_and_roles(self) -> None:
        """測試黑名單過濾（群友、大家、機器人、發語詞等）。"""
        # Arrange
        raw = ["大家", "某群友", "管理員", "BOT", "哈哈", "靠北", "雪莉", "@橘雪莉", "小明"]

        # Act
        cleaned = sanitize_aliases(raw)

        # Assert
        self.assertEqual(cleaned, ["小明"])

    def test_length_and_command_formats(self) -> None:
        """測試過短、過長、指令前綴與房號尾綴。"""
        # Arrange
        raw = ["a", "這是一個超過十五個字元超級長的暱稱無法使用", "!help", "/search", "lol 1", "正常的暱稱"]

        # Act
        cleaned = sanitize_aliases(raw)

        # Assert
        self.assertEqual(cleaned, ["正常的暱稱"])


class TestFactSimilarityAndDeduplication(unittest.TestCase):
    """驗證語意相似度計算與模糊去重機制。"""

    def test_user_reported_duplicate_poop_facts(self) -> None:
        """測試使用者回報案例：5 條不同講法的『大便』幹話應被模糊去重聚合為 1 條，且保留最高重要度 5。"""
        # Arrange
        duplicate_facts = [
            {"fact": "他說『你只能吃大便』", "importance": 3, "category": "habit"},
            {"fact": "他喜歡吃大便（或有此特質）", "importance": 4, "category": "interest"},
            {"fact": "他表示『你只能吃大便』(這是一個重複且強烈的特質)", "importance": 4, "category": "habit"},
            {"fact": "他只能吃大便。", "importance": 3, "category": "habit"},
            {"fact": "他喜歡吃大便的特質，這是重複且強烈的核心習慣。", "importance": 5, "category": "habit"},
        ]

        # Act
        merged, new_count = sanitize_and_merge_facts([], duplicate_facts)

        # Assert
        self.assertEqual(len(merged), 1)
        self.assertEqual(new_count, 1)
        self.assertEqual(merged[0]["importance"], 5)

    def test_distinct_topics_not_deduped(self) -> None:
        """測試不同主題的事實不應被誤判為重複。"""
        # Arrange
        distinct_facts = [
            {"fact": "他平時喜歡寫Python程式", "importance": 4, "category": "interest"},
            {"fact": "他很常玩賽爾號", "importance": 3, "category": "interest"},
            {"fact": "他住在台北", "importance": 4, "category": "identity"},
            {"fact": "他常常熬夜寫扣到凌晨三點", "importance": 3, "category": "habit"},
        ]

        # Act
        merged, new_count = sanitize_and_merge_facts([], distinct_facts)

        # Assert
        self.assertEqual(len(merged), 4)
        self.assertEqual(new_count, 4)

    def test_similarity_calculation(self) -> None:
        """測試相似度計算函數的分數輸出。"""
        # Arrange & Act
        sim_high = calculate_fact_similarity("他喜歡吃大便", "他只能吃大便。")
        sim_low = calculate_fact_similarity("他喜歡寫Python", "他今天吃了拉麵")

        # Assert
        self.assertGreaterEqual(sim_high, 0.65)
        self.assertLess(sim_low, 0.3)


class TestDiverseContextSelection(unittest.TestCase):
    """驗證 Prompt 注入時的類別多樣性選取演算法。"""

    def test_diverse_facts_round_robin(self) -> None:
        """測試多樣性選取能涵蓋不同類別，而非全被單一類別佔據。"""
        # Arrange
        facts: list[UserFact] = [
            make_fact("他喜歡寫Python", 5, "interest"),
            make_fact("他喜歡玩賽爾號", 4, "interest"),
            make_fact("他喜歡看動漫", 4, "interest"),
            make_fact("他住在台北", 4, "identity"),
            make_fact("他是雪莉的鐵粉", 5, "relationship"),
            make_fact("他習慣熬夜", 3, "habit"),
        ]

        # Act
        selected = get_diverse_facts_for_context(facts, max_count=4)

        # Assert
        self.assertEqual(len(selected), 4)
        categories = {f.get("category") for f in selected}
        # 應至少涵蓋 3 個不同類別（例如 interest, identity, relationship, habit）
        self.assertGreaterEqual(len(categories), 3)


class TestMigrationAndCompatibility(unittest.TestCase):
    """驗證向下相容遷移工具。"""

    def test_migrate_legacy_string_fact(self) -> None:
        # Arrange
        legacy = "他是個工程師"

        # Act
        mf = _migrate_fact(legacy)

        # Assert
        self.assertIsNotNone(mf)
        assert mf is not None
        self.assertEqual(mf["fact"], "他是個工程師")
        self.assertEqual(mf["importance"], 3)
        self.assertEqual(mf["category"], "identity")

    def test_migrate_dict_fact(self) -> None:
        # Arrange
        raw = {"fact": "他喜歡打LOL", "importance": 4, "category": "interest"}

        # Act
        mf = _migrate_fact(raw)

        # Assert
        self.assertIsNotNone(mf)
        assert mf is not None
        self.assertEqual(mf["fact"], "他喜歡打LOL")
        self.assertEqual(mf["importance"], 4)
        self.assertEqual(mf["category"], "interest")


if __name__ == "__main__":
    unittest.main()
