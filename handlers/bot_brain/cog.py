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
        self._background_tasks: set[asyncio.Task[Any]] = set()
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
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        for bg_t in list(self._background_tasks):
            if not bg_t.done():
                bg_t.cancel()
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
            try:
                task: InferenceTask = await self._queue.get()
                # 佇列取出時立即檢查取消狀態，避免浪費資源
                if getattr(task, "is_cancelled", False):
                    logger.info("BotBrain: 任務 '%s' 取出時已處於取消狀態，跳過推論！", task.task_id)
                    self._queue.task_done()
                    continue

                is_cloud = task.model.startswith(("openai:", "deepseek:", "gemini:", "claude:", "openrouter:"))
                # VRAM 防護與零等待降級：地端生圖中時自動將本地對話導向雲端模型
                if not is_cloud and self._sd_generating:
                    cloud_fallback = str(getattr(settings, "CLOUD_FALLBACK_MODEL", "gemini:gemini-2.5-flash"))
                    logger.info("BotBrain: 偵測到地端生圖中 (VRAM 鎖定)，任務 '%s' 自動切換至雲端模型 %s", task.task_id, cloud_fallback)
                    task.model = cloud_fallback
                    is_cloud = True

                if is_cloud:
                    # 雲端模型不佔用本地 GPU/Lock，背景並行放行（保持強引用防 GC）
                    t = asyncio.create_task(self._process_task(task, MAX_RETRIES, RETRY_DELAY))
                    self._background_tasks.add(t)
                    t.add_done_callback(self._background_tasks.discard)
                else:
                    # 本地模型依 PriorityQueue 優先級順序串行執行，落實 GPU 鎖與優先權
                    await self._process_task(task, MAX_RETRIES, RETRY_DELAY)
            except asyncio.CancelledError:
                logger.info("BotBrain: Inference Worker 收到取消信號，正常終止。")
                raise
            except Exception as exc:
                logger.critical("BotBrain: Inference Worker 主循環異常（已保護重啟）: %s", exc, exc_info=True)
                await asyncio.sleep(0.5)

    async def _process_task(self, task: InferenceTask, max_retries: int, retry_delay: float) -> None:
        from handlers.social.observability import get_trace_id, log_error, log_inference, make_timer

        # 幽靈任務防禦：上層若已超時取消，直接跳過推論以釋放本地 GPU / 雲端算力
        if getattr(task, "is_cancelled", False):
            logger.info("BotBrain: 偵測到 Task '%s' 已在排隊期間被取消，跳過推論！", task.task_id)
            return

        reply = ""
        callback_invoked = False
        max_timeout_retries = 1
        timeout_retries_done = 0
        queue_start = getattr(task, "enqueued_at", time.monotonic())
        queue_latency_ms = int((time.monotonic() - queue_start) * 1000)
        inf_timer = make_timer()
        current_model = task.model
        last_error_msg = ""
        try:
            has_images = bool(getattr(task, "images", None))

            for attempt in range(1, max_retries + 1):
                if getattr(task, "is_cancelled", False):
                    logger.info("BotBrain: Task '%s' 於 attempt %d 前被取消，中止後續嘗試！", task.task_id, attempt)
                    break

                attempt_timer = make_timer()
                # 【動態模型切換機制】降遲與容錯備援
                if attempt >= 2:
                    if current_model.startswith("gemini:") or task.model.startswith("gemini:"):
                        # Gemini 雲端模型（支援純文字與多模態圖片）依序嘗試官方穩定備援序列
                        fallback_pool = list(getattr(settings, "GEMINI_FALLBACK_MODELS", [
                            "gemini:gemini-2.5-flash",
                            "gemini:gemini-2.0-flash",
                            "gemini:gemini-1.5-flash",
                        ]))
                        candidates = [task.model] + [m for m in fallback_pool if m != task.model]
                        target_idx = min(attempt - 1, len(candidates) - 1)
                        gemini_fallback = candidates[target_idx]
                        if current_model != gemini_fallback:
                            logger.warning(
                                "BotBrain: attempt %d 觸發! task '%s' 啟用 Gemini 備援模型: %s (原模型: %s)",
                                attempt,
                                task.task_id,
                                gemini_fallback,
                                current_model,
                            )
                            current_model = gemini_fallback
                    elif attempt == 2 and not has_images:
                        # 本地/純文字任務：切換至快嘴小核降低等候秒數
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
                            max_tokens=getattr(task, "max_tokens", None),
                        ):
                            # 串流執行中若被上層取消（例如 router 對視覺模型超時），
                            # 立即中斷串流釋放本地 GPU，否則會一路跑完佔住 GPU
                            if getattr(task, "is_cancelled", False):
                                logger.info(
                                    "BotBrain: 串流推論中偵測到 Task '%s' 被取消，中斷串流！",
                                    task.task_id,
                                )
                                break
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
                            max_tokens=getattr(task, "max_tokens", None),
                        )

                try:
                    if is_local:
                        # 狀態三：圖片生成中，等待 Event 通知（零 CPU 輪詢開銷）
                        await self._sd_event.wait()

                        if getattr(task, "is_cancelled", False):
                            logger.info("BotBrain: Task '%s' 於等候 GPU 鎖期間被取消，跳過！", task.task_id)
                            break

                        async with self._lock:  # [強保證:永遠僅存在一列單排等待處裡任務送往本地]
                            if getattr(task, "is_cancelled", False):
                                break
                            lock_wait_ms = int((time.monotonic() - queue_start) * 1000)
                            gen_start = make_timer()
                            reply = await _execute_chat()
                            gen_ms = gen_start()
                            logger.info(
                                "BotBrain: task '%s' 拿到GPU鎖時已等 %dms, 推理生成 %dms, model=%s",
                                task.task_id, lock_wait_ms, gen_ms, current_model,
                            )
                    else:
                        # 雲端模型直接併發放行，不獲取鎖
                        reply = await _execute_chat()
                except asyncio.TimeoutError as exc:
                    # 明確的逾時：本機模型重試只會疊加 GPU 負載，限制重試次數
                    timeout_retries_done += 1
                    last_error_msg = f"TimeoutError: {exc}"
                    logger.error(
                        "BotBrain: 推理逾時 task '%s' (attempt %d/%d) -> err: %s",
                        task.task_id, attempt, max_retries, exc,
                    )
                except aiohttp.ClientError as exc:
                    last_error_msg = str(exc)
                    is_timeout = isinstance(exc, asyncio.TimeoutError) or "timeout" in str(exc).lower()
                    if is_timeout:
                        timeout_retries_done += 1
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
                    logger.info(
                        "BotBrain: task '%s' attempt %d/%d 成功, 本次耗時 %dms, 總耗時 %dms, prompt=%d字, 回覆=%d字, model=%s",
                        task.task_id, attempt, max_retries,
                        attempt_timer(), inf_timer(),
                        len(task.system_prompt or "") + len(task.user_prompt or ""),
                        len(reply), current_model,
                    )
                    break

                logger.warning(
                    "BotBrain: Empty reply! Task: %s attempt(%d/%d) 本次耗時 %dms 總耗時 %dms 逾時重試次數已用 %d/%d model=%s.",
                    task.task_id, attempt, max_retries,
                    attempt_timer(), inf_timer(),
                    timeout_retries_done, max_timeout_retries,
                    current_model,
                )
                # 逾時/連線類錯誤不無腦重試：超過上限即提前放棄，避免疊加本機 GPU 負載
                if attempt < max_retries and timeout_retries_done < max_timeout_retries:
                    is_rate_or_overload = any(
                        keyword in last_error_msg.lower()
                        for keyword in ("503", "429", "high demand", "service unavailable", "overloaded", "quota")
                    )
                    wait_delay = (retry_delay * attempt) if is_rate_or_overload else retry_delay
                    if is_rate_or_overload:
                        logger.info(
                            "BotBrain: 偵測到服務端負載尖峰 (503/429)，等待 %.1f 秒後進行第 %d 次嘗試...",
                            wait_delay,
                            attempt + 1,
                        )
                    await asyncio.sleep(wait_delay)

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
                log_error(
                    event="inference_failure",
                    task_id=task.task_id,
                    trace_id=getattr(task, "trace_id", "") or get_trace_id(),
                    error_type="InferenceGiveUp",
                    message=f"Task {task.task_id} 在 {attempt} 次嘗試後徹底失敗放棄: {last_error_msg}",
                    latency_ms=inf_latency_ms,
                    channel_id=getattr(task, "channel_id", 0),
                    user=getattr(task, "user_name", ""),
                    raw_input=task.user_prompt[:200] if task.user_prompt else "",
                    is_fatal=True,
                )
                callback_invoked = True
                await task.callback("")

        except Exception as exc:
            logger.exception("BotBrain: _process_task 致命異常！！ task: %s -> err:%s", task.task_id, exc)
            try:
                from handlers.social.observability import get_trace_id, log_error
                log_error(
                    event="inference_fatal_exception",
                    task_id=getattr(task, "task_id", ""),
                    trace_id=getattr(task, "trace_id", "") or get_trace_id(),
                    error_type=type(exc).__name__,
                    message=str(exc),
                    latency_ms=inf_timer() if "inf_timer" in locals() else 0,
                    channel_id=getattr(task, "channel_id", 0),
                    user=getattr(task, "user_name", ""),
                    raw_input=getattr(task, "user_prompt", "")[:200] if getattr(task, "user_prompt", "") else "",
                    is_fatal=True,
                )
            except Exception:
                pass
            if not callback_invoked:
                try:
                    await task.callback("")
                except Exception:
                    pass
        finally:
            self._queue.task_done()