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
from typing import Any

import discord

import settings
from handlers.social.utils import (
    BOT_MESSAGE_INTENTS,
    _chunk_message,
    format_thinking_steps_block,
    remove_hourglass_reaction,
)

logger = logging.getLogger(__name__)


class LiveThinkingSession:
    """管理單次對話請求的即時思考推演動態編輯生命週期。"""

    def __init__(self, target: Any) -> None:
        """初始化即時思考會話。

        Args:
            target: discord.Message 或 TextChannel 目標。
        """
        self.target: Any = target
        self.channel: Any = getattr(target, "channel", target)
        self.is_message: bool = hasattr(target, "reply")
        self.placeholder_msg: discord.Message | None = None
        self.steps: list[tuple[str, str]] = []
        self._stream_buffer: str = ""
        self._live_thought: str = ""
        self._last_edit_time: float = 0.0
        self._min_edit_interval: float = 0.8  # 避免過於頻繁觸發 Discord Rate Limit
        self._pending_task: asyncio.Task[None] | None = None
        self._failed: bool = False
        self._is_finished: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        """是否啟用動態編輯思考流程。"""
        return bool(getattr(settings, "LIVE_THINKING_EDIT", True) or getattr(settings, "DEBUG_DETAIL", False))

    async def start(self, initial_hint: str = "📋 意圖解析中") -> None:
        """發送初始佔位思考訊息（零延遲即時反饋）。

        Args:
            initial_hint: 當前正在進行的動作簡述。
        """
        if not self.is_enabled or self._failed:
            return

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
            self._last_edit_time = time.monotonic()
        except Exception as exc:
            logger.warning("LiveThinkingSession: 發送初始佔位訊息失敗: %s", exc)
            self._failed = True

    def push_step(self, step_type: str, text: str) -> None:
        """以非同步背景任務 (Fire-and-Forget) 推送新思考/工具執行階段，主執行緒 0 等待。

        Args:
            step_type: 步驟類型 ('plan', 'action', 'obs', 'persona')。
            text: 步驟描述內容。
        """
        if not self.is_enabled or self._failed or not self.placeholder_msg or self._is_finished:
            return

        self.steps.append((step_type, text.strip()))

        # 取消前一個尚未發送的背景節流任務，以最新進度為主
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

        self._stream_buffer += chunk

        # 檢查是否包含 <thought> 或 <think>
        thought_match = re.search(r'(?is)<(?:thought|think)>(.*?)(?:</(?:thought|think)>|\Z)', self._stream_buffer)
        if thought_match:
            current_thought = thought_match.group(1).strip()
            if current_thought and current_thought != self._live_thought:
                self._live_thought = current_thought
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

            if self._is_finished or not self.placeholder_msg:
                return

            async with self._lock:
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
        for idx, (stype, text) in enumerate(self.steps):
            num_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"][min(idx, 5)]
            clean_text = text.replace("\n", " ➔ ")
            if len(clean_text) > 180:
                clean_text = clean_text[:180] + "..."

            if stype == "plan":
                lines.append(f"> {num_emoji} 📋 **意圖拆解 [Plan]**：{clean_text}")
            elif stype in ("action", "obs"):
                lines.append(f"> {num_emoji} 🛠️ **工具與觀測 [Action & Obs]**：{clean_text}")
            elif stype == "persona":
                lines.append(f"> {num_emoji} 💭 **角色心智 [Persona]**：{clean_text}")
            else:
                lines.append(f"> {num_emoji} 🔍 **{stype.upper()}**：{clean_text}")

        # 若有即時串流進來的思考鏈，動態展示
        if self._live_thought:
            thought_preview = self._live_thought.replace("\n", " ➔ ")
            if len(thought_preview) > 150:
                thought_preview = "..." + thought_preview[-150:]
            step_num = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"][min(len(self.steps), 5)]
            lines.append(f"> {step_num} 💭 **心智推演中 [Thinking]**：{thought_preview}")
            lines.append("> ⏳ *🧠 正在深入思考中...*")
        else:
            last_stype = self.steps[-1][0] if self.steps else ""
            if last_stype == "plan":
                lines.append("> ⏳ *🛠️ 正在調用工具查詢數據...*")
            elif last_stype in ("action", "obs"):
                lines.append("> ⏳ *💭 正在組織小偵探回覆語氣...*")
            else:
                lines.append("> ⏳ *✨ 正在生成最終回覆...*")

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
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        sent_messages: list[discord.Message] = []
        clean_reply = final_reply.strip() or "（盯——你這是在說甚麼難懂的話呢！）"

        if getattr(settings, "DEBUG_DETAIL", False):
            steps_block = format_thinking_steps_block(
                thought_log=thought_log,
                intent_name=intent_name,
                model=model,
                latency_ms=latency_ms,
                prompt=prompt,
            )
            full_content = f"{steps_block}\n\n{clean_reply}"
        else:
            full_content = clean_reply

        chunks = _chunk_message(full_content, max_length=1900)

        no_mention = discord.AllowedMentions(replied_user=False)
        try:
            if self.is_enabled and self.placeholder_msg and not self._failed:
                # 1. 第一段：原地編輯原佔位訊息
                try:
                    await self.placeholder_msg.edit(content=chunks[0], allowed_mentions=no_mention)
                    sent_messages.append(self.placeholder_msg)
                except Exception as edit_exc:
                    logger.warning("LiveThinkingSession: 原地編輯失敗，改為重新發送: %s", edit_exc)
                    if self.is_message:
                        first_msg = await self.target.reply(chunks[0], mention_author=False, allowed_mentions=no_mention)
                    else:
                        first_msg = await self.channel.send(chunks[0], allowed_mentions=no_mention)
                    sent_messages.append(first_msg)

                # 2. 超長分段：發送後續片段
                for sub_chunk in chunks[1:]:
                    sub_msg = await self.channel.send(sub_chunk, allowed_mentions=no_mention)
                    sent_messages.append(sub_msg)
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
            if self.is_message:
                await remove_hourglass_reaction(self.target)

        return sent_messages