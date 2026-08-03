import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
import settings

logger = logging.getLogger(__name__)

SAFETY_FOOTER = (
    "\n\n【角色與安全提醒】\n"
    "永遠用妳自己鮮明活潑的方式作出任何不可預期的回應！如不明白便吐槽。\n"
    "嚴禁發送空言論死機。"
)

BOT_MESSAGE_INTENTS = {}

WIKIPEDIA_HEADERS = {
    "User-Agent": settings.DEFAULT_USER_AGENT
}

def append_thinking_log(entry: dict) -> None:
    """[Thin wrapper] 轉寫入 observability.log_event(event='interject')"""
    from handlers.social.observability import log_event
    log_event(
        "interject",
        channel_id=entry.get("channel_id", 0),
        intent=entry.get("trigger", ""),
        extra_score=entry.get("score", 0),
        extra_reasoning=entry.get("reasoning", ""),
        extra_acted=entry.get("acted", False),
        extra_reply=entry.get("reply") or "",
    )

def append_deep_thinking_log(thought_text: str, prompt: str = "", intent: str = "CHAT", full_reply: str = "",
                             model: str = "", system_prompt: str = "", user_prompt: str = "",
                             latency_ms: int = 0) -> None:
    """[Thin wrapper] 轉寫入 observability.log_event(event='chat_reply')"""
    from handlers.social.observability import log_event
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
    """[Thin wrapper] 轉寫入 observability.log_event(event='draw')"""
    from handlers.social.observability import log_event
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
    """[Thin wrapper] 轉寫入 observability.log_event(event='user_debug_report')"""
    from handlers.social.observability import log_event
    log_event(
        "user_debug_report",
        raw_input=text[:400],
    )

# ======================= (解析與記憶抽取核心) =======================

async def fetch_context_script(channel: discord.TextChannel, before_message: discord.Message) -> str:
    raw: list[discord.Message] = []
    async for msg in channel.history(limit=settings.CONTEXT_HISTORY_LIMIT, before=before_message, oldest_first=False):
        if msg.author.bot and msg.author.id != channel.guild.me.id: continue
        raw.append(msg)
    if not raw: return ""
    
    truncated: list[discord.Message] = [raw[0]]
    for i in range(1, len(raw)):
        gap = (raw[i - 1].created_at - raw[i].created_at).total_seconds()
        if gap > settings.CONTEXT_TIME_GAP_SECONDS: break
        truncated.append(raw[i])
    truncated.reverse()
    
    recent_count = 5
    older_msgs = truncated[:-recent_count] if len(truncated) > recent_count else []
    recent_msgs = truncated[-recent_count:]

    # 🔪 找出雪莉「最近 3 則」發言，這些保留全文；更早的才做截斷
    # ✅ [修復] 原本只保留最新 1 則（content[:15]），長對話中雪莉幾乎看不到自己剛說過的話
    # 現在改為最近 3 則全文保留，更早的才進行省略，大幅提升多輪連貫性
    BOT_FULL_PRESERVE_COUNT = 3
    recent_bot_msg_ids: set[int] = set()
    for msg in reversed(truncated):
        if msg.author.id == channel.guild.me.id:
            recent_bot_msg_ids.add(msg.id)
            if len(recent_bot_msg_ids) >= BOT_FULL_PRESERVE_COUNT:
                break

    # 🔪 工具性回覆偵測：如果雪莉自己的發言是由功能模块產生的，直接展示中立佔位符，防止沙盤指令漏入 CHAT 語境
    _TOOL_REPLY_PATTERNS = (
        # MUSIC
        r"(正在播放|播放清單|已加入|已跳過|已暫停|已停止|開始播放|YouTube|時長:|開始破曉魔播！|我收到神秘的電波了|已接收播放指令|找不到這首歌耶|目前魔法留聲機安靜得很呢|碰！💣 太棒了)",
        # DRAW
        r"(畫好了！|名偵探現在來畫圖|畫筆好像壞掉了|請等我一下)",
        # SEARCH / SUMMARY
        r"(搜尋結果|搜尋失敗|網路搜尋|維基百科)",
        # PIXIV
        r"(pixiv|Pixiv|P站|圖片連結)",
    )

    def _is_tool_reply(text: str) -> bool:
        for pat in _TOOL_REPLY_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                return True
        return False

    def _format_msg(msg: discord.Message, is_older: bool) -> str:
        is_self = (msg.author.id == channel.guild.me.id)
        speaker = f"妳自己(雪莉)" if is_self else f"群友 [{msg.author.display_name}]"
        content = msg.clean_content.strip()
        
        if not content:
            if msg.embeds: content = "（給出了圖片或檔案資訊）"
            elif msg.attachments: content = "（給出了實體的圖像物證）"
            else: return ""

        # 🔪 【工具御毒防綺術！】：分辨出功能模块產生的回覆，展示中立佔位符而不是內容
        if is_self and _is_tool_reply(content):
            content = "（妳執行了一個功能指令，細節已略去）"

        # 🔪 【不對稱記憶鬧割術啟動（升級版）】
        elif is_self:
            if msg.id not in recent_bot_msg_ids:
                # 最近 3 則之外 → 截斷防幻覺
                if is_older:
                    content = "（妳在此處發表了回應... 但已被系統掃除，防止妳被自己的幻覺影響）"
                else:
                    # recent 區但非最近 3 則（理論上不發生，保留 fallback）
                    content = content[:20] + " ...(內容已被系統強制截斷)"
            # 最近 3 則 → 保留全文，讓雪莉知道自己剛說過什麼

        return f"   {speaker}：「{content}」"

    lines = []
    if recent_msgs:
        lines.append("🔥 【剛剛的對話場景】(高度關注：這反映了目前的聊天焦點)：")
        for msg in recent_msgs:
            fmt = _format_msg(msg, is_older=False)
            if fmt: lines.append(fmt)

    return "\n".join(lines)




async def fetch_recent_script(channel: discord.TextChannel, limit: int) -> str:
    # 這裡主要是給主動插話 (Interject) 看的，一樣要上閹割手術防幻覺！
    raw: list[discord.Message] = []
    async for msg in channel.history(limit=limit * 2, oldest_first=False):
        if msg.author.bot and msg.author.id != channel.guild.me.id: continue
        raw.append(msg)
        if len(raw) >= limit: break
    raw.reverse()

    last_bot_msg_id = None
    for msg in reversed(raw):
        if msg.author.id == channel.guild.me.id:
            last_bot_msg_id = msg.id
            break

    lines = []
    for msg in raw:
        is_self = (msg.author.id == channel.guild.me.id)
        speaker_role = f"名偵探 {settings.BOT_NAME} (就是妳自己！)" if is_self else f"目標群眾 [{msg.author.display_name}]"
        content = msg.clean_content.strip()
        if not content:
            if msg.embeds: content = "（遞出給出資訊畫卡板神經操作。）"
            else: continue
            
        # 🔪 【不對稱記憶閹割術啟動】
        if is_self and msg.id != last_bot_msg_id:
            content = content[:15] + " ...(系統強制截斷)"

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
    thought_match = re.search(r'(?is)<thought>(.*?)</thought>', reply)
    if thought_match:
        thought_log = thought_match.group(1).strip()
        
    clean_text = re.sub(r'(?is)<thought>.*?</thought>\n*', '', reply).strip()
    clean_text = clean_text.replace("<thought>", "").replace("</thought>", "")
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


async def simulate_typing_and_send(
    target: discord.Message | discord.TextChannel | Any,
    reply: str,
    intent_name: str = "CHAT",
    prompt: str = "",
    model: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    latency_ms: int = 0,
) -> None:
    is_message = isinstance(target, discord.Message) or hasattr(target, "reply")
    channel = target.channel if is_message else target

    if is_message:
        try:
            guild = getattr(target, "guild", None)
            if guild and getattr(guild, "me", None):
                await target.remove_reaction("⏳", guild.me)
        except Exception:
            pass

    final_reply, thought_log = _process_micro_cot_extraction(
        reply, prompt=prompt, intent=intent_name,
        model=model, system_prompt=system_prompt, user_prompt=user_prompt,
        latency_ms=latency_ms,
    )
    if not final_reply or not final_reply.strip():
        final_reply = "（盯——你這是在說甚麼難懂的話呢！）"

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
        chunks = _chunk_message(final_reply, max_length=1900)
        sent_msg = None
        for idx, chunk in enumerate(chunks):
            if idx == 0 and is_message:
                sent_msg = await target.reply(chunk, mention_author=False)
            else:
                current_sent = await channel.send(chunk)
                if idx == 0:
                    sent_msg = current_sent

        if sent_msg:
            BOT_MESSAGE_INTENTS[sent_msg.id] = {"intent": intent_name, "thought": thought_log, "prompt": prompt}
            if len(BOT_MESSAGE_INTENTS) > 1000:
                keys = list(BOT_MESSAGE_INTENTS.keys())
                for k in keys[:-500]: del BOT_MESSAGE_INTENTS[k]
            
            try:
                await sent_msg.add_reaction("🔄")
                await sent_msg.add_reaction("📌")
                await sent_msg.add_reaction("🗑️")
            except Exception:
                pass
    except discord.HTTPException as exc:
        logger.error("utils: failed to send reply: %s (length: %d)", exc, len(final_reply))


async def simulate_typing_and_send_to_channel(
    channel: discord.TextChannel | Any,
    reply: str,
    intent_name: str = "CHAT",
    prompt: str = "",
    model: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
) -> None:
    await simulate_typing_and_send(
        channel,
        reply,
        intent_name=intent_name,
        prompt=prompt,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )