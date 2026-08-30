"""
handlers.roast.utils
====================
Utility functions for cleaning and safely extracting roast replies.

Provides text sanitization and safe fallback extraction to prevent
raw prompt instructions or condition tags from leaking to users.
"""

from __future__ import annotations

import re
from typing import Final

DEFAULT_FALLBACK_ROASTS: Final[list[str]] = [
    "說什麼傻話呢！本名偵探都看不下去了～",
    "哼，真是個無可救藥的大笨蛋～",
    "你在說什麼奇怪的話啊，真拿你沒辦法！",
]


def clean_roast_reply(raw_text: str) -> str:
    """清理 LLM 生成的吐槽文字，移除思考標籤、人名前綴、外層引號與殘留指令標籤。

    Args:
        raw_text: LLM 生成的原始文字。

    Returns:
        清理後乾淨生動的台詞字串。
    """
    if not raw_text:
        return ""

    cleaned = raw_text.strip()

    # 1. 移除 <thought>...</thought> 或 <think>...</think> 標籤
    cleaned = re.sub(r"<thought>.*?</thought>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    # 2. 移除最外層或前綴之角色名（如「雪莉：」、「橘雪莉：」）
    cleaned = re.sub(r"^(橘雪莉|雪莉|Sherry)\s*[:：]\s*", "", cleaned, flags=re.IGNORECASE).strip()

    # 3. 移除前後引號與常見包裹符號
    cleaned = cleaned.strip('"\'`「」『』()（）[]【】').strip()

    # 4. 移除行內可能殘留的 [若...:...] 或 [提示:...] 方括號指令
    cleaned = re.sub(r"\[(若|提示|條件|說明)[^\]]*?\]", "", cleaned).strip()

    return cleaned


def extract_fallback_roast(reply_guide: str, user_content: str = "") -> str:
    """當 LLM 推論超時或失敗時，安全提取 reply_guide 中的主要台詞並去除 Prompt 指令標籤。

    若 reply_guide 包含條件標籤（例如 [若提及雪莉:我才不是你麻麻]），會依據使用者發言自動判定
    匹配條件，或提取主要對白；若整個 reply_guide 均為 Prompt 指令且無口語文字，則使用預設名偵探台詞。

    Args:
        reply_guide: 後台設定的吐槽指引或範例台詞。
        user_content: 觸發此吐槽的使用者發言文字。

    Returns:
        安全且不含任何 Prompt 指令標籤的對白字串。
    """
    if not reply_guide:
        return DEFAULT_FALLBACK_ROASTS[0]

    guide = reply_guide.strip()
    user_lower = (user_content or "").lower()

    # 1. 檢查是否有條件式分支，例如 [若提及雪莉:我才不是你麻麻] 或 [若提到XXX:YYY]
    condition_matches = list(re.finditer(r"\[若(?:提及|提到|包含)?\s*([^:：\]]+)\s*[:：]\s*([^\]]+)\]", guide))
    matched_branch_reply = ""
    for match in condition_matches:
        target_keyword = match.group(1).strip().lower()
        branch_content = match.group(2).strip()
        if target_keyword in user_lower:
            matched_branch_reply = branch_content
            break

    if matched_branch_reply:
        cleaned_branch = clean_roast_reply(matched_branch_reply)
        if cleaned_branch:
            return cleaned_branch

    # 2. 移除所有方括號、圓括號條件/提示標籤（例如 [若提及雪莉:...]、(請用傲嬌語氣...)）
    text_without_brackets = re.sub(r"\[[^\]]*?\]", "", guide)
    text_without_brackets = re.sub(r"（(?:請|注意|提示|若)[^）]*?）", "", text_without_brackets)
    text_without_brackets = re.sub(r"\((?:請|注意|提示|若)[^\)]*?\)", "", text_without_brackets)
    text_without_brackets = re.sub(r"【(?:請|注意|提示|若)[^】]*?】", "", text_without_brackets)

    cleaned = clean_roast_reply(text_without_brackets)

    # 3. 檢查清理後的字串是否為純粹的指令敘述（例如「請用銳利的語氣吐槽」）
    instruction_patterns = [
        r"^請(?:用|以|根據|參考).+吐槽",
        r"^吐槽對方",
        r"^反駁對方",
    ]
    if any(re.search(pat, cleaned) for pat in instruction_patterns) or len(cleaned) < 2:
        return DEFAULT_FALLBACK_ROASTS[0]

    return cleaned
