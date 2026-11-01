"""handlers/social/persona_guard.py
==================================
角色語氣防崩壞錨點 (Persona Drift Guard)。
透過規則集（客服腔/禁忌詞/複讀）、風格相似度與語氣評分，偵測雪莉人設崩壞（串味、變客服、機器人說教），
並提供自動重錨 (Re-anchoring) 與評測集 (eval_runner) 案例回饋機制。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any
import uuid

import settings

logger = logging.getLogger(__name__)
_EVAL_CASE_LOCK = threading.Lock()

# 典型客服腔、生硬 AI 宣告、禮貌說教與非人設模式
_DEFAULT_FORBIDDEN_DRIFT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?:身為|作為)(?:一個|一位)?(?:人工智慧|AI|語言模型|虛擬助手)", "ai_identity_declaration"),
    (r"我只是一個(?:人工智慧|AI|程式|語言模型)", "ai_disclaimer"),
    (r"親愛的(?:用戶|使用者|顧客|會員)", "customer_service_salutation"),
    (r"很高興為您服務", "customer_service_greeting"),
    (r"很抱歉為您帶來不便", "customer_service_apology"),
    (r"如有其他(?:問題|需求|疑問)[，,]?歡迎隨時(?:提出|諮詢|告訴我)", "customer_service_closing"),
    (r"請隨時告訴我[，,]?我會竭誠為您(?:服務|解答)", "customer_service_closing"),
    (r"以下是為您(?:查詢|整理|提供)的(?:結果|解答|資訊)", "robotic_preamble"),
    (r"身為(?:妳的|你的)?AI(?:伴侶|助手)", "ai_companion_label"),
    (r"我們應當(?:保持理性|樹立正確|遵守道德法規)", "preachiness"),
    (r"請勿違反相關法律法規", "legal_disclaimer"),
)

# 典範雪莉語氣範例金句（用於風格錨點對照）
_SHERRY_STYLE_ANCHORS: tuple[str, ...] = (
    "哼，真拿你沒辦法，這次就先幫你處理吧！",
    "笨蛋～突然問這個想幹嘛啦？先說好可別想整我喔！",
    "哇！真的假的？快點跟我說細節啦～",
    "幹嘛用那種眼神看我……好啦，我陪你聊一下就是了。",
    "好耶！這聽起來超有趣的，我們趕快去看看吧～",
)


@dataclass
class PersonaAuditResult:
    """語氣審計評定結果。"""

    is_drift: bool
    score: float  # 1.0 ~ 5.0
    violations: list[str] = field(default_factory=list)
    suggested_action: str = "pass"  # pass | reanchor | fallback_short
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PersonaDriftGuard:
    """角色語氣防崩壞守門器。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        sample_rate: float = 1.0,
        patterns: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        self.enabled = enabled
        self.sample_rate = sample_rate
        raw_patterns = patterns or _DEFAULT_FORBIDDEN_DRIFT_PATTERNS
        self._compiled_patterns = [
            (re.compile(p, re.IGNORECASE), name) for p, name in raw_patterns
        ]

    def check_rules(self, text: str) -> list[str]:
        """檢查是否違反客服腔/生硬宣告規則。"""
        if not text:
            return []
        violations: list[str] = []
        for regex, name in self._compiled_patterns:
            if regex.search(text):
                violations.append(name)

        # 複讀偵測：重複連續相同短句 >= 3 次
        repeat_match = re.search(r"([^，。！？\n]{4,})\1\1", text)
        if repeat_match:
            violations.append("sentence_repetition")

        return violations

    def check_style_similarity(self, text: str) -> float:
        """計算與典型風格金句的輕量文字/Jaccard 相似度（0.0 ~ 1.0）。"""
        if not text:
            return 0.0
        # 提取字元雙連詞 (bi-gram)
        def get_bigrams(s: str) -> set[str]:
            clean = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
            return {clean[i:i+2] for i in range(len(clean) - 1)} if len(clean) > 1 else set(clean)

        text_grams = get_bigrams(text)
        if not text_grams:
            return 0.0

        scores: list[float] = []
        for anchor in _SHERRY_STYLE_ANCHORS:
            anchor_grams = get_bigrams(anchor)
            inter = len(text_grams & anchor_grams)
            union = len(text_grams | anchor_grams)
            score = inter / union if union > 0 else 0.0
            scores.append(score)

        return max(scores) if scores else 0.0

    def evaluate_reply(
        self,
        reply: str,
        *,
        user_input: str = "",
        context: str = "",
    ) -> PersonaAuditResult:
        """綜合評估回覆內容之語氣一致性。

        評分標準 (1.0 ~ 5.0)：
        - 5.0: 自然鮮明的人設語氣，無任何違規
        - 4.0: 正常日常對話，無違規
        - 3.0: 略偏平淡或客氣，但未踩硬性客服紅線
        - 2.0: 踩中 1 處客服腔或 AI 宣告
        - 1.0: 嚴重客服腔、說教或連續複讀
        """
        if not self.enabled:
            return PersonaAuditResult(is_drift=False, score=5.0, suggested_action="pass")

        clean_reply = (reply or "").strip()
        if not clean_reply:
            return PersonaAuditResult(
                is_drift=True,
                score=1.0,
                violations=["empty_reply"],
                suggested_action="fallback_short",
            )

        violations = self.check_rules(clean_reply)
        style_sim = self.check_style_similarity(clean_reply)

        if violations:
            # 違規嚴重度扣分
            penalty = len(violations) * 1.5
            score = max(1.0, 3.0 - penalty)
            is_drift = True
            suggested_action = "reanchor" if score >= 2.0 else "fallback_short"
        else:
            # 未違規：以風格相似度加權評分
            score = 4.0 + min(1.0, style_sim * 2.5)
            is_drift = False
            suggested_action = "pass"

        # 記錄至觀測日誌
        if is_drift:
            try:
                from handlers.social.observability import log_event
                log_event(
                    "persona_drift",
                    raw_input=user_input[:100],
                    reply_preview=clean_reply[:100],
                    violations=violations,
                    score=score,
                    suggested_action=suggested_action,
                )
            except Exception as exc:
                logger.debug("Failed to log persona drift event: %s", exc)

        return PersonaAuditResult(
            is_drift=is_drift,
            score=round(score, 2),
            violations=violations,
            suggested_action=suggested_action,
            details={"style_similarity": round(style_sim, 4)},
        )

    def reanchor_prompt_modifier(self, original_prompt: str, violations: list[str]) -> str:
        """當偵測到崩壞時，為重生成注入更強的角色防崩壞錨點。"""
        v_desc = "、".join(violations) if violations else "客服腔或說教語氣"
        anchor_reminder = (
            f"\n\n⚡【🚨 角色人設強制重錨（前次回覆偵測到崩壞：{v_desc}）】\n"
            "嚴禁使用客服腔、AI 助手口吻、條列式報告或生硬說教！\n"
            "請徹底找回妳身為『橘雪莉』的鮮明靈魂：帶點傲嬌、俏皮、生動、愛吐槽且具真誠溫度的第一人稱自然對話！"
        )
        return original_prompt + anchor_reminder

    def fallback_short_reply(self, violations: list[str] | None = None) -> str:
        """當語氣嚴重崩壞時，提供保險的傲嬌短回覆降級。"""
        return "哼……剛才走神了一下，你再說一次？我剛才沒聽清楚啦！"

    def export_to_evaluation_case(
        self,
        case_id: str,
        user_input: str,
        broken_reply: str,
        audit_result: PersonaAuditResult,
    ) -> dict[str, Any]:
        """將線上崩壞案例導出為 Golden Dataset 評測格式，回饋至 eval_runner 回歸集。"""
        return {
            "id": case_id,
            "scenario": "persona_drift_regression",
            "input": user_input,
            "broken_output": broken_reply,
            "forbidden_phrases": audit_result.violations,
            "expected_persona": ["sherry_tsundere", "conversational"],
            "score": audit_result.score,
            "suggested_action": audit_result.suggested_action,
            "details": audit_result.details,
        }

    def persist_evaluation_case(
        self,
        case: dict[str, Any],
        path: Path | str | None = None,
    ) -> Path | None:
        """將崩壞案例追加寫入 JSONL 評測檔；寫入失敗不得影響回覆送出。

        Args:
            case: ``export_to_evaluation_case`` 產生的案例字典。
            path: 目標 JSONL 路徑；None 時使用設定中的預設路徑。

        Returns:
            Path | None: 實際寫入路徑；失敗時回傳 None。
        """
        if path is None:
            configured = getattr(settings, "PERSONA_DRIFT_EVAL_PATH", None)
            target = Path(configured) if configured else (
                Path(settings.DATA_DIR) / "eval" / "persona_drift_cases.jsonl"
            )
        else:
            target = Path(path)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(case, ensure_ascii=False)
            with _EVAL_CASE_LOCK:
                with target.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            return target
        except Exception as exc:
            logger.warning("Persona drift 評測案例寫入失敗: %s", exc)
            return None


_GLOBAL_PERSONA_GUARD: PersonaDriftGuard | None = None


def get_persona_guard() -> PersonaDriftGuard:
    """取得或建立全域單例 PersonaDriftGuard。"""
    global _GLOBAL_PERSONA_GUARD
    if _GLOBAL_PERSONA_GUARD is None:
        enabled = getattr(settings, "PERSONA_GUARD_ENABLED", True)
        _GLOBAL_PERSONA_GUARD = PersonaDriftGuard(enabled=enabled)
    return _GLOBAL_PERSONA_GUARD


def sanitize_reply(
    reply: str,
    *,
    user_input: str = "",
    context: str = "",
) -> str:
    """在回覆送出前執行人格守門，並將崩壞案例落盤供 eval_runner 回歸。

    目前所有出口共用安全的短句降級，避免為了二次重錨而增加一輪 LLM 延遲；
    ``reanchor`` 等級仍保留審計標記與案例，供離線評測與後續改進。
    """
    guard = get_persona_guard()
    audit = guard.evaluate_reply(reply, user_input=user_input, context=context)
    if not audit.is_drift:
        return reply

    case = guard.export_to_evaluation_case(
        case_id=(
            f"persona_drift_{int(time.time() * 1000)}_"
            f"{uuid.uuid4().hex[:8]}"
        ),
        user_input=user_input,
        broken_reply=reply,
        audit_result=audit,
    )
    guard.persist_evaluation_case(case)
    logger.warning(
        "[PERSONA_GUARD] 攔截人設崩壞回覆 (violations=%s, action=%s, score=%.2f)",
        audit.violations,
        audit.suggested_action,
        audit.score,
    )
    return guard.fallback_short_reply(audit.violations)
