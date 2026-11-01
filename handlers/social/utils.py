import asyncio
import logging
import re
import time
from typing import Any

import discord
import settings
from handlers.social.observability import log_event
from handlers.social.persona_guard import sanitize_reply

logger = logging.getLogger(__name__)

SAFETY_FOOTER = (
    "\n\n【角色與安全提醒】\n"
    "永遠用妳自己鮮明活潑的方式作出任何不可預期的回應！如不明白便吐槽。\n"
    "嚴禁發送空言論死機。"
)

BOT_MESSAGE_INTENTS: dict[int, Any] = {}

_TOOL_REPLY_REGEX = re.compile(
    r"(正在播放|播放清單|已加入|已跳過|已暫停|已停止|開始播放|YouTube|時長:|開始破曉魔播！|我收到神秘的電波了|已接收播放指令|找不到這首歌耶|目前魔法留聲機安靜得很呢|碰！💣 太棒了|"
    r"畫好了！|名偵探現在來畫圖|畫筆好像壞掉了|請等我一下|"
    r"搜尋結果|搜尋失敗|網路搜尋|維基百科|"
    r"pixiv|Pixiv|P站|圖片連結)",
    re.IGNORECASE,
)


def is_bot_super_admin(target: discord.Member | discord.User | discord.Interaction | None) -> bool:
    """判斷目標使用者或 Interaction 是否具備全域機器人超級管理員權限 (Bot SuperAdmin)。

    僅驗證全域 settings.ADMIN_USER_IDS 與 settings.ADMIN_ROLE_IDS。
    不包含單一 Discord 伺服器之 Administrator 或 Server Owner。

    Args:
        target: Discord Interaction、Member 或 User 物件。

    Returns:
        bool: 是否為全域超級管理員。
    """
    if target is None:
        return False

    user: discord.Member | discord.User
    if isinstance(target, discord.Interaction):
        user = target.user
    elif isinstance(target, discord.Member):
        user = target
    else:
        user = target

    user_id = getattr(user, "id", None)
    if user_id is None:
        return False

    # 1. 顯式全域管理員 ID
    admin_uids = getattr(settings, "ADMIN_USER_IDS", [])
    if isinstance(admin_uids, int) and user_id == admin_uids:
        return True
    if isinstance(admin_uids, (list, tuple, set)) and user_id in admin_uids:
        return True

    # 2. 顯式指定之管理身分組 (ADMIN_ROLE_IDS)
    if settings.ADMIN_ROLE_IDS and hasattr(user, "roles"):
        roles = getattr(user, "roles", None)
        if isinstance(roles, (list, tuple, set)):
            return any(getattr(r, "id", None) in settings.ADMIN_ROLE_IDS for r in roles if hasattr(r, "id"))

    return False


def is_admin_user(
    target: discord.Member | discord.User | discord.Interaction | None,
    super_admin_only: bool = False,
) -> bool:
    """判斷目標使用者或 Interaction 是否具備管理員權限。

    相容 discord.Interaction、discord.Member 與 discord.User。
    判定依序：
    1. settings.ADMIN_USER_IDS 明確指定的使用者。
    2. settings.ADMIN_ROLE_IDS 明確指定之管理身分組成員。
    3. （若 super_admin_only=False）伺服器擁有者 (Guild Owner)。
    4. （若 super_admin_only=False）具備 Discord 原生 Administrator 權限之成員。
    遵循安全原則預設拒絕（Fail-Closed）：若皆不符合，判定為無權限。

    Args:
        target: Discord Interaction、Member 或 User 物件。
        super_admin_only: 若為 True，僅允許全域 Bot 超級管理員。

    Returns:
        bool: 是否具備相應之管理員權限。
    """
    if target is None:
        return False

    if is_bot_super_admin(target):
        return True

    if super_admin_only:
        return False

    user: discord.Member | discord.User
    guild: discord.Guild | None = None
    if isinstance(target, discord.Interaction):
        user = target.user
        guild = target.guild
    elif isinstance(target, discord.Member):
        user = target
        guild = target.guild
    else:
        user = target  # type: ignore
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False

    # 3. 伺服器擁有者 (Guild Owner)
    if guild and getattr(guild, "owner_id", None) == user_id:
        return True

    # 4. Discord 原生 Administrator 權限
    perms = getattr(user, "guild_permissions", None)
    if perms and getattr(perms, "administrator", False):
        return True

    return False


def append_thinking_log(entry: dict[str, Any]) -> None:
    """寫入插話評估記錄至統一觀測系統。

    Args:
        entry: 評估日誌內容字典。
    """
    log_event(
        "interject",
        channel_id=entry.get("channel_id", 0),
        intent=entry.get("trigger", ""),
        extra_score=entry.get("score", 0),
        extra_reasoning=entry.get("reasoning", ""),
        extra_acted=entry.get("acted", False),
        extra_reply=entry.get("reply") or "",
    )


def append_deep_thinking_log(
    thought_text: str,
    prompt: str = "",
    intent: str = "CHAT",
    full_reply: str = "",
    model: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    latency_ms: int = 0,
) -> None:
    """寫入深度思考與對話回覆記錄至統一觀測系統。"""
    log_event(
        "chat_reply",
        intent=intent,
        raw_input=prompt,
        cot_thought=thought_text,
        reply_preview=full_reply[:120] if full_reply else "",
        model_used=model,
        latency_ms=latency_ms,
    )


def append_draw_thinking_log(
    user_query: str,
    seed_tags: list[str],
    expanded_prompt: str,
    final_prompt: str,
    negative_prompt: str,
    source: str,
    success: bool,
    model_used: str = "",
) -> None:
    """寫入繪圖推論與標籤擴展記錄至統一觀測系統。"""
    log_event(
        "draw",
        raw_input=user_query,
        intent="DRAW",
        tags_resolved=seed_tags,
        model_used=model_used,
        extra_expanded_prompt=expanded_prompt[:200],
        extra_final_prompt=final_prompt[:200],
        extra_source=source,
        extra_success=success,
    )


def append_debug_log(text: str) -> None:
    """寫入使用者除錯回報記錄至統一觀測系統。"""
    log_event(
        "user_debug_report",
        raw_input=text[:400],
    )

from collections import OrderedDict

def _estimate_tokens(text: str) -> int:
    """中英混合與程式碼文字的 Token 數快速估算。"""
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    non_cjk_count = len(text) - cjk_count
    return cjk_count + int(non_cjk_count * 0.35)


class ContextCompressionCache:
    """具備容量上限 (LRU)、時間 TTL 與最新訊息水位感知之對話快取容器。"""

    def __init__(self, capacity: int = 256, default_ttl: float = 60.0) -> None:
        self._cache: OrderedDict[Any, tuple[float, str, int]] = OrderedDict()
        self._capacity = capacity
        self._default_ttl = default_ttl

    def __contains__(self, key: Any) -> bool:
        return key in self._cache

    def __getitem__(self, key: Any) -> tuple[float, str]:
        val = self._cache[key]
        return val[0], val[1]

    def __setitem__(self, key: Any, value: tuple[float, str] | tuple[float, str, int]) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        if len(value) == 2:
            self._cache[key] = (value[0], value[1], 0)
        else:
            self._cache[key] = (value[0], value[1], value[2])
        if len(self._cache) > self._capacity:
            self._cache.popitem(last=False)

    def set(self, key: Any, last_msg_id: int, summary: str) -> None:
        """提供易讀的 set 介面儲存摘要與水位。"""
        self[key] = (time.time(), summary, last_msg_id)

    def get(self, key: Any, default: Any = None) -> tuple[float, str] | Any:
        if key not in self._cache:
            return default
        self._cache.move_to_end(key)
        val = self._cache[key]
        return val[0], val[1]

    def get_valid_summary(self, key: Any, last_msg_id: int, ttl: float | None = None) -> str | None:
        """驗證 TTL 與最新訊息 ID 水位，最後訊息 ID 吻合且未過期才回傳摘要文字。"""
        if key not in self._cache:
            return None
        ts, summary, cached_last_id = self._cache[key]
        effective_ttl = ttl if ttl is not None else self._default_ttl
        now = time.time()
        if (now - ts > effective_ttl):
            return None
        if cached_last_id and last_msg_id and cached_last_id != last_msg_id:
            # 訊息水位已有新進展，不可直接作為當前全量有效摘要
            # 保留快取條目供水位節流時取出前輪摘要疊加新訊息使用
            return None
        self._cache.move_to_end(key)
        return summary

    def get_raw_summary(self, key: Any, ttl: float | None = None) -> str | None:
        """取得上一輪已快取的濃縮摘要文字（供水位節流疊加新訊息使用）。"""
        if key not in self._cache:
            return None
        ts, summary, _ = self._cache[key]
        effective_ttl = ttl if ttl is not None else (self._default_ttl * 5.0)
        if time.time() - ts > effective_ttl:
            return None
        return summary

    def get_watermark(self, key: Any) -> int:
        """回傳頻道已濃縮到的最新訊息水位（無紀錄則回傳 0）。"""
        val = self._cache.get(key)
        if val is None:
            return 0
        try:
            return int(val[2] or 0)
        except (TypeError, ValueError, IndexError):
            return 0

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


# 對話濃縮的記憶體 LRU + 水位快取
_CONTEXT_COMPRESS_CACHE = ContextCompressionCache(capacity=256, default_ttl=60.0)

# 回覆引用區塊的內容上限：避免引用一則超長公告時吃掉整個 Prompt。
REPLY_QUOTE_MAX_CHARS: int = 500


def _quoted_message_text(msg: Any) -> str:
    """取出被引用訊息的可用文字；沒有純文字時退回 Embed／附件／貼圖描述。"""
    text = getattr(msg, "clean_content", "") or getattr(msg, "content", "")
    text = str(text).strip()
    if text:
        return text

    embeds = getattr(msg, "embeds", None) or []
    if embeds:
        parts: list[str] = []
        for embed in embeds[:2]:
            title = str(getattr(embed, "title", "") or "").strip()
            desc = str(getattr(embed, "description", "") or "").strip()
            joined = "｜".join(p for p in (title, desc) if p)
            if joined:
                parts.append(joined)
        if parts:
            return "（內嵌資訊卡：" + " / ".join(parts) + "）"
        return "（貼了一張資訊卡或圖片）"

    attachments = getattr(msg, "attachments", None) or []
    if attachments:
        names = [str(getattr(a, "filename", "") or "檔案") for a in attachments[:3]]
        return "（附件：" + "、".join(names) + "）"

    stickers = getattr(msg, "stickers", None) or []
    if stickers:
        names = [str(getattr(s, "name", "") or "貼圖") for s in stickers[:3]]
        return "（貼圖：" + "、".join(names) + "）"

    return ""


async def resolve_reply_reference(message: Any) -> Any | None:
    """盡力取得「這則訊息所回覆的原訊息」。

    Discord 的 ``message.reference.resolved`` 只有在事件當下就載入到原訊息
    （或原訊息仍在客戶端快取）時才有值；機器人重啟後、或回覆幾小時前的舊訊息
    時通常是 ``None``。此時必須主動 ``fetch_message`` 補抓，否則呼叫端會誤以為
    「這則訊息沒有在回覆任何人」。

    Args:
        message: 觸發的 Discord 訊息。

    Returns:
        原訊息物件；取不到（無引用、已刪除、權限不足）時回傳 None。
    """
    reference = getattr(message, "reference", None)
    if reference is None:
        return None

    # 轉發（forward）不是回覆，原訊息通常也不存在，直接略過。
    if getattr(getattr(reference, "type", None), "name", "") == "forward":
        return None

    resolved = getattr(reference, "resolved", None)
    if isinstance(resolved, discord.Message):
        return resolved
    if isinstance(resolved, discord.DeletedReferencedMessage):
        # Discord 已告知原訊息被刪除，再打 API 只會拿到 404。
        return None

    message_id = getattr(reference, "message_id", None)
    if message_id is None:
        return None

    channel = getattr(message, "channel", None)
    ref_channel_id = getattr(reference, "channel_id", None)
    channel_id = getattr(channel, "id", None)
    if (
        channel is not None
        and ref_channel_id is not None
        and channel_id is not None
        and str(ref_channel_id) != str(channel_id)
    ):
        # 跨頻道／討論串引用：改從原訊息所在的頻道抓。
        guild = getattr(channel, "guild", None)
        getter = getattr(guild, "get_channel_or_thread", None) if guild is not None else None
        if callable(getter):
            try:
                target = getter(int(ref_channel_id))
            except (TypeError, ValueError):
                target = None
            if target is not None and hasattr(target, "fetch_message"):
                channel = target

    if channel is None or not hasattr(channel, "fetch_message"):
        return None

    try:
        return await channel.fetch_message(message_id)
    except Exception as exc:  # NotFound / Forbidden / HTTPException 一律視為讀不到
        logger.debug("resolve_reply_reference: 補抓引用訊息失敗 (id=%s): %s", message_id, exc)
        return None


async def build_reply_quote_context(
    message: Any,
    *,
    bot_user_id: int | str | None = None,
    max_chars: int = REPLY_QUOTE_MAX_CHARS,
) -> str:
    """把「使用者這則訊息正在回覆哪一則訊息」整理成 LLM 看得懂的引用區塊。

    只依賴 ``message.reference.resolved`` 是不夠的：重啟後或回覆舊訊息時它會是
    ``None``，導致雪莉看不到被引用的內容，只能對「所以誰再買」這類追問瞎猜。
    本函式會在 resolved 失效時主動補抓，並明確告訴模型「現在這句話是在針對那則
    訊息」，讓回覆聚焦在同一個話題上。

    Args:
        message: 觸發的 Discord 訊息。
        bot_user_id: 雪莉自己的 ID，用來標示「被引用的是妳自己說過的話」。
        max_chars: 引用內容的字數上限。

    Returns:
        str: 可直接塞進 Prompt 的引用區塊；這則訊息不是回覆時回傳空字串。
    """
    reference = getattr(message, "reference", None)
    if reference is None:
        return ""

    ref_msg = await resolve_reply_reference(message)
    if ref_msg is None:
        if getattr(reference, "message_id", None) is None:
            return ""
        return (
            "【對方這句是回覆某一則訊息】\n"
            "- 但雪莉讀不到被引用的原訊息（可能已被刪除或權限不足）。\n"
            "→ 請依現有對話脈絡回應；若真的無法判斷對方在指哪一則，就直接反問，不要憑空編造。"
        )

    author = getattr(ref_msg, "author", None)
    author_id = getattr(author, "id", None)
    is_self = bool(
        bot_user_id is not None
        and author_id is not None
        and str(author_id) == str(bot_user_id)
    )
    name = str(
        getattr(author, "display_name", "") or getattr(author, "name", "") or "群友"
    )
    who = "雪莉妳自己" if is_self else name

    content = _quoted_message_text(ref_msg)
    if len(content) > max_chars:
        content = content[:max_chars] + "……（原訊息過長，已截斷）"
    if not content:
        content = "（原訊息沒有可讀的文字內容）"

    lines = [
        "【使用者這句話是「回覆」下面這則訊息】",
        f"- 原訊息作者：{who}",
        f"- 原訊息內容：「{content}」",
    ]
    created_at = getattr(ref_msg, "created_at", None)
    if created_at is not None:
        try:
            lines.append(f"- 原訊息時間：{created_at.astimezone().strftime('%Y-%m-%d %H:%M')}")
        except Exception:
            pass
    if is_self:
        lines.append("→ 對方是在針對妳自己剛才說過的話追問、吐槽或補充，請延續那個話題回答。")
    else:
        lines.append(
            "→ 對方是在針對上面那則訊息追問、吐槽或補充，請把兩者當成同一個話題回答，不要另開新話題。"
        )
    return "\n".join(lines)


async def fetch_context_script(
    channel: Any,
    before_message: discord.Message | None = None,
    bot: Any = None,
) -> str:
    """擷取頻道歷史發言紀錄並格式化為上下文劇本。

    對話歷史採嚴格 Append-Only 格式，保護 Prefix Caching / KV Cache 命中率。
    密集長對話 (> CONTEXT_COMPRESS_THRESHOLD) 會以主力模型濃縮成摘要，
    當水位節流觸發時，取出前輪快取摘要疊加最新訊息，避免回退全量 raw 歷史。
    無論濃縮是否發生，歷史區塊都有硬性封頂（CONTEXT_HISTORY_MAX_TOKENS）兜底。

    Args:
        channel: Discord 頻道物件。
        before_message: 基準訊息（可選）。
        bot: BotBrain / Bot 實例，供濃縮呼叫模型（可選）。

    Returns:
        str: 格式化後的上下文對話紀錄（可能已濃縮）。
    """
    if not hasattr(channel, "history"):
        return ""
    guild = getattr(channel, "guild", None)
    me = getattr(guild, "me", None)
    me_id = getattr(me, "id", None)
    if me_id is None:
        state = getattr(channel, "_state", None)
        user = getattr(state, "user", None)
        if user:
            me_id = getattr(user, "id", None)

    raw: list[discord.Message] = []
    async for msg in channel.history(limit=settings.CONTEXT_HISTORY_LIMIT, before=before_message, oldest_first=False):
        if msg.author.bot and (me_id is None or msg.author.id != me_id):
            continue
        raw.append(msg)
    if not raw:
        return ""

    # ── 段化：只取「最近一個連續活躍段」（時間斷層內才連貫）────────────
    segment: list[discord.Message] = [raw[0]]
    for i in range(1, len(raw)):
        gap = (raw[i - 1].created_at - raw[i].created_at).total_seconds()
        if gap > settings.CONTEXT_TIME_GAP_SECONDS:
            break
        segment.append(raw[i])
    segment.reverse()

    # ── 純函數訊息格式化（Append-Only，單則訊息字串恆定不變）────────────
    def _format_msg(msg: discord.Message) -> str:
        is_self = (me_id is not None and msg.author.id == me_id)
        speaker = "妳自己" if is_self else f"群友 [{msg.author.display_name}]"
        content = msg.clean_content.strip()

        if not content:
            if msg.embeds:
                content = "（給出了圖片或檔案資訊）"
            elif msg.attachments:
                content = "（給出了實體的圖像物證）"
            else:
                return ""

        # 保留「雪莉剛剛做了什麼」的簡短線索，而不是整段抹除。
        # 這樣她才能回答「你剛剛播什麼歌」，同時避免長篇罐頭訊息吃掉 context。
        if is_self and _TOOL_REPLY_REGEX.search(content):
            short = re.sub(r"\s+", " ", content).strip()
            content = f"（妳剛才執行了功能：{short[:60]}）"
        elif len(content) > 300:
            content = content[:300] + " ...(過長截斷)"

        return f"   {speaker}：「{content}」"

    def _render_blocks(msgs: list[discord.Message]) -> str:
        out: list[str] = []
        for msg in msgs:
            fmt = _format_msg(msg)
            if fmt:
                out.append(fmt)
        return "\n".join(out)

    # ── 硬性封頂：歷史區塊 token 預算上限（預設 1500 tokens），優先從最舊發言依序裁切 ──
    def _bounded_render(msgs: list[discord.Message]) -> str:
        budget = int(getattr(settings, "CONTEXT_HISTORY_MAX_TOKENS", 1500))
        cur_msgs = list(msgs)
        text = _render_blocks(cur_msgs)
        while _estimate_tokens(text) > budget and len(cur_msgs) > 1:
            cur_msgs.pop(0)
            text = _render_blocks(cur_msgs)
        return text

    result = _render_blocks(segment)

    # ── 密集長對話 → 主力模型濃縮（摘要 + 尾 N 則原文）────────────────
    threshold = getattr(settings, "CONTEXT_COMPRESS_THRESHOLD", 2000)
    token_threshold = getattr(settings, "CONTEXT_COMPRESS_TOKEN_THRESHOLD", 1200)
    is_dense = (len(result) > threshold) or (_estimate_tokens(result) > token_threshold)
    compressed_result: str | None = None
    if result and bot is not None and is_dense:
        try:
            channel_id = getattr(channel, "id", None)
            last_msg_id = segment[-1].id if segment else 0
            ttl = getattr(settings, "CONTEXT_COMPRESS_CACHE_TTL", 60.0)
            min_gap = int(getattr(settings, "CONTEXT_COMPRESS_MIN_GAP_MESSAGES", 8))
            keep_recent = int(getattr(settings, "CONTEXT_COMPRESS_KEEP_RECENT", 4))

            # 1. 檢查完全相符之有效快取
            cached_summary = _CONTEXT_COMPRESS_CACHE.get_valid_summary(channel_id, last_msg_id, ttl)
            if cached_summary:
                compressed_result = cached_summary
                logger.info("fetch_context_script: 使用有效快取濃縮結果 (%d 字元)", len(compressed_result))
            else:
                # 2. 水位節流判定：距離上次濃縮累積的新訊息不足門檻
                watermark_id = _CONTEXT_COMPRESS_CACHE.get_watermark(channel_id)
                skip_for_now = False
                new_messages: list[discord.Message] = []
                if watermark_id and last_msg_id != watermark_id:
                    new_messages = [m for m in segment if getattr(m, "id", 0) > watermark_id]
                    if len(new_messages) < min_gap:
                        skip_for_now = True

                if skip_for_now:
                    prev_summary = _CONTEXT_COMPRESS_CACHE.get_raw_summary(channel_id)
                    if prev_summary:
                        new_rendered = "\n".join(line for m in new_messages if (line := _format_msg(m)))
                        if new_rendered:
                            compressed_result = f"{prev_summary}\n{new_rendered}"
                        else:
                            compressed_result = prev_summary
                        logger.info(
                            "fetch_context_script: 水位節流，新訊息 %d < %d，重用既有摘要並疊加新訊息",
                            len(new_messages), min_gap,
                        )
                    else:
                        # 快取已失效或遭驅逐，無法重用上一輪摘要，重設 skip_for_now 允許進行全量濃縮
                        skip_for_now = False

                if not skip_for_now and compressed_result is None:
                    compress_before = len(result)
                    temp_card: Any = None
                    try:
                        embed = discord.Embed(
                            title="🧹 對話上下文整理中 (Context Compressing)",
                            description="> 📜 *偵測到歷史對話篇幅較長，雪莉正在提煉關鍵話題記憶，請稍候...*",
                            color=0xFFB6C1,
                        )
                        if before_message and hasattr(before_message, "reply"):
                            temp_card = await before_message.reply(
                                embed=embed,
                                mention_author=False,
                                allowed_mentions=discord.AllowedMentions(replied_user=False),
                            )
                        elif hasattr(channel, "send"):
                            temp_card = await channel.send(
                                embed=embed,
                                allowed_mentions=discord.AllowedMentions(replied_user=False),
                            )
                    except Exception as card_err:
                        logger.debug("fetch_context_script: 發送壓縮提示卡片失敗: %s", card_err)

                    try:
                        summary = await _summarize_context(bot, result)
                        if summary:
                            tail_lines = segment[-keep_recent:] if len(segment) >= keep_recent else segment
                            tail_str = "\n".join(
                                line for m in tail_lines if (line := _format_msg(m))
                            )
                            compressed_result = (
                                f"【過往話題濃縮摘要】{summary}\n"
                                f"（以下為最新對話紀錄，供妳當下精準接話）\n{tail_str}"
                            )
                            _CONTEXT_COMPRESS_CACHE.set(channel_id, last_msg_id, compressed_result)
                            logger.info(
                                "fetch_context_script: 已濃縮 chat history %d -> %d 字元",
                                compress_before, len(compressed_result),
                            )
                    finally:
                        if temp_card is not None and hasattr(temp_card, "delete"):
                            try:
                                await temp_card.delete()
                            except Exception as del_err:
                                logger.debug("fetch_context_script: 刪除壓縮提示卡片失敗: %s", del_err)
        except Exception as exc:
            logger.warning("fetch_context_script: 對話濃縮失敗，回退原樣: %s", exc)

    if compressed_result is None:
        result = _bounded_render(segment)
    else:
        result = compressed_result

    logger.info(
        "fetch_context_script: chat history context %d 字元 (%d 則訊息)",
        len(result), len(segment),
    )
    return result


def _now_ts() -> float:
    """模組層級時間戳輔助。"""
    return time.time()


async def _summarize_context(bot: Any, raw_context: str) -> str:
    """用主力模型把密集對話濃縮成話題摘要（不帶人設，乾淨事實擷取）。

    Args:
        bot: Discord Bot 或 Cog 實例。
        raw_context: 原始對話上下文文字。

    Returns:
        str: 濃縮後的話題摘要字串，若失敗則回傳空字串。
    """
    brain: Any = bot.get_cog("BotBrain") if hasattr(bot, "get_cog") else None
    if brain is None:
        return ""
    try:
        from handlers.bot_brain import InferenceTask

        model = settings.PRIMARY_MODEL
        sys_prompt = (
            "你是一個中立、客觀的對話摘要工具。「不要」扮演任何角色或性格。\n"
            "請把以下群組聊天內容，濃縮成一段精簡的繁體中文『話題摘要』。\n"
            "必須保留：參與者對彼此的稱呼與關係、正在進行的話題主軸、重要公開事實與互相指認的內容。\n"
            "不要加油添醋、不要添加不存在的事實。只輸出摘要本身，不要任何前言或格式。"
        )

        loop = asyncio.get_running_loop()
        done: asyncio.Future[str] = loop.create_future()

        async def _on_reply(reply: str) -> None:
            if not done.done():
                done.set_result(reply.strip())

        task = InferenceTask(
            priority=settings.priority_for(settings.Priority.TOOL_BACKGROUND),
            system_prompt=sys_prompt,
            user_prompt=f"對話：\n{raw_context[:3000]}\n\n請輸出濃縮摘要：",
            callback=_on_reply,
            model=model,
            task_id=f"context-summarize-{int(_now_ts() * 1000)}",
        )
        await brain.enqueue_task(task)
        try:
            await asyncio.wait_for(done, timeout=6.0)
        except asyncio.TimeoutError:
            setattr(task, "is_cancelled", True)
            logger.warning("_summarize_context: 濃縮超時(6s)")
            return ""
        return str(done.result()) if done.done() else ""
    except Exception as exc:
        logger.warning("_summarize_context: 濃縮失敗: %s", exc)
        return ""




async def fetch_recent_script(channel: Any, limit: int) -> str:
    if not hasattr(channel, "history"):
        return ""
    guild = getattr(channel, "guild", None)
    me = getattr(guild, "me", None)
    me_id = getattr(me, "id", None)

    # 這裡主要是給主動插話 (Interject) 看的，一樣要上閹割手術防幻覺！
    raw: list[discord.Message] = []
    async for msg in channel.history(limit=limit * 2, oldest_first=False):
        if msg.author.bot and (me_id is None or msg.author.id != me_id):
            continue
        raw.append(msg)
        if len(raw) >= limit:
            break
    raw.reverse()

    lines = []
    for msg in raw:
        is_self = (me_id is not None and msg.author.id == me_id)
        speaker_role = "妳自己" if is_self else f"目標群眾 [{msg.author.display_name}]"
        content = msg.clean_content.strip()
        if not content:
            if msg.embeds:
                content = "（遞出給出資訊畫卡板神經操作。）"
            else:
                continue

        if is_self and _TOOL_REPLY_REGEX.search(content):
            short = re.sub(r"\s+", " ", content).strip()
            content = f"（妳剛才執行了功能：{short[:60]}）"
        elif len(content) > 300:
            content = content[:300] + " ...(過長截斷)"

        lines.append(f"▶此時，{speaker_role} 的聲音介入：「{content}」")
    return "\n".join(lines)

# ======================= (輸出與發送機制) =======================

def strip_markdown(text: str) -> str:
    """移除模型偷塞的 Markdown 格式化符號，確保在 Discord 中顯示為純文字。"""
    import re
    # 移除程式碼區塊 ```...```
    text = re.sub(r'```[\s\S]*?```', lambda m: m.group(0).strip('`').strip(), text)
    # 移除行內程式碼 `...`
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 移除引用 > 
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # 移除粗體/斜體
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    # 移除劇透 ||...||
    text = re.sub(r'\|\|([^|]+)\|\|', r'\1', text)
    # 移除標題 #
    text = re.sub(r'^#{1,6}\s', '', text, flags=re.MULTILINE)
    return text.strip()

def _process_micro_cot_extraction(reply: str, prompt: str = "", intent: str = "CHAT",
                                  model: str = "", system_prompt: str = "", user_prompt: str = "",
                                  latency_ms: int = 0) -> tuple[str, str]:
    """Returns (clean_text, thought_log)"""
    reply = reply.strip()
    thought_log = ""
    thought_match = re.search(r'(?is)<(?:thought|think)>(.*?)</(?:thought|think)>', reply)
    if thought_match:
        thought_log = thought_match.group(1).strip()
        
    clean_text = re.sub(r'(?is)<(?:thought|think|action)[^>]*>.*?</(?:thought|think|action)>\n*', '', reply).strip()
    clean_text = re.sub(r'</?(?:thought|think|final_answer|action)[^>]*>', '', clean_text)
    if not clean_text: 
        return "（盯——你這是在說甚麼難懂的話呢！）", thought_log
        
    clean_text = strip_markdown(clean_text)
    if not clean_text.strip():
        clean_text = "（盯——你這是在說甚麼難懂的話呢！）"

    # 如果有提供 prompt，代表這是一次完整的互動，將其紀錄為訓練資料
    if prompt:
        append_deep_thinking_log(thought_log, prompt=prompt, intent=intent, full_reply=clean_text,
                                 model=model, system_prompt=system_prompt, user_prompt=user_prompt,
                                 latency_ms=latency_ms)

    return clean_text, thought_log


def _chunk_message(text: str, max_length: int = 1900) -> list[str]:
    """將超長訊息安全拆分為不超過 max_length 的片段，避免 Discord 50035 Invalid Form Body"""
    if len(text) <= max_length:
        return [text]
    chunks = []
    while len(text) > max_length:
        split_idx = text.rfind('\n', 0, max_length)
        if split_idx == -1 or split_idx < max_length // 2:
            for p in ['。', '！', '？', ' ', '.', '!', '?']:
                split_idx = text.rfind(p, 0, max_length)
                if split_idx != -1 and split_idx >= max_length // 2:
                    split_idx += 1
                    break
        if split_idx == -1 or split_idx < max_length // 2:
            split_idx = max_length
        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
    if text:
        chunks.append(text)
    return [c for c in chunks if c]


async def remove_hourglass_reaction(
    target: discord.Message | Any,
    bot_user: discord.User | discord.Member | discord.ClientUser | None = None,
) -> None:
    """安全移除訊息上的 ⏳ 沙漏反應。"""
    if not hasattr(target, "remove_reaction"):
        return
    try:
        me = None
        guild = getattr(target, "guild", None)
        if guild and getattr(guild, "me", None):
            me = guild.me
        elif bot_user:
            me = bot_user

        if me:
            await target.remove_reaction("⏳", me)
        else:
            for reaction in getattr(target, "reactions", []):
                if str(reaction.emoji) == "⏳" and getattr(reaction, "me", False):
                    state = getattr(target, "_state", None)
                    current_user = getattr(state, "user", None) if state else None
                    if current_user:
                        await target.remove_reaction("⏳", current_user)
    except Exception as e:
        logger.debug("utils: failed to remove hourglass reaction: %s", e)


def _extract_pure_thought_summary(raw_thought: str, max_len: int = 180) -> str:
    """從複合思考日誌中萃取純思考心智摘要，濾除已在工具步驟列印之觀測數據。"""
    if not raw_thought:
        return ""
    section_pattern = re.compile(r"【([^】]+)】\s*\n?([\s\S]*?)(?=(?:【[^】]+】|\Z))")
    matches = section_pattern.findall(raw_thought)
    if matches:
        thought_parts: list[str] = []
        for title, content in matches:
            t_clean = title.strip()
            # 排除已獨立作為步驟展示的工具回傳數據
            if any(kw in t_clean for kw in ("工具", "數據", "回傳", "發動", "觀測")):
                continue
            c_clean = content.strip().replace("\n", " ➔ ")
            if c_clean:
                thought_parts.append(c_clean)
        summary = " ➔ ".join(thought_parts) if thought_parts else raw_thought.strip().replace("\n", " ➔ ")
    else:
        summary = raw_thought.strip().replace("\n", " ➔ ")

    if len(summary) > max_len:
        summary = summary[:max_len] + "..."
    return summary


def format_thinking_steps_block(
    thought_log: str = "",
    intent_name: str = "CHAT",
    model: str = "",
    latency_ms: int = 0,
    prompt: str = "",
    *,
    prefill_ms: int | None = None,
    thought_ms: int | None = None,
    tool_steps: list[Any] | None = None,
    generation_ms: int | None = None,
    generation_chars: int | None = None,
) -> str:
    """建構精美步驟化思考推演 Markdown 區塊 (方案 A 模式)。

    Args:
        thought_log: 原始思考日誌 (包含 Plan/Obs/Persona 或純 CoT)。
        intent_name: 意圖標籤名稱。
        model: 使用之模型名稱。
        latency_ms: 總執行耗時（毫秒）。
        prompt: 使用者原始輸入。
        prefill_ms: 首字生成 / 前綴快取運算耗時（毫秒，可選）。
        thought_ms: 心智思考推演耗時（毫秒，可選）。
        tool_steps: 工具調用或執行步驟紀錄列表（可選）。
        generation_ms: 最終對話生成耗時（毫秒，可選）。
        generation_chars: 生成對話總字元數（可選）。

    Returns:
        str: Markdown 引用格式之思考與步驟區塊。
    """
    used_model = model or getattr(settings, "PRIMARY_MODEL", "unknown")
    header_lines = [
        "> 💡 **雪莉思維推演與執行步驟 (Thinking Process)**",
        f"> ⚡ **模型**: `{used_model}` • **總耗時**: `{latency_ms} ms` • **意圖**: `{intent_name}`",
    ]

    steps: list[str] = []
    step_idx = 1
    num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]

    # 若傳入結構化耗時指標 (PREFILL / Thought / Tools / Generation)
    if prefill_ms is not None or thought_ms is not None or tool_steps is not None or generation_ms is not None:
        # 1. 前置步驟 (Plan / Tools / Action / Obs) - 依時序先執行先展示
        if tool_steps:
            for raw_step in tool_steps:
                stype = raw_step[0]
                text = raw_step[1]
                dur = raw_step[2] if len(raw_step) > 2 else None
                num_emoji = num_emojis[min(step_idx - 1, 7)]
                clean_text = text.replace("\n", " ➔ ")
                if len(clean_text) > 150:
                    clean_text = clean_text[:150] + "..."
                dur_str = f"`{dur} ms` ➔ " if dur is not None else ""

                if stype == "plan":
                    steps.append(f"> {num_emoji} 📋 **意圖拆解 [Plan]**：{dur_str}{clean_text}")
                elif stype in ("context", "intent"):
                    steps.append(f"> {num_emoji} 📋 **對話語境與意圖 [Context & Intent]**：{dur_str}{clean_text}")
                elif stype == "memory":
                    steps.append(f"> {num_emoji} 🧠 **長期記憶檢索 [Memory]**：{dur_str}{clean_text}")
                elif stype == "vision":
                    steps.append(f"> {num_emoji} 👁️ **多模態視覺感知 [Vision]**：{dur_str}{clean_text}")
                elif stype in ("action", "obs"):
                    steps.append(f"> {num_emoji} 🛠️ **工具與觀測 [Action & Obs]**：{dur_str}{clean_text}")
                elif stype == "persona":
                    steps.append(f"> {num_emoji} 💭 **角色心智 [Persona]**：{dur_str}{clean_text}")
                else:
                    steps.append(f"> {num_emoji} 🔍 **{stype.upper()}**：{dur_str}{clean_text}")
                step_idx += 1

        # 2. PREFILL 階段
        if prefill_ms is not None:
            steps.append(f"> {num_emojis[min(step_idx - 1, 7)]} ⚡ **PREFILL [首字生成]**：`{prefill_ms} ms` (前綴快取運算完成)")
            step_idx += 1

        # 3. 思考推演 (Thinking)
        clean_thought = _extract_pure_thought_summary(thought_log or "")
        if thought_ms is not None or clean_thought:
            dur_str = f"`{thought_ms} ms`" if thought_ms is not None else ""
            summary_str = f" ➔ {clean_thought}" if clean_thought else ""
            sep = " " if (dur_str and summary_str and not summary_str.startswith(" ")) else ""
            steps.append(f"> {num_emojis[min(step_idx - 1, 7)]} 🧠 **思維鏈推演 [Thinking]**：{dur_str}{sep}{summary_str}".rstrip())
            step_idx += 1

        # 4. 生成回覆 (Generation)
        if generation_ms is not None:
            speed_str = ""
            if generation_chars and generation_chars > 0 and generation_ms > 0:
                speed = generation_chars / (generation_ms / 1000.0)
                speed_str = f" (共 {generation_chars} 字 • {speed:.1f} 字/秒)"
            steps.append(f"> {num_emojis[min(step_idx - 1, 7)]} ✨ **生成回覆 [Generation]**：`{generation_ms} ms`{speed_str}")
        else:
            steps.append(f"> {num_emojis[min(step_idx - 1, 7)]} ✨ **完成輸出 [Done]**：已生成回覆")

        return "\n".join(header_lines + steps)

    # 傳統/向下相容：由 thought_log 解析
    raw_thought = (thought_log or "").strip()
    if raw_thought:
        # 嘗試解析結構化【...】區塊
        section_pattern = re.compile(r"【([^】]+)】\s*\n?([\s\S]*?)(?=(?:【[^】]+】|\Z))")
        matches = section_pattern.findall(raw_thought)

        if matches:
            step_idx = 1
            for title, content in matches:
                title_clean = title.strip()
                content_clean = content.strip()
                if not content_clean:
                    continue

                if len(content_clean) > 200:
                    summary = content_clean[:200].replace("\n", " ➔ ") + "..."
                else:
                    summary = content_clean.replace("\n", " ➔ ")

                num_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"][min(step_idx - 1, 5)]
                if any(k in title_clean for k in ("任務", "排程", "調度", "Plan", "規劃")):
                    steps.append(f"> {num_emoji} 📋 **意圖拆解 [Plan]**：{summary}")
                    step_idx += 1
                elif any(k in title_clean for k in ("工具", "數據", "回傳", "Obs", "觀測")):
                    steps.append(f"> {num_emoji} 🛠️ **工具與觀測 [Action & Obs]**：{summary}")
                    step_idx += 1
                elif any(k in title_clean for k in ("內心", "心智", "角色", "Persona", "思考")):
                    steps.append(f"> {num_emoji} 💭 **角色心智 [Persona]**：{summary}")
                    step_idx += 1
                else:
                    steps.append(f"> {num_emoji} 🔍 **{title_clean}**：{summary}")
                    step_idx += 1

        if not steps:
            # 純 CoT 思維鏈展示
            cot_summary = raw_thought.replace("\n", " ")
            if len(cot_summary) > 250:
                cot_summary = cot_summary[:250] + "..."
            steps.append(f"> 1️⃣ 🧠 **思維鏈推演 [CoT]**：{cot_summary}")
    else:
        steps.append("> 1️⃣ ⚡ **直接生成 [Direct]**：無需額外工具排程，由主力模型直接回應")

    final_step_num = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"][min(len(steps), 5)]
    steps.append(f"> {final_step_num} ✨ **完成輸出 [Done]**：已生成回覆")

    all_lines = header_lines + steps
    return "\n".join(all_lines)


async def simulate_typing_and_send(
    target: Any,
    reply: str,
    intent_name: str = "CHAT",
    prompt: str = "",
    model: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    latency_ms: int = 0,
    thought_log: str = "",
    target_user_id: int | None = None,
) -> None:
    is_message = hasattr(target, "reply")
    channel: Any = getattr(target, "channel", target)

    extracted_reply, extracted_thought = _process_micro_cot_extraction(
        reply, prompt=prompt, intent=intent_name,
        model=model, system_prompt=system_prompt, user_prompt=user_prompt,
        latency_ms=latency_ms,
    )
    final_reply = extracted_reply
    final_thought = thought_log.strip() if thought_log and thought_log.strip() else extracted_thought

    if not final_reply or not final_reply.strip():
        final_reply = "（盯——你這是在說甚麼難懂的話呢！）"

    # R22：所有一般聊天出口共用同一道人設守門；偵測到客服腔／AI 宣告時
    # 以安全短句降級，並將案例寫入 eval_runner 回歸集。
    final_reply = sanitize_reply(
        final_reply,
        user_input=prompt or user_prompt,
    )

    delay = settings.TYPING_BASE_DELAY + (len(final_reply) / 50) * settings.TYPING_DELAY_PER_50_CHARS
    try:
        if hasattr(channel, "typing"):
            async with channel.typing():
                await asyncio.sleep(delay)
        else:
            await asyncio.sleep(delay)
    except Exception as e:
        logger.warning("utils: failed to send typing status: %s", e)
        await asyncio.sleep(delay)

    try:
        sent_msg = None
        if getattr(settings, "DEBUG_DETAIL", False):
            # 方案 A：現代簡約步驟化 Markdown 思考欄
            steps_block = format_thinking_steps_block(
                thought_log=final_thought,
                intent_name=intent_name,
                model=model,
                latency_ms=latency_ms,
                prompt=prompt,
            )
            combined_reply = f"{steps_block}\n\n{final_reply}"
            chunks = _chunk_message(combined_reply, max_length=1900)
            for idx, chunk in enumerate(chunks):
                if idx == 0 and is_message:
                    sent_msg = await target.reply(chunk, mention_author=False)
                else:
                    current_sent = await channel.send(chunk)
                    if idx == 0:
                        sent_msg = current_sent
        else:
            chunks = _chunk_message(final_reply, max_length=1900)
            for idx, chunk in enumerate(chunks):
                if idx == 0 and is_message:
                    sent_msg = await target.reply(chunk, mention_author=False)
                else:
                    current_sent = await channel.send(chunk)
                    if idx == 0:
                        sent_msg = current_sent

        if sent_msg:
            resolved_user_id = target_user_id or getattr(getattr(target, "author", None), "id", None)
            BOT_MESSAGE_INTENTS[sent_msg.id] = {
                "intent": intent_name,
                "thought": final_thought,
                "prompt": prompt,
                "user_id": resolved_user_id,
            }
            if len(BOT_MESSAGE_INTENTS) > 1000:
                keys = list(BOT_MESSAGE_INTENTS.keys())
                for k in keys[:-500]:
                    del BOT_MESSAGE_INTENTS[k]
            
            try:
                await sent_msg.add_reaction("🔄")
                await sent_msg.add_reaction("📌")
                await sent_msg.add_reaction("🗑️")
            except Exception:
                pass
    except discord.HTTPException as exc:
        logger.error("utils: failed to send reply: %s (length: %d)", exc, len(final_reply))
    finally:
        if is_message:
            await remove_hourglass_reaction(target)


async def simulate_typing_and_send_to_channel(
    channel: discord.TextChannel | Any,
    reply: str,
    intent_name: str = "CHAT",
    prompt: str = "",
    model: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    latency_ms: int = 0,
    thought_log: str = "",
    target_user_id: int | None = None,
) -> None:
    await simulate_typing_and_send(
        channel,
        reply,
        intent_name=intent_name,
        prompt=prompt,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        latency_ms=latency_ms,
        thought_log=thought_log,
        target_user_id=target_user_id,
    )