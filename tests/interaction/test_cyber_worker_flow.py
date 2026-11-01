from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from handlers.care.cyber.contracts import ChatEvent, TurnDecision
from handlers.care.cyber.model import GenerationResult
from handlers.care.cyber.worker import (
    CyberRoomWorker,
    _resolve_tool_policy,
    _resolve_tool_runner,
)


class FakeMessage:
    _seq = 0

    def __init__(self) -> None:
        FakeMessage._seq += 1
        self.id = 9000 + FakeMessage._seq
        self.created_at = datetime.now(timezone.utc)


class FakeChannel:
    def __init__(self) -> None:
        self.id = 1
        self.sent: list[str] = []

    @asynccontextmanager
    async def typing(self):
        yield

    async def send(self, text: str):
        self.sent.append(text)
        return FakeMessage()


class FakeBot:
    def __init__(self, channel: FakeChannel) -> None:
        self.user = None
        self._channel = channel

    def get_channel(self, channel_id: int):
        return self._channel if int(channel_id) == self._channel.id else None

    async def fetch_channel(self, channel_id: int):
        return self.get_channel(channel_id)


class FakeModel:
    def __init__(self) -> None:
        self.tool_registry = None
        self.calls = 0

    async def generate(self, **kwargs) -> GenerationResult:
        self.calls += 1
        return GenerationResult(text="測試回覆", model="test-model")

    async def run_tool_loop(self, **kwargs) -> GenerationResult:
        return await self.generate(**kwargs)


class RecordingModel(FakeModel):
    """記錄實際送進模型的 prompt，用來驗證上網找到的話題有沒有被帶進去。"""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def generate(self, **kwargs) -> GenerationResult:
        self.prompts.append(str(kwargs.get("user_prompt", "")))
        return await super().generate(**kwargs)


def make_config(**overrides: object) -> SimpleNamespace:
    values = dict(
        enabled=True,
        chat_reply_enabled=True,
        active_chat_window_seconds=300,
        interject_threshold=1,
        interject_score_threshold=1,
        interject_cooldown_seconds=0,
        max_replies_per_hour=100,
        max_replies_per_day=1000,
        active_hour_start=0,
        active_hour_end=0,
        multi_message_enabled=True,
        include_url_mode="never",
        proactive_cooldown_seconds=0,
        mind_pace="normal",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


async def _config():
    return make_config()


@pytest.mark.asyncio
async def test_worker_mention_sends_and_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    bot = FakeBot(channel)
    worker = CyberRoomWorker(
        channel_id="1",
        bot=bot,
        config_provider=_config,
        model=FakeModel(),
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_persona_parts", lambda *args: ("persona", ""))
    monkeypatch.setattr(worker, "_memory_block", lambda *args, **kwargs: asyncio.sleep(0, result=""))
    monkeypatch.setattr(worker, "_after_send", lambda *args, **kwargs: None)

    event = ChatEvent(
        channel_id="1",
        message_id="m1",
        author_id="A",
        author_name="A",
        content="hi",
        created_at=100.0,
        is_mentioned=True,
    )
    request = SimpleNamespace(
        channel_id="1",
        mode="mention",
        events=(event,),
        priority=1,
        must_reply=True,
        tool_policy=None,
        target_author_id="A",
        message=None,
        reply_to=None,
        trace_id="t1",
    )

    worker.start()
    try:
        assert await worker.submit_turn(request) is True
        for _ in range(20):
            if channel.sent:
                break
            await asyncio.sleep(0.05)
        assert channel.sent == ["測試回覆"]
        assert worker.runtime.turns == 1
        assert worker.runtime.window_open(now=time_now(), window_seconds=300) is True
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_ambient_followup_after_short_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """熱聊窗口只有 60 秒時，5 分鐘後的追話仍應實際發送，而不是只回 lurk_wait。"""
    channel = FakeChannel()
    bot = FakeBot(channel)
    worker = CyberRoomWorker(
        channel_id="1",
        bot=bot,
        config_provider=lambda: asyncio.sleep(
            0,
            result=make_config(active_chat_window_seconds=60),
        ),
        model=FakeModel(),
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_persona_parts", lambda *args: ("persona", ""))
    monkeypatch.setattr(worker, "_memory_block", lambda *args, **kwargs: asyncio.sleep(0, result=""))
    monkeypatch.setattr(worker, "_after_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker,
        "_ui",
        lambda: SimpleNamespace(followup_window_seconds=900, memory_context_enabled=False),
    )

    now = time_now()
    worker.runtime.note_external_bot_speak(sent_at=now - 300.0, message_id="bot1")
    event = ChatEvent(
        channel_id="1",
        message_id="m1",
        author_id="A",
        author_name="A",
        content="什麼蘑菇AI",
        created_at=now,
    )
    request = SimpleNamespace(
        channel_id="1",
        mode="ambient",
        events=(event,),
        priority=2,
        must_reply=False,
        tool_policy=None,
        target_author_id="A",
        message=None,
        reply_to=None,
        trace_id="t1",
    )

    worker.start()
    try:
        assert await worker.submit_turn(request) is True
        for _ in range(20):
            if channel.sent:
                break
            await asyncio.sleep(0.05)
        assert channel.sent == ["測試回覆"]
        assert worker.runtime.turns == 1
        assert worker.runtime.last_reason == "followup"
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_debounces_multiple_messages_into_one_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """同一個人連打多句時，只生成一次，回覆也只送一次。"""
    channel = FakeChannel()
    bot = FakeBot(channel)
    model = FakeModel()
    worker = CyberRoomWorker(
        channel_id="1",
        bot=bot,
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0.1,
    )
    monkeypatch.setattr(worker, "_persona_parts", lambda *args: ("persona", ""))
    monkeypatch.setattr(worker, "_memory_block", lambda *args, **kwargs: asyncio.sleep(0, result=""))
    monkeypatch.setattr(worker, "_after_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker,
        "_ui",
        lambda: SimpleNamespace(followup_window_seconds=900, memory_context_enabled=False),
    )

    worker.start()
    try:
        now = time_now()
        for index, text in enumerate(("第一句", "第二句", "第三句")):
            event = ChatEvent(
                channel_id="1",
                message_id=f"m{index}",
                author_id="A",
                author_name="A",
                content=text,
                created_at=now + index * 0.01,
            )
            request = SimpleNamespace(
                channel_id="1",
                mode="ambient",
                events=(event,),
                priority=2,
                must_reply=False,
                tool_policy=None,
                target_author_id="A",
                message=None,
                reply_to=None,
                trace_id=f"t{index}",
            )
            assert await worker.submit_turn(request) is True

        for _ in range(40):
            if channel.sent:
                break
            await asyncio.sleep(0.05)

        assert channel.sent == ["測試回覆"]
        assert model.calls == 1
        assert worker.runtime.turns == 1
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_cancel_pending_drops_debounced_ambient(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """mention lane 先回覆時，先前那顆 ambient debounce 不該再發第二次。"""
    channel = FakeChannel()
    bot = FakeBot(channel)
    model = FakeModel()
    worker = CyberRoomWorker(
        channel_id="1",
        bot=bot,
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0.3,
    )
    monkeypatch.setattr(worker, "_persona_parts", lambda *args: ("persona", ""))
    monkeypatch.setattr(worker, "_memory_block", lambda *args, **kwargs: asyncio.sleep(0, result=""))
    monkeypatch.setattr(worker, "_after_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker,
        "_ui",
        lambda: SimpleNamespace(followup_window_seconds=900, memory_context_enabled=False),
    )

    worker.start()
    try:
        event = ChatEvent(
            channel_id="1",
            message_id="m1",
            author_id="A",
            author_name="A",
            content="我也好想吃喔",
            created_at=time_now(),
        )
        request = SimpleNamespace(
            channel_id="1",
            mode="ambient",
            events=(event,),
            priority=2,
            must_reply=False,
            tool_policy=None,
            target_author_id="A",
            message=None,
            reply_to=None,
            trace_id="t1",
        )
        assert await worker.submit_turn(request) is True
        assert await worker.submit_turn(request) is True  # 同訊息重複投遞不應累積
        assert worker.cancel_pending(reason="mention_lane") == 1

        await asyncio.sleep(0.5)
        assert channel.sent == []
        assert model.calls == 0
        assert worker.runtime.is_answered("m1") is True
    finally:
        await worker.stop()


def time_now() -> float:
    return datetime.now(timezone.utc).timestamp()


@pytest.mark.asyncio
async def test_proactive_turn_uses_web_topic_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """上網找到的話題要真的進到 prompt，而不是抓完就丟掉。"""
    channel = FakeChannel()
    bot = FakeBot(channel)
    model = RecordingModel()
    worker = CyberRoomWorker(
        channel_id="1",
        bot=bot,
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_persona_parts", lambda *args: ("persona", ""))
    monkeypatch.setattr(worker, "_memory_block", lambda *args, **kwargs: asyncio.sleep(0, result=""))
    monkeypatch.setattr(worker, "_after_send", lambda *args, **kwargs: None)

    recorded: dict[str, str] = {}

    async def fake_record(request, text: str) -> None:
        recorded["title"] = str(request.web_topic.get("title", ""))

    monkeypatch.setattr(worker, "_record_web_topic", fake_record)

    topic = {
        "title": "台中檸檬乳酪蛋糕評比",
        "summary": "1%bakery 的檸檬乳酪蛋糕拿下高分。",
        "source": "RSS (美食)",
        "url": "https://example.com/cake",
    }
    request = SimpleNamespace(
        channel_id="1",
        mode="proactive",
        events=(),
        priority=1,
        must_reply=False,
        tool_policy=None,
        target_author_id="",
        message=None,
        reply_to=None,
        trace_id="p1",
        web_topic=topic,
    )
    decision = TurnDecision(speak=True, reason="idle_proactive", trigger="proactive")

    try:
        await worker._execute_turn(request, decision, [], make_config())
    finally:
        await worker.stop()

    assert channel.sent == ["測試回覆"]
    assert model.prompts, "應該有實際呼叫模型"
    assert "台中檸檬乳酪蛋糕評比" in model.prompts[0]
    assert "1%bakery" in model.prompts[0]
    assert recorded["title"] == "台中檸檬乳酪蛋糕評比"


class CapturingToolModel(FakeModel):
    """記錄 run_tool_loop 收到的 policy／runner，用來驗證 proactive 的接線。"""

    def __init__(self) -> None:
        super().__init__()
        self.loop_kwargs: dict | None = None

    async def run_tool_loop(self, **kwargs) -> GenerationResult:
        self.loop_kwargs = kwargs
        return await self.generate(**kwargs)


def test_resolve_tool_policy_none_becomes_readonly() -> None:
    ui = SimpleNamespace(allowed_tools=["web_search", "STOCK"], tool_max_rounds=3)
    policy = _resolve_tool_policy(None, ui)
    assert policy is not None
    assert policy.backend == "readonly"
    assert policy.allowed == frozenset({"SEARCH", "STOCK"})
    assert policy.max_calls == 3


def test_resolve_tool_policy_explicit_none_disables_tools() -> None:
    ui = SimpleNamespace(allowed_tools=["SEARCH"], tool_max_rounds=2)
    explicit = SimpleNamespace(backend="none", allowed=frozenset(), max_calls=0)
    assert _resolve_tool_policy(explicit, ui) is None


def test_resolve_tool_runner_uses_readonly_broker() -> None:
    ui = SimpleNamespace(allowed_tools=["SEARCH", "STOCK", "SUMMARY"], tool_max_rounds=2)
    policy = _resolve_tool_policy(None, ui)
    runner = _resolve_tool_runner(policy, bot=None)
    assert runner is not None
    assert runner.policy.backend == "readonly"
    assert runner.policy.allowed == frozenset({"SEARCH", "STOCK", "SUMMARY"})


@pytest.mark.asyncio
async def test_proactive_turn_uses_readonly_runner_and_caps_tool_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """proactive 沒有指定 policy 時，不能再掉進只認 web_search 的 registry。"""
    channel = FakeChannel()
    bot = FakeBot(channel)
    model = CapturingToolModel()
    worker = CyberRoomWorker(
        channel_id="1",
        bot=bot,
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    ui = SimpleNamespace(
        tools_enabled=True,
        allowed_tools=["SEARCH", "STOCK", "SUMMARY"],
        tool_max_rounds=3,
        max_bubbles=2,
        dynamic_context_enabled=True,
        reasoning_effort="low",
    )
    monkeypatch.setattr(worker, "_ui", lambda: ui)
    monkeypatch.setattr(worker, "_persona_parts", lambda *args: ("persona", ""))
    monkeypatch.setattr(worker, "_memory_block", lambda *args, **kwargs: asyncio.sleep(0, result=""))
    monkeypatch.setattr(worker, "_after_send", lambda *args, **kwargs: None)

    request = SimpleNamespace(
        channel_id="1",
        mode="proactive",
        events=(),
        priority=1,
        must_reply=False,
        tool_policy=None,
        target_author_id="",
        message=None,
        reply_to=None,
        trace_id="p1",
        web_topic=None,
        motivation="無聊累積 82/80",
    )
    decision = TurnDecision(speak=True, reason="idle_proactive", trigger="proactive")

    try:
        await worker._execute_turn(request, decision, [], make_config())
    finally:
        await worker.stop()

    assert model.loop_kwargs is not None
    policy = model.loop_kwargs["tool_policy"]
    assert policy.backend == "readonly"
    assert policy.allowed == frozenset({"SEARCH", "STOCK", "SUMMARY"})
    assert model.loop_kwargs["tool_runner"] is not None
    assert model.loop_kwargs["tool_result_max_chars"] == 1200
    assert "本回合可用" in model.loop_kwargs["system_prompt"]
    assert "搜尋" in model.loop_kwargs["system_prompt"]
    assert "你自己想開話題" in model.loop_kwargs["user_prompt"]
    assert "無聊累積 82/80" in model.loop_kwargs["user_prompt"]


class StaticModel(FakeModel):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    async def generate(self, **kwargs) -> GenerationResult:
        self.calls += 1
        return GenerationResult(text=self.text, model="test-model")


class CapturingGenerateModel(StaticModel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.last_kwargs: dict | None = None

    async def generate(self, **kwargs) -> GenerationResult:
        self.last_kwargs = kwargs
        return await super().generate(**kwargs)


def make_autonomy_ui(**overrides: object) -> SimpleNamespace:
    values = dict(
        autonomy_enabled=True,
        autonomy_hourly=2,
        autonomy_daily=12,
        autonomy_wait_max_minutes=90,
        autonomy_thought_enabled=True,
        tools_enabled=False,
        allowed_tools=["SEARCH", "STOCK", "SUMMARY"],
        tool_max_rounds=3,
        max_bubbles=2,
        dynamic_context_enabled=True,
        reasoning_effort="low",
        followup_window_seconds=900,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _proactive_request(motivation: str = "無聊累積 82/80") -> SimpleNamespace:
    return SimpleNamespace(
        channel_id="1",
        mode="proactive",
        events=(),
        priority=1,
        must_reply=False,
        tool_policy=None,
        target_author_id="",
        message=None,
        reply_to=None,
        trace_id="p-autonomy",
        web_topic=None,
        motivation=motivation,
        mood="無聊 82/100、社交滿足 25/100",
        autonomy_wait_max_minutes=90,
    )


async def _run_proactive(
    worker: CyberRoomWorker,
    model: FakeModel,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: SimpleNamespace | None = None,
) -> None:
    monkeypatch.setattr(worker, "_persona_parts", lambda *args: ("persona", ""))
    monkeypatch.setattr(worker, "_memory_block", lambda *args, **kwargs: asyncio.sleep(0, result=""))
    decision = TurnDecision(speak=True, reason="proactive", trigger="proactive")
    try:
        await worker._execute_turn(
            _proactive_request(),
            decision,
            [],
            config or make_config(sandbox_channel_id="1"),
        )
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_proactive_silent_does_not_send(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = StaticModel("[[SILENT]]")
    events: list[dict] = []
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        event_sink=events.append,
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_ui", lambda: make_autonomy_ui())

    await _run_proactive(worker, model, monkeypatch)

    assert channel.sent == []
    assert model.calls == 1
    assert worker.last_autonomy is not None
    assert worker.last_autonomy["act"] == "silent"
    assert worker.last_autonomy["motivation"] == "無聊累積 82/80"
    assert "社交滿足" in worker.last_autonomy["mood"]
    assert worker.autonomy_next_at == 0.0
    assert any(item.get("event") == "autonomy_decision" for item in events)


@pytest.mark.asyncio
async def test_proactive_wait_delays_next_opportunity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = StaticModel("[[WAIT:5]]")
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_ui", lambda: make_autonomy_ui())

    await _run_proactive(worker, model, monkeypatch)

    assert channel.sent == []
    assert worker.last_autonomy is not None
    assert worker.last_autonomy["act"] == "wait"
    assert worker.last_autonomy["wait_minutes"] == 30
    assert worker.autonomy_next_at >= time.time() + 29 * 60


@pytest.mark.asyncio
async def test_proactive_channel_guard_blocks_other_channels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = StaticModel("測試回覆")
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_ui", lambda: make_autonomy_ui())

    await _run_proactive(
        worker,
        model,
        monkeypatch,
        config=make_config(sandbox_channel_id="999"),
    )

    assert channel.sent == []
    assert model.calls == 0


@pytest.mark.asyncio
async def test_autonomy_hourly_cap_blocks_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = StaticModel("測試回覆")
    events: list[dict] = []
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        event_sink=events.append,
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_ui", lambda: make_autonomy_ui(autonomy_hourly=1))
    worker.proactive_budget.record(time.time())

    await worker._handle_proactive()

    assert model.calls == 0
    assert any(
        item.get("event") == "autonomy_decision" and item.get("act") == "blocked"
        for item in events
    )


@pytest.mark.asyncio
async def test_autonomy_disabled_keeps_prompt_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = CapturingGenerateModel("測試回覆")
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(
        worker,
        "_ui",
        lambda: make_autonomy_ui(autonomy_enabled=False),
    )

    await _run_proactive(worker, model, monkeypatch)

    assert channel.sent == ["測試回覆"]
    assert model.last_kwargs is not None
    assert "<autonomy>" not in model.last_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_proactive_thought_and_silent_keeps_thought(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = StaticModel("[[THINK: 現在丟新聞有點怪]]\n[[SILENT]]")
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_ui", lambda: make_autonomy_ui())

    await _run_proactive(worker, model, monkeypatch)

    assert channel.sent == []
    assert worker.last_autonomy is not None
    assert worker.last_autonomy["act"] == "silent"
    assert worker.last_autonomy["thought"] == "現在丟新聞有點怪"
    assert worker.recent_autonomy(5)[0]["thought"] == "現在丟新聞有點怪"
    assert worker._consecutive_silents == 1
    assert worker._consecutive_speaks == 0


@pytest.mark.asyncio
async def test_proactive_thought_and_speak_strips_thought(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = StaticModel("[[THINK: 這個想吐槽]]\n欸你們看到那個了嗎")
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_ui", lambda: make_autonomy_ui())
    import handlers.care.surfer as surfer_module

    monkeypatch.setattr(surfer_module, "replenish_mind_state", lambda **kwargs: None)
    monkeypatch.setattr(surfer_module, "update_bot_speak_timestamp", lambda *args, **kwargs: None)
    monkeypatch.setattr(surfer_module, "add_token_consumption", lambda *args, **kwargs: None)
    monkeypatch.setattr(surfer_module, "record_proactive_action_memory", lambda *args, **kwargs: None)

    await _run_proactive(worker, model, monkeypatch)

    assert channel.sent == ["欸你們看到那個了嗎"]
    assert "THINK" not in channel.sent[0]
    assert worker.last_autonomy is not None
    assert worker.last_autonomy["act"] == "speak"
    assert worker.last_autonomy["thought"] == "這個想吐槽"
    assert worker._consecutive_speaks == 1
    assert worker._consecutive_silents == 0


@pytest.mark.asyncio
async def test_only_thought_becomes_silent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = StaticModel("[[THINK: 算了，不講了]]")
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_ui", lambda: make_autonomy_ui())

    await _run_proactive(worker, model, monkeypatch)

    assert channel.sent == []
    assert worker.last_autonomy is not None
    assert worker.last_autonomy["act"] == "silent"
    assert worker.last_autonomy["thought"] == "算了，不講了"


def test_recent_autonomy_returns_newest_first(tmp_path) -> None:
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(FakeChannel()),
        config_provider=_config,
        model=FakeModel(),
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    worker._append_autonomy_history({"ts": 1.0, "act": "silent"})
    worker._append_autonomy_history({"ts": 2.0, "act": "speak"})

    items = worker.recent_autonomy(2)
    assert [item["act"] for item in items] == ["speak", "silent"]


@pytest.mark.asyncio
async def test_autonomy_nudges_after_repeated_decisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    channel = FakeChannel()
    model = CapturingGenerateModel("測試回覆")
    worker = CyberRoomWorker(
        channel_id="1",
        bot=FakeBot(channel),
        config_provider=_config,
        model=model,
        shadow=False,
        ledger_path=tmp_path / "ledger.json",
        debounce_seconds=0,
    )
    monkeypatch.setattr(worker, "_ui", lambda: make_autonomy_ui())
    monkeypatch.setattr(worker, "_after_send", lambda *args, **kwargs: None)
    worker._consecutive_silents = 4
    worker._consecutive_speaks = 3

    await _run_proactive(worker, model, monkeypatch)

    assert model.last_kwargs is not None
    prompt = model.last_kwargs["user_prompt"]
    assert "安靜 4 次" in prompt
    assert "主動開過 3 次" in prompt
