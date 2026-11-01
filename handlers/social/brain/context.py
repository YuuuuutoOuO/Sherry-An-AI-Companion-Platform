"""handlers/social/brain/context.py
=================================
把 Discord 頻道歷史整理成雪莉看得懂的「對話上下文」。

舊版只把最近訊息拼成一段文字，模型看不到三件關鍵的事：
1. 這句是不是在回她（回覆關係）。
2. 她剛剛說過什麼（避免每句都像初次見面）。
3. 現在是別人在聊，還是有人在跟她講話。

這個模組把訊息轉成結構化的 ``ContextMessage``，再提供穩定的純字串渲染。
它會盡量從 Discord 取得歷史；取不到時（測試替身、權限不足、頻道太新）
會退回呼叫端傳入的既有 script，不讓生成流程整段失敗。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 工具型回覆的辨識規則，與 handlers/social/utils.py 保持一致。
_TOOL_REPLY_REGEX = re.compile(
    r"(正在播放|播放清單|已加入|已跳過|已暫停|已停止|開始播放|YouTube|時長:|"
    r"開始破曉魔播！|我收到神秘的電波了|已接收播放指令|找不到這首歌耶|"
    r"目前魔法留聲機安靜得很呢|碰！💣 太棒了|畫好了！|名偵探現在來畫圖|"
    r"畫筆好像壞掉了|請等我一下|搜尋結果|搜尋失敗|網路搜尋|維基百科|"
    r"pixiv|Pixiv|P站|圖片連結)",
    re.IGNORECASE,
)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _content_of(message: Any) -> str:
    """取出訊息文字；沒有文字時給圖片／附件一個可讀的替代描述。"""
    raw = getattr(message, "clean_content", "") or getattr(message, "content", "")
    content = _as_str(raw).strip()
    if content:
        return content
    if getattr(message, "embeds", None):
        return "（貼了一張資訊卡或圖片）"
    if getattr(message, "attachments", None):
        return "（丟了一張圖或檔案）"
    return ""


@dataclass(frozen=True)
class ContextMessage:
    """一則被放進上下文的訊息。"""

    message_id: str
    author_id: str
    author_name: str
    content: str
    created_at: float = 0.0
    is_self: bool = False
    reply_to_id: str = ""
    reply_to_author_id: str = ""
    reply_to_name: str = ""
    reply_to_self: bool = False
    mentioned_self: bool = False

    def to_line(self) -> str:
        """渲染成單行腳本；同一則訊息每次輸出都相同（append-only 友善）。"""
        speaker = "妳自己（雪莉）" if self.is_self else f"群友 [{self.author_name or '群友'}]"
        marker = ""
        if self.reply_to_self and not self.is_self:
            marker = "（回覆妳）"
        elif self.reply_to_name:
            marker = f"（回覆 {self.reply_to_name}）"
        elif self.mentioned_self and not self.is_self:
            marker = "（叫到妳）"
        return f"{speaker}{marker}：「{self.content}」"


@dataclass
class ConversationContext:
    """一次生成所看到的完整上下文。"""

    channel_id: str = ""
    messages: list[ContextMessage] = field(default_factory=list)
    focus: ContextMessage | None = None
    bot_id: str = ""
    bot_name: str = "雪莉"
    silence_minutes: float = 0.0
    raw_script: str = ""
    source: str = "history"

    # ------------------------------------------------------------------
    # 查詢
    # ------------------------------------------------------------------
    @property
    def self_messages(self) -> list[ContextMessage]:
        return [m for m in self.messages if m.is_self]

    @property
    def human_messages(self) -> list[ContextMessage]:
        return [m for m in self.messages if not m.is_self]

    @property
    def participants(self) -> list[str]:
        """最近有講話的人（不含自己），保留出現順序。"""
        seen: list[str] = []
        for msg in self.messages:
            if msg.is_self:
                continue
            name = msg.author_name or msg.author_id
            if name and name not in seen:
                seen.append(name)
        return seen

    @property
    def focus_to_self(self) -> bool:
        if self.focus is None:
            return False
        return self.focus.reply_to_self or self.focus.mentioned_self

    @property
    def last_human_at(self) -> float:
        for msg in reversed(self.messages):
            if not msg.is_self and msg.created_at:
                return msg.created_at
        return 0.0

    @property
    def is_quiet(self) -> bool:
        return self.silence_minutes > 0

    @property
    def topic_hint(self) -> str:
        """最近的具體話題線索；冷場時為空字串。"""
        for msg in reversed(self.human_messages):
            text = msg.content.strip()
            if text and not _TOOL_REPLY_REGEX.search(text):
                return text[:60]
        return ""

    def recent_self_lines(self, limit: int = 3) -> list[str]:
        lines = [m.content.strip() for m in self.self_messages if m.content.strip()]
        if not lines and self.raw_script:
            name_prefix = f"{self.bot_name}:"
            for raw_line in self.raw_script.splitlines():
                raw_line = raw_line.strip()
                if raw_line.startswith(name_prefix):
                    content = raw_line[len(name_prefix):].strip()
                    if content:
                        lines.append(content)
        try:
            keep = int(limit)
        except (TypeError, ValueError):
            keep = 0
        if keep <= 0:
            return []
        return lines[-keep:]

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def render_transcript(self, *, max_lines: int = 14) -> str:
        """渲染最近對話。沒有結構化訊息時退回原始 script。"""
        if not self.messages:
            return self.raw_script.strip()
        items = self.messages[-max(1, int(max_lines)) :]
        return "\n".join(m.to_line() for m in items if m.content.strip())

    def render_focus_block(
        self,
        *,
        to_sherry: bool = False,
        continuation: bool = False,
        addressed_by_name: bool = False,
    ) -> str:
        """描述「現在這則訊息」與雪莉的關係。"""
        if self.focus is None:
            return "（這一輪沒有特定訊息，是妳自己決定要開口）"

        focus = self.focus
        if focus.reply_to_self:
            relation = "對方直接回覆了妳剛才說的話"
        elif focus.mentioned_self or addressed_by_name:
            relation = "對方這句話直接叫到妳"
        elif continuation:
            relation = "這是接著你們剛才那串對話說的，沒有重新叫妳"
        elif to_sherry:
            relation = "對方正在對妳說話"
        else:
            relation = "這是群友之間的對話，沒有直接叫妳"

        return (
            "【現在要回應的訊息】\n"
            f"- 說話的人：{focus.author_name or '群友'}\n"
            f"- 內容：『{focus.content}』\n"
            f"- 關係：{relation}"
        )

    def render_recent_self(self, limit: int = 3) -> str:
        lines = self.recent_self_lines(limit)
        if not lines:
            return ""
        body = "\n".join(f"- {line}" for line in lines)
        return (
            f"【妳最近在頻道說過的話】\n{body}\n"
            "（防跳針約束：嚴禁使用與上一輪前 6~8 個字相同或高度相似的開頭起手式，句首必須換用全新詞彙或表達方式！）"
        )

    def render_participants(self) -> str:
        people = self.participants
        if not people:
            return ""
        return f"【最近在頻道講話的人】\n{('、'.join(people))}"

    def render_silence(self) -> str:
        if self.silence_minutes >= 1:
            return f"（頻道已經安靜了約 {int(self.silence_minutes)} 分鐘）"
        return "（頻道現在有人在講話）"

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "focus": self.focus.to_line() if self.focus else "",
            "focus_to_self": self.focus_to_self,
            "participants": self.participants,
            "silence_minutes": self.silence_minutes,
            "source": self.source,
            "message_count": len(self.messages),
            "transcript": self.render_transcript(),
        }

    # ------------------------------------------------------------------
    # 建構
    # ------------------------------------------------------------------
    @classmethod
    def from_script(
        cls,
        script: str,
        *,
        channel_id: int | str = "",
        focus_message: Any = None,
        bot_id: int | str = "",
        bot_name: str = "雪莉",
        silence_minutes: float = 0.0,
    ) -> "ConversationContext":
        """只有現成 script 時使用（例如窺屏評分）；不額外打 Discord API。"""
        ctx = cls(
            channel_id=_as_str(channel_id),
            raw_script=_as_str(script),
            bot_id=_as_str(bot_id),
            bot_name=bot_name or "雪莉",
            silence_minutes=max(0.0, float(silence_minutes or 0.0)),
            source="script",
        )
        if focus_message is not None:
            ctx.focus = _make_context_message(
                focus_message,
                bot_id=ctx.bot_id,
                bot_name=ctx.bot_name,
            )
        return ctx


def _make_context_message(
    message: Any,
    *,
    bot_id: str = "",
    bot_name: str = "雪莉",
    by_id: dict[str, ContextMessage] | None = None,
) -> ContextMessage:
    """把 discord.Message 轉成 ContextMessage；抓不到 reference 也不炸。"""
    author = getattr(message, "author", None)
    author_id = _as_str(getattr(author, "id", ""))
    author_name = _as_str(
        getattr(author, "display_name", "") or getattr(author, "name", "")
    )
    is_self = bool(bot_id and author_id == bot_id)

    content = _content_of(message)
    if is_self and content and _TOOL_REPLY_REGEX.search(content):
        content = f"（妳剛才執行了功能：{content[:60]}）"

    reply_to_id = ""
    reply_to_author_id = ""
    reply_to_name = ""
    reply_to_self = False
    reference = getattr(message, "reference", None)
    if reference is not None:
        reply_to_id = _as_str(
            getattr(reference, "message_id", "") or getattr(reference, "id", "")
        )
        resolved = getattr(reference, "resolved", None)
        if resolved is not None:
            ref_author = getattr(resolved, "author", None)
            reply_to_author_id = _as_str(getattr(ref_author, "id", ""))
            reply_to_name = _as_str(
                getattr(ref_author, "display_name", "") or getattr(ref_author, "name", "")
            )
            reply_to_self = bool(bot_id and reply_to_author_id == bot_id)
        if not reply_to_self and reply_to_id and by_id:
            target = by_id.get(reply_to_id)
            if target is not None:
                reply_to_author_id = reply_to_author_id or target.author_id
                reply_to_name = reply_to_name or target.author_name
                reply_to_self = bool(bot_id and target.author_id == bot_id)

    mentioned_self = False
    mentions = getattr(message, "mentions", None) or []
    for mentioned in mentions:
        if bot_id and _as_str(getattr(mentioned, "id", "")) == bot_id:
            mentioned_self = True
            break
    if not mentioned_self and content:
        lowered = content.lower()
        mentioned_self = "雪莉" in content or "sherry" in lowered

    created_at = 0.0
    raw_created = getattr(message, "created_at", None)
    if raw_created is not None:
        try:
            # 測試替身或非 datetime 物件可能沒有真實 timestamp；一律降級成 0.0。
            created_at = float(raw_created.timestamp())
        except Exception:
            created_at = 0.0

    return ContextMessage(
        message_id=_as_str(getattr(message, "id", "")),
        author_id=author_id,
        author_name=author_name,
        content=content,
        created_at=created_at,
        is_self=is_self,
        reply_to_id=reply_to_id,
        reply_to_author_id=reply_to_author_id,
        reply_to_name=reply_to_name,
        reply_to_self=reply_to_self,
        mentioned_self=mentioned_self,
    )


async def _collect_messages(
    channel: Any,
    *,
    limit: int,
    bot_id: str,
    bot_name: str,
) -> list[ContextMessage]:
    """盡力從頻道歷史收集訊息；任何錯誤都回傳空清單。"""
    history = getattr(channel, "history", None)
    if history is None:
        return []

    raw: list[Any] = []
    try:
        async for msg in history(limit=max(2, int(limit) * 2), oldest_first=False):
            author = getattr(msg, "author", None)
            # 其他機器人的訊息一律跳過；自己的訊息保留。
            if getattr(author, "bot", False) and not (
                bot_id and _as_str(getattr(author, "id", "")) == bot_id
            ):
                continue
            raw.append(msg)
            if len(raw) >= max(1, int(limit)):
                break
    except Exception as exc:
        logger.debug("[BrainContext] 讀取頻道歷史失敗: %s", exc)
        return []

    raw.reverse()
    messages: list[ContextMessage] = []
    by_id: dict[str, ContextMessage] = {}
    for msg in raw:
        item = _make_context_message(msg, bot_id=bot_id, bot_name=bot_name, by_id=by_id)
        messages.append(item)
        if item.message_id:
            by_id[item.message_id] = item
    return messages


def _resolve_bot_id(channel: Any, bot: Any) -> str:
    """依序從 bot、channel.guild.me、channel._state 找機器人 ID。"""
    user = getattr(bot, "user", None) if bot is not None else None
    bot_id = _as_str(getattr(user, "id", ""))
    if bot_id:
        return bot_id
    guild = getattr(channel, "guild", None)
    me = getattr(guild, "me", None)
    bot_id = _as_str(getattr(me, "id", ""))
    if bot_id:
        return bot_id
    state = getattr(channel, "_state", None)
    user = getattr(state, "user", None)
    return _as_str(getattr(user, "id", ""))


def _silence_minutes(messages: list[ContextMessage]) -> float:
    if not messages:
        return 0.0
    last = messages[-1].created_at
    if not last:
        return 0.0
    return max(0.0, (time.time() - last) / 60.0)


async def build_conversation_context(
    channel: Any,
    *,
    focus_message: Any = None,
    bot: Any = None,
    limit: int = 12,
    bot_name: str = "雪莉",
    fallback_script: str = "",
) -> ConversationContext:
    """建立一次生成所需的上下文。

    Args:
        channel: Discord 頻道物件。
        focus_message: 這一輪要回應的訊息（可選）。
        bot: Bot 實例，用來辨識「妳自己」。
        limit: 最多保留幾則訊息。
        bot_name: 顯示用的名字。
        fallback_script: 歷史讀不到時沿用的既有 script。

    Returns:
        ``ConversationContext``；即使什麼都讀不到也會是合法物件。
    """
    bot_id = _resolve_bot_id(channel, bot)
    channel_id = _as_str(getattr(channel, "id", ""))

    messages = await _collect_messages(
        channel,
        limit=limit,
        bot_id=bot_id,
        bot_name=bot_name,
    )

    if not messages and not fallback_script:
        # 取不到結構化歷史（權限、測試替身、頻道太新）時，退回既有的純文字腳本。
        try:
            from handlers.social.utils import fetch_recent_script

            fallback_script = await fetch_recent_script(channel, limit)
        except Exception as exc:
            logger.debug("[BrainContext] 退回 fetch_recent_script 失敗: %s", exc)
            fallback_script = ""

    ctx = ConversationContext(
        channel_id=channel_id,
        messages=messages,
        bot_id=bot_id,
        bot_name=bot_name,
        silence_minutes=_silence_minutes(messages),
        raw_script=fallback_script if not messages else "",
        source="history" if messages else "fallback",
    )

    if focus_message is not None:
        by_id = {m.message_id: m for m in messages if m.message_id}
        focus = _make_context_message(
            focus_message,
            bot_id=bot_id,
            bot_name=bot_name,
            by_id=by_id,
        )
        # focus 可能不在歷史（例如剛送達）；一律補到最後，確保模型看得到。
        if not any(m.message_id and m.message_id == focus.message_id for m in messages):
            ctx.messages.append(focus)
        ctx.focus = focus

    return ctx
