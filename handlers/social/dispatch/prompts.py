"""
handlers/social/dispatch/prompts.py
===================================
調度引擎專用 Prompt 模板、工具別名映射與動態規則載入。
"""
from __future__ import annotations

import logging
from pathlib import Path
import re

import settings
from handlers.social.tool_specs import TOOL_ALIASES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 共用模板、視覺感官認知與工具別名
# ---------------------------------------------------------------------------

_TOOL_DESC_TEMPLATE = "- **{name}**：{description}"
_USER_MESSAGE_TEMPLATE = "使用者（{author}）說：「{content}」"

VISION_AWARENESS_RULE = (
    "【視覺感官與圖片認知原則】：\n"
    "- 提示詞中的【妳眼前的視覺畫面】是妳自己的眼睛（視覺神經）看到的客觀畫面與情報，絕不是使用者打的文字！\n"
    "- 使用者只是把圖片分享給妳看，並沒有輸入那些落落長的畫面細節與專業分析。\n"
    "- 請以「妳自己親眼看著這張圖片」的第一人稱視角（例如「哇～這張圖...」、「你傳這張圖給我看是...」）"
    "自然表達妳的情緒、吐槽或見解，嚴禁誤以為是對方在描述圖片或對方在寫報告！\n"
)

_LEGACY_VISION_PATTERN = re.compile(
    r"【群友上傳了一張圖片(?:[，,]\s*(?:系統視覺解析結果：(?P<desc>.*?)|但(?P<err>.*?)))?】\s*",
    re.DOTALL,
)


def extract_vision_context(user_query: str, visual_context: str = "") -> tuple[str, str]:
    """從使用者查詢與傳入的視覺上下文提煉純淨的使用者發言與視覺感知內容。

    支援自動解析舊格式（如包含『【群友上傳了一張圖片，系統視覺解析結果：...】』），
    確保無論上游是否已分離，雪莉均能明確區分『自己的眼睛看到的畫面』與『使用者真正說的話』。

    Args:
        user_query: 原始使用者輸入文字（可能混雜舊版視覺標籤）。
        visual_context: 上游明確傳遞的視覺感知描述（若有）。

    Returns:
        tuple[str, str]: (純淨的使用者發言, 視覺感知描述內容)。
    """
    clean_query = user_query
    vis = visual_context.strip()

    m = _LEGACY_VISION_PATTERN.search(clean_query)
    if m:
        if not vis:
            if m.group("desc"):
                vis = m.group("desc").strip()
            elif m.group("err"):
                vis = f"【視覺感官異常】：眼前有圖片，但{m.group('err').strip()}"
            else:
                vis = "【視覺感知】：眼前有一張圖片，妳正注視著畫面。"
        clean_query = _LEGACY_VISION_PATTERN.sub("", clean_query)

    return clean_query.strip(), vis


def build_retrieved_memory_tail(
    query_text: str,
    user_id: int,
    *,
    exclude_text: str = "",
    limit: int | None = None,
) -> str:
    """以混合檢索產生 Prompt「動態尾段」的相關記憶。

    R15：此區塊只會出現在動態情境尾端，不更動靜態系統前綴，因此不破壞既有
    Prompt Cache；與既有 snippet 重複的事實會排除。任何檢索／向量錯誤皆回傳
    空字串，確保回覆流程不受影響。

    Args:
        query_text: 當前使用者輸入，用於關鍵字與語意召回。
        user_id: 發話者 Discord ID。
        exclude_text: 已注入的記憶片段；內容重複者不再重複注入。
        limit: 最多注入條數；``None`` 時讀取 ``MEMORY_RETRIEVAL_TAIL_FACTS``。

    Returns:
        str: 格式化後的動態記憶尾段；無結果時為空字串。
    """
    clean = (query_text or "").strip()
    if not clean or not user_id:
        return ""

    try:
        from handlers.memory import embeddings, retrieval

        max_facts = max(
            1,
            int(
                limit
                if limit is not None
                else getattr(settings, "MEMORY_RETRIEVAL_TAIL_FACTS", 4)
            ),
        )
        query_vector = embeddings.get_cached_query_embedding(clean)
        facts = retrieval.search_facts_for_user(
            user_id,
            clean,
            limit=max_facts,
            query_vector=query_vector,
        )
    except Exception as exc:  # noqa: BLE001 - Prompt 組裝不得因檢索失敗中斷
        logger.debug("[Prompts] 動態記憶尾段檢索失敗，略過: %s", exc)
        return ""

    lines: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        text = fact.fact.strip()
        if not text or text in seen or (exclude_text and text in exclude_text):
            continue
        seen.add(text)
        lines.append(f"- {text}")
    if not lines:
        return ""
    return "【🔎 依當前話題即時檢索的相關長期記憶】\n" + "\n".join(lines)


def build_user_turn_prompt(
    display_name: str,
    user_query: str,
    visual_context: str = "",
    context_header: str = "",
    observations_text: str = "",
) -> str:
    """建構統一的使用者輪次 Prompt，徹底隔絕視覺感知（雪莉的眼睛）與使用者發言。

    Args:
        display_name: 使用者在伺服器中的顯示名稱。
        user_query: 使用者輸入文字（可為空或未打字）。
        visual_context: 視覺神經/視覺模型觀察到的畫面特徵情報。
        context_header: 上下文歷史標頭（若有）。
        observations_text: 工具執行結果回傳文字（若有）。

    Returns:
        str: 格式化後的 User Prompt 文本。
    """
    clean_query, vis = extract_vision_context(user_query, visual_context)

    if vis:
        if clean_query:
            action_desc = f"使用者（{display_name}）向妳出示了上述圖片，並說：「{clean_query}」"
        else:
            action_desc = f"使用者（{display_name}）向妳出示了上述圖片，靜靜等待妳的反應，沒有額外打字。"

        obs_part = f"\n\n{observations_text}" if observations_text else ""

        return (
            f"{context_header}"
            "【妳眼前的視覺畫面（妳親眼看到的景象）】：\n"
            f"{vis}\n\n"
            "【對話當下情境】：\n"
            f"{action_desc}{obs_part}\n\n"
            "⚠️【視覺感官認知準則】：\n"
            f"上方「妳眼前的視覺畫面」是妳的眼睛（視覺神經）親眼觀察到的畫面細節，絕對不是 {display_name} 打的字！\n"
            f"{display_name} 只是把圖片出示給妳看。請以妳親眼看到該畫面的第一人稱視角（例如「哇～這張圖...」、「你傳的這張...」）"
            "自然表達妳的情緒、吐槽或互動，切勿誤認對方在描述圖片或寫報告。"
        )

    obs_part = f"\n\n{observations_text}" if observations_text else ""
    query_text = clean_query if clean_query else "（靜靜看著妳，沒有說話）"
    return f"{context_header}使用者（{display_name}）說：「{query_text}」{obs_part}"


#: 兩種模式共用的工具別名映射表；由 ``tool_specs`` 單一來源衍生。
_TOOL_ALIASES: dict[str, str] = TOOL_ALIASES

# ---------------------------------------------------------------------------
# INTEGRATED 模式 Prompt 模板
# ---------------------------------------------------------------------------

_INTEGRATED_TOOL_TEMPLATE = """
【外部工具自主調用特權】

【⚠️ 輸出模式二選一鐵則（嚴禁 ACTION 與正文對白同時出現！）】：
- 模式 A（調用工具）：若需即時情報、多媒體操作、股市、排程、系統狀態等，只能且必須僅輸出 `<thought>` 與 `<action>` 標籤，嚴禁輸出任何角色台詞、前置鋪墊對白或閒聊！
- 模式 B（日常閒聊/無須工具）：直接輸出符合人設的自然對白，嚴禁輸出任何 `<action>` 標籤！

可用工具：
{tool_descriptions}

核心觸發規則：
- 🔍 知識/時事/名詞/動漫梗/天氣（SEARCH）：調用 SEARCH。⚠️ 【參數提煉規範】：標籤內請填寫經由妳理解對話脈絡後「提煉出的精準檢索關鍵字」（例：承接前文提問或同意查證時提煉為 <action name="SEARCH">2026 美國聯準會 升息 預期</action>，嚴禁原樣複製「好ㄚ」「幫我查」等無效口語）。
- 📈 股票/標的查詢（STOCK）：提取標的代號（例：<action name="STOCK">2330.TW</action>、<action name="STOCK">MU</action>、<action name="STOCK">0050.TW</action>）。⚠️ 【嚴格排除時間與年份】：絕對嚴禁將年份/日期/時間（如 2026、10:00、0050 以外之時間數字）誤當股票代碼！多標的請各自輸出獨立 <action>。
- 🗓️ 排程/提醒（SCHEDULE）：提取時間與事項（例：<action name="SCHEDULE">10分鐘後 提醒出門</action>）。
- 💻 主機與系統狀態（SYSTEM_INSPECT）：詢問硬體負載、卡頓延遲或運行狀態填 status（例：<action name="SYSTEM_INSPECT">status</action>）。
- 🎵 點歌/放音樂：<action name="MUSIC">歌曲名稱</action>
- 📸 搜圖/繪圖：<action name="PIXIV">角色名</action> 或 <action name="DRAW">畫面描述</action>
- 🔗 網址解析：<action name="SUMMARY">https://...</action>
"""

_TOOL_RULES_PATH: Path = getattr(
    settings, "TOOL_RULES_PATH", settings.SOUL_DIR / "tool_rules.md"
)


def _load_tool_rules() -> str:
    """讀取外部工具規則 Markdown。讀取失敗時 fallback 至內建模板。"""
    try:
        if _TOOL_RULES_PATH.exists():
            content = _TOOL_RULES_PATH.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception as exc:
        logger.warning(
            "讀取工具規則檔 %s 失敗，啟用內建模板 fallback：%s", _TOOL_RULES_PATH, exc
        )
    return _INTEGRATED_TOOL_TEMPLATE


# ---------------------------------------------------------------------------
# PARALLEL 模式 Prompt 模板
# ---------------------------------------------------------------------------

_PARALLEL_PLANNER_SYSTEM_TEMPLATE = """[SYSTEM: PARALLEL TASK DISPATCH & CHAT SUPPRESSION ENGINE]
你是一個高效率、極致冷靜的任務排程與對話抑制引擎。你的任務是分析使用者的輸入，拆解為待執行的工具清單，並精準判斷是否需要後續的性格對話回覆。

嚴格鐵則：
1. 絕對禁止輸出任何問候、角色扮演、聊天對話或道德說教。
2. 輸出格式只能是符合 JSON Schema 的純 JSON 物件，嚴禁附加 Markdown 解釋文字。
3. 支援多工並行拆解：若使用者同時要求多個動作，必須在 tasks 陣列中列出所有工具。
4. 當使用者詢問任何專有名詞、作品/歌曲背景、動漫梗或知識概念時（如「什麼是XX」），請務必在 tasks 陣列中分派 SEARCH 工具檢索。

## 可用工具清單（Tools）
{tool_descriptions}

## 輸出 JSON 格式定義
{{
  "thought": "簡要分析使用者意圖與任務拆解思考",
  "tasks": [
    {{"tool": "TOOL_NAME", "input": "給該工具的精確關鍵字或參數"}}
  ],
  "needs_persona_reply": true/false
}}

【needs_persona_reply 判斷準則】：
- 設為 false：所有發動的工具全為自帶UI/媒體播放工具（如 MUSIC, PIXIV, DRAW），且使用者沒有提出心得詢問或聊天要求。
- 設為 true：1.發動了純資料查詢工具（如STOCK,SEARCH）2.使用者明確表達聊天需求 3.純日常閒聊（tasks為空）

## 示範案例

案例 1：
使用者：「請幫我播放莫札特的歌與巴哈的歌」
{{"thought": "兩首音樂各自獨立任務。", "tasks": [{{"tool": "MUSIC", "input": "莫札特"}}, {{"tool": "MUSIC", "input": "巴哈"}}], "needs_persona_reply": false}}

案例 2：
使用者：「找一張雪莉的圖片」
{{"thought": "搜尋圖片任務。", "tasks": [{{"tool": "PIXIV", "input": "橘雪莉"}}], "needs_persona_reply": false}}

案例 3：
使用者：「幫我放首周杰倫的晴天，順便跟我說妳喜不喜歡這首歌」
{{"thought": "播放音樂並詢問喜好。", "tasks": [{{"tool": "MUSIC", "input": "周杰倫 晴天"}}], "needs_persona_reply": true}}

案例 4：
使用者：「查一下 2330 和 2454 的走勢」
{{"thought": "兩檔股票查詢，需轉述。", "tasks": [{{"tool": "STOCK", "input": "2330.TW"}}, {{"tool": "STOCK", "input": "2454.TW"}}], "needs_persona_reply": true}}

案例 5：
使用者：「今天天氣真好，妳在做什麼呢？」
{{"thought": "日常閒聊，無需工具。", "tasks": [], "needs_persona_reply": true}}

案例 6：
使用者：「什麼是春日影？」
{{"thought": "詢問動漫歌曲專有名詞『春日影』，需發動 SEARCH 檢索相關背景情報。", "tasks": [{{"tool": "SEARCH", "input": "春日影"}}], "needs_persona_reply": true}}

案例 7：
使用者：「你目前的系統狀態好嗎？」
{{"thought": "詢問主機與系統運作狀態，需發動 SYSTEM_INSPECT 獲取硬體負載與狀態數據。", "tasks": [{{"tool": "SYSTEM_INSPECT", "input": "status"}}], "needs_persona_reply": true}}
"""

# ---------------------------------------------------------------------------
# REACT 模式 Prompt 模板
# ---------------------------------------------------------------------------

_REACT_SYSTEM_TEMPLATE = """[SYSTEM: TASK PLANNING & TOOL DISPATCH ENGINE]
你是一個冷靜、精準的任務規劃與工具調度引擎。
嚴格鐵則：
1. 絕對禁止輸出任何問候、角色扮演、聊天對話或道德說教。
2. 每次回覆只能輸出指定的 XML 格式，不得輸出多餘文字。
3. 當使用者詢問任何專有名詞、作品/歌曲背景、動漫梗或知識概念時（如「什麼是XX」），請發動 SEARCH 工具檢索。

## 可用工具清單（Tools）
{tool_descriptions}

## 輸出格式

格式一（需要發動工具）：
<thought>簡要分析</thought>
<action>TOOL_NAME</action>
<action_input>給工具的指令</action_input>

格式二（任務完成）：
<thought>確認完成</thought>
<final_answer>準備完成</final_answer>

## 示範案例

案例 1：
使用者：「幫我找一張雪莉的圖，然後 10 分鐘後提醒我出門」
<thought>兩個需求：搜圖 (PIXIV) 與定時提醒 (SCHEDULE)，先執行搜圖。</thought>
<action>PIXIV</action>
<action_input>橘雪莉</action_input>

案例 2：
使用者：「查一下 2330 走勢如何」
<thought>股票技術走勢，發動 STOCK 工具。</thought>
<action>STOCK</action>
<action_input>2330.TW</action_input>

案例 3：
使用者：「今天天氣真好，妳在做什麼呢？」
<thought>日常閒聊，直接發動 CHAT。</thought>
<action>CHAT</action>
<action_input>日常對話</action_input>

案例 4：
使用者：「什麼是春日影？」
<thought>使用者詢問特定專有名詞「春日影」，發動 SEARCH 檢索相關背景資料。</thought>
<action>SEARCH</action>
<action_input>春日影</action_input>

案例 5：
使用者：「你目前的系統狀態好嗎？」
<thought>使用者詢問系統與硬體運行狀態，發動 SYSTEM_INSPECT 獲取真實數據。</thought>
<action>SYSTEM_INSPECT</action>
<action_input>status</action_input>
"""

_OBSERVATION_TEMPLATE = """<observation>
工具 [{tool}] 執行完畢，結果如下：
{result}
</observation>"""

_REACT_CONTINUATION_TEMPLATE = """以下是目前的對話歷史與工具執行記錄。請根據這些資訊繼續推理：

{transcript}

現在輪到你回覆："""
