import dataclasses
from typing import Awaitable, Callable

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
    images: list[str] | None = dataclasses.field(default=None, compare=False)
    temperature: float | None = dataclasses.field(default=None, compare=False)
    max_tokens: int | None = dataclasses.field(default=None, compare=False)
    stream_callback: Callable[[str], Awaitable[None]] | None = dataclasses.field(default=None, compare=False)
    is_cancelled: bool = dataclasses.field(default=False, compare=False)

    def cancel(self) -> None:
        """標記該推論任務已取消，通知佇列 Worker 與底層推論跳過或中斷執行。"""
        self.is_cancelled = True