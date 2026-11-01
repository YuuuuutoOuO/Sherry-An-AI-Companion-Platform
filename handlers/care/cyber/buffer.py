from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable

from .contracts import ChatEvent


class ReplyBuffer:
    """每頻道的人類訊息緩衝。

    設計重點：
    - 以 message_id 去重，重送 Gateway 事件不會重複回覆。
    - 不因「正在生成」而丟棄；生成期間的訊息會留在這裡，回合結束後立即取出。
    - TTL 以「放入時間」計算，避免 Discord 延遲或測試假時鐘把訊息誤刪。
    """

    def __init__(self, *, ttl_seconds: float = 1800.0, max_items: int = 300) -> None:
        self._ttl = max(60.0, float(ttl_seconds))
        self._max = max(10, int(max_items))
        self._items: OrderedDict[str, tuple[ChatEvent, float]] = OrderedDict()
        self._lock = threading.RLock()
        self._last_put_at = 0.0

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked(time.time())
            return len(self._items)

    def put(self, event: ChatEvent) -> bool:
        """放入一則訊息；重複回傳 False。"""
        if not event.message_id:
            return False
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            key = event.dedupe_key
            if key in self._items:
                return False
            self._items[key] = (event, now)
            self._last_put_at = now
            while len(self._items) > self._max:
                self._items.popitem(last=False)
            return True

    def put_many(self, events: Iterable[ChatEvent]) -> int:
        return sum(1 for event in events if self.put(event))

    def pending(self, *, after: float = 0.0) -> list[ChatEvent]:
        with self._lock:
            self._prune_locked(time.time())
            return [
                event for event, _inserted in self._items.values()
                if event.created_at >= after
            ]

    def has_pending_after(self, ts: float) -> bool:
        return any(event.created_at >= float(ts) for event in self.pending(after=ts))

    def drain(
        self,
        predicate: Callable[[ChatEvent], bool] | None = None,
    ) -> list[ChatEvent]:
        """取出符合條件的訊息並移除；predicate 為 None 時全取。"""
        with self._lock:
            self._prune_locked(time.time())
            if predicate is None:
                drained = [event for event, _inserted in self._items.values()]
                self._items.clear()
                return drained
            drained: list[ChatEvent] = []
            for key, (event, _inserted) in list(self._items.items()):
                if predicate(event):
                    drained.append(event)
                    self._items.pop(key, None)
            return drained

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _prune_locked(self, now: float) -> None:
        if not self._items:
            return
        cutoff = now - self._ttl
        for key, (_event, inserted_at) in list(self._items.items()):
            if inserted_at < cutoff:
                self._items.pop(key, None)
