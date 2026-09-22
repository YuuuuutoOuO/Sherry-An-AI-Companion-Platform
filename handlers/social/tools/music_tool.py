import asyncio
import discord
from discord.ext import commands
import re
import json

from typing import Any

import settings
from handlers.bot_brain import InferenceTask
from handlers.social.contracts import ToolResult
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import simulate_typing_and_send


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
            r"第\s*\d+\s*首|播第|撥第|跳到第|優先播)",
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
        # [FAST PATH #1] YouTube 網址直接貼入 → 跳過 LLM，強制 PLAY
        # ──────────────────────────────────────────────────────
        url_in_query = re.search(
            r'(https?://(?:www\.youtube\.com|youtu\.be)/[^\s>]+)', user_query
        )
        if url_in_query:
            if not voice or not voice.channel:
                msg = "你這不就鬧空城計嗎...人進去了魔法室(語音房)，再來點歌喔！"
                await _send(msg)
                return ToolResult.error(msg, is_direct_rendered=True)
            real_target = url_in_query.group(1)
            is_insert = bool(re.search(r"(插播|插歌|插一首|優先插播|優先播|下一首播|下一首放)", user_query))
            await handler.headless_play(message.channel, voice.channel, real_target, insert_at_top=is_insert)
            if is_insert:
                msg = f"📌 收到神秘的電波了！沒問題...這就發功幫你優先插播到最前面：「{real_target}」！🎵"
            else:
                msg = f"我收到神秘的電波了。沒問題...這就發功幫你載入：「{real_target}」！🎵"
            await _send(msg)
            return ToolResult.success(msg, is_direct_rendered=True)

        # ──────────────────────────────────────────────────────
        # [FAST PATH #2] 「跳過 / 下一首」→ 不用問 LLM，直接 SKIP
        # ──────────────────────────────────────────────────────
        if re.search(r"(跳過|跳下一首|卡歌|切歌|換下一首|下一首歌?$)", user_query) and not re.search(r"(下一首播|下一首放|下一首唱)", user_query):
            vc = guild.voice_client
            if isinstance(vc, discord.VoiceClient) and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                msg = "收到！直接幫你喀擦！切去下一首囉！⏭️"
            else:
                msg = "目前魔法留聲機安靜得很呢，你是不是聽錯了？沒有播歌喔！"
            await _send(msg)
            return ToolResult.success(msg, is_direct_rendered=True)

        # ──────────────────────────────────────────────────────
        # [FAST PATH #3] 「第N首」→ 不用問 LLM，直接 JUMP_TO
        # ──────────────────────────────────────────────────────
        jump_match = re.search(r"(播第|撥第|跳到第|優先播|第)\s*(\d+)\s*首", user_query)
        if jump_match:
            idx = int(jump_match.group(2))
            queue = handler.queues.get(guild_id, [])
            if not queue:
                msg = "佇列裡空空如也，沒有歌可以跳轉喔！"
                await _send(msg)
                return ToolResult.error(msg, is_direct_rendered=True)
            song = handler.jump_to(guild_id, idx)
            if not song:
                msg = f"找不到第 {idx} 首耶！清單目前只有 {len(queue)} 首，確認一下再叫我喔！"
                await _send(msg)
                return ToolResult.error(msg, is_direct_rendered=True)
            vc = guild.voice_client
            if isinstance(vc, discord.VoiceClient) and (vc.is_playing() or vc.is_paused()):
                vc.stop()  # 觸發 after_playback → _play_next → 播 jump 後的首位
            msg = f"🎯 收到！立刻跳到第 {idx} 首：**{song['title']}**！"
            await _send(msg)
            return ToolResult.success(msg, is_direct_rendered=True)

        # ──────────────────────────────────────────────────────
        # [FAST PATH #4] 「清空 / 刪除全部」→ 不用問 LLM，直接 CLEAR
        # ──────────────────────────────────────────────────────
        if re.search(r"(清空|刪除全部|刪除整個|清空清單|清空佇列|清空歌單|刪除歌單|清空播放清單|刪除播放清單|清掉全部|清除全部|刪掉全部)", user_query):
            queue = handler.queues.get(guild_id, [])
            if not queue:
                msg = "目前播放清單本來就是空的喔，沒有東西可以清空啦！"
                await _send(msg)
                return ToolResult.error(msg, is_direct_rendered=True)
            count = handler.clear_queue(guild_id)
            msg = f"🧹 收到！名偵探已經幫你把播放清單裡的 **{count}** 首歌曲全部清空囉！"
            await _send(msg)
            return ToolResult.success(msg, is_direct_rendered=True)

        # ──────────────────────────────────────────────────────
        # [FAST PATH #5] 「插播 / 優先插播」→ 不用問 LLM，直接 INSERT
        # ──────────────────────────────────────────────────────
        insert_match = re.search(r"(?:插播|插歌|插一首|優先插播|優先播|下一首播|下一首放)\s*[:：]?\s*(.+)", user_query)
        if insert_match:
            if not voice or not voice.channel:
                msg = "你這不就鬧空城計嗎...人進去了魔法室(語音房)，再來點歌喔！"
                await _send(msg)
                return ToolResult.error(msg, is_direct_rendered=True)
            target = insert_match.group(1).strip()
            if target:
                await handler.headless_play(message.channel, voice.channel, target, insert_at_top=True)
                msg = f"📌 沒問題！已經幫你把「**{target}**」優先插播到清單最前面囉！🎵"
                await _send(msg)
                return ToolResult.success(msg, is_direct_rendered=True)

        # ──────────────────────────────────────────────────────
        # [FAST PATH #6] 「查看清單 / 歌單」→ 不用問 LLM，直接 LIST
        # ──────────────────────────────────────────────────────
        if re.search(r"(查看清單|列出播放清單|播放清單|排隊清單|看歌單|列出清單|查看歌單|目前的播放清單|當前播放清單|當前清單|查清單|看清單)", user_query):
            queue = handler.queues.get(guild_id, [])
            current = handler.current_song.get(guild_id)
            if not current and not queue:
                msg = "目前魔法音響的紀錄空空如也耶！要不要交給我加首刺激的？"
                await _send(msg)
                return ToolResult.no_data(msg, is_direct_rendered=True)
            await _send("來啦！這就是現在魔法留聲機的排隊清單：🎵")
            await handler.send_queue_embed(message.channel, guild_id)
            return ToolResult.success("已在頻道中發布播放清單 Embed。", is_direct_rendered=True)

        # ──────────────────────────────────────────────────────
        # LLM 分析路徑（歌名搜尋 / LIST / REMOVE 等模糊指令）
        # ──────────────────────────────────────────────────────
        action_prompt = (
            "分析音樂操作指令，以JSON格式回傳意圖：\n"
            '搜尋或點播歌曲（含歌名/風格） -> {"action": "PLAY", "keyword": "歌名或搜尋詞"}\n'
            '優先插播歌曲 -> {"action": "INSERT", "keyword": "歌名或搜尋詞"}\n'
            '看排隊佇列 -> {"action": "LIST", "keyword": ""}\n'
            '移除或刪除佇列中指定的歌曲編號 -> {"action": "REMOVE", "keyword": "1, 2, 5, 6"}\n'
            '清空整個播放清單 -> {"action": "CLEAR", "keyword": ""}\n'
            "嚴格遵守這五個JSON輸出，必須只包含字典屬性，絕對不要有解釋文字！\n"
            "若遇到指示移除/刪除/去掉時，在keyword提取對方的數字並逗號區分即可"
        )

        # silent 模式：用 future 收集 _handle_action 的回傳值
        _result_future: asyncio.Future[str | None] = asyncio.get_event_loop().create_future()

        async def _handle_action(json_reply: str) -> None:
            action, keyword = "PLAY", "熱門隨機音樂"
            try:
                cleaned = json_reply.strip().lstrip("```json").rstrip("```").strip()
                data = json.loads(cleaned)
                action  = data.get("action", "PLAY")
                keyword = data.get("keyword", "")
            except Exception:
                pass

            async def _reply(msg: str) -> None:
                """在 _handle_action 中統一發送訊息並更新 future。"""
                await simulate_typing_and_send(message, msg)
                if silent and not _result_future.done():
                    _result_future.set_result(msg)

            # ── LIST ───────────────────────────────────────────
            if action == "LIST":
                queue   = handler.queues.get(guild_id, [])
                current = handler.current_song.get(guild_id)
                if not current and not queue:
                    await _reply("目前魔法音響的紀錄空空如也耶！要不要交給我加首刺激的？")
                    return
                await _reply("來啦！這就是現在魔法留聲機的排隊清單：🎵")
                await handler.send_queue_embed(message.channel, guild_id)
                if silent and not _result_future.done():
                    _result_future.set_result("已在頻道中發布播放清單 Embed。")

            # ── REMOVE ─────────────────────────────────────────
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

            # ── CLEAR ──────────────────────────────────────────
            elif action == "CLEAR":
                queue = handler.queues.get(guild_id, [])
                if not queue:
                    await _reply("哎呀！現在底下的清單根本就是空的阿！你要名偵探憑空變掉什麼？")
                    return
                count = handler.clear_queue(guild_id)
                await _reply(f"碰！💣 太棒了，這可是為名偵探展示破壞技巧的大場面！\n我幫你把清單裡 **{count}** 首歌曲全部清空，丟入虛空啦！")

            # ── INSERT ─────────────────────────────────────────
            elif action == "INSERT":
                if not voice or not voice.channel:
                    await _reply("你這不就鬧空城計嗎...人進去了魔法室(語音房)，再來點歌喔！")
                    return
                real_target = str(keyword).strip() or user_query
                if not real_target:
                    await _reply("你想優先聽什麼呀？請告訴名偵探確切的歌名喔！")
                    return
                await handler.headless_play(message.channel, voice.channel, real_target, insert_at_top=True)
                await _reply(f"📌 收到神秘的電波了！沒問題...這就發功幫你優先把「{real_target}」插播到清單最前面！🎵")

            # ── PLAY（歌名搜尋）────────────────────────────────
            else:
                if not voice or not voice.channel:
                    await _reply("你這不就鬧空城計嗎...人進去了魔法室(語音房)，再來點歌喔！")
                    return
                real_target = str(keyword).strip() or user_query
                if not real_target:
                    await _reply("你想聽什麼呀？請告訴名偵探確切的歌名喔！")
                    return
                await handler.headless_play(message.channel, voice.channel, real_target)
                await _reply(f"我收到神秘的電波了。沒問題...這就發功幫你找出：「{real_target}」！🎵")

            # 確保 future 被解決（_reply 因故未被呼叫時的保險）
            if silent and not _result_future.done():
                _result_future.set_result(None)

        await brain.enqueue_task(InferenceTask(
            priority=settings.PRIORITY_TAG_RESPONSE,
            system_prompt=action_prompt,
            user_prompt=user_query,
            callback=_handle_action,
            model=settings.QUICK_MODEL,
            task_id=f"tool-music-act-{message.id}"
        ))

        try:
            res = await asyncio.wait_for(_result_future, timeout=settings.REACT_STEP_TIMEOUT)
            return ToolResult.success(res or "音樂操作已執行。", is_direct_rendered=True)
        except asyncio.TimeoutError:
            return ToolResult.error("音樂操作已送出，但未能及時取得確認結果。", is_direct_rendered=True)