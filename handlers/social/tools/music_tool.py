import asyncio
import discord
from discord.ext import commands
import logging
import re
import json

from typing import Any

import settings
from handlers.bot_brain import InferenceTask
from handlers.social.contracts import ToolResult
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import simulate_typing_and_send

logger = logging.getLogger(__name__)


class MusicTool(BaseTool):
    @property
    def intent_name(self) -> str:
        return "MUSIC"

    @property
    def llm_description(self) -> str:
        return "播放音樂、點歌、播放 YouTube 網址（包含 youtube.com 或 youtu.be 連結）、跳過歌曲、優先播放、查看清單、切歌。"

    @property
    def response_mode(self) -> ToolResponseMode:
        return ToolResponseMode.DIRECT_UI

    def can_handle_by_regex(self, text: str) -> bool:
        # YouTube 網址直接命中
        is_yt = bool(re.search(r"(www\.youtube\.com|youtu\.?be)\/.+", text))
        # 明確音樂操作關鍵字
        is_exact = bool(re.search(
            r"(跳過|跳下一首|卡歌|下一首|列出播放清單|播歌|播放|撥放|點歌|我想聽|"
            r"幫我找.*歌|找.*歌|搜尋.*歌|放.*歌|聽.*歌|"
            r"刪除|刪歌|刪掉|移除|清空|"
            r"第\s*\d+\s*首|播第|撥第|跳到第|優先播|"
            r"快轉|倒退|倒轉|跳到|跳至|時間軸|斷點續播|繼續播|接續播|恢復播放)",
            text
        ))
        return is_yt or is_exact

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> ToolResult:
        """音樂工具核心。

        Returns:
            標準 ToolResult 成果合約。
        """
        silent = bool(kwargs.get("silent", False))
        brain_cog = bot.get_cog("BotBrain")
        handler_cog = bot.get_cog("MusicHandler")
        if not brain_cog or not handler_cog:
            return ToolResult.error("音樂系統未就緒。", is_direct_rendered=True)
        brain: Any = brain_cog
        handler: Any = handler_cog

        guild = message.guild
        if not guild:
            return ToolResult.error("音樂功能僅支援伺服器頻道使用。", is_direct_rendered=True)
        guild_id = guild.id
        voice = getattr(message.author, "voice", None)

        async def _send(msg: str) -> None:
            """統一發送 Discord 訊息給使用者。"""
            await simulate_typing_and_send(message, msg)

        # ──────────────────────────────────────────────────────
        # [BARE URL DIRECT PATH] 僅當使用者輸入「純 YouTube 網址」（無任何文字/指令贅字）時直通播放
        # ──────────────────────────────────────────────────────
        naked_url_match = re.fullmatch(
            r'https?://(?:www\.youtube\.com|youtu\.be)/[^\s>]+', user_query.strip()
        )
        if naked_url_match:
            if not voice or not voice.channel:
                msg = "你這不就鬧空城計嗎...人進去了魔法室(語音房)，再來點歌喔！"
                await _send(msg)
                return ToolResult.error(msg, is_direct_rendered=True)
            real_target = naked_url_match.group(0)
            await handler.headless_play(message.channel, voice.channel, real_target)
            msg = f"我收到神秘的電波了。沒問題...這就發功幫你載入：「{real_target}」！🎵"
            await _send(msg)
            return ToolResult.success(msg, is_direct_rendered=True)

        # ──────────────────────────────────────────────────────
        # LLM 語意深度分析路徑（點歌、時間軸、切歌、斷點續播、清單、閒聊鑑別）
        # ──────────────────────────────────────────────────────
        action_prompt = (
            "你是橘雪莉的音樂語意分析助理。請分析使用者的自然語言訊息，以 JSON 格式回傳音樂操作意圖：\n"
            "支援的 action 類型：\n"
            '1. PLAY: 搜尋或點播歌曲/YouTube網址 -> {"action": "PLAY", "keyword": "歌名或YouTube網址", "time": "起播時間(如 1:50, 20:00, 1分30秒)或空字串"}\n'
            '2. INSERT: 優先插播到清單首位 -> {"action": "INSERT", "keyword": "歌名或YouTube網址", "time": "起播時間或空字串"}\n'
            '3. SKIP: 切歌、換歌、跳過此曲、換下一首、下一首歌 -> {"action": "SKIP"}\n'
            '4. SEEK: 時間軸跳轉、快轉、倒退、跳至指定時間 -> {"action": "SEEK", "time": "目標時間或偏移(如 1:50, 20:00, +30s, -15s, +2分鐘)"}\n'
            '5. RESUME: 斷點續播、接續播放中斷的歌曲 -> {"action": "RESUME"}\n'
            '6. LIST: 查看目前的排隊播放清單 -> {"action": "LIST"}\n'
            '7. JUMP: 跳到清單中指定序號的歌曲 -> {"action": "JUMP", "index": 歌曲數字序號}\n'
            '8. REMOVE: 刪除或移除清單中指定的歌曲 -> {"action": "REMOVE", "keyword": "歌曲序號如 1, 3, 5"}\n'
            '9. CLEAR: 清空整個播放清單 -> {"action": "CLEAR"}\n'
            '10. NONE: 使用者僅是閒聊、評價音樂或詢問問題，並非下達播放控制指令 -> {"action": "NONE"}\n'
            "\n"
            "重要規則：\n"
            "- 嚴格只輸出 JSON 物件，絕對不要附帶任何 markdown 標記、解釋或多餘字元！\n"
            "- keyword 請自動濾除「幫我播」、「請放」、「的部分」、「謝謝」等無關語氣贅詞，保留純歌名或完整的網址。\n"
            "- time 欄位若使用者有指定時間點（如「1:50的部分」、「從20:00開始」、「快轉30秒」），請提取出對應時間；若無則填空字串 \"\"。\n"
            "- 若使用者只是詢問問題或閒聊（例如「你剛剛切的是什麼歌」、「這首歌很好聽」），請務必回傳 {\"action\": \"NONE\"}，避免誤切歌或誤播放。"
        )

        # 異步 Future 收集 _handle_action 執行成果，無論是否為 silent 模式皆能即時響應
        _result_future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()

        async def _handle_action(json_reply: str) -> None:
            action, keyword, time_val, index_val = "PLAY", "", "", None
            try:
                cleaned = json_reply.strip().lstrip("```json").rstrip("```").strip()
                data = json.loads(cleaned)
                action = str(data.get("action", "PLAY")).upper()
                keyword = str(data.get("keyword", "")).strip()
                time_val = str(data.get("time", "")).strip()
                index_val = data.get("index")
            except Exception as exc:
                logger.warning("[MusicTool] LLM 回應 JSON 解析失敗 (%s): %s", exc, json_reply)
                # 容錯 Fallback：若 LLM 輸出非標準 JSON，嘗試從文字提取
                if re.search(r"(跳過|切歌|換下一首|下一首)", user_query):
                    action = "SKIP"
                elif re.search(r"(清空)", user_query):
                    action = "CLEAR"
                elif re.search(r"(查看清單|播放清單)", user_query):
                    action = "LIST"
                elif re.search(r"(斷點續播|繼續播)", user_query):
                    action = "RESUME"
                else:
                    action = "PLAY"
                    keyword = user_query

            async def _reply(msg: str) -> None:
                """在 _handle_action 中統一發送訊息並更新 future。"""
                await simulate_typing_and_send(message, msg)
                if not _result_future.done():
                    _result_future.set_result(msg)

            # ── 1. SKIP (切歌 / 下一首) ───────────────────────
            if action == "SKIP":
                vc = guild.voice_client
                if isinstance(vc, discord.VoiceClient) and (vc.is_playing() or vc.is_paused()):
                    vc.stop()
                    await _reply("收到！直接幫你喀擦！切去下一首囉！⏭️")
                else:
                    await _reply("目前魔法留聲機安靜得很呢，你是不是聽錯了？沒有播歌喔！")
                return

            # ── 2. SEEK (時間軸跳轉 / 快轉 / 倒退) ──────────────
            elif action == "SEEK":
                current = handler.current_song.get(guild_id)
                if not current:
                    await _reply("目前沒有音樂正在播放，沒辦法為你跳轉時間軸喔！")
                    return
                time_token = time_val or user_query
                cur_elapsed = handler.get_elapsed_time(guild_id)
                target_sec = handler.parse_time_to_seconds(time_token, cur_elapsed)
                if target_sec is not None:
                    success = await handler.seek_to(guild, message.channel, target_sec)
                    if success:
                        dur_str = handler.format_duration(target_sec)
                        await _reply(f"⏱️ 收到！名偵探已經幫你把時間軸跳轉到 **{dur_str}** 囉！🎵")
                    else:
                        await _reply("❌ 時間軸跳轉失敗，可能是音訊串流暫時無法重連。")
                else:
                    await _reply(f"❌ 無法識別時間軸格式「{time_token}」，請輸入如 `1:50`、`20:00` 或 `+30s` 喔！")
                return

            # ── 3. RESUME (斷點續播) ───────────────────────────
            elif action == "RESUME":
                cp = await handler.resume_from_checkpoint(guild, message.channel, getattr(voice, "channel", None))
                if cp:
                    song = cp.get("song", {})
                    sec = cp.get("offset_seconds", 0)
                    dur_str = handler.format_duration(sec)
                    await _reply(f"🔄 沒問題！名偵探幫你接續播放《**{song.get('title', '未知')}**》（從 **{dur_str}** 處開始）！🎵")
                else:
                    await _reply("目前查無可恢復的音樂斷點紀錄喔！（或者語音房裡沒有人呢）")
                return

            # ── 4. LIST (查看播放清單) ─────────────────────────
            elif action == "LIST":
                queue = handler.queues.get(guild_id, [])
                current = handler.current_song.get(guild_id)
                if not current and not queue:
                    await _reply("目前魔法音響的紀錄空空如也耶！要不要交給我加首刺激的？")
                    return
                await simulate_typing_and_send(message, "來啦！這就是現在魔法留聲機的排隊清單：🎵")
                await handler.send_queue_embed(message.channel, guild_id)
                if not _result_future.done():
                    _result_future.set_result("已在頻道中發布播放清單 Embed。")
                return

            # ── 5. JUMP (跳到指定第 N 首) ──────────────────────
            elif action == "JUMP":
                idx = int(index_val) if index_val is not None else 1
                queue = handler.queues.get(guild_id, [])
                if not queue:
                    await _reply("佇列裡空空如也，沒有歌可以跳轉喔！")
                    return
                song = handler.jump_to(guild_id, idx)
                if not song:
                    await _reply(f"找不到第 {idx} 首耶！清單目前只有 {len(queue)} 首，確認一下再叫我喔！")
                    return
                vc = guild.voice_client
                if isinstance(vc, discord.VoiceClient) and (vc.is_playing() or vc.is_paused()):
                    vc.stop()
                await _reply(f"🎯 收到！立刻跳到第 {idx} 首：**{song['title']}**！")
                return

            # ── 6. REMOVE (刪除指定歌曲) ───────────────────────
            elif action == "REMOVE":
                queue = handler.queues.get(guild_id, [])
                if not queue:
                    await _reply("哎呀！現在底下的清單根本就是空的阿！你要名偵探憑空變掉什麼？")
                    return

                if str(keyword).strip().lower() in ("all", "全部", "0") or re.search(r"(全部|所有|整份|整個|清空)", user_query):
                    count = handler.clear_queue(guild_id)
                    await _reply(f"碰！💣 太棒了，這可是為名偵探展示破壞技巧的大場面！\n我幫你把清單裡 **{count}** 首歌曲全部清空，丟入虛空啦！")
                    return

                def _parse_remove_targets(keyword_str: str, query_str: str) -> list[int]:
                    combined = f"{keyword_str} {query_str}"
                    result_set: set[int] = set()
                    for m in re.finditer(r'(\d+)\s*[~\-到至]\s*(\d+)', combined):
                        start, end = int(m.group(1)), int(m.group(2))
                        if start > end:
                            start, end = end, start
                        result_set.update(range(start, min(end, 9999) + 1))
                    for n in re.findall(r'\d+', combined):
                        result_set.add(int(n))
                    return sorted(result_set)

                num_list_int = _parse_remove_targets(str(keyword), user_query)
                if not num_list_int:
                    await _reply("要告訴我要用魔法炸掉「哪一個號碼」呀！例如你可以叫我：「刪除1、3、5號」或「刪除1~10」。")
                    return

                indexes_to_purge = sorted(num_list_int, reverse=True)
                removed_titles = []
                for idx in indexes_to_purge:
                    if 1 <= idx <= len(queue):
                        target = queue.pop(idx - 1)
                        removed_titles.append(f"❌ {target['title']}")

                if removed_titles:
                    removed_titles.reverse()
                    text_r = "\n".join(removed_titles[:15])
                    if len(removed_titles) > 15:
                        text_r += f"\n...以及另外 {len(removed_titles)-15} 首全部丟入虛空！"
                    await _reply(f"碰！💣 太棒了，這可是為名偵探展示破壞技巧的大場面！\n我幫你把這堆沒意思的東西丟入虛空啦：\n\n{text_r}")
                else:
                    await _reply("真是的，你的號碼到底是指向甚麼時空座標啊？我可沒在這個頻道的留聲清單看到你的對應目標呀！自己再去清單確認一次！")
                return

            # ── 7. CLEAR (清空清單) ────────────────────────────
            elif action == "CLEAR":
                queue = handler.queues.get(guild_id, [])
                if not queue:
                    await _reply("哎呀！現在底下的清單根本就是空的阿！你要名偵探憑空變掉什麼？")
                    return
                count = handler.clear_queue(guild_id)
                await _reply(f"碰！💣 太棒了，這可是為名偵探展示破壞技巧的大場面！\n我幫你把清單裡 **{count}** 首歌曲全部清空，丟入虛空啦！")
                return

            # ── 8. INSERT (優先插播) ───────────────────────────
            elif action == "INSERT":
                if not voice or not voice.channel:
                    await _reply("你這不就鬧空城計嗎...人進去了魔法室(語音房)，再來點歌喔！")
                    return
                target_song = keyword or user_query
                offset_sec = 0
                if time_val:
                    parsed = handler.parse_time_to_seconds(time_val)
                    if parsed is not None and parsed > 0:
                        offset_sec = parsed
                await handler.headless_play(message.channel, voice.channel, target_song, insert_at_top=True, start_offset=offset_sec)
                offset_text = f"（從 **{handler.format_duration(offset_sec)}** 處起播）" if offset_sec > 0 else ""
                await _reply(f"📌 收到神秘的電波了！沒問題...這就發功幫你優先把「{target_song}」插播到清單最前面{offset_text}！🎵")
                return

            # ── 9. NONE (非播放指令/閒聊) ──────────────────────
            elif action == "NONE":
                await _reply("嗯哼？名偵探有在聽喔，不過這看起來不是播放指令呢！如果要聽歌或操作音樂隨時吩咐我～")
                return

            # ── 10. PLAY (搜尋/點播/播放網址) ──────────────────
            else:
                if not voice or not voice.channel:
                    await _reply("你這不就鬧空城計嗎...人進去了魔法室(語音房)，再來點歌喔！")
                    return
                target_song = keyword or user_query
                offset_sec = 0
                if time_val:
                    parsed = handler.parse_time_to_seconds(time_val)
                    if parsed is not None and parsed > 0:
                        offset_sec = parsed
                await handler.headless_play(message.channel, voice.channel, target_song, start_offset=offset_sec)
                offset_text = f"（從 **{handler.format_duration(offset_sec)}** 處起播）" if offset_sec > 0 else ""
                await _reply(f"我收到神秘的電波了。沒問題...這就發功幫你找出{offset_text}：「{target_song}」！🎵")
                return

            # 確保 future 被解決（_reply 因故未被呼叫時的保險）
            if not _result_future.done():
                _result_future.set_result(None)

        await brain.enqueue_task(InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=action_prompt,
            user_prompt=user_query,
            callback=_handle_action,
            model=settings.LIGHT_MODEL,
            task_id=f"tool-music-act-{message.id}"
        ))

        try:
            res = await asyncio.wait_for(_result_future, timeout=settings.REACT_STEP_TIMEOUT)
            return ToolResult.success(res or "音樂操作已執行。", is_direct_rendered=True)
        except asyncio.TimeoutError:
            return ToolResult.error("音樂操作已送出，但未能及時取得確認結果。", is_direct_rendered=True)