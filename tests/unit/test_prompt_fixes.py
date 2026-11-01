"""
tests/unit/test_prompt_fixes.py
===============================
驗證提示詞修復：
1. 股票標的排除年份 (2026 等) 與多標的並行調用 Prompt 規範。
2. 工具自主調用輸出二選一鐵則（Action 與 Response 互斥，嚴禁混雜）。
3. 身臨其境面對面 (Face-to-Face) 視角與抹除電腦螢幕/打字描寫。
"""
from __future__ import annotations

import unittest
from handlers.social.dispatch.prompts import _load_tool_rules
from handlers.social.persona import _load_common_rules, _load_soul_persona_by_name
from handlers.social.tools.stock_tool import _TICKER_EXTRACT_SYSTEM_PROMPT


class TestPromptFixes(unittest.TestCase):
    """驗證 Prompt 規則修訂之正確性。"""

    def test_tool_rules_stock_year_exclusion(self) -> None:
        """驗證工具調用規則中明確排除年份與時間數字。"""
        tool_rules = _load_tool_rules()
        self.assertIn("嚴格排除時間與年份", tool_rules)
        self.assertIn("2026", tool_rules)
        self.assertIn("MU", tool_rules)
        self.assertIn("0050", tool_rules)

    def test_tool_rules_mutual_exclusivity(self) -> None:
        """驗證工具調用輸出模式二選一鐵則（Action 與正文對白互斥）。"""
        tool_rules = _load_tool_rules()
        self.assertIn("輸出模式二選一鐵則", tool_rules)
        self.assertIn("嚴禁 ACTION 與正文對白同時出現", tool_rules)
        self.assertIn("只能且必須僅輸出", tool_rules)

    def test_common_rules_face_to_face(self) -> None:
        """驗證通用互動規則包含面對面 (Face-to-Face) 視角且抹除螢幕/打字。"""
        common_rules = _load_common_rules()
        self.assertIn("面對面實體身臨其境互動視角", common_rules)
        self.assertIn("Face-to-Face", common_rules)
        self.assertIn("絕對嚴禁", common_rules)
        self.assertIn("坐在電腦前", common_rules)
        self.assertIn("鍵盤打字", common_rules)

    def test_hiro_soul_face_to_face(self) -> None:
        """驗證二階堂希羅人設已改為面對面交流視角，無 Discord 實況群友字樣。"""
        hiro_soul = _load_soul_persona_by_name("hiro_default.md")
        self.assertIn("面對面交流相處", hiro_soul)
        self.assertNotIn("Discord 實況群友", hiro_soul)

    def test_stock_ticker_extract_prompt(self) -> None:
        """驗證股票提取 Prompt 明確排除年份與時間數字。"""
        self.assertIn("2026", _TICKER_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("誤識別為股票代號", _TICKER_EXTRACT_SYSTEM_PROMPT)

    def test_common_rules_length_and_action_simplicity(self) -> None:
        """驗證通用互動規則包含日常閒聊簡短有溫度與動作描寫嚴格從簡之規範。"""
        common_rules = _load_common_rules()
        self.assertIn("日常閒聊篇幅鐵則", common_rules)
        self.assertIn("保持簡短有溫度（約 30～80 字、1～3 句話為佳）", common_rules)
        self.assertIn("動作描寫嚴格從簡", common_rules)
        self.assertIn("至多偶爾帶 1 個簡短神態或動作點綴", common_rules)
        self.assertIn("撰寫大段小說式的心理活動", common_rules)

    def test_sherry_soul_concise_chat(self) -> None:
        """驗證雪莉預設靈魂包含日常對話活潑簡短（1~3句為佳）之設定。"""
        sherry_soul = _load_soul_persona_by_name("manosaba_sherry_default.md")
        self.assertIn("日常對話保持活潑簡短（1~3句為佳）", sherry_soul)

    def test_lorebook_detail_expansion_turn_isolation(self) -> None:
        """驗證細節擴展指令僅在當前提問明確要求時觸發，不會被普通詞彙或歷史紀錄誤觸發。"""
        from handlers.social.persona import build_dynamic_persona

        # 1. 普通詞彙（如「清楚」、「具體」）不應觸發細節擴展指令
        _, tail_lore_normal = build_dynamic_persona(
            current_text="我想了解情況，請你說清楚具體一點",
            intent_name="CHAT",
            user_query="我想了解情況，請你說清楚具體一點",
        )
        self.assertNotIn("【⚠️細節擴展指令】", tail_lore_normal)

        # 2. 明確要求「詳細說明」時應正確觸發
        _, tail_lore_detail = build_dynamic_persona(
            current_text="請詳細說明這個案件的經過",
            intent_name="CHAT",
            user_query="請詳細說明這個案件的經過",
        )
        self.assertIn("【⚠️細節擴展指令】", tail_lore_detail)

        # 3. 歷史紀錄中雖有「詳細說明」，但當前輪次只是閒聊時，不應殘留觸發
        history_context = "User: 請詳細說明案件\nBot: 詳細內容如下...\nUser: 那漢娜今天心情如何？"
        _, tail_lore_history = build_dynamic_persona(
            current_text=history_context,
            intent_name="CHAT",
            user_query="那漢娜今天心情如何？",
        )
        self.assertNotIn("【⚠️細節擴展指令】", tail_lore_history)



if __name__ == "__main__":
    unittest.main()
