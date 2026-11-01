from __future__ import annotations

import threading
import time
from collections import OrderedDict

from .buffer import ReplyBuffer
from .contracts import ChatEvent, RoomPhase, RoomSnapshot, SendTicket


class ChannelRuntime:
    """單一頻道的狀態機與交易式開窗。

    不變量：
    1. begin_turn 之後、commit/abort 之前，window_open() 必為 True。
    2. 人類訊息只進 buffer，不會因為 phase 而丟棄。
    3. commit_send 只寫一次 last_bot_at；abort_turn 回復上一個錨點。
    """

    def __init__(self, channel_id: str, *, max_humans_since_bot: int = 3) -> None:
        self.channel_id = str(channel_id)
        self._lock = threading.RLock()
        self.buffer = ReplyBuffer()

        self.phase = RoomPhase.IDLE
        self.pending_ticket: SendTicket | None = None

        self.last_bot_at = 0.0
        self.last_human_at = 0.0
        self.opened_at = 0.0
        # 雪莉上一句主要是在對誰說；用來判斷「這個人的下一句」是否為追話。
        # 主動發言沒有指定對象時保持空字串，代表窗口內任何人的第一句都算續聊。
        self.last_bot_target_id = ""
        self.participants: set[str] = set()
        self.humans_since_bot = 0
        self.lurk_streak = 0
        self.turns = 0
        self.cooldown_until = 0.0

        self.last_reason = ""
        self.last_model = ""
        self.last_gen_ms = 0
        self.last_error = ""
        self.updated_at = time.time()

        self.max_humans_since_bot = max(1, int(max_humans_since_bot))
        self.window_seconds = 180.0
        # 熱聊窗口關閉後，只要對方是在回雪莉上一句，仍給一段較長的追話寬限。
        # 這段期間不需要插話分數；但仍受 max_humans_since_bot 與額度限制。
        self.followup_seconds = 900.0
        self._answered: OrderedDict[str, float] = OrderedDict()
        self._turn_seq = 0

    def configure_window(
        self,
        window_seconds: float,
        *,
        followup_seconds: float | None = None,
    ) -> None:
        """由 worker 在每次讀取 WebUI 設定後同步，供 note_human 判斷插話。"""
        with self._lock:
            self.window_seconds = max(0.0, float(window_seconds))
            if followup_seconds is not None:
                self.followup_seconds = max(0.0, float(followup_seconds))

    # ------------------------------------------------------------------
    # 人類訊息
    # ------------------------------------------------------------------
    def note_human(self, event: ChatEvent) -> bool:
        """記錄人類訊息。回傳是否為新訊息（重複則 False）。"""
        with self._lock:
            new = self.buffer.put(event)
            if not new:
                return False
            self.last_human_at = max(self.last_human_at, float(event.created_at))
            self.updated_at = time.time()

            now = time.time()
            if not self._window_open_locked(now, self.window_seconds):
                self.lurk_streak += 1
                # 窗口關閉但仍在追話寬限期內：把這句算進「同一段對話」。
                # 超過 max_humans_since_bot 後自動不再視為追話，避免整群人洗版。
                if self._followup_open_locked(
                    now,
                    self.followup_seconds,
                    str(event.author_id),
                ):
                    self.humans_since_bot += 1
            elif self.participants and str(event.author_id) not in self.participants:
                self.humans_since_bot += 1
            return True

    def has_pending_after(self, ts: float) -> bool:
        return self.buffer.has_pending_after(ts)

    def drain_pending(self, *, after: float = 0.0) -> list[ChatEvent]:
        return self.buffer.drain(lambda event: event.created_at >= after)

    def pending_events(self, *, after: float = 0.0) -> list[ChatEvent]:
        return self.buffer.pending(after=after)

    # ------------------------------------------------------------------
    # 回合交易
    # ------------------------------------------------------------------
    def begin_turn(
        self,
        *,
        now: float | None = None,
        target_author_id: str = "",
        reason: str = "",
        event_ids: tuple[str, ...] = (),
    ) -> SendTicket:
        with self._lock:
            if self.pending_ticket is not None:
                # 正常情況下 worker 會序列化；若真的重入，先中止舊 ticket。
                self._abort_locked(self.pending_ticket)
            ts = time.time() if now is None else float(now)
            self._turn_seq += 1
            ticket = SendTicket(
                channel_id=self.channel_id,
                turn_id=f"{self.channel_id}-{int(ts * 1000)}-{self._turn_seq}",
                opened_at=ts,
                target_author_id=str(target_author_id or ""),
                reason=reason,
                event_ids=tuple(event_ids),
                prev_last_bot_at=self.last_bot_at,
                prev_last_bot_target_id=self.last_bot_target_id,
                prev_participants=set(self.participants),
                prev_humans_since_bot=self.humans_since_bot,
            )
            self.pending_ticket = ticket
            self.phase = RoomPhase.THINKING
            # 【核心】先開窗：生成期間抵達的訊息會被視為延續。
            self.last_bot_at = max(self.last_bot_at, ts)
            self.opened_at = ts
            # 主動發言沒有特定對象時，清空 participants，讓窗口內任何人都能被視為續聊；
            # 被動回覆則鎖定本次的 target。
            self.participants = {str(target_author_id)} if target_author_id else set()
            self.last_bot_target_id = str(target_author_id or "")
            self.updated_at = time.time()
            return ticket

    def commit_send(
        self,
        ticket: SendTicket,
        *,
        sent_at: float,
        message_ids: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            if ticket.cancelled or self.pending_ticket is not ticket:
                return
            self.pending_ticket = None
            ticket.committed_at = float(sent_at)
            ticket.message_ids = tuple(message_ids)
            self.last_bot_at = float(sent_at)
            self.last_reason = ticket.reason
            self.humans_since_bot = 0
            self.lurk_streak = 0
            self.turns += 1
            self.phase = RoomPhase.LISTENING
            for mid in message_ids:
                if mid:
                    self._answered[str(mid)] = time.time()
            for mid in ticket.event_ids:
                self._answered[str(mid)] = time.time()
            self._prune_answered_locked()
            self.updated_at = time.time()

    def abort_turn(self, ticket: SendTicket, *, error: str = "") -> None:
        with self._lock:
            if ticket.cancelled:
                return
            ticket.cancelled = True
            self._abort_locked(ticket)
            if error:
                self.last_error = str(error)[:300]
            self.updated_at = time.time()

    def _abort_locked(self, ticket: SendTicket) -> None:
        if self.pending_ticket is not ticket:
            return
        self.pending_ticket = None
        # 回復上一個錨點，避免「生成失敗卻開著窗」的幽靈狀態。
        self.last_bot_at = ticket.prev_last_bot_at
        self.last_bot_target_id = str(ticket.prev_last_bot_target_id or "")
        self.participants = set(ticket.prev_participants)
        self.humans_since_bot = int(ticket.prev_humans_since_bot)
        self.phase = (
            RoomPhase.LISTENING
            if self._window_open_locked(time.time(), self.window_seconds)
            else RoomPhase.IDLE
        )

    # ------------------------------------------------------------------
    # 外部發言同步
    # ------------------------------------------------------------------
    def note_external_bot_speak(
        self,
        *,
        sent_at: float,
        target_author_id: str = "",
        message_id: str = "",
    ) -> None:
        """讓 Cyber 知道 @雪莉／正常 router 已經發言，避免狀態分裂。"""
        with self._lock:
            ts = float(sent_at)
            self.last_bot_at = max(self.last_bot_at, ts)
            self.opened_at = ts
            # 有明確對象才鎖定；主動／手動冒泡沒有對象，清空 participants。
            self.participants = {str(target_author_id)} if target_author_id else set()
            self.last_bot_target_id = str(target_author_id or "")
            self.humans_since_bot = 0
            self.lurk_streak = 0
            self.phase = RoomPhase.LISTENING
            if message_id:
                self._answered[str(message_id)] = time.time()
            self.updated_at = time.time()

    # ------------------------------------------------------------------
    # 查詢
    # ------------------------------------------------------------------
    def window_open(self, *, now: float | None = None, window_seconds: float = 180.0) -> bool:
        with self._lock:
            return self._window_open_locked(
                time.time() if now is None else float(now),
                float(window_seconds),
            )

    def _window_open_locked(self, now: float, window_seconds: float) -> bool:
        if self.pending_ticket is not None:
            return True
        if window_seconds <= 0 or self.last_bot_at <= 0:
            return False
        if self.humans_since_bot > self.max_humans_since_bot:
            return False
        return (now - self.last_bot_at) <= window_seconds

    def in_followup(
        self,
        *,
        now: float | None = None,
        followup_seconds: float | None = None,
        author_id: str = "",
    ) -> bool:
        """是否仍在「回雪莉上一句」的追話寬限期內。

        與 window_open 不同：這裡不要求秒級熱聊窗口仍開著，只要求：
        - 雪莉最近有發言；
        - 距今未超過 followup_seconds；
        - 雪莉上一句不是針對別人（若已知 target）；
        - 這段追話還沒被太多人／太多句洗掉。
        """
        with self._lock:
            seconds = self.followup_seconds if followup_seconds is None else float(followup_seconds)
            ts = time.time() if now is None else float(now)
            return self._followup_open_locked(ts, seconds, str(author_id or ""))

    def _followup_open_locked(self, now: float, followup_seconds: float, author_id: str) -> bool:
        if self.pending_ticket is not None:
            return True
        if followup_seconds <= 0 or self.last_bot_at <= 0:
            return False
        if self.humans_since_bot > self.max_humans_since_bot:
            return False
        if (now - self.last_bot_at) > followup_seconds:
            return False
        if self.last_bot_target_id and author_id and str(author_id) != self.last_bot_target_id:
            # 雪莉上一句是對 A 說的，B 突然接話不自動算追話；
            # B 若真的要找雪莉，會走 @／名字／引用回覆的 addressed 路徑。
            return False
        return True

    def in_cooldown(self, now: float | None = None) -> bool:
        with self._lock:
            return (time.time() if now is None else float(now)) < self.cooldown_until

    def mark_cooldown(self, seconds: float, *, now: float | None = None) -> None:
        with self._lock:
            ts = time.time() if now is None else float(now)
            self.cooldown_until = max(self.cooldown_until, ts + max(0.0, float(seconds)))
            self.phase = RoomPhase.COOLDOWN
            self.updated_at = time.time()

    def set_phase(self, phase: RoomPhase) -> None:
        with self._lock:
            self.phase = phase
            self.updated_at = time.time()

    def reset_lurk(self) -> None:
        with self._lock:
            self.lurk_streak = 0
            self.updated_at = time.time()

    def note_generation(self, *, model: str, gen_ms: int) -> None:
        with self._lock:
            self.last_model = str(model)
            self.last_gen_ms = int(gen_ms)
            self.last_error = ""
            self.updated_at = time.time()

    def is_answered(self, message_id: str) -> bool:
        with self._lock:
            return str(message_id) in self._answered

    def mark_answered(self, message_ids: tuple[str, ...] | list[str]) -> None:
        """放棄重試時使用：標記為已處理，訊息仍留在 Discord 歷史中。"""
        with self._lock:
            for mid in message_ids:
                if mid:
                    self._answered[str(mid)] = time.time()
            self._prune_answered_locked()
            self.updated_at = time.time()

    def snapshot(self, *, window_seconds: float | None = None) -> RoomSnapshot:
        with self._lock:
            now = time.time()
            seconds = self.window_seconds if window_seconds is None else float(window_seconds)
            open_ = self._window_open_locked(now, seconds)
            remaining = max(0.0, seconds - (now - self.last_bot_at)) if open_ else 0.0
            followup_open = self._followup_open_locked(now, self.followup_seconds, "")
            followup_remaining = (
                max(0.0, self.followup_seconds - (now - self.last_bot_at))
                if followup_open
                else 0.0
            )
            return RoomSnapshot(
                channel_id=self.channel_id,
                phase=self.phase.value,
                window_open=open_,
                window_remaining=remaining,
                followup_open=followup_open,
                followup_remaining=followup_remaining,
                opened_at=self.opened_at,
                last_bot_at=self.last_bot_at,
                last_human_at=self.last_human_at,
                pending=len(self.buffer),
                lurk_streak=self.lurk_streak,
                turns=self.turns,
                last_reason=self.last_reason,
                last_model=self.last_model,
                last_gen_ms=self.last_gen_ms,
                last_error=self.last_error,
                updated_at=self.updated_at,
            )

    def _prune_answered_locked(self) -> None:
        cutoff = time.time() - 3600.0
        for mid, ts in list(self._answered.items()):
            if ts < cutoff:
                self._answered.pop(mid, None)
            else:
                break
