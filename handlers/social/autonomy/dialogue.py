"""handlers/social/autonomy/dialogue.py
=====================================
SOLO（正式群）的對話鏈判定。

這裡只回答一個問題：**這則人類訊息，是不是在跟雪莉說話？**

舊版只用「距離她上次發言 < N 秒」的計時器判斷，結果是：
她只要講過一句話（甚至只是被 @ 一次），接下來幾分鐘內每個人的每句話都會被她接，
變成她自己跟自己續杯的話癆循環。

新版把「熱聊窗口」換成「對話鏈」：
- ADDRESSED    ：明確叫她（tag / 引用回覆 / 文中帶名字）→ 直接回。
- CONTINUATION ：她剛才就在跟這個人一來一往，而且中間沒有別人插隊 → 接話。
- AMBIENT      ：別人在聊，沒有她的位置 → 最多只能走低機率插話評分。

判定本身不做 I/O，方便單元測試；時間與額度由呼叫端注入。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable


class DialoguePhase(str, Enum):
    """一則人類訊息相對雪莉的關係。"""

    ADDRESSED = "addressed"
    CONTINUATION = "continuation"
    AMBIENT = "ambient"

    @property
    def is_for_sherry(self) -> bool:
        """是否算「在跟她說話」，可以直接進入生成。"""
        return self is not DialoguePhase.AMBIENT


# 預設會被視為「叫她的名字」的字串；比對時不分大小寫。
DEFAULT_NAME_TOKENS: tuple[str, ...] = ("雪莉", "sherry")


@dataclass
class DialogueState:
    """單一頻道的對話鏈狀態。只存在記憶體，重啟後自然冷卻。"""

    last_bot_at: float = 0.0
    last_target_author_id: str = ""
    participants: set[str] = field(default_factory=set)
    humans_since_bot: int = 0
    open: bool = False


class DialogueTracker:
    """追蹤「雪莉正在跟誰講話」的極簡狀態機。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        max_humans_since_bot: int = 1,
        name_tokens: Iterable[str] = DEFAULT_NAME_TOKENS,
    ) -> None:
        self._clock = clock or time.time
        self.max_humans_since_bot = max(0, int(max_humans_since_bot))
        self.name_tokens = tuple(str(t).strip().lower() for t in name_tokens if str(t).strip())
        self._states: dict[str, DialogueState] = {}

    # ------------------------------------------------------------------
    # 狀態查詢
    # ------------------------------------------------------------------
    def state(self, channel_id: int | str) -> DialogueState:
        key = str(channel_id)
        state = self._states.get(key)
        if state is None:
            state = DialogueState()
            self._states[key] = state
        return state

    def reset(self, channel_id: int | str | None = None) -> None:
        """清空狀態；測試與熱重載用。"""
        if channel_id is None:
            self._states.clear()
            return
        self._states.pop(str(channel_id), None)

    # ------------------------------------------------------------------
    # 事件回報
    # ------------------------------------------------------------------
    def note_human_message(self, channel_id: int | str, author_id: int | str = "") -> DialogueState:
        """記錄一則人類訊息。

        ``humans_since_bot`` 只計算「別人插進來的句子」：
        她正在跟 A 講話時，A 連續補兩句不算被打斷，只有 B 插話才會讓對話鏈斷掉。
        ``participants`` 只由 ``note_bot_speak`` 維護（她實際回過的人），
        否則每個路過的群友都會被算成「正在跟她聊」。
        """
        state = self.state(channel_id)
        author = str(author_id or "")
        current_target = str(state.last_target_author_id or "")
        if not (current_target and author and author == current_target):
            state.humans_since_bot += 1
        return state

    def note_bot_speak(
        self,
        channel_id: int | str,
        *,
        target_author_id: int | str = "",
        now: float | None = None,
        keep_participants: bool = False,
    ) -> DialogueState:
        """記錄雪莉發言：開啟對話鏈，並重置「中間插了幾句」的計數。

        ``target_author_id`` 是這次發言實際回覆的人；沒給就清空參與者，
        代表這是一次「沒有特定對象」的發言（例如主動冒泡）。
        ``keep_participants`` 用於機器人自己訊息的回音事件：只刷新時間，不動參與者。
        """
        ts = self._clock() if now is None else float(now)
        state = self.state(channel_id)
        state.last_bot_at = ts
        state.open = True
        state.humans_since_bot = 0
        if target_author_id:
            state.participants = {str(target_author_id)}
            state.last_target_author_id = str(target_author_id)
        elif not keep_participants:
            state.participants = set()
            state.last_target_author_id = ""
        return state

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def classify(
        self,
        channel_id: int | str,
        *,
        author_id: int | str = "",
        content: str = "",
        is_reply_to_bot: bool = False,
        is_mentioned: bool = False,
        window_seconds: float = 180.0,
        now: float | None = None,
    ) -> DialoguePhase:
        """判定這則訊息是不是在跟雪莉說話。

        注意：``is_mentioned`` 為 True 時呼叫端通常已經把訊息交給一般 router，
        這裡保留參數是為了讓判定本身可獨立測試。
        """
        ts = self._clock() if now is None else float(now)
        author = str(author_id)

        if is_mentioned or is_reply_to_bot:
            return DialoguePhase.ADDRESSED
        if self._mentions_name(content):
            return DialoguePhase.ADDRESSED

        state = self.state(channel_id)
        if not state.open:
            return DialoguePhase.AMBIENT

        window = max(0.0, float(window_seconds))
        if window <= 0 or (ts - state.last_bot_at) > window:
            return DialoguePhase.AMBIENT

        # 中間已經有別人插話、而她沒有回，對話鏈就斷了。
        if state.humans_since_bot > self.max_humans_since_bot:
            return DialoguePhase.AMBIENT

        if state.participants and author and author not in state.participants:
            # 沒被叫到、也不是她剛才在聊的人 → 當成別人的對話。
            return DialoguePhase.AMBIENT
        if state.participants and not author:
            return DialoguePhase.AMBIENT

        return DialoguePhase.CONTINUATION

    def _mentions_name(self, content: str) -> bool:
        if not content or not self.name_tokens:
            return False
        lowered = str(content).lower()
        return any(token in lowered for token in self.name_tokens)


_tracker: DialogueTracker | None = None


def get_dialogue_tracker() -> DialogueTracker:
    """取得全域對話鏈追蹤器（正式群共用一份狀態）。"""
    global _tracker
    if _tracker is None:
        _tracker = DialogueTracker()
    return _tracker


def reset_dialogue_tracker() -> None:
    """測試用：清空全域追蹤器。"""
    global _tracker
    if _tracker is not None:
        _tracker.reset()
