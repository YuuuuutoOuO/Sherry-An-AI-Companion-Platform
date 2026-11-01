"""
handlers/social/streaming_thinking.py
======================================
即時動態編輯思考推演會話管理員 (LiveThinkingSession)。

以 Discord message.edit 原地動態更新技術，模擬現代 AI 逐階段展開思考的視覺體驗。
採用背景非同步 (Fire-and-Forget) 與智慧節流防抖 (Debounce) 設計，
保證後端推論與工具查詢速度 0 阻塞、0 延遲。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Sequence

import discord

import settings
from handlers.social.persona_guard import sanitize_reply
from handlers.social.utils import (
    BOT_MESSAGE_INTENTS,
    _chunk_message,
    format_thinking_steps_block,
    remove_hourglass_reaction,
)

logger = logging.getLogger(__name__)


class LiveThinkingSession:
    """管理單次對話請求的即時思考推演動態編輯生命週期。"""

    def __init__(self, target: Any, prompt: str = "") -> None:
        """初始化即時思考會話。

        Args:
            target: discord.Message 或 TextChannel 目標。
            prompt: 使用者提問或請求內容（可選）。
        """
        self.target: Any = target
        self.prompt: str = prompt
        self.channel: Any = getattr(target, "channel", target)
        self.is_message: bool = hasattr(target, "reply")
        self.placeholder_msg: discord.Message | None = None
        self._task_id: str | None = None
        self.steps: list[tuple[str, str, int | None]] = []
        self._stream_buffer: str = ""
        self._live_thought: str = ""
        self._last_edit_time: float = 0.0
        self._min_edit_interval: float = 1.0  # 避免過於頻繁觸發 Discord Rate Limit (Discord 要求訊息編輯間隔通常需 ≥1.0s)
        self._pending_task: asyncio.Task[None] | None = None
        self._failed: bool = False
        self._is_finished: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()
        self._initial_hint: str = "📋 意圖解析中"

        # 耗時指標追蹤
        self._session_start_time: float = time.monotonic()
        self._pass_start_time: float = self._session_start_time
        self._pass_prefill_ms: int | None = None
        self._thought_start_time: float | None = None
        self._thought_end_time: float | None = None
        self._pass_thought_ms: int | None = None
        self._generation_start_time: float | None = None
        self._generation_end_time: float | None = None
        self._generation_ms: int | None = None
        self._generated_chars: int = 0

    @property
    def _prefill_ms(self) -> int | None:
        """向上相容屬性別名：首字前綴運算耗時。"""
        return self._pass_prefill_ms

    @_prefill_ms.setter
    def _prefill_ms(self, val: int | None) -> None:
        self._pass_prefill_ms = val

    @property
    def _thought_ms(self) -> int | None:
        """向上相容屬性別名：心智推演耗時。"""
        return self._pass_thought_ms

    @_thought_ms.setter
    def _thought_ms(self, val: int | None) -> None:
        self._pass_thought_ms = val

    @property
    def is_enabled(self) -> bool:
        """是否啟用動態編輯思考流程。"""
        return bool(getattr(settings, "LIVE_THINKING_EDIT", True))

    async def start(self, initial_hint: str = "📋 意圖解析中", prompt: str | None = None) -> None:
        """發送初始佔位思考訊息（零延遲即時反饋）。

        Args:
            initial_hint: 當前正在進行的動作簡述。
            prompt: 可選覆寫的使用者提問文字。
        """
        if not self.is_enabled or self._failed:
            return

        if prompt is not None:
            self.prompt = prompt

        self._initial_hint = initial_hint
        self._session_start_time = time.monotonic()
        self._pass_start_time = self._session_start_time
        placeholder_text = (
            "> 💡 **雪莉思維推演與執行步驟 (Thinking Process)**\n"
            f"> ⏳ *💭 雪莉正在思考中... ({initial_hint})*"
        )
        try:
            if self.is_message:
                self.placeholder_msg = await self.target.reply(
                    placeholder_text,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions(replied_user=False),
                )
            elif hasattr(self.channel, "send"):
                self.placeholder_msg = await self.channel.send(
                    placeholder_text,
                    allowed_mentions=discord.AllowedMentions(replied_user=False),
                )
            now = time.monotonic()
            self._last_edit_time = now
            # 重設 pass_start_time，扣除 Discord REST 發送佔位訊息的網路延遲，精確反映真實 LLM 首字耗時
            self._pass_start_time = now

            if self.placeholder_msg:
                try:
                    from handlers.social.task_registry import get_in_flight_registry

                    task_id = f"ai-task-{self.placeholder_msg.id}"
                    origin_id = getattr(self.target, "id", None) if self.is_message else None
                    author = getattr(self.target, "author", None)
                    user_id = getattr(author, "id", 0)
                    resolved_prompt = (
                        self.prompt
                        or getattr(self.target, "clean_content", "")
                        or getattr(self.target, "content", "")
                    )
                    get_in_flight_registry().register(
                        task_id=task_id,
                        channel_id=self.placeholder_msg.channel.id,
                        message_id=self.placeholder_msg.id,
                        prompt=resolved_prompt,
                        user_id=user_id,
                        task_type="chat",
                        origin_message_id=origin_id,
                    )
                    self._task_id = task_id
                except Exception as reg_exc:
                    logger.debug("LiveThinkingSession: 登記 InFlightTask 失敗 (非致命): %s", reg_exc)
        except Exception as exc:
            logger.warning("LiveThinkingSession: 發送初始佔位訊息失敗: %s", exc)
            self._failed = True

    def mark_inference_start(self) -> None:
        """在向推論引擎發出請求當下標記開始時間，排除排隊與前置處理延遲。"""
        self._pass_start_time = time.monotonic()

    def prepare_synthesis_pass(self) -> None:
        """為第二階段合成對話生成重置串流與計時狀態。"""
        self._pass_start_time = time.monotonic()
        self._stream_buffer = ""
        self._live_thought = ""
        self._pass_prefill_ms = None
        self._thought_start_time = None
        self._thought_end_time = None
        self._pass_thought_ms = None
        self._generation_start_time = None
        self._generation_ms = None
        self._generated_chars = 0

    def push_step(self, step_type: str, text: str, duration_ms: int | None = None) -> None:
        """以非同步背景任務 (Fire-and-Forget) 推送新思考/工具執行階段，主執行緒 0 等待。

        Args:
            step_type: 步驟類型 ('plan', 'context', 'intent', 'memory', 'vision', 'action', 'obs', 'persona')。
            text: 步驟描述內容。
            duration_ms: 該步驟執行耗時（毫秒，可選）。
        """
        if not self.is_enabled or self._failed or not self.placeholder_msg or self._is_finished:
            return

        self.steps.append((step_type, text.strip(), duration_ms))

        # 取消前一個尚未發送的背景節流任務，以最新進度為主
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        self._pending_task = asyncio.create_task(
            self._debounced_edit_step(),
            name=f"live-thinking-edit-{getattr(self.placeholder_msg, 'id', 0)}",
        )

    def push_steps(self, steps: Sequence[tuple[str, str] | tuple[str, str, int | None]]) -> None:
        """批量推送多個前置階段步驟，合併單一防抖節流任務。

        Args:
            steps: 步驟元組列表 [(step_type, text, duration_ms?), ...]
        """
        if not self.is_enabled or self._failed or not self.placeholder_msg or self._is_finished:
            return

        for s in steps:
            stype, text, *rest = s
            dur = rest[0] if rest else None
            self.steps.append((stype, text.strip(), dur))

        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        self._pending_task = asyncio.create_task(
            self._debounced_edit_step(),
            name=f"live-thinking-edit-{getattr(self.placeholder_msg, 'id', 0)}",
        )

    async def on_stream_chunk(self, chunk: str) -> None:
        """接收 LLM 串流 Token，即時萃取思考鏈並以防抖方式背景更新 Discord 訊息。

        Args:
            chunk: LLM 生成的即時 Token 片段。
        """
        if not self.is_enabled or self._failed or not self.placeholder_msg or self._is_finished:
            return

        now = time.monotonic()
        should_schedule_edit = False

        # 1. 首字到達 (TTFT / Prefill)
        if self._pass_prefill_ms is None:
            self._pass_prefill_ms = max(1, int((now - self._pass_start_time) * 1000))
            should_schedule_edit = True

        self._stream_buffer += chunk

        # 2. 高效字串索引定位思考標籤，避免每 Token 全文正則回溯
        buf = self._stream_buffer
        start_idx = -1
        tag_len = 0
        for tag in ("<thought>", "<think>", "<THOUGHT>", "<THINK>"):
            pos = buf.find(tag)
            if pos != -1:
                start_idx = pos
                tag_len = len(tag)
                if self._thought_start_time is None:
                    self._thought_start_time = now
                break

        if start_idx != -1:
            content_start = start_idx + tag_len
            end_idx = -1
            end_tag_len = 0
            for end_tag in ("</thought>", "</think>", "</THOUGHT>", "</THINK>"):
                pos = buf.find(end_tag, content_start)
                if pos != -1:
                    end_idx = pos
                    end_tag_len = len(end_tag)
                    if self._thought_end_time is None:
                        self._thought_end_time = now
                        if self._thought_start_time is not None:
                            self._pass_thought_ms = max(1, int((self._thought_end_time - self._thought_start_time) * 1000))
                        self._generation_start_time = now
                        should_schedule_edit = True
                    break

            if end_idx != -1:
                current_thought = buf[content_start:end_idx].strip()
                # 思考已閉合，統計後續生成之字元數（排除可能出現的 <action> 標籤）
                reply_part = buf[end_idx + end_tag_len:]
                clean_reply_part = re.sub(r"(?is)<action[^>]*>.*?</action>", "", reply_part)
                clean_reply_part = re.sub(r"<action[^>]*>.*$", "", clean_reply_part)
                self._generated_chars = len(clean_reply_part.strip())
                should_schedule_edit = True
            else:
                current_thought = buf[content_start:].strip()

            if current_thought and current_thought != self._live_thought:
                self._live_thought = current_thought
                should_schedule_edit = True
        else:
            # 無思考標籤之直接回覆（若出現 <action> 標籤則不計入對話字元）
            if "<action" in buf:
                clean_buf = re.sub(r"(?is)<action[^>]*>.*?</action>", "", buf)
                clean_buf = re.sub(r"<action[^>]*>.*$", "", clean_buf)
                self._generated_chars = len(clean_buf.strip())
            else:
                if self._generation_start_time is None:
                    self._generation_start_time = now
                self._generated_chars += len(chunk)
            should_schedule_edit = True

        if should_schedule_edit:
            if not self._pending_task or self._pending_task.done():
                self._pending_task = asyncio.create_task(
                    self._debounced_edit_step(),
                    name=f"live-thinking-stream-{getattr(self.placeholder_msg, 'id', 0)}",
                )

    async def _debounced_edit_step(self) -> None:
        """非同步背景執行的節流編輯任務。"""
        try:
            now = time.monotonic()
            elapsed = now - self._last_edit_time
            if elapsed < self._min_edit_interval:
                await asyncio.sleep(self._min_edit_interval - elapsed)

            if self._is_finished or not self.placeholder_msg or self._failed:
                return

            async with self._lock:
                if self._is_finished or not self.placeholder_msg or self._failed:
                    return
                content = self._render_in_progress_block()
                await self.placeholder_msg.edit(
                    content=content,
                    allowed_mentions=discord.AllowedMentions(replied_user=False),
                )
                self._last_edit_time = time.monotonic()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("LiveThinkingSession: 中間步驟動態編輯失敗 (非致命): %s", exc)

    def _render_in_progress_block(self) -> str:
        """組裝進行中的步驟 Markdown 區塊。"""
        lines = ["> 💡 **雪莉思維推演與執行步驟 (Thinking Process)**"]
        step_idx = 1
        num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]

        # 1. 外部步驟 (Plan / Context / Intent / Memory / Vision / Action / Obs / Persona)
        for raw_step in self.steps:
            stype = raw_step[0]
            text = raw_step[1]
            dur = raw_step[2] if len(raw_step) > 2 else None
            num_emoji = num_emojis[min(step_idx - 1, 7)]
            clean_text = text.replace("\n", " ➔ ")
            if len(clean_text) > 150:
                clean_text = clean_text[:150] + "..."
            dur_str = f"`{dur} ms` ➔ " if dur is not None else ""

            if stype == "plan":
                lines.append(f"> {num_emoji} 📋 **意圖拆解 [Plan]**：{dur_str}{clean_text}")
            elif stype in ("context", "intent"):
                lines.append(f"> {num_emoji} 📋 **對話語境與意圖 [Context & Intent]**：{dur_str}{clean_text}")
            elif stype == "memory":
                lines.append(f"> {num_emoji} 🧠 **長期記憶檢索 [Memory]**：{dur_str}{clean_text}")
            elif stype == "vision":
                lines.append(f"> {num_emoji} 👁️ **多模態視覺感知 [Vision]**：{dur_str}{clean_text}")
            elif stype in ("action", "obs"):
                lines.append(f"> {num_emoji} 🛠️ **工具與觀測 [Action & Obs]**：{dur_str}{clean_text}")
            elif stype == "persona":
                lines.append(f"> {num_emoji} 💭 **角色心智 [Persona]**：{dur_str}{clean_text}")
            else:
                lines.append(f"> {num_emoji} 🔍 **{stype.upper()}**：{dur_str}{clean_text}")
            step_idx += 1

        # 2. PREFILL 階段 (若已到達首字生成)
        if self._pass_prefill_ms is not None:
            lines.append(f"> {num_emojis[min(step_idx - 1, 7)]} ⚡ **PREFILL [首字生成]**：`{self._pass_prefill_ms} ms` (前綴快取運算完成)")
            step_idx += 1

        # 3. 思考推演階段 (Thinking / CoT)
        if self._pass_thought_ms is not None:
            thought_preview = self._live_thought.replace("\n", " ➔ ")
            if len(thought_preview) > 120:
                thought_preview = thought_preview[:120] + "..."
            summary_str = f" ➔ {thought_preview}" if thought_preview else ""
            lines.append(f"> {num_emojis[min(step_idx - 1, 7)]} 🧠 **思維鏈推演 [Thinking]**：`{self._pass_thought_ms} ms`{summary_str}")
            step_idx += 1
        elif self._live_thought:
            thought_preview = self._live_thought.replace("\n", " ➔ ")
            if len(thought_preview) > 120:
                thought_preview = "..." + thought_preview[-120:]
            lines.append(f"> {num_emojis[min(step_idx - 1, 7)]} 🧠 **心智推演中 [Thinking]**：➔ {thought_preview}")
            step_idx += 1

        # 4. 當前正在進行之動態提示 (⏳)
        if not self._is_finished:
            if self._generated_chars > 0 or self._generation_start_time is not None:
                speed_str = ""
                if self._generated_chars > 0 and self._generation_start_time is not None:
                    gen_elapsed = max(0.1, time.monotonic() - self._generation_start_time)
                    speed = self._generated_chars / gen_elapsed
                    speed_str = f" • {speed:.1f} 字/秒"
                char_str = f" (已輸出 {self._generated_chars} 字{speed_str})" if self._generated_chars > 0 else "..."
                lines.append(f"> ⏳ *✨ 正在生成回覆中{char_str}*")
            elif self._live_thought:
                lines.append("> ⏳ *🧠 正在深入思考中...*")
            elif self.steps and self.steps[-1][0] in ("action", "obs"):
                lines.append("> ⏳ *💭 正在組織回覆語氣...*")
            elif self.steps and self.steps[-1][0] in ("plan", "context", "intent", "memory", "vision"):
                lines.append("> ⏳ *🛠️ 正在調用工具查詢數據...*")
            elif self._pass_prefill_ms is not None:
                lines.append("> ⏳ *💭 正在組織推演路徑...*")
            else:
                lines.append(f"> ⏳ *💭 雪莉正在思考中... ({self._initial_hint})*")

        return "\n".join(lines)

    async def finish(
        self,
        final_reply: str,
        *,
        thought_log: str = "",
        intent_name: str = "CHAT",
        model: str = "",
        latency_ms: int = 0,
        prompt: str = "",
    ) -> list[discord.Message]:
        """完成思考，將佔位訊息原地編輯為正式回覆。

        Args:
            final_reply: 最終回覆文字。
            thought_log: 完整思維鏈。
            intent_name: 意圖標籤。
            model: 模型名稱。
            latency_ms: 總耗時。
            prompt: 使用者提問。

        Returns:
            list[discord.Message]: 發送或更新成功之 Discord 訊息列表。
        """
        self._is_finished = True
        now = time.monotonic()
        if self._generation_start_time is not None and self._generation_ms is None:
            self._generation_ms = max(1, int((now - self._generation_start_time) * 1000))
        elif self._pass_prefill_ms is not None and latency_ms > 0 and self._generation_ms is None:
            used_so_far = (self._pass_prefill_ms or 0) + (self._pass_thought_ms or 0) + sum(s[2] for s in self.steps if len(s) > 2 and s[2])
            self._generation_ms = max(1, latency_ms - used_so_far)

        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            try:
                await self._pending_task
            except (asyncio.CancelledError, Exception):
                pass
            self._pending_task = None

        sent_messages: list[discord.Message] = []
        clean_reply = re.sub(r"(?is)<(?:thought|think|action)[^>]*>.*?</(?:thought|think|action)>", "", final_reply).strip()
        clean_reply = re.sub(r"<\/?(?:thought|think|final_answer|action)[^>]*>", "", clean_reply).strip()
        if not clean_reply:
            clean_reply = "（盯——你這是在說甚麼難懂的話呢！）"

        # R22：LiveThinkingSession 不經 simulate_typing_and_send，必須在此
        # 套用同一道人設守門，否則串流／原地編輯路徑會成為治理缺口。
        clean_reply = sanitize_reply(clean_reply, user_input=prompt)

        if getattr(settings, "DEBUG_DETAIL", False):
            steps_block = format_thinking_steps_block(
                thought_log=thought_log or self._live_thought,
                intent_name=intent_name,
                model=model,
                latency_ms=latency_ms,
                prompt=prompt,
                prefill_ms=self._pass_prefill_ms,
                thought_ms=self._pass_thought_ms,
                tool_steps=self.steps,
                generation_ms=self._generation_ms,
                generation_chars=len(clean_reply),
            )
            full_content = f"{steps_block}\n\n{clean_reply}"
        else:
            full_content = clean_reply

        chunks = _chunk_message(full_content, max_length=1900)
        no_mention = discord.AllowedMentions(replied_user=False)

        try:
            if self.is_enabled and self.placeholder_msg and not self._failed:
                async with self._lock:
                    # 1. 第一段：原地編輯原佔位訊息（含冷卻保護與 429 重試機制）
                    edit_success = False
                    for attempt in range(3):
                        try:
                            # 確保距離上次 message.edit 滿足冷卻間隔，防範 Discord 429 RateLimit
                            cur_time = time.monotonic()
                            elapsed = cur_time - self._last_edit_time
                            if elapsed < self._min_edit_interval:
                                await asyncio.sleep(max(0.05, self._min_edit_interval - elapsed))

                            await self.placeholder_msg.edit(content=chunks[0], allowed_mentions=no_mention)
                            self._last_edit_time = time.monotonic()
                            sent_messages.append(self.placeholder_msg)
                            edit_success = True
                            break
                        except discord.HTTPException as http_exc:
                            if getattr(http_exc, "status", 0) == 429:
                                retry_wait = getattr(http_exc, "retry_after", 1.2) or 1.2
                                logger.warning("LiveThinkingSession: 原地編輯遭遇 429 Rate Limit，等待 %.2fs 重試 (%d/3)...", retry_wait, attempt + 1)
                                await asyncio.sleep(retry_wait)
                            else:
                                logger.warning("LiveThinkingSession: 原地編輯失敗 (HTTP %s): %s", getattr(http_exc, "status", "?"), http_exc)
                                break
                        except Exception as edit_exc:
                            logger.warning("LiveThinkingSession: 原地編輯發生非預期異常: %s", edit_exc)
                            break

                    if not edit_success:
                        # 降級重發模式：嘗試以新訊息回覆並清理殘留佔位訊息
                        try:
                            if self.is_message:
                                first_msg = await self.target.reply(chunks[0], mention_author=False, allowed_mentions=no_mention)
                            else:
                                first_msg = await self.channel.send(chunks[0], allowed_mentions=no_mention)
                            sent_messages.append(first_msg)
                            try:
                                await self.placeholder_msg.delete()
                            except Exception:
                                pass
                        except Exception as send_fallback_exc:
                            logger.error("LiveThinkingSession: 降級發送新訊息亦失敗: %s", send_fallback_exc)

                    # 2. 超長分段：發送後續片段
                    for sub_chunk in chunks[1:]:
                        try:
                            sub_msg = await self.channel.send(sub_chunk, allowed_mentions=no_mention)
                            sent_messages.append(sub_msg)
                        except Exception as chunk_exc:
                            logger.error("LiveThinkingSession: 發送分段訊息失敗: %s", chunk_exc)
            else:
                # 未開啟 LIVE_THINKING_EDIT 或無佔位訊息時的標準發送流程
                for idx, chunk in enumerate(chunks):
                    if idx == 0 and self.is_message:
                        msg = await self.target.reply(chunk, mention_author=False, allowed_mentions=no_mention)
                    else:
                        msg = await self.channel.send(chunk, allowed_mentions=no_mention)
                    sent_messages.append(msg)

            # 3. 補充互動按鈕 (🔄, 📌, 🗑️) 與紀錄 Intent
            if sent_messages:
                main_msg = sent_messages[0]
                BOT_MESSAGE_INTENTS[main_msg.id] = {
                    "intent": intent_name,
                    "thought": thought_log,
                    "prompt": prompt,
                }
                if len(BOT_MESSAGE_INTENTS) > 1000:
                    keys = list(BOT_MESSAGE_INTENTS.keys())
                    for k in keys[:-500]:
                        BOT_MESSAGE_INTENTS.pop(k, None)

                for emoji in ("🔄", "📌", "🗑️"):
                    try:
                        await main_msg.add_reaction(emoji)
                    except Exception:
                        pass

        except Exception as exc:
            logger.error("LiveThinkingSession: 訊息發送/更新異常: %s", exc)
        finally:
            if self._task_id:
                try:
                    from handlers.social.task_registry import get_in_flight_registry

                    get_in_flight_registry().unregister(self._task_id)
                    self._task_id = None
                except Exception as unreg_exc:
                    logger.debug("LiveThinkingSession: 註銷 InFlightTask 失敗 (非致命): %s", unreg_exc)
            if self.is_message:
                await remove_hourglass_reaction(self.target)

        return sent_messages

    def abort(self) -> None:
        """終止推演會話並自登記簿註銷任務。"""
        self._is_finished = True
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
        if self._task_id:
            try:
                from handlers.social.task_registry import get_in_flight_registry

                get_in_flight_registry().unregister(self._task_id)
                self._task_id = None
            except Exception:
                pass
