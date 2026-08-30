"""
core/dispatcher.py
==================
智慧輸出分發器 (SmartMessageDispatcher)。

功能：
1. 單一輸出聚合：文字、Embed 卡片與圖片附件一次性原子發送，杜絕重複刷屏。
2. 2000 字智慧分段 (Smart Markdown Chunker)：當回覆過長時，依段落、句子或代碼塊邊界安全拆分，保證 Markdown 不破版。
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from core.contracts import SherryResponse

logger = logging.getLogger(__name__)


class SmartMessageDispatcher:
    """智慧訊息分發與分段發送器。"""

    @staticmethod
    def split_text_smart(text: str, max_chars: int = 1900) -> list[str]:
        """將超長文字依段落、標點與代碼塊邊界智慧拆分為多個合法區塊。

        Args:
            text: 待發送的完整文字。
            max_chars: 單則訊息字數上限 (Discord 上限 2000，預設保留緩衝為 1900)。

        Returns:
            list[str]: 拆分後之文字區塊列表。
        """
        if not text:
            return [""]
        if len(text) <= max_chars:
            return [text]

        chunks: list[str] = []
        remaining = text
        in_code_block = False
        code_lang = ""

        while len(remaining) > max_chars:
            split_pos = -1

            # 1. 優先嘗試在段落雙換行處分割 (\n\n)
            last_para = remaining[:max_chars].rfind("\n\n")
            if last_para > max_chars // 2:
                split_pos = last_para + 2
            else:
                # 2. 次選在單換行處分割 (\n)
                last_nl = remaining[:max_chars].rfind("\n")
                if last_nl > max_chars // 2:
                    split_pos = last_nl + 1
                else:
                    # 3. 再選在句號/驚嘆號/問號處分割
                    punct_matches = list(re.finditer(r"[。！？!?]\s*", remaining[:max_chars]))
                    if punct_matches:
                        split_pos = punct_matches[-1].end()
                    else:
                        # 4. 實在無處分割時，在空格或硬邊界切開
                        last_space = remaining[:max_chars].rfind(" ")
                        if last_space > max_chars // 2:
                            split_pos = last_space + 1
                        else:
                            split_pos = max_chars

            chunk = remaining[:split_pos].rstrip()
            remaining = remaining[split_pos:].lstrip()

            # 檢查代碼塊 ``` 完整性，若在代碼塊中斷則補齊收尾並在新段重啟
            fence_count = chunk.count("```")
            if fence_count % 2 == 1:
                if not in_code_block:
                    # 尋找當前代碼塊語言標記
                    last_fence = chunk.rfind("```")
                    lang_match = re.match(r"```(\w+)", chunk[last_fence:])
                    code_lang = lang_match.group(1) if lang_match else ""
                    in_code_block = True
                    chunk = chunk + "\n```"
                    remaining = f"```{code_lang}\n" + remaining
                else:
                    in_code_block = False
                    chunk = chunk + "\n```"
                    remaining = f"```{code_lang}\n" + remaining

            chunks.append(chunk)

        if remaining:
            chunks.append(remaining)

        return chunks

    @classmethod
    async def dispatch(
        cls,
        target: Any,
        response: SherryResponse,
        reference_msg: discord.Message | None = None,
    ) -> list[discord.Message]:
        """將 SherryResponse 一次性原子發送至 Discord 頻道或作為回覆。

        Args:
            target: discord.TextChannel, Thread, 或其他 Messageable 實例。
            response: 包含文字、卡片、圖片與思維的 SherryResponse。
            reference_msg: 原使用者觸發訊息（若有則進行 reply 引用回覆）。

        Returns:
            list[discord.Message]: 發送成功之 Discord 訊息物件列表。
        """
        sent_messages: list[discord.Message] = []
        chunks = cls.split_text_smart(response.reply_text, max_chars=1900)

        # 首則訊息：附帶 Embeds, Files 與 View
        first_content = chunks[0] if chunks else ""
        kwargs: dict[str, Any] = {}
        if first_content:
            kwargs["content"] = first_content
        if response.embeds:
            kwargs["embeds"] = response.embeds
        if response.view is not None:
            kwargs["view"] = response.view
        if response.files:
            kwargs["files"] = response.files

        try:
            if reference_msg and hasattr(reference_msg, "reply"):
                kwargs["mention_author"] = True
                msg = await reference_msg.reply(**kwargs)
            elif hasattr(target, "send"):
                msg = await target.send(**kwargs)
            else:
                return sent_messages

            sent_messages.append(msg)

            # 後續超長分段訊息：純文字發送
            for subsequent_chunk in chunks[1:]:
                if subsequent_chunk.strip():
                    if hasattr(target, "send"):
                        sub_msg = await target.send(content=subsequent_chunk)
                        sent_messages.append(sub_msg)

        except Exception as exc:
            logger.error("SmartMessageDispatcher: 訊息發送失敗: %s", exc, exc_info=True)

        return sent_messages
