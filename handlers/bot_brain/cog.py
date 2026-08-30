import asyncio
import logging
from typing import Any
import aiohttp
from discord.ext import commands

import time
import settings
# 直接引用自己剛切出去的分身夥伴檔案！
from handlers.bot_brain.models import InferenceTask
from handlers.bot_brain.client import OllamaClient

logger = logging.getLogger(__name__)


class BotBrain(commands.Cog):
    """大腦推斷單步佇列守衛：限制GPU顯存佔滿之生命線守衛兵。"""
    
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._queue: asyncio.PriorityQueue[InferenceTask] = asyncio.PriorityQueue()
        self._lock = asyncio.Lock()
        self._ollama = OllamaClient()
        self._worker_task: asyncio.Task[None] | None = None
        # VRAM state flags & Event
        self._sd_generating = False
        self._sd_event = asyncio.Event()
        self._sd_event.set()  # Initial state: SD is NOT generating
        self._voice_mode_active = False
        self._task_counter = 0

    @property
    def sd_generating(self) -> bool:
        return self._sd_generating

    @sd_generating.setter
    def sd_generating(self, value: bool) -> None:
        self._sd_generating = value
        if value:
            self._sd_event.clear()
        else:
            self._sd_event.set()

    async def cog_load(self) -> None:
        self._worker_task = asyncio.create_task(
            self._inference_worker(), name="inference-worker"
        )
        logger.info("BotBrain: 代理人智能佇列 Worker 已經連線上線！")

    async def cog_unload(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try: await self._worker_task
            except asyncio.CancelledError: pass
        await self._ollama.close()
        logger.info("BotBrain: Inference worker 退役中止。")

    async def enqueue_task(self, task: InferenceTask) -> None:
        self._task_counter += 1
        task.sequence_id = self._task_counter
        setattr(task, "enqueued_at", time.monotonic())
        await self._queue.put(task)
        logger.debug(
            "BotBrain: enqueued task '%s' priority:%d seq:%d size:(%d)",
            task.task_id,
            task.priority,
            task.sequence_id,
            self._queue.qsize(),
        )

    def get_pending_tasks(self) -> list[dict[str, Any]]:
        """回傳目前在 PriorityQueue 內排隊等待大腦推理的任務列表。"""
        items = list(getattr(self._queue, "_queue", []))
        result = []
        for task in items:
            prompt_raw = getattr(task, "user_prompt", "") or ""
            prompt_preview = (prompt_raw[:120] + "...") if len(prompt_raw) > 120 else prompt_raw
            result.append({
                "task_id": getattr(task, "task_id", "unnamed"),
                "priority": getattr(task, "priority", 0),
                "sequence_id": getattr(task, "sequence_id", 0),
                "model": getattr(task, "model", settings.PRIMARY_MODEL),
                "prompt_preview": prompt_preview,
                "has_images": bool(getattr(task, "images", None)),
            })
        return result

    async def _inference_worker(self) -> None:
        MAX_RETRIES = settings.BOT_BRAIN_MAX_RETRIES
        RETRY_DELAY = settings.BOT_BRAIN_RETRY_DELAY

        logger.info("BotBrain: 大腦非同步心跳跳動中。等待新推斷任務注入。")
        while True:
            task: InferenceTask = await self._queue.get()
            is_cloud = task.model.startswith(("openai:", "deepseek:", "gemini:", "claude:", "openrouter:"))
            if is_cloud:
                # 雲端模型不佔用本地 GPU/Lock，背景並行放行
                asyncio.create_task(self._process_task(task, MAX_RETRIES, RETRY_DELAY))
            else:
                # 本地模型依 PriorityQueue 優先級順序串行執行，落實 GPU 鎖與優先權
                await self._process_task(task, MAX_RETRIES, RETRY_DELAY)

    async def _process_task(self, task: InferenceTask, max_retries: int, retry_delay: float) -> None:
        from handlers.social.observability import log_inference, make_timer

        reply = ""
        callback_invoked = False
        queue_start = getattr(task, "enqueued_at", time.monotonic())
        queue_latency_ms = int((time.monotonic() - queue_start) * 1000)
        inf_timer = make_timer()
        current_model = task.model
        last_error_msg = ""
        try:
            has_images = bool(getattr(task, "images", None))

            for attempt in range(1, max_retries + 1):
                # 【動態模型切換機制】降遲迴避功能！（僅限無圖片之純文字任務）
                if attempt == 2 and not has_images:
                    quick_model = getattr(settings, "QUICK_MODEL", current_model)
                    if current_model != quick_model:
                        logger.warning(
                            "BotBrain: attempt %d 觸發! task '%s' 自行捨棄邏輯大核轉快嘴小核降低等候秒數: %s !",
                            attempt,
                            task.task_id,
                            quick_model,
                        )
                        current_model = quick_model

                is_local = True
                if current_model.startswith(("openai:", "deepseek:", "gemini:", "claude:", "openrouter:")):
                    is_local = False

                task_temp = getattr(task, "temperature", None)
                stream_cb = getattr(task, "stream_callback", None)

                async def _execute_chat() -> str:
                    if stream_cb:
                        token_chunks: list[str] = []
                        async for chunk in self._ollama.chat_stream(
                            model=current_model,
                            system_prompt=task.system_prompt,
                            user_prompt=task.user_prompt,
                            images=task.images,
                            temperature=task_temp,
                        ):
                            token_chunks.append(chunk)
                            try:
                                await stream_cb(chunk)
                            except Exception as stream_err:
                                logger.warning("BotBrain: stream_callback 處理異常: %s", stream_err)
                        return "".join(token_chunks).strip()
                    else:
                        return await self._ollama.chat(
                            model=current_model,
                            system_prompt=task.system_prompt,
                            user_prompt=task.user_prompt,
                            images=task.images,
                            temperature=task_temp,
                        )

                try:
                    if is_local:
                        # 狀態三：圖片生成中，等待 Event 通知（零 CPU 輪詢開銷）
                        await self._sd_event.wait()

                        async with self._lock:  # [強保證:永遠僅存在一列單排等待處裡任務送往本地]
                            reply = await _execute_chat()
                    else:
                        # 雲端模型直接併發放行，不獲取鎖
                        reply = await _execute_chat()
                except aiohttp.ClientError as exc:
                    last_error_msg = str(exc)
                    logger.error(
                        "BotBrain: 連線或 API 訪問異常 task '%s' (attempt %d/%d) -> err: %s",
                        task.task_id,
                        attempt,
                        max_retries,
                        exc,
                    )
                except ValueError as exc:
                    last_error_msg = str(exc)
                    logger.error(
                        "BotBrain: Schema Json 返回資料毀損異常 task '%s' (attempt %d/%d) -> err: %s",
                        task.task_id,
                        attempt,
                        max_retries,
                        exc,
                    )
                except Exception as exc:
                    last_error_msg = str(exc)
                    logger.exception(
                        "BotBrain: 未預期報錯 task '%s' (attempt %d/%d) -> err: %s",
                        task.task_id,
                        attempt,
                        max_retries,
                        exc,
                    )

                if reply:
                    break

                logger.warning("BotBrain: Empty reply! Task: %s attempt(%d/%d).", task.task_id, attempt, max_retries)
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)

            inf_latency_ms = inf_timer()
            prompt_len = len(task.system_prompt or "") + len(task.user_prompt or "")
            completion_len = len(reply)
            status_str = "success" if reply else "error"

            log_inference(
                task_id=task.task_id,
                model=current_model,
                prompt_length=prompt_len,
                completion_length=completion_len,
                queue_latency_ms=queue_latency_ms,
                inference_latency_ms=inf_latency_ms,
                status=status_str,
                prompt_preview=task.user_prompt[:150] if task.user_prompt else "",
                completion_preview=reply[:150] if reply else "",
                error_msg=last_error_msg,
            )

            if reply:
                callback_invoked = True
                await task.callback(reply)
            else:
                logger.error("BotBrain: 徹查無獲(give-up)放棄 task: '%s'.", task.task_id)
                callback_invoked = True
                await task.callback("")

        except Exception as exc:
            logger.exception("BotBrain: _process_task 致命異常！！ task: %s -> err:%s", task.task_id, exc)
            if not callback_invoked:
                try:
                    await task.callback("")
                except Exception:
                    pass
        finally:
            self._queue.task_done()