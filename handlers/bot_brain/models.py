import asyncio
import dataclasses
from typing import Any, Awaitable, Callable

import settings

@dataclasses.dataclass(order=True)
class InferenceTask:
    """
    提交到任務排程優先級對列內的單一資料模塊 (Task Schema)。
    包含對話邏輯要求，與目標指定要發到什麼模型處理。
    """
    priority: int
    system_prompt: str = dataclasses.field(compare=False)
    user_prompt: str = dataclasses.field(compare=False)
    callback: Callable[[str], Awaitable[None]] = dataclasses.field(compare=False)
    model: str = dataclasses.field(default=settings.PRIMARY_MODEL, compare=False)
    task_id: str = dataclasses.field(default="unnamed", compare=False)
    sequence_id: int = dataclasses.field(default=0)
    enqueued_at: float = dataclasses.field(default=0.0, compare=False)
    images: list[str] | None = dataclasses.field(default=None, compare=False)
    temperature: float | None = dataclasses.field(default=None, compare=False)
    max_tokens: int | None = dataclasses.field(default=None, compare=False)
    # 單一任務的推理強度覆寫（low/medium/high）；None 表示沿用全域設定。
    # 短對話回覆用 low 可以大幅降低思考 Token 與首字延遲。
    reasoning_effort: str | None = dataclasses.field(default=None, compare=False)
    stream_callback: Callable[[str], Awaitable[None]] | None = dataclasses.field(default=None, compare=False)
    is_cancelled: bool = dataclasses.field(default=False, compare=False)
    is_content_filtered: bool = dataclasses.field(default=False, compare=False)
    # 顯式標記是否為自主／背景任務；None 表示由提交端依 priority 門檻判定。
    is_autonomous: bool | None = dataclasses.field(default=None, compare=False)
    # 治理閘門使用的行動代碼與額外參數；讓不同自主任務套用各自的風險映射，
    # 同時仍由 BotBrain 單一 choke point 執行一次預算扣減。
    governance_action: str = dataclasses.field(
        default="bot_brain_autonomous", compare=False
    )
    governance_params: dict[str, object] = dataclasses.field(
        default_factory=dict, compare=False
    )
    # 提交者識別（用於雲端 per-user 併發閘門）；None 表示未提供。
    user_id: int | str | None = dataclasses.field(default=None, compare=False)
    # 本地任務重試回佇列時保留的嘗試次數與逾時重試計數。
    attempt: int = dataclasses.field(default=0, compare=False)
    timeout_retry_count: int = dataclasses.field(default=0, compare=False)
    # 上一輪實際使用的模型（含備援切換）；重試回佇列後維持原本的輪替語意。
    retry_model: str = dataclasses.field(default="", compare=False)
    # 本地任務標記：失敗重試交由排程 Task 放回佇列，而非在 worker 內 sleep。
    requeue_retries: bool = dataclasses.field(default=False, compare=False)
    # 執行中推論請求的強引用：取消時直接 cancel()，讓 aiohttp 請求與上游算力
    # 立即中止，而不是等到下一個 chunk 到來才靠旗標 break。
    _execution_task: asyncio.Task[Any] | None = dataclasses.field(
        default=None, compare=False, repr=False
    )

    def attach_execution_task(self, execution_task: asyncio.Task[Any]) -> None:
        """註冊目前承載串流請求的 Task，供取消入口直接傳播 CancelledError。"""
        self._execution_task = execution_task

    def detach_execution_task(self, execution_task: asyncio.Task[Any] | None = None) -> None:
        """解除執行 Task 引用；傳入 Task 時僅在仍為同一物件時解除。"""
        if execution_task is None or self._execution_task is execution_task:
            self._execution_task = None

    def cancel(self) -> None:
        """標記任務已取消並立即取消底層串流 Task（真實取消傳播）。"""
        self.is_cancelled = True
        execution_task = self._execution_task
        if execution_task is not None and not execution_task.done():
            execution_task.cancel()