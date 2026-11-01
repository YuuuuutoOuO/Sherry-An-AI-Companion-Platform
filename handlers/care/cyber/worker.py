from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from .contracts import ChatEvent, RoomPhase, SentBatch, TurnDecision
from .model import GenerationResult, PrimaryModelClient, canonical_tool_name
from .policy import ReplyBudget, ReplyPolicy
from .prompts import (
    build_dynamic_context,
    build_identity_block,
    build_system_prompt,
    build_user_prompt,
    build_web_topic_block,
    format_events,
    extract_autonomy_thought,
    parse_autonomy_reply,
    sanitize_reply,
    split_bubbles,
)
from .send import send_bubbles
from .state import ChannelRuntime

logger = logging.getLogger(__name__)


class _BrokerAdapter:
    def __init__(self, broker: Any, policy: Any) -> None:
        self.broker = broker
        self.policy = policy

    async def run_with_policy(self, policy: Any, name: str, args: dict[str, Any]) -> str:
        return await self.broker.run(policy or self.policy, name, args)

    async def run(self, name: str, args: dict[str, Any]) -> str:
        return await self.broker.run(self.policy, name, args)


# cyber lane 只允許唯讀工具；和 AUTONOMOUS_POLICY 保持一致。
_READONLY_TOOLS = frozenset({"SEARCH", "STOCK", "SUMMARY"})


def _readonly_policy(allowed: Any, *, max_calls: Any = 2) -> Any:
    """把 UI／policy 的工具清單收斂成合法的唯讀 ToolPolicy。"""
    from handlers.interaction.contracts import ToolPolicy

    names: list[str] = []
    for item in allowed or ():
        canonical = canonical_tool_name(str(item))
        if canonical in _READONLY_TOOLS and canonical not in names:
            names.append(canonical)
    if not names:
        names = ["SEARCH", "STOCK", "SUMMARY"]
    return ToolPolicy(
        backend="readonly",
        allowed=frozenset(names),
        max_calls=max(1, min(3, int(max_calls or 2))),
        allow_direct_ui=False,
    )


def _resolve_tool_policy(tool_policy: Any, ui: Any) -> Any:
    """決定這一回合實際可用的工具 policy。

    ``None`` 代表呼叫端沒有指定（proactive / submit_human），不是「禁止工具」。
    明確的 ``backend="none"`` 才代表這回合不給工具。
    """
    if tool_policy is not None:
        if str(getattr(tool_policy, "backend", "none")) == "none":
            return None
        return tool_policy
    return _readonly_policy(
        getattr(ui, "allowed_tools", None),
        max_calls=getattr(ui, "tool_max_rounds", 2),
    )


def _resolve_tool_runner(policy: Any, bot: Any) -> Any:
    """把 policy 接到真正的工具後端；cyber lane 一律走唯讀 ToolBroker。"""
    if policy is None:
        return None
    from handlers.interaction.tool_policy import ToolBroker

    backend = str(getattr(policy, "backend", "none"))
    if backend != "readonly":
        logger.warning("[CyberV2] cyber lane 不支援 backend=%s，降級為唯讀", backend)
        policy = _readonly_policy(
            getattr(policy, "allowed", None),
            max_calls=getattr(policy, "max_calls", 2),
        )
    return _BrokerAdapter(ToolBroker(bot), policy)


class CyberRoomWorker:
    """每個頻道唯一的回覆寫入者。"""

    def __init__(
        self,
        *,
        channel_id: str,
        bot: Any,
        config_provider: Callable[[], Awaitable[Any]],
        model: PrimaryModelClient | None = None,
        policy: ReplyPolicy | None = None,
        memory: Any = None,
        shadow: bool = False,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        ledger_path: Path | str | None = None,
        debounce_seconds: float | None = None,
    ) -> None:
        self.channel_id = str(channel_id)
        self.bot = bot
        self.config_provider = config_provider
        self.runtime = ChannelRuntime(self.channel_id)
        self.model = model or PrimaryModelClient(bot)
        self.policy = policy or ReplyPolicy()
        self.memory = memory
        self.shadow = bool(shadow)
        self.event_sink = event_sink

        self.budget = ReplyBudget(
            channel_id=self.channel_id,
            path=ledger_path if ledger_path is not None else self._default_ledger_path(),
        )
        # 只計算「主動發言」的額度，和一般回覆的 budget 分開。
        autonomy_ledger_path = (
            Path(ledger_path).parent / "cyber_autonomy_ledger.json"
            if ledger_path is not None
            else self._default_autonomy_ledger_path()
        )
        self.proactive_budget = ReplyBudget(
            channel_id=self.channel_id,
            path=autonomy_ledger_path,
        )
        self._autonomy_history_path = (
            Path(ledger_path).parent / "cyber_autonomy.jsonl"
            if ledger_path is not None
            else self._default_autonomy_history_path()
        )
        # 連續訊息先進 pending，靜止 debounce_seconds 後才合併成一個回合。
        # 這樣一次打三、四句只會得到一次回覆，而不是四連發。
        self._pending_requests: list[Any] = []
        self._debounce_until = 0.0
        self._debounce_started_at = 0.0
        self._debounce_override = debounce_seconds
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._proactive_due = False
        # 這一輪主動開話題要用的網路情報；由 engine 抓好後帶進來。
        self._pending_web_topic: dict[str, Any] | None = None
        # 這一輪為什麼主動開口（無聊／想聊天／安靜太久）；由 engine 帶進來。
        self._pending_motivation = ""
        self._pending_mood = ""
        # L2 自主：下次可以再考慮的時間，以及上次的決定（給 WebUI 顯示）。
        self._autonomy_next_at = 0.0
        self._last_autonomy: dict[str, Any] | None = None
        # 連續說話／安靜計數，只用來偶爾提醒她自我調節。
        self._consecutive_speaks = 0
        self._consecutive_silents = 0
        self._backoff_until = 0.0
        self._sequence = 0
        self._consecutive_failures = 0
        self._last_decision: TurnDecision | None = None
        # 頻道歷史變動很慢，短 TTL 快取可省掉每回合一次 Discord API 往返，
        # 也讓連續幾回合的 prompt 開頭更穩定（前綴快取更容易命中）。
        self._transcript_cache: tuple[float, str] = (0.0, "")
        self._transcript_cache_ttl = 4.0

    @staticmethod
    def _default_ledger_path() -> Path:
        try:
            import settings

            base = Path(getattr(settings, "DATA_DIR", Path("data")))
        except Exception:
            base = Path("data")
        return base / "cyber_ledger.json"

    @staticmethod
    def _default_autonomy_ledger_path() -> Path:
        try:
            import settings

            base = Path(getattr(settings, "DATA_DIR", Path("data")))
        except Exception:
            base = Path("data")
        return base / "cyber_autonomy_ledger.json"

    @staticmethod
    def _default_autonomy_history_path() -> Path:
        try:
            import settings

            base = Path(getattr(settings, "DATA_DIR", Path("data")))
        except Exception:
            base = Path("data")
        return base / "cyber_autonomy.jsonl"

    # ------------------------------------------------------------------
    # 生命週期
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(
                self._run(),
                name=f"cyber-v2-{self.channel_id}",
            )

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ------------------------------------------------------------------
    # 投遞
    # ------------------------------------------------------------------
    async def submit_turn(self, request: Any) -> bool:
        if self._stopping:
            return False
        events = tuple(getattr(request, "events", ()) or ())
        for event in events:
            self.runtime.note_human(event)

        now = time.time()
        if not self._pending_requests:
            self._debounce_started_at = now
        mode = self._mode_name(request)
        delay = self._debounce_delay(mode)
        deadline = now + delay
        if delay > 0:
            # 防止連續洗版讓 debounce 永遠往後延；最多等 max_wait 秒。
            max_wait = 8.0 if mode in {"mention", "reply"} else 30.0
            deadline = min(deadline, self._debounce_started_at + max_wait)
        self._debounce_until = max(self._debounce_until, deadline)

        self._sequence += 1
        self._pending_requests.append(request)
        self._wake.set()
        return True

    @staticmethod
    def _mode_name(request: Any) -> str:
        mode = getattr(request, "mode", "ambient")
        return str(getattr(mode, "value", mode) or "ambient")

    def _debounce_delay(self, mode: str) -> float:
        if self._debounce_override is not None:
            return max(0.0, float(self._debounce_override))
        ui = self._ui()
        if mode in {"mention", "reply"}:
            return max(0.0, float(getattr(ui, "mention_debounce_seconds", 2)))
        return max(0.0, float(getattr(ui, "debounce_seconds", 10)))

    @staticmethod
    def _mode_rank(request: Any) -> int:
        mode = CyberRoomWorker._mode_name(request)
        return {"mention": 4, "reply": 3, "followup": 2, "ambient": 1, "proactive": 0}.get(mode, 0)

    def _merge_pending(self) -> Any:
        requests = self._pending_requests
        self._pending_requests = []
        self._debounce_until = 0.0
        self._debounce_started_at = 0.0
        if not requests:
            return None
        if len(requests) == 1:
            return requests[0]

        events: list[Any] = []
        seen: set[str] = set()
        for request in requests:
            for event in tuple(getattr(request, "events", ()) or ()):
                key = str(getattr(event, "message_id", "") or "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                events.append(event)
        events.sort(key=lambda event: (float(getattr(event, "created_at", 0.0)), str(getattr(event, "message_id", ""))))
        events = events[-8:]

        strongest = max(requests, key=self._mode_rank)
        latest_request = requests[-1]
        latest_event = events[-1] if events else None
        return SimpleNamespace(
            channel_id=getattr(strongest, "channel_id", self.channel_id),
            mode=getattr(strongest, "mode", "ambient"),
            events=tuple(events),
            priority=min(int(getattr(req, "priority", 2)) for req in requests),
            must_reply=any(bool(getattr(req, "must_reply", False)) for req in requests),
            tool_policy=getattr(strongest, "tool_policy", None),
            target_author_id=str(getattr(latest_event, "author_id", "") or ""),
            target_author_name=str(getattr(latest_event, "author_name", "") or ""),
            message=getattr(latest_request, "message", None),
            reply_to=getattr(latest_request, "reply_to", None),
            trace_id=str(getattr(strongest, "trace_id", "") or ""),
            source="debounce",
        )

    async def submit_human(self, event: ChatEvent) -> bool:
        request = SimpleNamespace(
            channel_id=self.channel_id,
            mode="ambient",
            events=(event,),
            priority=2,
            must_reply=False,
            tool_policy=None,
            target_author_id=event.author_id,
            message=event.raw,
            reply_to=event.raw,
            trace_id="",
        )
        return await self.submit_turn(request)

    async def submit_proactive(
        self,
        *,
        reason: str = "proactive",
        web_topic: dict[str, Any] | None = None,
        motivation: str = "",
        mood: str = "",
    ) -> None:
        self._proactive_due = True
        self._pending_web_topic = web_topic
        self._pending_motivation = str(motivation or "")
        self._pending_mood = str(mood or "")
        self._wake.set()

    def cancel_pending(self, *, reason: str = "") -> int:
        """被動 lane 已接手同一段對話時，丟棄尚未生成的 ambient 批次。

        典型情境：使用者先打一句一般訊息（進入 10 秒 debounce），
        下一句卻 Tag 了雪莉，由既有 router 立刻回覆。若不取消前一顆
        debounce，10 秒後 Cyber 會再回一次，形成「一問兩答」。
        """
        requests = self._pending_requests
        self._pending_requests = []
        self._debounce_until = 0.0
        self._debounce_started_at = 0.0
        message_ids: list[str] = list(dict.fromkeys(
            str(getattr(event, "message_id", "") or "")
            for request in requests
            for event in tuple(getattr(request, "events", ()) or ())
            if str(getattr(event, "message_id", "") or "")
        ))
        if message_ids:
            self.runtime.mark_answered(tuple(message_ids))
        if requests:
            self._record_event({
                "event": "pending_cancelled",
                "reason": reason,
                "events": len(message_ids),
            })
        self._wake.set()
        return len(message_ids)

    # ------------------------------------------------------------------
    # 主迴圈
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        while not self._stopping:
            try:
                now = time.time()
                if now < self._backoff_until:
                    await asyncio.sleep(min(5.0, self._backoff_until - now))
                    self._wake.set()
                    continue

                if self._pending_requests:
                    delay = self._debounce_until - time.time()
                    if delay > 0:
                        try:
                            await asyncio.wait_for(self._wake.wait(), timeout=delay)
                        except asyncio.TimeoutError:
                            pass
                        self._wake.clear()
                        continue
                    request = self._merge_pending()
                    if request is not None:
                        await self._handle_request(request)
                    continue

                if self._proactive_due:
                    self._proactive_due = False
                    await self._handle_proactive()
                    continue

                await self._wake.wait()
                self._wake.clear()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("[CyberV2] worker %s 回合異常", self.channel_id)
                self.runtime.last_error = str(exc)[:300]
                self._record_event({"event": "worker_error", "error": str(exc)[:300]})
                self._backoff_until = time.time() + 3.0
                self._wake.set()

    # ------------------------------------------------------------------
    # 回合
    # ------------------------------------------------------------------
    async def _handle_request(self, request: Any) -> None:
        config = await self.config_provider()
        if not bool(getattr(config, "enabled", False)):
            return

        mode = str(getattr(request, "mode", "ambient"))
        if hasattr(getattr(request, "mode", None), "value"):
            mode = str(request.mode.value)

        now = time.time()
        ui = self._ui()
        self.runtime.configure_window(
            float(getattr(config, "active_chat_window_seconds", 180)),
            followup_seconds=float(getattr(ui, "followup_window_seconds", 900)),
        )

        if mode == "proactive":
            await self._handle_proactive()
            return

        events = [event for event in (getattr(request, "events", ()) or ())]
        if not events:
            events = self._collect_batch(now, config, drain=True)
        if not events:
            return

        must_reply = bool(getattr(request, "must_reply", False)) or mode in {"mention", "reply"}
        if must_reply:
            decision = self.policy.decide_mention(events, self.runtime, config, now=now)
        else:
            decision = self.policy.decide_human(events, self.runtime, config, now=now, budget=self.budget)

        self._last_decision = decision
        self._record_decision(decision, events)

        if decision.reason in {"ambient_low_score", "ambient_score"}:
            self.runtime.reset_lurk()

        if not decision.speak:
            window = float(getattr(config, "active_chat_window_seconds", 180))
            self.runtime.set_phase(
                RoomPhase.LISTENING if self.runtime.window_open(now=now, window_seconds=window)
                else RoomPhase.IDLE
            )
            return

        if self.shadow:
            self._shadow_turn(decision, events, now)
            return

        await self._execute_turn(request, decision, events, config)

    def _collect_batch(self, now: float, config: Any, *, drain: bool) -> list[ChatEvent]:
        anchor = max(float(self.runtime.opened_at or 0.0), float(self.runtime.last_bot_at or 0.0))
        cutoff = max(now - 300.0, (anchor or now) - 60.0)
        events = (
            self.runtime.drain_pending(after=cutoff)
            if drain
            else self.runtime.pending_events(after=cutoff)
        )
        events = [event for event in events if not self.runtime.is_answered(event.message_id)]
        events.sort(key=lambda event: (event.created_at, event.message_id))
        if len(events) > 8:
            events = events[-8:]
        return events

    def _shadow_turn(self, decision: TurnDecision, events: list[ChatEvent], now: float) -> None:
        ticket = self.runtime.begin_turn(
            now=min(event.created_at for event in events),
            target_author_id=decision.target_author_id,
            reason=decision.reason,
            event_ids=tuple(event.message_id for event in events),
        )
        self.runtime.commit_send(ticket, sent_at=now, message_ids=(f"shadow-{ticket.turn_id}",))
        self._record_event({
            "event": "shadow_turn",
            "turn_id": ticket.turn_id,
            "reason": decision.reason,
            "events": len(events),
            "outcome": "would_send",
        })

    async def _execute_turn(
        self,
        request: Any,
        decision: TurnDecision,
        events: list[ChatEvent],
        config: Any,
    ) -> None:
        import settings

        ui = self._ui()
        mode = str(getattr(request, "mode", "ambient"))
        if hasattr(getattr(request, "mode", None), "value"):
            mode = str(request.mode.value)
        autonomy_enabled = bool(getattr(ui, "autonomy_enabled", True)) and mode == "proactive"
        thought_enabled = autonomy_enabled and bool(getattr(ui, "autonomy_thought_enabled", True))

        opened_at = min((event.created_at for event in events), default=time.time())
        ticket = self.runtime.begin_turn(
            now=opened_at,
            target_author_id=decision.target_author_id,
            reason=decision.reason,
            event_ids=tuple(event.message_id for event in events),
        )
        self.runtime.set_phase(RoomPhase.THINKING)

        try:
            channel = await self._resolve_channel(events)
            if channel is None:
                raise RuntimeError("channel_not_found")
            if mode == "proactive":
                allowed_channel = str(getattr(config, "sandbox_channel_id", "") or "")
                if allowed_channel and str(getattr(channel, "id", "")) != allowed_channel:
                    raise RuntimeError("channel_not_allowed")

            prep_started = time.monotonic()
            # 頻道歷史（Discord API）與記憶整理（SQLite／向量檢索）互不相依，
            # 平行處理可以省掉一段序列等待。
            memory_enabled = bool(getattr(ui, "memory_context_enabled", True))
            transcript, memory_block = await asyncio.gather(
                self._transcript(channel, events),
                self._memory_block(request, events, mode)
                if memory_enabled
                else asyncio.sleep(0, result=""),
            )
            # head 是靜態人設（可被前綴快取重用）；tail 是時間／記憶／世界書等
            # 每回合變動的內容，必須放在 user prompt，不能污染 system prompt。
            persona, tail_lore = self._persona_parts(transcript, events)
            autonomy_lines: list[str] = []
            if autonomy_enabled:
                mood = str(getattr(request, "mood", "") or "")
                if mood:
                    autonomy_lines.append(f"- 你現在：{mood}")
                autonomy_lines.append(
                    f"- 今天你主動開話題：{self.proactive_budget.count_today(time.time())} 次"
                )
                last = self._last_autonomy or {}
                last_at = float(last.get("at", 0.0) or 0.0)
                if last_at:
                    minutes = int(max(0.0, time.time() - last_at) // 60)
                    labels = {
                        "silent": "上次你選擇安靜",
                        "wait": "上次你說晚點再說",
                        "speak": "上次你主動開口",
                    }
                    label = labels.get(str(last.get("act", "")), "上次你的決定")
                    autonomy_lines.append(f"- {label}：{minutes} 分鐘前")
                if self._consecutive_speaks >= 3:
                    autonomy_lines.append(
                        f"- 你最近已經主動開過 {self._consecutive_speaks} 次話題；"
                        "這次沒有真正想說的，可以安靜。"
                    )
                if self._consecutive_silents >= 4:
                    autonomy_lines.append(
                        f"- 你已經安靜 {self._consecutive_silents} 次了；"
                        "真的有話想說就說，不用勉強。"
                    )
            dynamic = build_dynamic_context(
                now=time.time(),
                unanswered=len(events),
                last_bot_at=self.runtime.last_bot_at,
                last_human_at=self.runtime.last_human_at,
                phase=self.runtime.phase.value,
                silence_seconds=max(0.0, time.time() - self.runtime.last_human_at),
                trigger="proactive" if mode == "proactive" else "",
                motivation=str(getattr(request, "motivation", "") or ""),
                autonomy_lines=autonomy_lines,
            ) if bool(getattr(ui, "dynamic_context_enabled", True)) else ""
            tool_policy = _resolve_tool_policy(getattr(request, "tool_policy", None), ui)
            tools_enabled = (
                bool(getattr(settings, "CYBER_V2_TOOLS_ENABLED", True))
                and bool(getattr(ui, "tools_enabled", True))
                and tool_policy is not None
            )
            system_prompt = build_system_prompt(
                persona=persona,
                tools_enabled=tools_enabled,
                mode=mode,
                allowed_tools=getattr(tool_policy, "allowed", None) if tools_enabled else None,
                autonomy_enabled=autonomy_enabled,
                autonomy_wait_max_minutes=int(
                    getattr(request, "autonomy_wait_max_minutes", 90) or 90
                ),
                autonomy_thought_enabled=thought_enabled,
            )
            extra = ""
            if mode == "proactive":
                extra = "這一回合是你自己想開話題。不要打招呼、不要問「大家在嗎」，直接從近期對話或你的興趣接一個具體話題。"
                topic_block = build_web_topic_block(getattr(request, "web_topic", None))
                if topic_block:
                    extra = (
                        "這一回合是你自己想開話題。不要打招呼、不要問「大家在嗎」，"
                        "直接講一件具體的事。\n\n"
                        f"{topic_block}"
                    )
            user_prompt = build_user_prompt(
                transcript=transcript,
                events=events,
                extra=extra,
                memory_block=memory_block,
                dynamic_block=dynamic,
                tail_block=tail_lore,
            )
            prep_ms = int((time.monotonic() - prep_started) * 1000)

            priority = int(getattr(request, "priority", 2))
            timeout = 25.0 if decision.addressed else 35.0
            task_id = f"cyber-{self.channel_id}-{int(opened_at)}"
            # 短對話不需要深度推理：Gemini 系後端在 medium 會先花數千個思考
            # Token，體感延遲非常明顯。這一項由 WebUI 的「推理強度」控制。
            effort = str(getattr(ui, "reasoning_effort", "low") or "low")
            # 中文在 Gemma 這類 tokenizer 常常 2 個 token 才 1 個字，
            # 240/320 會在講到一半（例如「1%bakery」）被切斷。
            # 上限拉高只影響「允許講多長」，模型講完就停，不會固定生成這麼多。
            reply_tokens = 900 if decision.addressed else 800

            runner = _resolve_tool_runner(tool_policy, self.bot) if tools_enabled else None

            if tools_enabled and runner is not None:
                result = await self.model.run_tool_loop(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_rounds=int(getattr(ui, "tool_max_rounds", 3) or 3),
                    max_tokens=reply_tokens,
                    # 第一輪同時可能是「直接回答」；把上限壓到 256 會讓
                    # 不需要工具的回覆在 1~2 句後硬生生斷掉。0 = 不額外限制。
                    decision_max_tokens=0,
                    timeout=timeout,
                    priority=priority,
                    task_id=task_id,
                    tool_policy=tool_policy,
                    tool_runner=runner,
                    tool_result_max_chars=1200 if mode == "proactive" else 0,
                    reasoning_effort=effort,
                )
            else:
                result = await self.model.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=reply_tokens,
                    timeout=timeout,
                    priority=priority,
                    task_id=task_id,
                    reasoning_effort=effort,
                )

            if not result.ok:
                raise RuntimeError(result.error or "generation_empty")

            self.runtime.note_generation(model=result.model, gen_ms=result.gen_ms)

            thought = ""
            raw_text = result.text
            if autonomy_enabled:
                if thought_enabled:
                    thought, raw_text = extract_autonomy_thought(raw_text)
                if not str(raw_text or "").strip():
                    self._finish_autonomy(
                        ticket,
                        "silent",
                        0,
                        decision,
                        motivation=str(getattr(request, "motivation", "") or ""),
                        mood=str(getattr(request, "mood", "") or ""),
                        thought=thought,
                    )
                    return
                act, wait_minutes = parse_autonomy_reply(
                    raw_text,
                    max_wait=int(getattr(request, "autonomy_wait_max_minutes", 90) or 90),
                )
                if act in {"silent", "wait"}:
                    self._finish_autonomy(
                        ticket,
                        act,
                        wait_minutes,
                        decision,
                        motivation=str(getattr(request, "motivation", "") or ""),
                        mood=str(getattr(request, "mood", "") or ""),
                        thought=thought,
                    )
                    return

            allow_urls = str(getattr(config, "include_url_mode", "never")) in {"rare", "always"}
            cleaned = sanitize_reply(raw_text, allow_urls=allow_urls, max_chars=1200)
            multi = bool(getattr(config, "multi_message_enabled", True))
            max_bubbles = int(getattr(ui, "max_bubbles", 2) or 2) if multi else 1
            # 70 字是「一顆氣泡看起來像人打的」的軟上限；拆分器只切不丟，
            # 超過上限會併回最後一顆，不會再出現講到一半被截斷的句子。
            bubbles = split_bubbles(cleaned, max_bubbles=max_bubbles, max_chars=70)
            if not bubbles:
                raise RuntimeError("empty_bubbles")

            self.runtime.set_phase(RoomPhase.SPEAKING)
            sent = await send_bubbles(
                channel,
                bubbles,
                reply_to=self._reply_target(request, events),
                turn_id=ticket.turn_id,
                max_bubbles=max_bubbles,
            )
            if sent.empty:
                raise RuntimeError("send_failed")

            self.runtime.commit_send(ticket, sent_at=sent.first_ts, message_ids=sent.message_ids)
            self._consecutive_failures = 0
            self._after_send(
                decision,
                events,
                sent,
                result,
                config,
                motivation=str(getattr(request, "motivation", "") or ""),
                mood=str(getattr(request, "mood", "") or ""),
                thought=thought,
            )
            await self._record_web_topic(request, cleaned)
            self._record_event({
                "event": "cyber_turn",
                "turn_id": ticket.turn_id,
                "mode": mode,
                "phase": "sent",
                "model": result.model,
                "gen_ms": result.gen_ms,
                "prep_ms": prep_ms,
                "reasoning_effort": effort,
                "tool_rounds": result.tool_rounds,
                "outcome": "sent",
                "bubbles": len(sent.bubbles),
                "events": len(events),
                "buffered_during_turn": len(self.runtime.buffer),
            })
        except Exception as exc:
            logger.warning("[CyberV2] 回合失敗 (%s): %s", ticket.turn_id, exc)
            self.runtime.abort_turn(ticket, error=str(exc))
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self.runtime.mark_answered(tuple(event.message_id for event in events))
                self._consecutive_failures = 0
                self._backoff_until = time.time() + 30.0
            else:
                self.runtime.buffer.put_many(events)
                self._backoff_until = time.time() + min(30.0, 5.0 * self._consecutive_failures)
            self._record_event({
                "event": "cyber_turn",
                "turn_id": ticket.turn_id,
                "mode": mode,
                "phase": "error",
                "outcome": "error",
                "error": str(exc)[:300],
                "events": len(events),
            })

    async def _handle_proactive(self) -> None:
        config = await self.config_provider()
        if not bool(getattr(config, "enabled", False)):
            return
        now = time.time()
        ui = self._ui()
        self.runtime.configure_window(
            float(getattr(config, "active_chat_window_seconds", 180)),
            followup_seconds=float(getattr(ui, "followup_window_seconds", 900)),
        )
        decision = self.policy.decide_proactive(self.runtime, config, now=now, budget=self.budget)
        self._last_decision = decision
        web_topic = self._pending_web_topic
        self._pending_web_topic = None
        motivation = self._pending_motivation
        self._pending_motivation = ""
        mood = self._pending_mood
        self._pending_mood = ""
        if not decision.speak:
            self._record_decision(decision, [])
            return
        if self.shadow:
            self._record_event({"event": "shadow_proactive", "reason": decision.reason})
            return
        autonomy_enabled = bool(getattr(ui, "autonomy_enabled", True))
        if autonomy_enabled:
            hourly = int(getattr(ui, "autonomy_hourly", 2) or 0)
            daily = int(getattr(ui, "autonomy_daily", 12) or 0)
            if not self.proactive_budget.allow(now=now, hourly=hourly, daily=daily):
                self._record_event({
                    "event": "autonomy_decision",
                    "act": "blocked",
                    "reason": "autonomy_budget",
                    "ts": now,
                })
                return
        request = SimpleNamespace(
            channel_id=self.channel_id,
            mode="proactive",
            events=(),
            priority=int(getattr(__import__("settings"), "PRIORITY_INTERJECT", 2)),
            must_reply=False,
            tool_policy=None,
            target_author_id="",
            message=None,
            reply_to=None,
            trace_id="",
            web_topic=web_topic,
            motivation=motivation,
            mood=mood,
            autonomy_enabled=autonomy_enabled,
            autonomy_wait_max_minutes=int(getattr(ui, "autonomy_wait_max_minutes", 90) or 90),
        )
        await self._execute_turn(request, decision, [], config)

    # ------------------------------------------------------------------
    # 記憶與上下文
    # ------------------------------------------------------------------
    def _ui(self) -> Any:
        try:
            from .ui_config import get_cyber_ui_config

            return get_cyber_ui_config()
        except Exception:
            return SimpleNamespace()

    async def _memory_block(self, request: Any, events: list[ChatEvent], mode: str) -> str:
        try:
            from handlers.memory.context_service import MemoryContextService

            target_id = str(getattr(request, "target_author_id", "") or "")
            if not target_id and events:
                target_id = events[-1].author_id
            target_name = events[-1].author_name if events else ""
            query = events[-1].content if events else ""
            guild_id = events[-1].guild_id if events else ""
            bundle = await asyncio.to_thread(
                MemoryContextService.build,
                bot=self.bot,
                user_id=target_id,
                user_name=target_name,
                query=query,
                channel_id=self.channel_id,
                guild_id=guild_id,
                mode=mode,
            )
            # 一般接話不需要被動 lane 那麼多記憶；縮短 prompt 也能直接縮短 prefill。
            budget = 2600 if str(mode) in {"mention", "reply"} else 1400
            return bundle.render(max_chars=budget)
        except Exception as exc:
            logger.debug("[CyberV2] MemoryContextService 失敗，改用 identity block: %s", exc)
            return build_identity_block(query_text=events[-1].content if events else "")

    async def _resolve_channel(self, events: list[ChatEvent]) -> Any:
        for event in reversed(events):
            channel = getattr(event.raw, "channel", None)
            if channel is not None:
                return channel
        try:
            cid = int(self.channel_id)
        except (TypeError, ValueError):
            return None
        channel = self.bot.get_channel(cid) if self.bot else None
        if channel is None and self.bot and hasattr(self.bot, "fetch_channel"):
            try:
                channel = await self.bot.fetch_channel(cid)
            except Exception:
                channel = None
        return channel

    async def _transcript(self, channel: Any, events: list[ChatEvent]) -> str:
        cached_at, cached_text = self._transcript_cache
        if cached_text and (time.monotonic() - cached_at) < self._transcript_cache_ttl:
            return cached_text
        lines: list[str] = []
        bot_user_id = getattr(getattr(self.bot, "user", None), "id", None)
        ui = self._ui()
        try:
            if channel is not None and hasattr(channel, "history"):
                async for message in channel.history(limit=12):
                    author = getattr(message, "author", None)
                    is_bot = bool(getattr(author, "id", None) == bot_user_id)
                    name = "雪莉" if is_bot else (getattr(author, "display_name", "") or "群友")
                    if not is_bot and bool(getattr(ui, "identity_tags_enabled", True)):
                        name = f"{name}[{getattr(author, 'id', '')}]"
                    content = str(getattr(message, "content", "") or "").replace("\n", " ")[:200]
                    if not content.strip():
                        continue
                    ts = getattr(message, "created_at", None)
                    clock = time.strftime("%H:%M", time.localtime(ts.timestamp())) if ts else "--:--"
                    lines.append(f"[{clock}] {name}: {content}")
                lines.reverse()
        except Exception as exc:
            logger.debug("[CyberV2] 讀取歷史失敗: %s", exc)
        if not lines:
            lines = [format_events(events)] if events else []
        text = "\n".join(lines).strip()
        if text:
            self._transcript_cache = (time.monotonic(), text)
        return text

    def _persona_parts(self, transcript: str, events: list[ChatEvent]) -> tuple[str, str]:
        """回傳 (靜態 head persona, 動態 tail notes)。

        ``build_dynamic_persona`` 原本就把「SOUL + 通用規則」與「時間／世界書／
        作者備註」拆成兩段，目的就是讓前綴快取能重複利用 head。v2 之前把兩段
        直接串起來塞進 system prompt，等於每分鐘都讓整段人設失效，這裡改為
        分開注入：head 進 system prompt，tail 留在 user prompt 尾端。
        """
        try:
            from handlers.social.persona import build_dynamic_persona

            guild_id = ""
            if events:
                guild_id = str(getattr(getattr(events[-1].raw, "guild", None), "id", "") or "")
            try:
                channel_id_int = int(self.channel_id)
            except (TypeError, ValueError):
                channel_id_int = None
            try:
                head, tail = build_dynamic_persona(
                    transcript,
                    "CHAT",
                    channel_id=channel_id_int,
                    user_query=events[-1].content if events else "",
                    bot=self.bot,
                    guild_id=guild_id,
                    include_memory=False,
                )
            except TypeError:
                head, tail = build_dynamic_persona(
                    transcript,
                    "CHAT",
                    channel_id=channel_id_int,
                    user_query=events[-1].content if events else "",
                    bot=self.bot,
                    guild_id=guild_id,
                )
            if str(head or "").strip():
                return str(head).strip(), str(tail or "").strip()
        except Exception as exc:
            logger.debug("[CyberV2] 動態人格失敗，改用 CORE_PERSONA: %s", exc)
        import settings

        fallback = str(getattr(settings, "CORE_PERSONA", "") or "你是雪莉，一個在群組裡自然聊天的女生。")
        return fallback, ""

    def _reply_target(self, request: Any, events: list[ChatEvent]) -> Any:
        explicit = getattr(request, "reply_to", None)
        if explicit is not None:
            return explicit
        for event in reversed(events):
            raw = event.raw
            if raw is not None and hasattr(raw, "reply"):
                return raw
        return None

    async def _record_web_topic(self, request: Any, reply_text: str) -> None:
        """把這一輪上網看到的話題記進閱讀紀錄，下次不會再講同一則。"""
        topic = getattr(request, "web_topic", None)
        if not isinstance(topic, dict) or not str(topic.get("url", "")).strip():
            return
        try:
            from handlers.memory.self_memory import record_reading_item

            await asyncio.to_thread(
                record_reading_item,
                str(topic.get("url", "")),
                str(topic.get("title", "")),
                source=str(topic.get("source", "") or "rss"),
                summary=str(topic.get("summary", "")),
                sherry_opinion=str(reply_text or "")[:200],
                channel_id=self.channel_id,
                post_text=str(reply_text or ""),
            )
        except Exception as exc:
            logger.debug("[CyberV2] 記錄閱讀情報失敗: %s", exc)

    # ------------------------------------------------------------------
    # 發送後處理
    # ------------------------------------------------------------------
    def _append_autonomy_history(self, entry: dict[str, Any]) -> None:
        """把自主決定寫進 JSONL；最多保留 500 筆，重啟後仍可顯示。"""
        try:
            path = self._autonomy_history_path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # 約 500 筆以後才輪替，避免每一輪都重寫檔案。
            if path.stat().st_size > 512_000:
                lines = path.read_text(encoding="utf-8").splitlines()[-500:]
                path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except Exception as exc:
            logger.debug("[CyberV2] 寫入自主歷史失敗: %s", exc)

    def recent_autonomy(self, limit: int = 20) -> list[dict[str, Any]]:
        n = max(1, min(100, int(limit)))
        try:
            lines = self._autonomy_history_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except Exception as exc:
            logger.debug("[CyberV2] 讀取自主歷史失敗: %s", exc)
            return []
        items: list[dict[str, Any]] = []
        for line in lines[-n:]:
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                items.append(payload)
        items.reverse()
        return items

    def _finish_autonomy(
        self,
        ticket: Any,
        act: str,
        wait_minutes: int,
        decision: TurnDecision,
        *,
        motivation: str = "",
        mood: str = "",
        thought: str = "",
    ) -> None:
        """她選擇安靜或晚點說：不送訊息，只記錄決定與下次機會。"""
        now = time.time()
        if act == "wait":
            self._autonomy_next_at = now + max(30, int(wait_minutes)) * 60
        else:
            self._autonomy_next_at = 0.0
        self._last_autonomy = {
            "act": act,
            "at": now,
            "wait_minutes": int(wait_minutes or 0),
            "reason": decision.reason,
            "motivation": str(motivation or ""),
            "mood": str(mood or ""),
            "thought": str(thought or ""),
        }
        self._consecutive_silents += 1
        self._consecutive_speaks = 0
        self.runtime.abort_turn(ticket)
        self._record_event({
            "event": "autonomy_decision",
            "act": act,
            "wait_minutes": int(wait_minutes or 0),
            "next_at": self._autonomy_next_at,
            "reason": decision.reason,
            "thought": str(thought or ""),
            "ts": now,
        })
        self._append_autonomy_history({
            "ts": now,
            "act": act,
            "motivation": str(motivation or ""),
            "mood": str(mood or ""),
            "thought": str(thought or ""),
            "wait_minutes": int(wait_minutes or 0),
            "next_at": self._autonomy_next_at,
            "reason": decision.reason,
            "bubbles": 0,
        })

    def _after_send(
        self,
        decision: TurnDecision,
        events: list[ChatEvent],
        sent: SentBatch,
        result: GenerationResult,
        config: Any,
        *,
        motivation: str = "",
        mood: str = "",
        thought: str = "",
    ) -> None:
        self.budget.record(sent.first_ts)
        if decision.trigger == "proactive":
            self.proactive_budget.record(sent.first_ts)
            self._autonomy_next_at = 0.0
            self._last_autonomy = {
                "act": "speak",
                "at": sent.first_ts,
                "wait_minutes": 0,
                "reason": decision.reason,
                "motivation": str(motivation or ""),
                "mood": str(mood or ""),
                "thought": str(thought or ""),
            }
            self._consecutive_speaks += 1
            self._consecutive_silents = 0
            self._append_autonomy_history({
                "ts": sent.first_ts,
                "act": "speak",
                "motivation": str(motivation or ""),
                "mood": str(mood or ""),
                "thought": str(thought or ""),
                "wait_minutes": 0,
                "next_at": 0.0,
                "reason": decision.reason,
                "bubbles": len(sent.bubbles),
            })
            cooldown = float(getattr(config, "proactive_cooldown_seconds", 120))
            try:
                from handlers.care.surfer import replenish_mind_state

                replenish_mind_state(reset_boredom=True, reset_curiosity=True)
            except Exception:
                pass
        else:
            cooldown = float(getattr(config, "interject_cooldown_seconds", 45))
        if cooldown > 0:
            self.runtime.mark_cooldown(cooldown, now=sent.first_ts)

        try:
            from handlers.care.surfer import (
                add_token_consumption,
                record_proactive_action_memory,
                update_bot_speak_timestamp,
            )

            update_bot_speak_timestamp(
                self.channel_id,
                ts=sent.first_ts,
                target_author_id=decision.target_author_id,
            )
            add_token_consumption(max(80, len("".join(sent.bubbles)) * 3))
            record_proactive_action_memory(
                summary=f"賽博群友 v2（{decision.reason}）：{sent.bubbles[0][:60]}",
                channel_id=self.channel_id,
                user_id=decision.target_author_id,
                user_name=events[-1].author_name if events else "",
                detail={
                    "turn_id": sent.turn_id,
                    "reason": decision.reason,
                    "model": result.model,
                    "bubbles": list(sent.bubbles),
                },
            )
        except Exception as exc:
            logger.debug("[CyberV2] 發送後統計失敗: %s", exc)

        if self.memory is not None:
            try:
                self.memory.observe_turn(
                    channel_id=self.channel_id,
                    transcript=format_events(events),
                    reply="\n".join(sent.bubbles),
                    reason=decision.reason,
                )
            except Exception as exc:
                logger.debug("[CyberV2] 記憶觀察失敗: %s", exc)

    # ------------------------------------------------------------------
    # 診斷
    # ------------------------------------------------------------------
    async def preview(self) -> dict[str, Any]:
        config = await self.config_provider()
        now = time.time()
        batch = self._collect_batch(now, config, drain=False)
        if batch:
            decision = self.policy.decide_human(batch, self.runtime, config, now=now, budget=self.budget)
        else:
            decision = self.policy.decide_proactive(self.runtime, config, now=now, budget=self.budget)
        return {
            "decision": {
                "speak": decision.speak,
                "reason": decision.reason,
                "score": decision.score,
                "addressed": decision.addressed,
                "continuation": decision.continuation,
                "events": len(batch),
            },
            "snapshot": self.runtime.snapshot(
                window_seconds=float(getattr(config, "active_chat_window_seconds", 180))
            ).to_dict(),
        }

    @property
    def autonomy_next_at(self) -> float:
        """下次可以再考慮主動開口的時間；0 代表交給一般間隔。"""
        return float(self._autonomy_next_at or 0.0)

    @property
    def last_autonomy(self) -> dict[str, Any] | None:
        return dict(self._last_autonomy) if self._last_autonomy else None

    def _record_decision(self, decision: TurnDecision, events: list[ChatEvent]) -> None:
        self._record_event({
            "event": "decision",
            "reason": decision.reason,
            "speak": decision.speak,
            "score": decision.score,
            "addressed": decision.addressed,
            "continuation": decision.continuation,
            "events": len(events),
            "lurk_streak": self.runtime.lurk_streak,
        })

    def _record_event(self, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            try:
                self.event_sink(payload)
            except Exception:
                pass
