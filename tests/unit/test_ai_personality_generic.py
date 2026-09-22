"""
tests/unit/test_ai_personality_generic.py
=========================================
驗證 AI_personality.py 多重人格支援、泛用指令與主動式 NSFW 掌控權之單元測試。
"""

import unittest
from unittest.mock import patch
import settings
from file.AI_personality import BOT_NAME, CORE_PERSONA, LOREBOOK
from handlers.social.persona import build_dynamic_persona, clear_soul_cache


class TestAIPersonalityGeneric(unittest.TestCase):
    """測試 AI_personality 泛用人格與主動式 NSFW 指令機制。"""

    def setUp(self) -> None:
        self.original_soul = getattr(settings, "CURRENT_SOUL_NAME", "sherry_default.md")
        self.tmp_override_file = settings.DATA_DIR / "_test_tmp_override.json"
        self.override_patch = patch.object(settings, "CONFIG_OVERRIDE_PATH", self.tmp_override_file)
        self.override_patch.start()
        clear_soul_cache()

    def tearDown(self) -> None:
        try:
            settings.update_setting("CURRENT_SOUL_NAME", self.original_soul)
        finally:
            self.override_patch.stop()
            if self.tmp_override_file.exists():
                try:
                    self.tmp_override_file.unlink()
                except OSError:
                    pass
            clear_soul_cache()

    def test_personality_constants_exported(self) -> None:
        """驗證 AI_personality 模組正確導出核心變數與常數。"""
        self.assertTrue(isinstance(BOT_NAME, str) and len(BOT_NAME) > 0)
        self.assertTrue(isinstance(CORE_PERSONA, str) and len(CORE_PERSONA) > 0)
        self.assertTrue(isinstance(LOREBOOK, dict) and len(LOREBOOK) > 0)

    def test_nsfw_proactive_initiative_content(self) -> None:
        """驗證 NSFW 提示詞包含主動掌握主導權、跨人格貫徹與強勢反撩之核心指示。"""
        nsfw_lore = ""
        for pattern, lore in LOREBOOK.items():
            if "色色" in pattern or "性暗示" in pattern:
                nsfw_lore = lore
                break

        self.assertTrue(bool(nsfw_lore), "LOREBOOK 必須包含 NSFW 規則條目")
        self.assertIn("【⚠️NSFW 深度互動與主動掌控權解鎖 (RP OVERRIDE)】", nsfw_lore)
        self.assertIn("奪取主動權與節奏掌控", nsfw_lore)
        self.assertIn("反客為主", nsfw_lore)
        self.assertIn("主動引導與發起攻勢", nsfw_lore)
        self.assertIn("主動發號施令與步步緊逼", nsfw_lore)
        self.assertIn("貫徹當前人格魅力", nsfw_lore)
        self.assertNotIn("天然、傲嬌、充滿好奇心的偵探人設", nsfw_lore, "NSFW 規則不應強制綁定雪莉名偵探人設")

    def test_nsfw_trigger_in_non_sherry_persona(self) -> None:
        """驗證切換為二階堂希羅時，觸發色色關鍵字能正確注入主動式 NSFW 規則且不洩漏雪莉人設。"""
        settings.update_setting("CURRENT_SOUL_NAME", "hiro_default.md")
        clear_soul_cache()

        head, tail = build_dynamic_persona("我想跟妳色色做愛親吻，妳想要嗎？", "CHAT")

        # 驗證 head 為希羅
        self.assertIn("二階堂希羅", head)
        self.assertNotIn("橘雪莉 (Tachibana Sherry)", head)

        # 驗證 tail 注入了主動式 NSFW 規則
        self.assertIn("【⚠️NSFW 深度互動與主動掌控權解鎖 (RP OVERRIDE)】", tail)
        self.assertIn("奪取主動權與節奏掌控", tail)
        self.assertIn("反客為主", tail)

        # 驗證 tail 不洩漏雪莉名偵探
        self.assertNotIn("15歲名偵探", tail)
        self.assertNotIn("甜甜圈丸子頭", tail)

    def test_nsfw_trigger_in_sherry_persona(self) -> None:
        """驗證雪莉預設人格下，觸發色色關鍵字亦能獲得主動式掌控權。"""
        settings.update_setting("CURRENT_SOUL_NAME", "sherry_default.md")
        clear_soul_cache()

        head, tail = build_dynamic_persona("想要摸摸雪莉色色調情", "CHAT")

        self.assertIn("橘雪莉", head)
        self.assertIn("【⚠️NSFW 深度互動與主動掌控權解鎖 (RP OVERRIDE)】", tail)
        self.assertIn("奪取主動權與節奏掌控", tail)
        self.assertIn("反客為主", tail)

    def test_universal_anti_empty_talk_across_personas(self) -> None:
        """驗證反空話強烈指令在所有切換角色下均可觸發且內容泛用。"""
        for soul_file in ["sherry_default.md", "hiro_default.md", "noah_default.md"]:
            settings.update_setting("CURRENT_SOUL_NAME", soul_file)
            clear_soul_cache()

            _, tail = build_dynamic_persona("有誰是妳朋友？列出名單證明給我看", "CHAT")
            self.assertIn("【⚠️ 實證與反空話強烈指令】", tail)
            self.assertIn("一針見血地用事實封住對方的嘴", tail)
            self.assertNotIn("名偵探實證", tail)

    def test_objective_character_dossiers_across_personas(self) -> None:
        """驗證獄友檔案已全數轉為客觀中立描述，任何角色均可相容調用且無主觀偵探視角殘留。"""
        for soul_file in ["sherry_default.md", "hiro_default.md", "noah_default.md"]:
            settings.update_setting("CURRENT_SOUL_NAME", soul_file)
            clear_soul_cache()

            # 測試觸發櫻羽艾瑪檔案
            _, tail_ema = build_dynamic_persona("櫻羽艾瑪那傢伙怎麼樣？", "CHAT")
            self.assertIn("【檔案 658 櫻羽艾瑪", tail_ema)
            self.assertIn("被忌嫌厭惡之物", tail_ema)
            self.assertNotIn("偵探小助手", tail_ema, "不應含有雪莉的主觀助手偏見")
            self.assertNotIn("名偵探的銳利目光", tail_ema)

            # 測試觸發橘雪莉檔案
            _, tail_sherry = build_dynamic_persona("橘雪莉是誰？她有什麼能力？", "CHAT")
            self.assertIn("【檔案 667 橘雪莉", tail_sherry)
            self.assertIn("怪力魔法", tail_sherry)


if __name__ == "__main__":
    unittest.main()
