"""
handlers/social/dispatch/integrated.py
======================================
單階段整合式自主調度策略（IntegratedStrategy）。
雪莉主力調度模式：一次推論內嵌 <action> 標籤，純對話直接回覆，多工具並行發動。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import discord
from discord.ext import commands

import settings
from handlers.social.contracts import ToolCategory
from handlers.social.dispatch.base import BaseDispatchStrategy
from handlers.social.dispatch.models import PlannedTask, _extract_xml_tag, _parse_actions
from handlers.social.dispatch.prompts import (
    VISION_AWARENESS_RULE,
    _load_tool_rules,
    build_user_turn_prompt,
    extract_vision_context,
)
from handlers.social.memory_builder import build_user_memory_block
from handlers.social.observability import log_event
from handlers.social.persona import build_dynamic_persona
from handlers.social.utils import remove_hourglass_reaction, simulate_typing_and_send

logger = logging.getLogger(__name__)


class IntegratedStrategy(BaseDispatchStrategy):
    """單階段整合式自主調度策略。"""

    @property
    def mode_name(self) -> str:
        return "INTEGRATED"

    async def execute(
        self,
        brain: Any,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        images: list[str] | None,
        start_time: float,
        visual_context: str = "",
    ) -> None:
        """INTEGRATED 模式主流程（單階段內嵌 Action 標籤調度）。"""
        import handlers.social.dispatch_agent as da

        channel_id = message.channel.id
        display_name = message.author.display_name
        user_id = message.author.id

        # 啟動即時思考推演會話
        session = da.LiveThinkingSession(message)
        await session.start("💭 正在思考中")

        clean_query, active_vis = extract_vision_context(user_query, visual_context)

        recent_text = (full_context[-1500:] + "\n" + clean_query) if full_context else clean_query
        head_persona, tail_lore = build_dynamic_persona(
            recent_text,
            "CHAT",
            channel_id=channel_id,
            server_id=str(message.guild.id) if message.guild else "",
            user_query=clean_query,
            bot=bot,
            guild_id=message.guild.id if message.guild else None,
        )
        memory_block = build_user_memory_block(bot, user_id, display_name, query_text=clean_query)

        if session and session.is_enabled:
            pre_steps: list[tuple[str, str] | tuple[str, str, int | None]] = []
            vibe_hint = "感知群聊話題氛圍與對話脈絡" if vibe_snippet else "解析對話情境與話題脈絡"
            query_len = len(clean_query.strip())
            pre_steps.append(("intent", f"{vibe_hint} (長度: {query_len} 字)"))

            if memory_block and memory_block.strip():
                mem_lines = [line.strip() for line in memory_block.splitlines() if line.strip().startswith("-")]
                mem_count = len(mem_lines) if mem_lines else 1
                pre_steps.append(("memory", f"檢索群友 [{display_name}] 互動記憶 ({mem_count} 筆相關事實與親密度)"))
            else:
                pre_steps.append(("memory", f"檢索群友 [{display_name}] 互動記錄 (無特定前置印象，啟用通用社交模型)"))

            if images or active_vis:
                pre_steps.append(("vision", "載入多模態圖像線索與名偵探特徵感知"))

            session.push_steps(pre_steps)

        vibe_block = (
            "【頻道近期話題氛圍】(這只是讓妳用來讀空氣的背景氣氛！)\n"
            f"{vibe_snippet}\n\n"
        ) if vibe_snippet else ""

        is_sherry = getattr(settings, "CURRENT_SOUL_NAME", "manosaba_sherry_default.md") in (
            "sherry_default.md",
            "manosaba_sherry_default.md",
        )
        vision_self_lore = (
            "\n【多模態視覺自我感知提示】：若圖片中出現淺藍髮色、丸子頭、白鳥羽毛帽、偵探斗篷等特徵的少女，這就是妳自己（橘雪莉）！\n"
        ) if (is_sherry and (images or active_vis)) else ""

        author_note = f"\n{tail_lore}\n{vision_self_lore}" if (tail_lore or vision_self_lore) else ""

        tool_guidelines = ""
        # 圖片訊息：聚焦於看圖對話與視覺互動，不載入工具自主調用規則（避免視覺模型誤觸發多餘工具）
        if not (bool(images) or bool(active_vis) or "【群友上傳了一張圖片" in user_query):
            tool_guidelines = _load_tool_rules().format(
                tool_descriptions=self._build_tool_descriptions()
            )

        length_rule = (
            "2. 【自然生動且精準的篇幅】：徹底貫徹角色個性與說話口吻。\n"
            "   - 【日常互動與閒聊】：篇幅請保持精簡自然（約 30~80 字，1~3 句話為佳），有溫度且口語化，如同身邊好友面對面隨口交流。可帶至多 1 個簡短神態或動作點綴，嚴禁輸出大段小說式心理戲、繁冗動作描寫或說教式長文！\n"
            "   - 【分析、搜尋與深度解答】：僅在處理工具回傳數據（如情報搜尋、股票分析、長文總結）或對方明確要求「詳細說明/深度分析」時，才展開條理分明、豐富詳實的完整回答。"
        )

        cot_instruction = ""
        if getattr(settings, "MICRO_COT_ENABLED", True):
            cot_instruction = (
                "0. 🧠 【深度思考機制】：在給出任何動作或回覆前，妳「必須」先用 <thought>...</thought> 標籤包覆妳的內心思考、局勢分析與回覆策略。\n"
            )

        vision_rule = f"{VISION_AWARENESS_RULE}\n" if active_vis else ""

        # ── [第一區：靜態系統前綴 (Static Prefix) — 最大化 KV/Prefix Cache 命中率] ──
        # 包含：核心靈魂人設 (Soul) + 工具調用規範 + 恆定回覆原則 (CoT, 篇幅, 工具優先與誠實原則)
        # 此區塊在相同頻道與意圖下保持完全靜態，確保推論引擎首 3,500+ 字 100% 命中前綴快取。
        static_reply_rules = (
            "【回覆規則】\n"
            f"{cot_instruction}"
            "1. 【對話連貫與話題推進】：自然接續剛才的對話脈絡與互動情境。若當前話題正在進行，請順著剛才的情緒與情境深入延續，嚴禁每輪重複「欸～笨蛋」、「你在說什麼」等初次驚訝的模板化開場白！\n"
            f"{length_rule}\n"
            "3. 【工具優先】：調用工具時僅輸出 <action> 標籤，無須工具的純閒聊則直接輸出性格對白；若已知事實不足或查無資料請如實告知，嚴禁臆造不存在之事實。"
        )

        static_parts = [head_persona]
        if tool_guidelines:
            static_parts.append(tool_guidelines)
        static_parts.append(static_reply_rules)
        static_prefix = "\n\n".join(static_parts)

        # ── [第二區：動態對話情境 (Dynamic Context)] ──
        # 依穩定度（高→低）排序以最大化同頻道多輪與跨群友之間的 KV Cache / Prefix Caching 共享：
        # 1. 頻道近期氣氛 (同頻道全體共用，數十分鐘更新一次)
        # 2. 頻道歷史對話 (同頻道全體共用且持續在末端 Append，跨群友聊天不破前綴快取)
        # 3. 視覺感官認知規則 (若有圖片)
        # 4. 當前發話者個人記憶 (換人說話時僅在末端切換，不破壞前面的歷史前綴)
        # 5. 作者備註與動態尾部 (世界記憶 + 時間戳記 + 即時線索，緊貼生成點)
        dynamic_parts = []
        if vibe_block:
            dynamic_parts.append(vibe_block.strip())
        if full_context:
            dynamic_parts.append(full_context.strip())
        if vision_rule:
            dynamic_parts.append(vision_rule.strip())
        if memory_block:
            dynamic_parts.append(memory_block)
        if author_note:
            dynamic_parts.append(author_note.strip())

        dynamic_suffix = "\n\n".join(dynamic_parts)
        sys_prompt = f"{static_prefix}\n\n{dynamic_suffix}".strip() if dynamic_suffix else static_prefix

        user_prompt = build_user_turn_prompt(
            display_name=display_name,
            user_query=clean_query,
            visual_context=active_vis,
        )
        stream_cb = session.on_stream_chunk if (session and session.is_enabled) else None

        vision_model_setting = str(getattr(settings, "VISION_MODEL", "") or "").strip()
        active_model = vision_model_setting if (images and vision_model_setting) else settings.PRIMARY_MODEL

        logger.info(
            "DispatchAgent[INTEGRATED]: pass1 prompt 組成 (模型=%s, 人設=%d字, 工具規則=%d字, 回覆規則=%d字, 記憶=%d字, 氛圍=%d字, 歷史=%d字, 視覺=%d字, 備註=%d字, user=%d字, 總計=%d字)",
            active_model,
            len(head_persona or ""),
            len(tool_guidelines or ""),
            len(static_reply_rules or ""),
            len(memory_block or ""),
            len(vibe_block or ""),
            len(full_context or ""),
            len(vision_rule or ""),
            len(author_note or ""),
            len(user_prompt),
            len(sys_prompt) + len(user_prompt),
        )

        if session and session.is_enabled:
            session.mark_inference_start()

        raw_response = await self._agent._call_llm(
            brain,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            task_id=f"integrated-pass1-{int(time.time() * 1000)}",
            model=active_model,
            temperature=getattr(settings, "LLM_TEMPERATURE", 0.85),
            timeout=float(settings.OLLAMA_TIMEOUT),
            images=images,
            stream_callback=stream_cb,
            user_id=message.author.id,
        )

        # ── 【降級防護：Pass-1 規劃空回覆時，跳過工具規劃直接合成回覆】 ───────
        if not raw_response or not raw_response.strip():
            logger.warning(
                "DispatchAgent[INTEGRATED]: Pass-1 規劃空回覆，跳過工具規劃直接合成回覆"
            )
            final_speech, synth_thought, _final_sys_prompt, _final_user_prompt = (
                await self._agent._generate_final_response(
                    brain=brain,
                    user_query=clean_query,
                    channel_id=channel_id,
                    bot=bot,
                    user_id=user_id,
                    display_name=display_name,
                    observations=[],
                    vibe_snippet=vibe_snippet,
                    full_context=full_context,
                    images=images,
                    session=session,
                    server_id=str(message.guild.id) if message.guild else "",
                    visual_context=active_vis,
                )
            )
            elapsed_ms = int((time.time() - start_time) * 1000)
            log_event(
                "chat_reply",
                message_id=message.id,
                user=display_name,
                channel_id=channel_id,
                raw_input=user_query,
                intent="INTEGRATED_CHAT",
                intent_source="integrated_empty_fallback",
                cot_thought=synth_thought or "",
                reply_preview=final_speech[:120],
                model_used=active_model,
                latency_ms=elapsed_ms,
                tools_invoked=[],
            )
            if session.is_enabled and session.placeholder_msg:
                await session.finish(
                    final_speech,
                    thought_log=synth_thought or "",
                    intent_name="INTEGRATED_CHAT",
                    model=active_model,
                    latency_ms=elapsed_ms,
                    prompt=user_query,
                )
            else:
                await simulate_typing_and_send(
                    message,
                    final_speech,
                    intent_name="INTEGRATED_CHAT",
                    prompt=user_query,
                    model=active_model,
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    latency_ms=elapsed_ms,
                    thought_log=synth_thought or "",
                )
            return

        persona_thought = _extract_xml_tag(raw_response, "thought") or ""
        actions = _parse_actions(raw_response)

        # ── 【分支一：無 Action，純日常閒聊直接回覆（單次推論秒回）】 ─────
        if not actions:
            clean_speech = re.sub(r"(?is)<(?:thought|think|action)[^>]*>.*?</(?:thought|think|action)>", "", raw_response).strip()
            clean_speech = re.sub(r"<\/?(?:thought|think|final_answer|action)[^>]*>", "", clean_speech).strip()
            if not clean_speech or len(clean_speech.strip()) < 2:
                clean_speech = "（歪頭看著你）嗯？怎麼啦？"

            elapsed_ms = int((time.time() - start_time) * 1000)
            full_cot_thought = f"【雪莉內心思考】\n{persona_thought}" if persona_thought else ""

            log_event(
                "chat_reply",
                message_id=message.id,
                user=display_name,
                channel_id=channel_id,
                raw_input=user_query,
                intent="INTEGRATED_CHAT",
                intent_source="integrated_agent_direct",
                cot_thought=full_cot_thought,
                reply_preview=clean_speech[:120],
                model_used=active_model,
                latency_ms=elapsed_ms,
                tools_invoked=[],
            )

            if session.is_enabled and session.placeholder_msg:
                await session.finish(
                    clean_speech,
                    thought_log=full_cot_thought,
                    intent_name="INTEGRATED_CHAT",
                    model=active_model,
                    latency_ms=elapsed_ms,
                    prompt=user_query,
                )
            else:
                await simulate_typing_and_send(
                    message,
                    clean_speech,
                    intent_name="INTEGRATED_CHAT",
                    prompt=user_query,
                    model=active_model,
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    latency_ms=elapsed_ms,
                    thought_log=full_cot_thought,
                )
            return

        # ── 【分支二：偵測到 Action，發動工具並進行第二階段合成】 ─────────
        logger.info(
            "DispatchAgent[INTEGRATED]: 偵測到工具呼叫 -> %s",
            [(a.tool, a.input) for a in actions],
        )

        max_tasks = getattr(settings, "PARALLEL_MAX_TASKS", 5)
        seen_keys: set[tuple[str, str]] = set()
        deduped: list[PlannedTask] = []
        for a in actions[:max_tasks]:
            key = (a.tool.upper(), a.input.strip())
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(a)

        session.push_step("action", f"調用工具: {', '.join([t.tool for t in deduped])}")

        t_tools_start = time.monotonic()
        executed_results = list(await asyncio.gather(*[
            self._agent._parallel_execute_single_task(t, bot, message, vibe_snippet, full_context, images=images)
            for t in deduped
        ]))
        tools_latency_ms = max(1, int((time.monotonic() - t_tools_start) * 1000))

        invoked_tool_names = [name for name, _, _ in executed_results]
        if executed_results:
            obs_desc = ", ".join([f"{name}: {obs[:50]}" for name, _, obs in executed_results])
            session.push_step("obs", f"取得數據: {obs_desc}", duration_ms=tools_latency_ms)

        # 檢查 DIRECT_UI 自適應早期終止
        is_all_direct_ui = all(mode == ToolCategory.DIRECT_UI for _, mode, _ in executed_results)
        has_chat_intent = bool(re.search(r"(並|而且|順便|跟我說|告訴我|分享|覺得|看法|心得|對話|聊天|喜不喜歡|喜愛|早安|晚安)", user_query))
        if is_all_direct_ui and not has_chat_intent and getattr(settings, "ACTION_EARLY_TERMINATION_ENABLED", True):
            await remove_hourglass_reaction(message, bot.user)
            elapsed_ms = int((time.time() - start_time) * 1000)
            cot_thought = (
                f"【雪莉內心思考】\n{persona_thought}\n\n【已發動工具】\n"
                + "\n".join([f"- {name}: {obs}" for name, _, obs in executed_results])
            )
            log_event(
                "chat_reply",
                message_id=message.id,
                user=display_name,
                channel_id=channel_id,
                raw_input=user_query,
                intent="INTEGRATED_ACTION",
                intent_source="integrated_early_term",
                cot_thought=cot_thought,
                reply_preview=f"已為您發動: {invoked_tool_names}",
                model_used=active_model,
                latency_ms=elapsed_ms,
                tools_invoked=invoked_tool_names,
            )
            if session.is_enabled and session.placeholder_msg:
                await session.finish(
                    f"✨ 已為您發動: {', '.join(invoked_tool_names)}",
                    thought_log=cot_thought,
                    intent_name="INTEGRATED_ACTION",
                    model=active_model,
                    latency_ms=elapsed_ms,
                    prompt=user_query,
                )
            return

        # 純資料工具或需性格回覆：調用第二輪對話文本合成
        obs_pairs = [(name, obs) for name, _, obs in executed_results]
        final_speech, synth_thought, final_sys_prompt, final_user_prompt = await self._agent._generate_final_response(
            brain=brain,
            user_query=clean_query,
            channel_id=channel_id,
            bot=bot,
            user_id=user_id,
            display_name=display_name,
            observations=obs_pairs,
            vibe_snippet=vibe_snippet,
            full_context=full_context,
            images=images,
            session=session,
            server_id=str(message.guild.id) if message.guild else "",
            visual_context=active_vis,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)
        thought_sections: list[str] = []
        if persona_thought:
            thought_sections.append(f"【雪莉初始思考】\n{persona_thought}")
        if obs_pairs:
            thought_sections.append("【工具執行回傳數據】\n" + "\n".join([f"- {t}: {o}" for t, o in obs_pairs]))
        if synth_thought:
            thought_sections.append(f"【雪莉最終思考】\n{synth_thought}")
        full_cot_thought = "\n\n".join(thought_sections)

        log_event(
            "chat_reply",
            message_id=message.id,
            user=display_name,
            channel_id=channel_id,
            raw_input=user_query,
            intent="INTEGRATED_ACTION",
            intent_source="integrated_agent_full",
            cot_thought=full_cot_thought,
            reply_preview=final_speech[:120],
            model_used=active_model,
            latency_ms=elapsed_ms,
            tools_invoked=invoked_tool_names,
        )

        if session.is_enabled and session.placeholder_msg:
            await session.finish(
                final_speech,
                thought_log=full_cot_thought,
                intent_name="INTEGRATED_ACTION",
                model=active_model,
                latency_ms=elapsed_ms,
                prompt=user_query,
            )
        else:
            await simulate_typing_and_send(
                message,
                final_speech,
                intent_name="INTEGRATED_ACTION",
                prompt=user_query,
                model=active_model,
                system_prompt=final_sys_prompt,
                user_prompt=final_user_prompt,
                latency_ms=elapsed_ms,
                thought_log=full_cot_thought,
            )
