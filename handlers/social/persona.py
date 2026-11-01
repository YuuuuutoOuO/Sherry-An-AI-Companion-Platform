import functools
import logging
from pathlib import Path
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import settings

logger = logging.getLogger(__name__)

_DETAIL_EXPANSION_PATTERN = r"(詳細(說明|解釋|分析|介紹|講|回答)|仔細(說明|分析|解釋)|鉅細靡遺|完整(說明|解釋|分析))"

# 高優先度的 Lorebook 關鍵字 pattern — 這些會被注入在對話歷史的「尾部」(緊貼生成點)，
# 模仿 SillyTavern Author's Note 的「深度注入」效果。
_HIGH_PRIORITY_LORE_PATTERNS = frozenset([
    r"(性暗示|色色|瑟瑟|開車|撩|黃腔|調情|曖昧|色|做愛|摸|胸部?|乳|奶|臀|屁股|下面|裸|脫|舔|插入?|射精|弄壞|發情|想要妳?|肉體|性感|瑟|汁|雞雞|ㄐㄐ|老二|屌|巨根|長短|公分|射出來|中出|無套|內射|高潮|性愛|懷孕|親吻?|嘴|愛愛|小穴)",
    r"(橘雪莉|tachibana.?sher|sherry).{0,10}(的圖|照片|圖片|看|搜尋|找|搜圖|本人|自己)",
    _DETAIL_EXPANSION_PATTERN,
    r"(有誰|是誰|誰啦|舉例|列出|說出來|講出來|條列|點名|證明|名單|沒人|騙人|哪有|誰會|喜歡|愛|討厭|朋友|夥伴|人際|關係|認識|有哪些|甚麼人|什麼人)",
])

# 僅限於當前使用者輸入（即時指令）才觸發的高優先度 pattern，避免歷史紀錄殘留持續觸發
_CURRENT_TURN_ONLY_PATTERNS = frozenset([
    _DETAIL_EXPANSION_PATTERN,
])


@functools.lru_cache(maxsize=16)
def _load_soul_persona_by_name(soul_name: str) -> str:
    """載入指定名稱的 SOUL 靈魂 Markdown 檔案內容。

    Args:
        soul_name: 人設檔案名稱（例如 sherry_default.md）。

    Returns:
        str: 人設文本內容，若讀取失敗則 fallback 至 CORE_PERSONA。
    """
    soul_dir: Path = getattr(settings, "SOUL_DIR", settings.BASE_DIR / "file" / "soul")
    mapped_path = Path(soul_name)
    if not mapped_path.is_absolute():
        soul_file = soul_dir / soul_name
    else:
        soul_file = mapped_path

    if not soul_file.exists() and not soul_name.startswith("manosaba_"):
        fallback_file = soul_dir / f"manosaba_{soul_name}"
        if fallback_file.exists():
            soul_file = fallback_file

    try:
        if soul_file.exists():
            content = soul_file.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception as exc:
        logger.warning("[SOUL] 載入靈魂檔 '%s' 失敗，啟動 CORE_PERSONA fallback: %s", soul_file, exc)

    return getattr(settings, "CORE_PERSONA", "")


def _load_soul_persona(channel_id: int | None = None) -> str:
    """載入當前有效之 SOUL 靈魂 Markdown 檔案，支援頻道映射與全域預設。

    Args:
        channel_id: 可選的頻道 ID。

    Returns:
        str: 角色人設文本。
    """
    if channel_id and hasattr(settings, "CHANNEL_SOUL_MAP") and channel_id in settings.CHANNEL_SOUL_MAP:
        return _load_soul_persona_by_name(settings.CHANNEL_SOUL_MAP[channel_id])

    active_name = getattr(settings, "CURRENT_SOUL_NAME", "sherry_default.md")
    return _load_soul_persona_by_name(active_name)


@functools.lru_cache(maxsize=1)
def _load_common_rules() -> str:
    """載入通用防幻覺與互動鐵則 Markdown 檔案。

    Returns:
        str: 通用互動規則文本。
    """
    rules_file: Path = getattr(
        settings,
        "COMMON_RULES_PATH",
        getattr(settings, "SOUL_DIR", settings.BASE_DIR / "file" / "soul") / "common_rules.md",
    )
    try:
        if rules_file.exists():
            content = rules_file.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception as exc:
        logger.warning("[SOUL] 載入通用規則檔 '%s' 失敗: %s", rules_file, exc)

    return ""


@functools.lru_cache(maxsize=16)
def _compile_lorebook(
    items_tuple: tuple[tuple[str, str], ...]
) -> list[tuple[re.Pattern[str], bool, str]]:
    """預編譯 Lorebook 正則表達式並快取編譯結果。

    Args:
        items_tuple: Lorebook 鍵值對元組。

    Returns:
        list[tuple[re.Pattern[str], bool, str]]: (compiled_regex, is_high_priority, lore_fact) 列表。
    """
    return [
        (re.compile(pattern, re.IGNORECASE), pattern in _HIGH_PRIORITY_LORE_PATTERNS, fact)
        for pattern, fact in items_tuple
    ]


def clear_soul_cache() -> None:
    """清除人設與通用規則快取，供動態熱切換時調用。"""
    _load_soul_persona_by_name.cache_clear()
    _load_common_rules.cache_clear()
    _compile_lorebook.cache_clear()


def build_dynamic_persona(
    current_text: str,
    intent_name: str,
    channel_id: int | None = None,
    server_id: str = "",
    user_query: str = "",
    bot: Any | None = None,
    guild_id: int | str | None = None,
    soul_name: str | None = None,
    *,
    include_memory: bool = True,
) -> tuple[str, str]:
    """動態人格拼裝系統（ST 雙層注入與 SOUL 外部化版）。

    將 SOUL/CORE_PERSONA 與 LOREBOOK 拆分成兩段回傳：
      - head_persona: 放在 Prompt 最前面（背景設定、低優先度世界觀）
      - tail_lore:    放在對話歷史最後面（高優先度鐵則，緊貼生成點，模仿 ST Author's Note）

    Args:
        current_text: 當前對話歷史或輸入文字（用於觸發世界書關鍵字匹配）。
        intent_name: 意圖分類名稱（如 CHAT, STOCK, DRAW 等）。
        channel_id: 當前頻道 ID（用於特定頻道 SOUL 設定載入）。
        server_id: 當前伺服器識別碼（用於過濾跨伺服器世界記憶）。
        user_query: 當前使用者輸入（用於行為記憶的自我指涉判斷）。
        bot: Discord Bot 實例（用於讀取即時情境狀態，可選）。
        guild_id: 當前伺服器 ID（用於讀取音樂播放狀態，可選）。
        soul_name: 指定 SOUL 檔名（沙盒多角色用）；None 代表沿用頻道映射／全域預設。

    Returns:
        tuple[str, str]: (head_persona, tail_lore) 二元組。
    """
    # ── 頭部人設（從 SOUL 檔載入或 fallback 至 CORE_PERSONA + 通用互動規則）──────────────
    # 為了最大化 LLM 前綴快取 (Prefix/KV Cache) 命中率，動態時間標籤改移至尾部 tail_lore，
    # 確保 head_persona 內容在相同頻道與意圖下完全靜態恆定。
    TAIWAN_TZ = timezone(timedelta(hours=8))
    now_tw = datetime.now(TAIWAN_TZ)
    weekday_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    time_block = f"⏰【目前時間】：{now_tw.strftime('%Y-%m-%d %H:%M')}（台灣時間，{weekday_names[now_tw.weekday()]}）"

    if soul_name:
        # 沙盒 NPC：只使用指定的 SOUL，不套用 CHANNEL_SOUL_MAP，避免污染正式群設定。
        soul_text = _load_soul_persona_by_name(str(soul_name))
    else:
        soul_text = _load_soul_persona(channel_id)
    rule_text = _load_common_rules()
    head_blocks: list[str] = [soul_text]
    if rule_text:
        head_blocks.append(rule_text)

    if intent_name == "MUSIC":
        head_blocks.append("🎙️【狀態設定】：現在正在為頻道播放音樂與帶動氣氛，請以妳一貫的性格與口吻陪伴大家聽音樂。")
    elif intent_name in ("SEARCH", "SUMMARY"):
        head_blocks.append("🔎【狀態設定】：正在為大家彙整與解讀查到的重要情報！請以妳的核心人設與性格，條理分明、一針見血地解說核心重點。")
    elif intent_name == "PIXIV":
        head_blocks.append("📸【狀態設定】：正在為大家展示剛找到的精美圖片！請以符合妳性格特質與風格的語氣向大家分享與介紹。")
    elif intent_name == "STOCK":
        head_blocks.append(
            "📈【狀態設定】：現在正在解讀股市與財金數據！\n"
            "請貫徹妳的核心性格與說話風格，用自信清晰且符合角色的口吻報告行情與趨勢分析。"
        )
    elif intent_name == "WEATHER":
        head_blocks.append(
            "⛅【狀態設定】：現在正在為大家播報即時氣象與天氣預報！\n"
            "請用符合妳角色的語氣，自然地告知天氣狀況並適度給予貼心提醒。"
        )
    elif intent_name == "SCHEDULE":
        head_blocks.append(
            "🗓️【狀態設定】：現在正在協助管理與確認排程、鬧鐘或提醒事項！\n"
            "請以符合妳角色的口吻確認時間與內容，條理分明地協助處理。"
        )
    elif intent_name == "DRAW":
        head_blocks.append(
            "🎨【狀態設定】：現在正在接受大家的繪圖與創作委託！\n"
            "請以符合妳角色的語氣回應委託，完成後展示作品，展現個人創作風格。"
        )
    elif intent_name == "CHAT":
        head_blocks.append(
            "💬【狀態設定】：正在與身邊重要的人面對面輕鬆交談！\n"
            "請徹底貫徹妳當前設定的核心人設、性格特質與說話風格。以兩人就在眼前的面對面臨場感，自然流暢地互動交流，多表達情緒與想法，結尾可適時延續話題；嚴禁生硬說教或機械式回覆！"
        )


    # ── head_persona 保持 100% 靜態恆定（僅包含 SOUL 靈魂設定與意圖狀態）───────
    head_persona = "\n\n".join(head_blocks)

    # ── 掃描 LOREBOOK，依優先度分流至動態尾部 (tail_lore) ───────────────
    text_to_scan = current_text.lower()
    target_turn_text = (
        user_query.lower()
        if user_query
        else (current_text.splitlines()[-1].lower() if current_text else "")
    )
    general_lores: list[str] = []
    override_lores: list[str] = []

    lorebook_items = tuple(getattr(settings, "LOREBOOK", {}).items())
    for pattern_obj, is_high_priority, lore_fact in _compile_lorebook(lorebook_items):
        scan_target = target_turn_text if pattern_obj.pattern in _CURRENT_TURN_ONLY_PATTERNS else text_to_scan
        if pattern_obj.search(scan_target):
            if is_high_priority:
                override_lores.append(f"- {lore_fact}")
            else:
                general_lores.append(f"- {lore_fact}")

    # 限制通用世界書條目上限 (Top-3)，避免長尾條目擠佔 context 與破壞前綴結構
    if len(general_lores) > settings.LOREBOOK_MAX_GENERAL_ENTRIES:
        general_lores = general_lores[: settings.LOREBOOK_MAX_GENERAL_ENTRIES]

    # ── 尾部注入（Author's Note 模仿層，緊貼對話歷史末端）──────────
    # 將時間戳記、世界記憶、動態線索與即時人設覆蓋全數置於尾部，
    # 既符合注意力臨近原則，又能確保最前端數千字元的靜態前綴 (head_persona)
    # 永不因對話提及人物或話題而改變，徹底保證 Prefix Caching 100% 命中！
    tail_parts: list[str] = [time_block]

    # 世界記憶注入（世界近期大事，屬動態情境，預算控制在 5~8 筆）
    if include_memory:
        try:
            from handlers.memory.world_memory import get_world_memory_snippet
            world_snippet = get_world_memory_snippet(
                days=3, min_importance=3, server_id=server_id, limit=6
            )
            if world_snippet:
                tail_parts.append(world_snippet)
        except Exception:
            pass    # 世界記憶模組未載入時靜默跳過

    # 雪莉自我專屬記憶注入（日記、生活小劇場、近期看過的文章與個人觀點，尾部注入確保 KV Cache 命中）
    if include_memory:
        try:
            from handlers.memory.self_memory import get_self_memory_snippet
            self_snippet = get_self_memory_snippet(query_text=current_text, limit=4)
            if self_snippet:
                tail_parts.append(self_snippet)
        except Exception as exc:
            logger.debug("[SelfMemory] 動態自我記憶尾段注入略過: %s", exc)

    # 雪莉行為記憶注入（只有使用者主動問「你剛剛／你記得」時才檢索，平常 0 Token）
    if include_memory:
        try:
            from handlers.memory.self_memory import get_action_memory_snippet
            action_snippet = get_action_memory_snippet(
                query_text=user_query or current_text,
                channel_id=channel_id,
            )
            if action_snippet:
                tail_parts.append(action_snippet)
        except Exception as exc:
            logger.debug("[ActionMemory] 動態行為記憶尾段注入略過: %s", exc)

    # 即時情境狀態注入（目前播放的音樂；沒有播放時 0 Token）
    if bot is not None and guild_id not in (None, ""):
        try:
            from handlers.social.live_state import build_live_state_snippet
            live_state = build_live_state_snippet(bot, guild_id)
            if live_state:
                tail_parts.append(live_state)
        except Exception as exc:
            logger.debug("[LiveState] 即時狀態尾段注入略過: %s", exc)

    # 動態情感狀態注入（R21：僅影響尾端動態指引，嚴禁污染靜態前綴，確保 KV Cache 命中）
    if getattr(settings, "EMOTION_ENABLED", True):
        try:
            import math
            from handlers.emotion.service import get_emotion_service
            from handlers.emotion.state import build_emotion_prompt_tail

            emotion_svc = get_emotion_service()
            target_entity_type = "channel" if channel_id else ("guild" if server_id else "global")
            target_entity_id = str(channel_id) if channel_id else (str(server_id) if server_id else "default")
            aff_state = emotion_svc.get_state(target_entity_type, target_entity_id)
            mag = math.sqrt(aff_state.pleasure**2 + aff_state.arousal**2 + aff_state.dominance**2)
            if mag >= 0.25:
                tail_parts.append(build_emotion_prompt_tail(aff_state))
        except Exception as exc:
            logger.debug("[EMOTION] 動態情感尾段注入略過: %s", exc)

    if general_lores:
        tail_parts.append(
            "📖【專屬的秘密線索庫啟動！】:\n"
            "注意！若是涉及探討以下紀錄中的關鍵點或人物；妳回答前必須『依照底下給的事實背景出發與互動』不能自生矛盾與假事實喔：\n"
            + "\n".join(general_lores)
        )

    if override_lores:
        tail_parts.append(
            "⚡【作者備註 / 即時人設覆蓋】\n"
            "（本段置於最近對話之後，確保優先被模型讀取）\n"
            + "\n".join(override_lores)
        )

    tail_lore = "\n\n".join(tail_parts)

    return head_persona, tail_lore


def get_current_time_block() -> str:
    """取得當前台灣時間標籤字串。"""
    TAIWAN_TZ = timezone(timedelta(hours=8))
    now_tw = datetime.now(TAIWAN_TZ)
    weekday_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    return f"⏰【目前時間】：{now_tw.strftime('%Y-%m-%d %H:%M')}（台灣時間，{weekday_names[now_tw.weekday()]}）"
