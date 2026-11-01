"""handlers/social/autonomy/generation.py
======================================
共用的 LLM 生成入口。

所有 action / speaker 都走這裡，好處是：
- 逾時與失敗的處理只有一份。
- 生成失敗一律回傳空字串，由 SocialDirector 記成 SILENT。
- 未來要換模型或加 guard，只需改這一處。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import settings

logger = logging.getLogger(__name__)


async def generate_text(
    bot: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int = 300,
    timeout: float = 30.0,
    priority: int | None = None,
    task_id: str = "",
    temperature: float | None = None,
) -> str:
    """送一個 InferenceTask 並等待回應；失敗或逾時回傳空字串。"""
    if bot is None or not system_prompt:
        return ""
    brain_cog = None
    try:
        brain_cog = bot.get_cog("BotBrain")
    except Exception:
        brain_cog = None
    if brain_cog is None or not hasattr(brain_cog, "enqueue_task"):
        logger.debug("[Generation] BotBrain 尚未載入，跳過生成")
        return ""

    from handlers.bot_brain import InferenceTask

    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()

    async def _on_done(result: str) -> None:
        if not future.done():
            future.set_result(result or "")

    task = InferenceTask(
        priority=priority if priority is not None else int(getattr(settings, "PRIORITY_INTERJECT", 2)),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        callback=_on_done,
        model=model or str(getattr(settings, "PRIMARY_MODEL", "")),
        task_id=task_id or f"social-autonomy-{int(time.time() * 1000)}",
        temperature=temperature,
        max_tokens=max_tokens,
    )
    try:
        await brain_cog.enqueue_task(task)
    except Exception as exc:
        logger.warning("[Generation] 任務入列失敗: %s", exc)
        return ""

    try:
        return str(await asyncio.wait_for(future, timeout=timeout)).strip()
    except asyncio.TimeoutError:
        try:
            setattr(task, "is_cancelled", True)
        except Exception:
            pass
        logger.info("[Generation] 生成逾時（task=%s）", task.task_id)
        return ""
    except Exception as exc:
        logger.warning("[Generation] 生成失敗（task=%s）: %s", task.task_id, exc)
        return ""
