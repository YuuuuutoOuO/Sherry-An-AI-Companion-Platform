import asyncio
import logging
from typing import Any
import aiohttp
from discord.ext import commands

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
        # VRAM state flags
        self._sd_generating = False
        self._voice_mode_active = False
        self._task_counter = 0

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
        await self._queue.put(task)
        logger.debug("BotBrain: enqueued task '%s' priority:%d seq:%d size:(%d)",
            task.task_id, task.priority, task.sequence_id, self._queue.qsize())

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
            # 建立背景任務以實現真正的並行處理，特別是為了讓雲端模型無需等待
            asyncio.create_task(self._process_task(task, MAX_RETRIES, RETRY_DELAY))

    async def _process_task(self, task: InferenceTask, max_retries: int, retry_delay: float) -> None:
        try:
            reply = ""
            current_model = task.model
            
            for attempt in range(1, max_retries + 1):
                # 【動態模型切換機制】降遲迴避功能！
                if attempt == 2:
                    quick_model = getattr(settings, "QUICK_MODEL", current_model)
                    if current_model != quick_model:
                        logger.warning(
                            "BotBrain: attempt %d 觸發! task '%s' 自行捨棄邏輯大核轉快嘴小核降低等候秒數: %s !",
                            attempt, task.task_id, quick_model)
                        current_model = quick_model

                is_local = True
                if current_model.startswith(("openai:", "deepseek:", "gemini:", "claude:")):
                    is_local = False

                if is_local:
                    # 狀態三：圖片生成中，暫停本地 LLM 推理排程
                    while getattr(self, "_sd_generating", False):
                        await asyncio.sleep(0.5)
                        
                    async with self._lock: # [強保證:永遠僅存在一列單排等待處裡任務送往本地]
                        reply = await self._ollama.chat(
                            model=current_model, system_prompt=task.system_prompt, user_prompt=task.user_prompt, images=task.images
                        )
                else:
                    # 雲端模型直接併發放行，不獲取鎖
                    reply = await self._ollama.chat(
                        model=current_model, system_prompt=task.system_prompt, user_prompt=task.user_prompt, images=task.images
                    )

                if reply: break
                    
                logger.warning("BotBrain: Empty reply! Task: %s attempt(%d/%d).", task.task_id, attempt, max_retries)
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)

            if reply:
                await task.callback(reply)
            else:
                logger.error("BotBrain: 徹查無獲(give-up)放棄 task: '%s'.", task.task_id)

        except aiohttp.ClientError as exc:
            logger.error("BotBrain: Ollama 本機拒絕訪問 %s -> err: %s", task.task_id, exc)
        except ValueError as exc:
            logger.error("BotBrain: Schema Json 返回資料毀損異常 -> %s err: %s", task.task_id, exc)
        except Exception as exc:
            logger.exception("BotBrain: 大破預期未解之報錯！！ task: %s -> err:%s", task.task_id, exc)
        finally:
            self._queue.task_done()