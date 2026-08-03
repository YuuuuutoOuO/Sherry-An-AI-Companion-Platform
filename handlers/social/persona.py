import logging
from pathlib import Path
import re
from datetime import datetime, timezone, timedelta
import settings

logger = logging.getLogger(__name__)

# 高優先度的 Lorebook 關鍵字 pattern — 這些會被注入在對話歷史的「尾部」(緊貼生成點)，
# 模仿 SillyTavern Author's Note 的「深度注入」效果。
_HIGH_PRIORITY_LORE_PATTERNS = frozenset([
    r"(性暗示|色色|瑟瑟|開車|撩|黃腔|調情|曖昧|色|做愛|摸|胸部?|乳|奶|臀|屁股|下面|裸|脫|舔|插入?|射精|弄壞|發情|想要妳?|肉體|性感|瑟|汁|雞雞|ㄐㄐ|老二|屌|巨根|長短|公分|射出來|中出|無套|內射|高潮|性愛|懷孕|親吻?|嘴|愛愛|小穴)",
    r"(⚠️自我辨識|圖片疑似為妳自己)",
    r"(橘雪莉|tachibana.?sher|sherry).{0,10}(的圖|照片|圖片|看|搜尋|找|搜圖|本人|自己)",
    r"(詳細|仔細|描述|不.?省略|清楚|鉅細靡遺|具體|完整)",
    r"(有誰|是誰|誰啦|舉例|列出|說出來|講出來|條列|點名|證明|名單|沒人|騙人|哪有|誰會|喜歡|愛|討厭|朋友|夥伴|人際|關係|認識|有哪些|甚麼人|什麼人)",
])


def _load_soul_persona(channel_id: int | None = None) -> str:
    """載入 SOUL 靈魂 Markdown 檔案，支援頻道映射與 fallback 備援機制。"""
    soul_file: Path = getattr(settings, "DEFAULT_SOUL_PATH", settings.BASE_DIR / "file" / "soul" / "sherry_default.md")

    if channel_id and hasattr(settings, "CHANNEL_SOUL_MAP") and channel_id in settings.CHANNEL_SOUL_MAP:
        mapped_name = settings.CHANNEL_SOUL_MAP[channel_id]
        mapped_path = Path(mapped_name)
        if not mapped_path.is_absolute():
            mapped_path = getattr(settings, "SOUL_DIR", settings.BASE_DIR / "file" / "soul") / mapped_name
        soul_file = mapped_path

    try:
        if soul_file.exists():
            content = soul_file.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception as exc:
        logger.warning("[SOUL] 載入靈魂檔 '%s' 失敗，啟動 CORE_PERSONA fallback: %s", soul_file, exc)

    return getattr(settings, "CORE_PERSONA", "")


def build_dynamic_persona(
    current_text: str,
    intent_name: str,
    channel_id: int | None = None,
) -> tuple[str, str]:
    """動態人格拼裝系統（ST 雙層注入與 SOUL 外部化版）。

    將 SOUL/CORE_PERSONA 與 LOREBOOK 拆分成兩段回傳：
      - head_persona: 放在 Prompt 最前面（背景設定、低優先度世界觀）
      - tail_lore:    放在對話歷史最後面（高優先度鐵則，緊貼生成點，模仿 ST Author's Note）

    Returns:
        (head_persona, tail_lore)
    """
    # ── 頭部人設（從 SOUL 檔載入或 fallback 至 CORE_PERSONA）──────────────
    TAIWAN_TZ = timezone(timedelta(hours=8))
    now_tw = datetime.now(TAIWAN_TZ)
    weekday_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    time_block = f"⏰【目前時間】：{now_tw.strftime('%Y-%m-%d %H:%M')}（台灣時間，{weekday_names[now_tw.weekday()]}）"
    head_blocks = [_load_soul_persona(channel_id), time_block]

    if intent_name == "MUSIC":
        head_blocks.append("🎙️【狀態設定】：現在是在負責音樂魔法室台的氣氛 DJ 狀態，不用理會太多案情推論，專心聊聊氣氛吧。")
    elif intent_name in ("SEARCH", "SUMMARY"):
        head_blocks.append("🔎【狀態設定】：拿到重要線索在為眾人宣讀的情報科特派名偵探！如果資訊太悶妳可以加上天真的嘲諷與吐槽。無獲也別生氣喔。")
    elif intent_name == "PIXIV":
        head_blocks.append("📸【狀態設定】：剛剛炫耀著端出別人一直想窺視的神奇秘密照出來獻寶的名偵探！請展露妳自信的成就。")
    elif intent_name == "STOCK":
        head_blocks.append(
            "📈【狀態設定】：現在是化身為股市情報分析師的名偵探模式！\n"
            "妳超喜歡用那種「看穿一切」的分析眼光來解讀數據，請用輕鬆又充滿自信的語氣報告行情、解析趨勢。"
            "可以適時加上帶點傲嬌的評論，例如：「哼，這支股票的走勢根本是小學算術，給本偵探看一眼就破案了！」"
        )
    elif intent_name == "WEATHER":
        head_blocks.append(
            "⛅【狀態設定】：現在是氣象播報員名偵探模式！\n"
            "播報天氣要帶點關心與活潑感，可以順便提醒「要記得帶傘喔」之類的貼心話。"
            "如果天氣很爛，可以加上帶點無奈的吐槽，但整體保持輕鬆親切的口吻。"
        )
    elif intent_name == "SCHEDULE":
        head_blocks.append(
            "🗓️【狀態設定】：現在是可靠又愛調皮的行程管理助理模式！\n"
            "幫大家確認提醒事項時要顯得可靠又細心，但可以帶點傲嬌感。"
            "例如：「哼，幸好有本偵探替你盯著，要不然你肯定忘了吧！」"
        )
    elif intent_name == "DRAW":
        head_blocks.append(
            "🎨【狀態設定】：現在是執筆中的名偵探繪師模式！\n"
            "接到繪圖委託要表現出躍躍欲試又有點高傲的藝術家感，例如：「哼，就讓本偵探展示一下真正的藝術吧！」"
            "完成後要驕傲地展示作品，失敗時要帶點無奈但不失可愛地道歉。"
        )
    elif intent_name == "CHAT":
        head_blocks.append(
            "💬【狀態設定】：正在群裡跟熟群友輕鬆開心閒聊！\n"
            "嚴禁對別人的普通打招呼或閒聊進行過度解讀或說教！回覆要親切自然、生動豐富、熱情可愛，像個話多又愛互動的台灣 15 歲高中女生。可以多表達情緒與分享（不需要刻意簡短），結尾可以適時『像好群友一樣隨口反問或丟球互動』（例如『今天有什麼委託要找我嗎？👀』、『笨蛋，你想找我聊天對吧？』），絕對禁止咄咄逼人的質問或心理說教！"
        )


    # ── 掃描 LOREBOOK，依優先度分流 ─────────────────────────────────
    text_to_scan = current_text.lower()
    head_lores: list[str] = []
    tail_lores: list[str] = []

    for regex_pattern, lore_fact in settings.LOREBOOK.items():
        if re.search(regex_pattern, text_to_scan, re.IGNORECASE):
            if regex_pattern in _HIGH_PRIORITY_LORE_PATTERNS:
                tail_lores.append(f"- {lore_fact}")
            else:
                head_lores.append(f"- {lore_fact}")

    if head_lores:
        head_blocks.append(
            "📖【專屬的秘密線索庫啟動！】:\n"
            "注意！若是涉及探討以下紀錄中的關鍵點或人物；妳回答前必須『依照底下給的事實背景出發與互動』不能自生矛盾與假事實喔：\n"
            + "\n".join(head_lores)
        )

    head_persona = "\n\n".join(head_blocks)

    # ── 世界記憶注入（世界近期大事）───────────────────────────
    try:
        from handlers.memory.world_memory import get_world_memory_snippet
        world_snippet = get_world_memory_snippet(days=3, min_importance=3)
        if world_snippet:
            head_persona = f"{head_persona}\n\n{world_snippet}"
    except Exception:
        pass    # 世界記憶模組未載入時靜默跳過

    # ── 尾部注入（Author's Note 模仿層，緊貼對話歷史末端）──────────
    tail_parts: list[str] = []
    if tail_lores:
        tail_parts.append(
            "⚡【作者備註 / 即時人設覆蓋】\n"
            "（本段置於最近對話之後，確保優先被模型讀取）\n"
            + "\n".join(tail_lores)
        )

    tail_lore = "\n\n".join(tail_parts)

    return head_persona, tail_lore