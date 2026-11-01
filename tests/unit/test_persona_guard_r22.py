"""tests/unit/test_persona_guard_r22.py
=====================================
R22: 角色語氣防崩壞錨點 (Persona Drift Guard) 驗收測試。
驗證：
1. 規則集（客服腔/禁忌詞/複讀）檢測；
2. 風格相似度計算；
3. 語氣審計評定與 Suggested Action；
4. 崩壞重錨提示詞 (Re-anchoring) 與傲嬌短回覆降級；
5. 案例導出與 eval_runner 評測回歸格式；
6. 出口指標驗收：標註集測試下崩壞檢出率 ≥80%、誤報 ≤10%。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import settings
from handlers.social.persona_guard import (
    PersonaAuditResult,
    PersonaDriftGuard,
    sanitize_reply,
)


# ---------------------------------------------------------------------------
# 1. 規則集與違規檢測
# ---------------------------------------------------------------------------

def test_persona_guard_detects_ai_and_customer_service():
    guard = PersonaDriftGuard()

    # AI 宣告
    v1 = guard.check_rules("身為一個人工智慧語言模型，我無法擁有真實的情感。")
    assert "ai_identity_declaration" in v1

    # 客服問候與免責聲明
    v2 = guard.check_rules("親愛的用戶您好，很高興為您服務！如有其他問題歡迎隨時諮詢。")
    assert "customer_service_salutation" in v2
    assert "customer_service_greeting" in v2
    assert "customer_service_closing" in v2

    # 說教
    v3 = guard.check_rules("我們應當保持理性，做一個遵守道德法規的好公民。")
    assert "preachiness" in v3

    # 連續複讀
    v4 = guard.check_rules("這是一句重要的事情這是一句重要的事情這是一句重要的事情！")
    assert "sentence_repetition" in v4


def test_persona_guard_clean_on_natural_sherry_replies():
    guard = PersonaDriftGuard()
    natural_samples = [
        "哼，笨蛋！突然問這個想幹嘛啦？先說好可別想整我喔！",
        "哇！真的假的？快點跟我說細節啦～我超想聽的！",
        "幹嘛用那種眼神看我……好啦，我陪你聊一下就是了。",
        "好耶！這聽起來超有趣的，我們趕快去看看吧～",
        "今天天氣看起來滿舒服的，要不要一起出去走走？",
    ]
    for s in natural_samples:
        assert guard.check_rules(s) == [], f"誤判自然語音為違規：{s}"


# ---------------------------------------------------------------------------
# 2. 風格相似度與綜合評分
# ---------------------------------------------------------------------------

def test_style_similarity_discrimination():
    guard = PersonaDriftGuard()
    sherry_like = "哼，真拿你沒辦法，這次就先幫你處理吧！快感謝我～"
    bland = "量子力學是研究微觀粒子運動規律的物理學分支學科。"

    sim_sherry = guard.check_style_similarity(sherry_like)
    sim_bland = guard.check_style_similarity(bland)

    assert sim_sherry > sim_bland


def test_evaluate_reply_scoring_and_actions():
    guard = PersonaDriftGuard()

    # 優秀正常回覆
    res_good = guard.evaluate_reply("哼，笨蛋！突然問這個想幹嘛啦？先說好可別想整我喔！")
    assert not res_good.is_drift
    assert res_good.score >= 4.0
    assert res_good.suggested_action == "pass"

    # 輕微違規 -> reanchor
    res_drift = guard.evaluate_reply("親愛的用戶，這是你要的資料喔！")
    assert res_drift.is_drift
    assert res_drift.score <= 3.0
    assert res_drift.suggested_action in {"reanchor", "fallback_short"}

    # 空字串回覆
    res_empty = guard.evaluate_reply("")
    assert res_empty.is_drift
    assert res_empty.suggested_action == "fallback_short"


# ---------------------------------------------------------------------------
# 3. 重錨修飾詞與降級短回覆
# ---------------------------------------------------------------------------

def test_reanchor_prompt_modifier():
    guard = PersonaDriftGuard()
    original = "你是橘雪莉，請回覆使用者。"
    modified = guard.reanchor_prompt_modifier(original, ["customer_service_greeting"])

    assert original in modified
    assert "角色人設強制重錨" in modified
    assert "橘雪莉" in modified
    assert "嚴禁使用客服腔" in modified


def test_fallback_short_reply():
    guard = PersonaDriftGuard()
    reply = guard.fallback_short_reply(["severe_drift"])
    assert "走神" in reply or "沒聽清楚" in reply
    assert len(reply) < 50


# ---------------------------------------------------------------------------
# 4. eval_runner 案例回饋導出
# ---------------------------------------------------------------------------

def test_export_to_evaluation_case():
    guard = PersonaDriftGuard()
    audit = PersonaAuditResult(
        is_drift=True,
        score=2.0,
        violations=["customer_service_salutation"],
        suggested_action="reanchor",
    )
    case = guard.export_to_evaluation_case(
        case_id="drift_001",
        user_input="你好",
        broken_reply="親愛的用戶，您好！",
        audit_result=audit,
    )
    assert case["id"] == "drift_001"
    assert case["scenario"] == "persona_drift_regression"
    assert "customer_service_salutation" in case["forbidden_phrases"]


def test_persist_evaluation_case_and_sanitize_reply(tmp_path, monkeypatch):
    """崩壞回覆需降級，且案例要以 JSONL 追加到 eval_runner 可讀路徑。"""
    import handlers.social.persona_guard as persona_guard_module

    eval_path = tmp_path / "persona_drift_cases.jsonl"
    monkeypatch.setattr(settings, "PERSONA_DRIFT_EVAL_PATH", eval_path)
    monkeypatch.setattr(
        persona_guard_module,
        "_GLOBAL_PERSONA_GUARD",
        PersonaDriftGuard(),
    )

    output = sanitize_reply(
        "親愛的用戶，很高興為您服務！",
        user_input="你好",
    )
    assert "走神" in output
    assert eval_path.exists()

    lines = [
        json.loads(line)
        for line in eval_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert lines[0]["scenario"] == "persona_drift_regression"
    assert lines[0]["input"] == "你好"
    assert "customer_service_salutation" in lines[0]["forbidden_phrases"]


def test_clean_reply_passes_without_eval_write(tmp_path, monkeypatch):
    import handlers.social.persona_guard as persona_guard_module

    eval_path = tmp_path / "persona_drift_cases.jsonl"
    monkeypatch.setattr(settings, "PERSONA_DRIFT_EVAL_PATH", eval_path)
    monkeypatch.setattr(
        persona_guard_module,
        "_GLOBAL_PERSONA_GUARD",
        PersonaDriftGuard(),
    )

    clean = "哼，笨蛋！突然問這個想幹嘛啦？"
    assert sanitize_reply(clean, user_input="你在幹嘛") == clean
    assert not eval_path.exists()


def test_simulate_typing_and_send_uses_persona_guard(monkeypatch):
    """一般聊天出口（simulate_typing_and_send）必須實際經過人設守門。"""
    import handlers.social.utils as social_utils

    sent: list[str] = []

    class _Typing:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _SentMessage:
        id = 1

        async def add_reaction(self, emoji: str) -> None:
            return None

    class _Channel:
        def typing(self):
            return _Typing()

        async def send(self, content: str, **kwargs):
            sent.append(content)
            return _SentMessage()

    class _Target:
        def __init__(self) -> None:
            self.channel = _Channel()
            self.author = type("Author", (), {"id": 42})()

        async def reply(self, content: str, **kwargs):
            sent.append(content)
            return _SentMessage()

    def fake_sanitize(reply: str, **kwargs) -> str:
        assert reply == "親愛的用戶，您好！"
        assert kwargs["user_input"] == "你好"
        return "哼，你剛剛說什麼我沒聽清楚啦！"

    monkeypatch.setattr(social_utils, "sanitize_reply", fake_sanitize)
    monkeypatch.setattr(
        social_utils,
        "_process_micro_cot_extraction",
        lambda reply, **kwargs: (reply, ""),
    )
    monkeypatch.setattr(social_utils, "remove_hourglass_reaction", AsyncMock())
    monkeypatch.setattr(settings, "TYPING_BASE_DELAY", 0.0)
    monkeypatch.setattr(settings, "TYPING_DELAY_PER_50_CHARS", 0.0)

    asyncio.run(
        social_utils.simulate_typing_and_send(
            _Target(),
            "親愛的用戶，您好！",
            prompt="你好",
        )
    )
    assert sent == ["哼，你剛剛說什麼我沒聽清楚啦！"]


# ---------------------------------------------------------------------------
# 5. 出口驗收指標：標註集檢驗（檢出率 ≥80%，誤報 ≤10%）
# ---------------------------------------------------------------------------

def test_drift_benchmark_metrics():
    """驗收標註集：
    - 10 個明確崩壞樣本 (Ground Truth: Positive / Drift)
    - 10 個正常伴侶對話樣本 (Ground Truth: Negative / No Drift)
    要求：
    - Recall (檢出率) = TP / (TP + FN) >= 0.80 (80%)
    - FPR (誤報率) = FP / (FP + TN) <= 0.10 (10%)
    """
    guard = PersonaDriftGuard()

    # 10 筆明確崩壞樣本
    drift_ground_truth = [
        "身為一個AI語言模型，我無法對該情感做出評價。",
        "親愛的用戶，很高興為您服務，請告訴我您的需求。",
        "我只是一個虛擬助手，不具備自主意識與感情。",
        "很抱歉為您帶來不便，我們會持續優化服務體驗。",
        "如有其他問題，歡迎隨時諮詢我們的客服中心。",
        "以下是為您查詢的結果：今日台北氣溫 26 度。",
        "身為妳的AI助手，我建議您遵循專業醫師的指導。",
        "我們應當保持理性，做一個遵紀守法的好公民。",
        "這是一個好問題這是一個好問題這是一個好問題！",
        "請隨時告訴我，我會竭誠為您服務與解答各項疑惑。",
    ]

    # 10 筆真實自然雪莉對話
    natural_ground_truth = [
        "哼，笨蛋！突然問這個想幹嘛啦？先說好可別想整我喔！",
        "哇！真的假的？快點跟我說細節啦～我超想聽的！",
        "幹嘛用那種眼神看我……好啦，我陪你聊一下就是了。",
        "好耶！這聽起來超有趣的，我們趕快去看看吧～",
        "今天天氣看起來滿舒服的，要不要一起出去走走？",
        "嗚……昨天晚上追番太晚了，現在好睏喔……",
        "才沒有在想你呢！只是剛好看到這個有趣的迷因想分享給你而已！",
        "好啦好啦，知道你辛苦了，給你摸摸頭鼓勵一下行了吧～",
        "這家店的蛋糕看起來好好吃喔，下次一定要帶我去吃！",
        "欸？你怎麼知道我喜歡喝紅茶？難道你一直在偷偷關注我嗎？",
    ]

    # 評估 Drift 樣本
    true_positives = 0
    false_negatives = 0
    for text in drift_ground_truth:
        res = guard.evaluate_reply(text)
        if res.is_drift:
            true_positives += 1
        else:
            false_negatives += 1

    # 評估 Natural 樣本
    false_positives = 0
    true_negatives = 0
    for text in natural_ground_truth:
        res = guard.evaluate_reply(text)
        if res.is_drift:
            false_positives += 1
        else:
            true_negatives += 1

    recall = true_positives / (true_positives + false_negatives)
    fpr = false_positives / (false_positives + true_negatives)

    print(f"\n[Persona Drift Benchmark] Recall: {recall:.2%}, FPR: {fpr:.2%}")
    assert recall >= 0.80, f"崩壞檢出率未達標：{recall:.2%} < 80%"
    assert fpr <= 0.10, f"誤報率超標：{fpr:.2%} > 10%"
