import asyncio
import logging
import random
from typing import Any
import aiohttp
from discord.ext import commands

import time
import settings
# 直接引用自己剛切出去的分身夥伴檔案！
from core.autonomy_guard import AutonomyGuard, global_autonomy_guard
from handlers.bot_brain.models import InferenceTask
from handlers.bot_brain.client import (
    LLMAuthError,
    LLMContentFiltered,
    LLMRateLimitError,
    LLMStreamError,
    OllamaClient,
    _is_content_filtered,
)

logger = logging.getLogger(__name__)


def _retry_delay_seconds(attempt: int, base_delay: float, max_delay: float = 30.0) -> float:
    """計算指數退避＋抖動的重試等待秒數（attempt 從 1 起算）。

    公式：``min(base * 2**(attempt-1), cap) * uniform(0.8, 1.2)``。
    抖動可避免多個任務在同一時間點同步重試造成二次尖峰。
    """
    if base_delay <= 0:
        return 0.0
    exponential = base_delay * (2 ** max(0, attempt - 1))
    capped = min(exponential, max(0.0, max_delay))
    return capped * random.uniform(0.8, 1.2)


def _worker_backoff_delay(failures: int, base: float = 0.5, cap: float = 60.0) -> float:
    """Worker 連續失敗的指數退避（0.5s → 60s cap）＋抖動。"""
    if failures <= 0:
        return 0.0
    delay = min(base * (2 ** (failures - 1)), max(base, cap))
    return delay * random.uniform(0.85, 1.15)


def _is_cloud_model(model_name: str) -> bool:
    """判斷模型是否屬於雲端免鎖模型（包含前綴與常用名稱模式）。"""
    m = model_name.strip().lower()
    return m.startswith((
        "openai:", "deepseek:", "gemini:", "claude:", "openrouter:", "groq:",
        "gemini-", "models/gemini", "gpt-", "o1-", "o3-", "claude-", "deepseek-",
    ))


def _canonical_model_name(model_name: str) -> str:
    """去除供應商前綴後的模型正規化名稱，用於比較是否為同一個模型。"""
    m = str(model_name).strip().lower()
    for prefix in ("gemini:", "openai:", "deepseek:", "claude:", "openrouter:", "groq:", "models/"):
        if m.startswith(prefix):
            m = m[len(prefix):]
            break
    return m


def _fallback_candidates(task_model: str, current_model: str) -> list[str]:
    """依 FALLBACK_MODELS 設定回傳備援序列，剔除與原模型／當前模型重複者。

    這是「推論失敗怎麼辦」的單一真相來源，可在 Web UI 設定。
    字串直接比較無法辨識 "gemini-3.5-flash-lite" 與 "gemini:gemini-3.5-flash-lite"
    其實是同一個模型，會造成備援時重送完全相同的失敗請求而白費一次嘗試。

    FALLBACK_ENABLED 為 False 時回傳空清單 → 純地端部署失敗就明確失敗，不外送。
    """
    if not bool(getattr(settings, "FALLBACK_ENABLED", True)):
        return []

    seen = {_canonical_model_name(task_model), _canonical_model_name(current_model)}
    candidates: list[str] = []
    for raw in list(getattr(settings, "FALLBACK_MODELS", []) or []):
        name = str(raw).strip()
        if not name:
            continue
        canonical = _canonical_model_name(name)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        candidates.append(name)
    return candidates


def _alternative_models(task_model: str, current_model: str) -> list[str]:
    """蒐集所有可切換的替代模型（含備援序列與快嘴小核）。

    用於判斷「值不值得再試一次」：安全過濾等確定性失敗只有換模型才可能成功。
    """
    seen = {_canonical_model_name(task_model), _canonical_model_name(current_model)}
    candidates: list[str] = []

    def _add(raw: object) -> None:
        name = str(raw).strip()
        if not name:
            return
        canonical = _canonical_model_name(name)
        if canonical in seen:
            return
        seen.add(canonical)
        candidates.append(name)

    for model in _fallback_candidates(task_model, current_model):
        _add(model)
    _add(getattr(settings, "LIGHT_MODEL", ""))
    return candidates


class BotBrain(commands.Cog):
    """大腦推斷單步佇列守衛：限制GPU顯存佔滿之生命線守衛兵。"""
    
    def __init__(self, bot: commands.Bot, autonomy_guard: AutonomyGuard | None = None) -> None:
        self.bot = bot
        self._queue: asyncio.PriorityQueue[InferenceTask] = asyncio.PriorityQueue()
        self._lock = asyncio.Lock()
        self._ollama = OllamaClient()
        self._worker_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        # 本地重試回佇列的延遲任務（強引用，cog_unload 時一併取消）
        self._retry_tasks: set[asyncio.Task[Any]] = set()
        # 雲端併發雙層閘門：全域上限 + 每人上限，避免單一使用者打出請求炸彈。
        self._cloud_gate_global = asyncio.Semaphore(
            max(1, int(getattr(settings, "BOT_BRAIN_CLOUD_MAX_CONCURRENCY", 4)))
        )
        self._cloud_gate_user_limit = max(1, int(getattr(settings, "BOT_BRAIN_CLOUD_PER_USER_CONCURRENCY", 1)))
        self._cloud_gate_users: dict[str, asyncio.Semaphore] = {}
        # Worker 韌性狀態
        self._worker_failures = 0
        self._worker_degraded = False
        # 治理閘門：自主任務提交前檢查熔斷與行動預算（預設共用全域單例）
        self._autonomy_guard = autonomy_guard or global_autonomy_guard
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
        for retry_t in list(self._retry_tasks):
            if not retry_t.done():
                retry_t.cancel()
        await self._ollama.close()
        logger.info("BotBrain: Inference worker 退役中止。")

    def _is_autonomous_task(self, task: InferenceTask) -> bool:
        """判定任務是否屬自主／背景行為而需受治理閘門管制。

        顯式 ``is_autonomous`` 優先；未指定時以優先序門檻判定
        （``settings.PRIORITY_INTERJECT``，數值越大越偏背景）。
        互動任務（Tag 回應、使用者指令）優先序較小，不受閘門影響。

        Args:
            task: 待提交的推論任務。

        Returns:
            bool: True 表示需先通過熔斷／預算檢查。
        """
        explicit = getattr(task, "is_autonomous", None)
        if explicit is not None:
            return bool(explicit)
        try:
            threshold = int(getattr(settings, "PRIORITY_INTERJECT", 10))
        except (TypeError, ValueError):
            threshold = 10
        return int(getattr(task, "priority", 0) or 0) >= threshold

    async def _passes_governance_gate(self, task: InferenceTask) -> bool:
        """自主任務提交前的治理閘門；拒絕時以空回覆通知呼叫端（fail-closed）。

        呼叫端（如主動插話、背景萃取）以 callback 等待結果，若直接丟棄任務
        會讓呼叫端永久懸置；因此拒絕時仍回呼空字串代表本輪無產出。

        Args:
            task: 待提交的推論任務。

        Returns:
            bool: True 表示可入列；False 表示已被拒絕並完成回呼。
        """
        if not self._is_autonomous_task(task):
            return True

        action_type = str(
            getattr(task, "governance_action", "") or "bot_brain_autonomous"
        )
        params: dict[str, object] = {
            "task_id": task.task_id,
            "priority": task.priority,
        }
        extra_params = getattr(task, "governance_params", None)
        if isinstance(extra_params, dict):
            params.update(extra_params)

        decision = self._autonomy_guard.check(
            action_type,
            params=params,
            description=f"背景推論任務 {task.task_id}",
        )
        if decision.allowed:
            return True

        logger.warning(
            "BotBrain: 自主任務 '%s' 被治理閘門拒絕（%s），改以空回覆結束。",
            task.task_id,
            decision.reason,
        )
        try:
            await task.callback("")
        except Exception as exc:
            logger.error("BotBrain: 拒絕自主任務 '%s' 後回呼失敗: %s", task.task_id, exc)
        return False

    async def enqueue_task(self, task: InferenceTask) -> None:
        if not await self._passes_governance_gate(task):
            return
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

    async def generate_reply(
        self,
        prompt: str,
        user_id: int | str = 0,
        channel_id: int | str = 0,
        system_prompt: str | None = None,
        timeout: float = 40.0,
    ) -> str:
        """輕量化同步生成對話回覆（用於狀態恢復接續或直接呼叫）。"""
        from handlers.social.persona import build_dynamic_persona

        head_persona, tail_lore = build_dynamic_persona(
            prompt, "CHAT", channel_id=channel_id if isinstance(channel_id, int) else None
        )
        sys_prompt = system_prompt or f"{head_persona}\n\n{tail_lore}".strip()
        user_instruct = f"對話提問：「{prompt}」\n妳的回覆："

        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        async def _on_reply(reply: str) -> None:
            if not fut.done():
                fut.set_result(reply.strip())

        task = InferenceTask(
            priority=getattr(settings, "PRIORITY_TAG_RESPONSE", 1),
            system_prompt=sys_prompt,
            user_prompt=user_instruct,
            callback=_on_reply,
            model=settings.PRIMARY_MODEL,
            task_id=f"direct-gen-{int(time.time() * 1000)}",
            user_id=user_id,
        )
        await self.enqueue_task(task)
        return await asyncio.wait_for(fut, timeout=timeout)

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

    def _cloud_user_gate(self, task: InferenceTask) -> asyncio.Semaphore | None:
        """取得（或建立）該使用者的雲端併發閘門；未提供 user_id 時僅受全域閘門約束。"""
        user_id = getattr(task, "user_id", None)
        if user_id is None:
            return None
        key = str(user_id)
        gate = self._cloud_gate_users.get(key)
        if gate is None:
            gate = asyncio.Semaphore(self._cloud_gate_user_limit)
            self._cloud_gate_users[key] = gate
        return gate

    async def _run_cloud_task(self, task: InferenceTask, max_retries: int, retry_delay: float) -> None:
        """雲端任務執行外殼：先過全域閘門，再過 per-user 閘門。"""
        user_gate = self._cloud_user_gate(task)
        async with self._cloud_gate_global:
            if user_gate is None:
                await self._process_task(task, max_retries, retry_delay)
            else:
                async with user_gate:
                    await self._process_task(task, max_retries, retry_delay)

    def _schedule_local_retry(self, task: InferenceTask, delay: float) -> None:
        """將本地任務的重試排程回佇列，避免 sleep 佔住唯一的 GPU worker。"""
        retry_task = asyncio.create_task(
            self._requeue_after(task, delay),
            name=f"inference-retry-{task.task_id}",
        )
        self._retry_tasks.add(retry_task)
        retry_task.add_done_callback(self._retry_tasks.discard)

    async def _requeue_after(self, task: InferenceTask, delay: float) -> None:
        """等待退避時間後把任務放回優先級佇列；期間被取消則以空回覆收尾。"""
        try:
            await asyncio.sleep(max(0.0, delay))
        except asyncio.CancelledError:
            if not task.is_cancelled:
                try:
                    await task.callback("")
                except Exception:
                    logger.debug("BotBrain: 重試排程取消後回呼失敗", exc_info=True)
            raise

        if task.is_cancelled:
            try:
                await task.callback("")
            except Exception:
                logger.debug("BotBrain: 已取消任務的重試回呼失敗", exc_info=True)
            return

        await self._queue.put(task)
        logger.info(
            "BotBrain: 本地任務 '%s' 第 %d 次嘗試失敗，已於 %.1fs 後排回佇列（不佔用 GPU worker）。",
            task.task_id,
            getattr(task, "attempt", 0),
            delay,
        )

    async def _inference_worker(self) -> None:
        MAX_RETRIES = settings.BOT_BRAIN_MAX_RETRIES
        RETRY_DELAY = settings.BOT_BRAIN_RETRY_DELAY
        max_worker_failures = max(1, int(getattr(settings, "BOT_BRAIN_WORKER_MAX_FAILURES", 5)))
        backoff_cap = float(getattr(settings, "BOT_BRAIN_WORKER_BACKOFF_CAP", 60.0))

        logger.info("BotBrain: 大腦非同步心跳跳動中。等待新推斷任務注入。")
        while True:
            try:
                task: InferenceTask = await self._queue.get()
                # 佇列取出時立即檢查取消狀態，避免浪費資源
                if getattr(task, "is_cancelled", False):
                    logger.info("BotBrain: 任務 '%s' 取出時已處於取消狀態，跳過推論！", task.task_id)
                    self._queue.task_done()
                    continue

                is_cloud = _is_cloud_model(task.model)
                # VRAM 防護與零等待降級：地端生圖中時自動將本地對話導向雲端模型
                if not is_cloud and self._sd_generating:
                    # 地端生圖佔用 VRAM 時，需把本地對話導向雲端。
                    # 若未設定 FALLBACK_MODELS（或已停用備援），維持本地排隊等待，
                    # 不可在未設定的情況下擅自把內容送到雲端。
                    cloud_fallback = _fallback_candidates(task.model, task.model)
                    if cloud_fallback:
                        target = cloud_fallback[0]
                        logger.info(
                            "BotBrain: 偵測到地端生圖中 (VRAM 鎖定)，任務 '%s' 依 FALLBACK_MODELS 切換至 %s",
                            task.task_id, target,
                        )
                        task.model = target
                        is_cloud = True
                    else:
                        logger.info(
                            "BotBrain: 偵測到地端生圖中 (VRAM 鎖定)，任務 '%s' 未設定可用備援模型，"
                            "維持本地模型排隊等待。",
                            task.task_id,
                        )

                if is_cloud:
                    # 雲端模型不佔用本地 GPU/Lock，但仍受全域＋per-user 雙層閘門管制。
                    t = asyncio.create_task(
                        self._run_cloud_task(task, MAX_RETRIES, RETRY_DELAY),
                        name=f"inference-cloud-{task.task_id}",
                    )
                    self._background_tasks.add(t)
                    t.add_done_callback(self._background_tasks.discard)
                else:
                    # 本地模型依 PriorityQueue 優先級順序串行執行，落實 GPU 鎖與優先權；
                    # 失敗重試由 _process_task 排回佇列，不在此 sleep。
                    task.requeue_retries = True
                    await self._process_task(task, MAX_RETRIES, RETRY_DELAY)

                # 任一輪成功完成即視為 worker 恢復健康
                self._worker_failures = 0
                self._worker_degraded = False
            except asyncio.CancelledError:
                logger.info("BotBrain: Inference Worker 收到取消信號，正常終止。")
                raise
            except Exception as exc:
                self._worker_failures += 1
                delay = _worker_backoff_delay(self._worker_failures, cap=backoff_cap)
                if self._worker_failures >= max_worker_failures and not self._worker_degraded:
                    self._worker_degraded = True
                    logger.critical(
                        "BotBrain: Inference Worker 連續失敗 %d 次，進入降級模式（持續以 %.0fs 上限退避，等待人工介入）。",
                        self._worker_failures,
                        backoff_cap,
                    )
                logger.critical(
                    "BotBrain: Inference Worker 主循環異常（第 %d 次，%.1fs 後重啟）: %s",
                    self._worker_failures,
                    delay,
                    exc,
                    exc_info=True,
                )
                await asyncio.sleep(delay)

    async def _process_task(
        self,
        task: InferenceTask,
        max_retries: int,
        retry_delay: float,
    ) -> None:
        from handlers.social.observability import get_trace_id, log_error, log_inference, make_timer

        reply = ""
        callback_invoked = False
        requeue_local_retries = bool(getattr(task, "requeue_retries", False))
        max_timeout_retries = int(getattr(settings, "BOT_BRAIN_MAX_TIMEOUT_RETRIES", 1))
        max_retry_delay = float(getattr(settings, "BOT_BRAIN_RETRY_MAX_DELAY", 30.0))
        # 重試回佇列後從上次的進度續跑，避免 attempt 歸零導致重試次數被重置。
        timeout_retries_done = int(getattr(task, "timeout_retry_count", 0) or 0)
        first_attempt = max(1, int(getattr(task, "attempt", 0) or 0) + 1)
        attempt = first_attempt - 1
        queue_start = getattr(task, "enqueued_at", time.monotonic())
        queue_latency_ms = int((time.monotonic() - queue_start) * 1000)
        inf_timer = make_timer()
        current_model = getattr(task, "retry_model", "") or task.model
        last_error_msg = ""
        # 本地與雲端任務的備援策略不同：本地優先轉快嘴小核降等候，
        # 雲端直接依設定序列輪替。此旗標在重試過程中不變。
        is_local_task = not _is_cloud_model(task.model)
        try:
            # 幽靈任務防禦：上層若已超時取消，直接跳過推論以釋放本地 GPU / 雲端算力
            if getattr(task, "is_cancelled", False):
                logger.info("BotBrain: 偵測到 Task '%s' 已在排隊期間被取消，跳過推論！", task.task_id)
                return

            has_images = bool(getattr(task, "images", None))

            for attempt in range(first_attempt, max_retries + 1):
                task.attempt = attempt
                if getattr(task, "is_cancelled", False):
                    logger.info("BotBrain: Task '%s' 於 attempt %d 前被取消，中止後續嘗試！", task.task_id, attempt)
                    break

                attempt_timer = make_timer()
                # 【備援切換】統一依 FALLBACK_MODELS 設定決定下一輪要試哪個模型。
                # 不再依供應商分流寫死：純地端部署（FALLBACK_MODELS 留空或
                # FALLBACK_ENABLED=False）會讓本地模型重試到耗盡後明確失敗，
                # 而不會在未設定的情況下把內容外送到雲端。
                if attempt >= 2:
                    if is_local_task and attempt == 2 and not has_images:
                        # 本地純文字任務：先換輕量模型降低等候
                        light_model = str(getattr(settings, "LIGHT_MODEL", "") or "").strip()
                        if light_model and _canonical_model_name(light_model) != _canonical_model_name(current_model):
                            logger.warning(
                                "BotBrain: attempt %d 觸發! task '%s' 轉輕量模型降低等候秒數: %s !",
                                attempt, task.task_id, light_model,
                            )
                            current_model = light_model

                    if _canonical_model_name(current_model) == _canonical_model_name(task.model) or not is_local_task:
                        pool = _fallback_candidates(task.model, current_model)
                        if not pool:
                            logger.warning(
                                "BotBrain: task '%s' 無可用備援模型"
                                "（FALLBACK_ENABLED=False 或 FALLBACK_MODELS 已耗盡），維持原模型重試。",
                                task.task_id,
                            )
                        else:
                            nxt = pool[min(attempt - 2, len(pool) - 1)]
                            logger.warning(
                                "BotBrain: attempt %d 觸發! task '%s' 依 FALLBACK_MODELS 切換至: %s (原模型: %s)",
                                attempt, task.task_id, nxt, current_model,
                            )
                            current_model = nxt
                    else:
                        pool = _fallback_candidates(task.model, current_model)
                        if not pool:
                            logger.warning(
                                "BotBrain: task '%s' 本地模型已無可用備援"
                                "（FALLBACK_MODELS 未設定或已耗盡），維持原模型重試至耗盡。",
                                task.task_id,
                            )
                        else:
                            nxt = pool[min(attempt - 2, len(pool) - 1)]
                            logger.warning(
                                "BotBrain: attempt %d 觸發! task '%s' 本地重試耗盡，依 FALLBACK_MODELS 切換至: %s",
                                attempt, task.task_id, nxt,
                            )
                            current_model = nxt

                is_local = not _is_cloud_model(current_model)

                task_temp = getattr(task, "temperature", None)
                stream_cb = getattr(task, "stream_callback", None)
                task_effort = getattr(task, "reasoning_effort", None)

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
                            reasoning_effort=task_effort,
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
                            reasoning_effort=task_effort,
                        )

                async def _execute_chat_cancellable() -> str:
                    """把實際請求放進獨立 Task，讓 InferenceTask.cancel() 能真正中止。

                    取消會以 CancelledError 傳入 aiohttp 請求，連線立即關閉，
                    不再依賴「下一個 chunk 到來」才靠旗標 break。
                    """
                    chat_task = asyncio.create_task(_execute_chat())
                    task.attach_execution_task(chat_task)
                    try:
                        return await chat_task
                    finally:
                        task.detach_execution_task(chat_task)

                retryable = True
                timed_out = False
                structured_error = False
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
                            reply = await _execute_chat_cancellable()
                            gen_ms = gen_start()
                            logger.info(
                                "BotBrain: task '%s' 拿到GPU鎖時已等 %dms, 推理生成 %dms, model=%s",
                                task.task_id, lock_wait_ms, gen_ms, current_model,
                            )
                    else:
                        # 雲端模型直接併發放行，不獲取鎖
                        reply = await _execute_chat_cancellable()
                except asyncio.CancelledError:
                    if getattr(task, "is_cancelled", False):
                        logger.info(
                            "BotBrain: Task '%s' 的串流請求已取消（連線中止），停止後續嘗試。",
                            task.task_id,
                        )
                        last_error_msg = "cancelled"
                        reply = ""
                        break
                    raise
                except asyncio.TimeoutError as exc:
                    # 明確的逾時：本機模型重試只會疊加 GPU 負載，逾時重試上限獨立計算。
                    timeout_retries_done += 1
                    timed_out = True
                    last_error_msg = f"TimeoutError: {exc}"
                    retryable = timeout_retries_done <= max_timeout_retries
                    logger.error(
                        "BotBrain: 推理逾時 task '%s' (attempt %d/%d) -> err: %s",
                        task.task_id, attempt, max_retries, exc,
                    )
                except LLMContentFiltered as exc:
                    structured_error = True
                    retryable = False
                    task.is_content_filtered = True
                    last_error_msg = f"content_filtered: {exc}"
                    logger.warning(
                        "BotBrain: task '%s' 遭遇供應商內容安全過濾，判定為不可重試之錯誤，立即終止！",
                        task.task_id,
                    )
                except LLMAuthError as exc:
                    structured_error = True
                    retryable = False
                    last_error_msg = str(exc)
                    logger.error(
                        "BotBrain: task '%s' 憑證／權限錯誤，重試與換模型皆無效，立即終止：%s",
                        task.task_id, exc,
                    )
                except LLMRateLimitError as exc:
                    structured_error = True
                    last_error_msg = str(exc)
                    logger.warning(
                        "BotBrain: task '%s' 上游配額／過載 (attempt %d/%d)：%s",
                        task.task_id, attempt, max_retries, exc,
                    )
                except LLMStreamError as exc:
                    structured_error = True
                    retryable = exc.retryable
                    last_error_msg = str(exc)
                    logger.error(
                        "BotBrain: task '%s' 串流錯誤 (attempt %d/%d, retryable=%s)：%s",
                        task.task_id, attempt, max_retries, retryable, exc,
                    )
                except aiohttp.ClientError as exc:
                    last_error_msg = str(exc)
                    # 連線層錯誤通常具暫時性（DNS、重置、連線逾時），保留重試。
                    is_timeout = isinstance(exc, asyncio.TimeoutError) or "timeout" in str(exc).lower()
                    if is_timeout:
                        timeout_retries_done += 1
                        timed_out = True
                        retryable = timeout_retries_done <= max_timeout_retries
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

                # 舊式後端仍可能以 finish_reason=content_filter 表達過濾（HTTP 200 空回應）。
                # 僅在沒有結構化錯誤時採用此 fallback，避免 429/5xx 被誤判。
                if not structured_error and not timed_out and _is_content_filtered(
                    getattr(self._ollama, "last_stream_diagnostics", {})
                ):
                    diag = getattr(self._ollama, "last_stream_diagnostics", {})
                    finish_reason_str = str(diag.get("finish_reason") or "PROHIBITED_CONTENT")
                    logger.warning(
                        "BotBrain: task '%s' 遭遇供應商內容安全過濾 (%s)，判定為不可重試之錯誤，立即終止重試！",
                        task.task_id,
                        finish_reason_str,
                    )
                    last_error_msg = f"content_filtered: {finish_reason_str}"
                    task.is_content_filtered = True
                    break

                if not retryable:
                    break
                if attempt >= max_retries:
                    break

                wait_delay = _retry_delay_seconds(attempt, retry_delay, max_retry_delay)
                if requeue_local_retries:
                    # 本地重試不能 sleep 佔住唯一的 GPU worker：把退避延遲交給
                    # 獨立排程 Task，Worker 立刻回頭服務優先序更高的任務。
                    task.timeout_retry_count = timeout_retries_done
                    task.retry_model = current_model
                    self._schedule_local_retry(task, wait_delay)
                    logger.info(
                        "BotBrain: task '%s' 第 %d 次嘗試失敗，將於 %.1f 秒後排回佇列"
                        "（指數退避＋抖動，不佔用 GPU worker）...",
                        task.task_id,
                        attempt,
                        wait_delay,
                    )
                    return

                logger.info(
                    "BotBrain: task '%s' 將於 %.1f 秒後進行第 %d 次嘗試（指數退避＋抖動）...",
                    task.task_id,
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
                # 內容過濾後放棄是預期行為（非故障），降級為 WARNING 且不標記 fatal，
                # 避免每次過濾都產生「嚴重異常」告警噪音。
                is_filtered = "content_filter" in last_error_msg.lower()
                if is_filtered:
                    logger.warning(
                        "BotBrain: task '%s' 因供應商安全過濾放棄（非故障，內容待檢視）。",
                        task.task_id,
                    )
                else:
                    logger.error("BotBrain: 徹查無獲(give-up)放棄 task: '%s'.", task.task_id)
                log_error(
                    event="inference_failure",
                    task_id=task.task_id,
                    trace_id=getattr(task, "trace_id", "") or get_trace_id(),
                    error_type="ContentFiltered" if is_filtered else "InferenceGiveUp",
                    message=f"Task {task.task_id} 在 {attempt} 次嘗試後徹底失敗放棄: {last_error_msg}",
                    latency_ms=inf_latency_ms,
                    channel_id=getattr(task, "channel_id", 0),
                    user=getattr(task, "user_name", ""),
                    raw_input=task.user_prompt[:200] if task.user_prompt else "",
                    is_fatal=not is_filtered,
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