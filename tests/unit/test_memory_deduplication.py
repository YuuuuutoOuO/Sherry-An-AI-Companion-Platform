"""
tests/unit/test_memory_deduplication.py
=======================================
記憶去重與深夜大掃除整合之單元測試。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from handlers.memory.storage import (
    UserFact,
    UserProfile,
    make_fact,
    deduplicate_facts,
    sanitize_and_merge_facts,
    consolidate_user_memory,
    consolidate_all_user_memories,
    get_diverse_facts_for_context,
    save_single_user_memory,
    load_user_memory,
)
from handlers.memory.cog import MemoryManager


class TestMemoryDeduplication:
    """測試記憶單元與清單去重邏輯。"""

    def test_deduplicate_facts_exact_and_semantic(self) -> None:
        """測試 deduplicate_facts 能正確去除完全重複與高語意重疊的事實。"""
        raw_facts: list[UserFact] = [
            make_fact("在進行按摩情境互動時，要求必須使用正確的身體部位名稱（如：肩膀、胸部、肚子、屁股、肉棒、屁眼等）", importance=5, category="rule"),
            make_fact("在進行按摩情境互動時，要求必須使用正確的身體部位名稱（如：肩膀、胸部、肚子、屁股、肉棒、屁眼等）", importance=4, category="rule"),
            make_fact("偏好純愛內容，並對 NTR 內容持有排斥態度", importance=4, category="preference"),
            make_fact("偏好純愛內容並排斥 NTR", importance=3, category="preference"),
            make_fact("是露琪亞廚", importance=4, category="preference"),
            make_fact("是露琪亞廚", importance=4, category="preference"),
        ]

        deduped = deduplicate_facts(raw_facts)
        texts = [f["fact"] for f in deduped]

        # 按摩規則只保留 1 筆且重要度為 5
        massage_rules = [f for f in deduped if "按摩情境" in f["fact"]]
        assert len(massage_rules) == 1
        assert massage_rules[0]["importance"] == 5

        # 純愛與排斥 NTR 語意高度相似，應合併為 1 筆且重要度為 4
        pure_love_facts = [f for f in deduped if "純愛" in f["fact"] or "NTR" in f["fact"]]
        assert len(pure_love_facts) == 1
        assert pure_love_facts[0]["importance"] == 4

        # 露琪亞廚只保留 1 筆
        assert texts.count("是露琪亞廚") == 1
        assert len(deduped) == 3

    def test_deduplicate_facts_composite_and_bracket_examples(self) -> None:
        """測試包含括號舉例之長規則能與複合摘要規則正確辨識並合併。"""
        raw_facts: list[UserFact] = [
            make_fact("在進行按摩情境互動時，要求必須使用正確的身體部位名稱（如：肩膀、胸部、肚子、屁股、肉棒、屁眼等）", importance=5, category="preference"),
            make_fact("要求按摩情境必須使用正確的身體部位名稱並包含詳細動作說明", importance=4, category="preference"),
        ]
        deduped = deduplicate_facts(raw_facts)
        assert len(deduped) == 1
        assert deduped[0]["importance"] == 5

    def test_deduplicate_facts_filters_banter_and_mentions(self) -> None:
        """測試過濾幹話/嘴砲與 @mention 標記。"""
        facts: list[UserFact] = [
            make_fact("他平時喜歡寫程式", importance=3, category="habit"),
            make_fact("去吃大便啦", importance=5, category="opinion"),
            make_fact("@某群友 是笨蛋", importance=3, category="opinion"),
            make_fact("包含 [某群友] 的發言", importance=3, category="identity"),
            make_fact("這是一句超級無敵超級無敵超級無敵超級無敵超級無敵超級無敵超級無敵超級無敵超級無敵超級無敵長長長長長長長長長長長長長長長長長長長長長長的廢話句子", importance=2, category="identity"),
        ]

        deduped = deduplicate_facts(facts)
        assert len(deduped) == 1
        assert deduped[0]["fact"] == "他平時喜歡寫程式"

    def test_sanitize_and_merge_facts_self_heals_existing_duplicates(self) -> None:
        """測試 sanitize_and_merge_facts 能自體修復傳入的既有重複事實。"""
        existing: list[UserFact] = [
            make_fact("在進行按摩情境互動時，要求描述內容必須包含詳細的動作說明", importance=5, category="rule"),
            make_fact("在進行按摩情境互動時，要求描述內容必須包含詳細的動作說明", importance=5, category="rule"),
            make_fact("偏好包含全裸、潤滑液使用、全身摩擦等情色內容的互動情境", importance=5, category="preference"),
            make_fact("偏好包含全裸、潤滑液使用、全身摩擦等情色內容的互動情境", importance=5, category="preference"),
        ]

        # 傳入空的新事實，仍應將 existing 內的 4 筆收斂為 2 筆
        final_facts, new_count = sanitize_and_merge_facts(existing, [])
        assert len(final_facts) == 2
        assert new_count == 0

        # 新增一筆不相關的事實
        final_facts2, new_count2 = sanitize_and_merge_facts(existing, ["喜歡原創角色望實"])
        assert len(final_facts2) == 3
        assert new_count2 == 1

    def test_consolidate_user_memory_and_all(self) -> None:
        """測試單人與全庫記憶大掃除函式。"""
        profile = UserProfile(
            aliases=["小明", "小明", "@某人", "雪莉", "Ming"],
            facts=[
                make_fact("喜歡吃拉麵", importance=4, category="preference"),
                make_fact("喜歡吃拉麵", importance=3, category="preference"),
                make_fact("討厭香菜", importance=3, category="preference"),
            ],
        )

        consolidated, a_rem, f_rem = consolidate_user_memory(profile)
        assert consolidated["aliases"] == ["小明", "Ming"]
        assert len(consolidated["facts"]) == 2
        assert a_rem == 3  # 重複小明、@某人、雪莉
        assert f_rem == 1  # 重複拉麵

        store = {
            "u1": profile,
            "u2": UserProfile(aliases=["阿華"], facts=[make_fact("住在台北", importance=3)]),
        }
        stats = consolidate_all_user_memories(store)
        assert stats["users_cleaned"] == 1
        assert stats["facts_removed"] == 1
        assert stats["aliases_removed"] == 3

    def test_get_diverse_facts_for_context_rule_deduplication(self) -> None:
        """測試 Prompt 注入時的 rule 規則不會有重複項。"""
        facts: list[UserFact] = [
            make_fact("要求使用正確部位名稱", importance=5, category="rule"),
            make_fact("要求使用正確部位名稱", importance=5, category="rule"),
            make_fact("喜歡玩遊戲", importance=4, category="preference"),
        ]

        diverse = get_diverse_facts_for_context(facts, max_count=5)
        rule_facts = [f for f in diverse if f["category"] == "rule"]
        assert len(rule_facts) == 1
        assert rule_facts[0]["fact"] == "要求使用正確部位名稱"

    def test_get_full_user_memory_snippet_deduplication(self) -> None:
        """測試全量檢視 Snippet 會自動去重展示。"""
        bot = MagicMock()
        mgr = MemoryManager(bot)
        mgr._store = {
            "12345": UserProfile(
                aliases=["貓貓", "貓貓"],
                facts=[
                    make_fact("在進行按摩情境互動時，要求描述內容必須包含詳細的動作說明", importance=5, category="rule"),
                    make_fact("在進行按摩情境互動時，要求描述內容必須包含詳細的動作說明", importance=5, category="rule"),
                    make_fact("偏好純愛內容，並對 NTR 內容持有排斥態度", importance=4, category="preference"),
                    make_fact("偏好純愛內容並排斥 NTR", importance=4, category="preference"),
                ],
            )
        }

        snippet = mgr.get_full_user_memory_snippet(12345)
        assert snippet.count("在進行按摩情境互動時，要求描述內容必須包含詳細的動作說明") == 1
        # 知識點歸納中純愛/NTR 應只出現 1 次
        assert snippet.count("共 1 條") == 1

    def test_save_single_user_memory(self, tmp_path: Path) -> None:
        """測試 save_single_user_memory 能針對單一使用者正確寫入與更新。"""
        profile: UserProfile = {
            "aliases": ["小雪", "Sherry"],
            "facts": [
                make_fact("喜歡吃巧克力", importance=4, category="preference"),
                make_fact("平時在台北工作", importance=3, category="identity"),
            ],
        }
        json_path = tmp_path / "user_memory.json"
        save_single_user_memory("99999", profile, {"99999": profile}, json_path)

        loaded = load_user_memory(json_path)
        assert "99999" in loaded
        assert "小雪" in loaded["99999"]["aliases"]
        assert len(loaded["99999"]["facts"]) == 2

    def test_deduplicate_complex_technical_facts(self) -> None:
        """測試針對語意分割、意圖拆解、Kernel/Mod 等複雜長句的多重變體能正確合併。"""
        raw_facts = [
            make_fact("正在進行雪莉（Sherry）的語意分割與意圖拆解優化，目標是讓雪莉能精準處理複雜指令並調用工具", importance=5, category="preference"),
            make_fact("對雪莉（Sherry）的運算邏輯、語意分割與意圖拆解進行優化研究", importance=4, category="preference"),
            make_fact("致力於優化雪莉 (Sherry) 的 NLP 解析能力、語意分割與意圖拆解功能", importance=4, category="preference"),
            make_fact("致力於優化雪莉的技術架構，包括語意分割、意圖拆解與工具調用精準度", importance=5, category="preference"),
            make_fact("對雪莉的架構進行了 Kernel 與 Mod 的功能拆分與限制", importance=4, category="preference"),
            make_fact("致力於透過 Kernel 與 Mod 的功能拆分來管控雪莉的人格與實驗功能，並優化其 NLP 解析能力與工具調用精準度", importance=5, category="preference"),
            make_fact("具備遊戲機制分析專家的能力，能對遊戲（如《Warframe》）的改動進行深度分析與市場影響評估", importance=4, category="identity"),
            make_fact("具備遊戲機制分析能力，會針對怪物階段與血量機制進行討論與評估", importance=4, category="identity"),
        ]

        deduped = deduplicate_facts(raw_facts)
        # 8 條高度重複的長事實應收斂為 3~4 條核心事實
        assert len(deduped) <= 4

        # 語意分割與意圖拆解類只保留 1 條且重要度為 5
        nlp_facts = [f for f in deduped if "語意分割" in f["fact"] and "意圖拆解" in f["fact"]]
        assert len(nlp_facts) >= 1
        assert max(f["importance"] for f in nlp_facts) == 5

        # 遊戲機制分析類只保留 1 條
        game_facts = [f for f in deduped if "遊戲機制分析" in f["fact"]]
        assert len(game_facts) == 1

    def test_merge_fact_attributes_prioritizes_rule_category(self) -> None:
        """測試當事實去重合併時，rule 類別享有最高優先權，不會被降級為 social 或 preference。"""
        raw_facts = [
            make_fact("對雪莉（Sherry）的內容與運算邏輯具備強烈的介入感、判斷意識與主導權", importance=4, category="rule"),
            make_fact("對雪莉（Sherry）的行為與邏輯有著強烈的介入感與主導意識，會對其內容進行判斷（如：雪莉是錯的、雪莉自己決定）", importance=4, category="social"),
        ]

        deduped = deduplicate_facts(raw_facts)
        assert len(deduped) == 1
        assert deduped[0]["category"] == "rule"
        assert "雪莉自己決定" in deduped[0]["fact"]

    def test_real_world_user_memory_consolidation(self) -> None:
        """測試真實使用者記憶範例（15 條高重複事實）的收斂、分類矯正與實體保留。"""
        raw_facts = [
            # Rules
            make_fact("對雪莉（Sherry/希羅醬）具有強烈的介入感與主導權，致力於研究如何增加其易上頭的個性特質，並要求其人格穩定性，不准變成怪怪的大叔機器人", importance=5, category="rule"),
            make_fact("對雪莉（Sherry/希羅醬）具有高度開發熱情，透過技術手段研究其人格、語意分割、意圖拆解與運算邏輯優化，並致力於增加其「易上頭」的個性特質", importance=4, category="rule"),
            make_fact("對於他人對其家人進行的不當稱呼表現出極高的道德底線與嚴厲的反感", importance=5, category="rule"),
            make_fact("對雪莉（Sherry）展現高度依賴與情感需求，偏好包含親密行為、調情與性暗示的沉浸式角色扮演（RP）互動，並要求其人格穩定性，不准變得奇怪", importance=5, category="rule"),
            make_fact("在與雪莉互動時，明確表達不接受雪莉變成「大叔機器人」或「怪怪的機器人」，要求其人格穩定且符合預期", importance=5, category="rule"),
            make_fact("對雪莉（Sherry）的對話邏輯、人格穩定性與架構設計具有高度掌控欲，並要求其維持特定人格設定", importance=4, category="rule"),
            make_fact("在技術開發與架構設計上，要求開發者（如 PHD）必須將架構與其既定標準對齊，並傾向於使用原始碼進行架構建立", importance=5, category="rule"),
            # Knowledge / Facts
            make_fact("對程式碼架構具有高度審美，能辨識以 Handler 為核心模組、Mod 為實驗模組的設計，並致力於優化 NLP 解析能力與工具調用精準度", importance=4, category="preference"),
            make_fact("曾攻讀博士學位，研究題目餅畫太大，但也因此讓全實驗室得以畢業，並對這段經歷感到又屌又後悔", importance=4, category="identity"),
            make_fact("具備博士學位背景，曾經歷畫大餅但成功帶領實驗室畢業的過程", importance=3, category="identity"),
            make_fact("具備遊戲機制分析專家能力，專注於《Warframe》與《陰陽師》的平衡性與角色強度評估，對角色強度劇變（如更新後直接幹死普咖）持有批判態度", importance=4, category="preference"),
            make_fact("對雪莉（Sherry）相關功能、檔案內容、語意分割與架構解耦具有深入研究，並致力於透過 LLM 技術進行對話互動與技術實作", importance=4, category="preference"),
            make_fact("專注於台美股（如 VOO、0050、MU、SNDK、SKHY）與加密貨幣（BTC）投資，並持續追蹤 OpenAI 相關公司與市場消息炒作", importance=4, category="preference"),
            make_fact("對資源消耗與 Token 消耗量極度敏感，會評估開發成本是否導致 RAM 爆掉或產生等同於股票賠錢的經濟損失", importance=4, category="habit"),
            make_fact("高度關注台美股（如 VOO、0050、MU、SNDK、SKHY）與加密貨幣（BTC）市場，並利用雪莉進行美股交易量監測與提醒", importance=4, category="preference"),
        ]

        profile = UserProfile(
            aliases=["Yuuto", "歐潤吉", "orion", "沒有CUDA的歐潤吉"],
            facts=raw_facts,
        )

        consolidated, a_rem, f_rem = consolidate_user_memory(profile)
        final_facts = consolidated["facts"]

        # 1. 總條數大幅精簡：由 15 條收斂至 10 條以內（高重複項全數合併）
        assert len(final_facts) <= 10
        assert f_rem >= 5

        # 2. 博士學位兩條必合併為 1 條
        phd_facts = [f for f in final_facts if "博士" in f["fact"]]
        assert len(phd_facts) == 1
        assert phd_facts[0]["category"] == "identity"
        assert "畢業" in phd_facts[0]["fact"]

        # 3. 台美股 VOO/0050 兩條必合併為 1 條，且保留代號實體
        stock_facts = [f for f in final_facts if "VOO" in f["fact"] or "0050" in f["fact"]]
        assert len(stock_facts) == 1
        assert "BTC" in stock_facts[0]["fact"]

        # 4. RP 親密互動之寄生規則尾巴被剝離，並正確歸類為 preference
        rp_facts = [f for f in final_facts if "親密行為" in f["fact"] or "角色扮演" in f["fact"]]
        assert len(rp_facts) == 1
        assert rp_facts[0]["category"] == "preference"
        assert "變得奇怪" not in rp_facts[0]["fact"]

        # 5. 規則區不再無限膨脹，收斂至 3 條左右核心規則
        rule_facts = [f for f in final_facts if f["category"] == "rule"]
        assert len(rule_facts) <= 4

