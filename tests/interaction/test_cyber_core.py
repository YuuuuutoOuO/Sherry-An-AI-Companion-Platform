from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from handlers.care.cyber.contracts import ChatEvent
from handlers.care.cyber.engine import CyberEngine
from handlers.care.cyber.model import (
    GenerationResult,
    PrimaryModelClient,
    parse_tool_calls,
    resolve_primary_model,
)
from handlers.care.cyber.policy import ReplyBudget, ReplyPolicy
from handlers.care.cyber.prompts import (
    build_dynamic_context,
    build_system_prompt,
    build_user_prompt,
    build_web_topic_block,
    extract_autonomy_thought,
    looks_like_tool_promise,
    parse_autonomy_reply,
    sanitize_reply,
    split_bubbles,
)
from handlers.care.cyber.state import ChannelRuntime
from handlers.social.tool_specs import TOOL_SPECS, format_capability_hint


def make_event(mid: str, ts: float, *, author: str = "A", content: str = "hi", reply: bool = False) -> ChatEvent:
    return ChatEvent(
        channel_id="1",
        message_id=mid,
        author_id=author,
        author_name=author,
        content=content,
        created_at=ts,
        is_reply_to_bot=reply,
    )


def test_same_minute_reply_is_not_lost() -> None:
    runtime = ChannelRuntime("1")
    assert runtime.note_human(make_event("m1", 100.0, reply=True)) is True
    ticket = runtime.begin_turn(now=101.0, target_author_id="A", reason="addressed", event_ids=("m1",))
    assert runtime.note_human(make_event("m2", 102.0, content="你還沒回我")) is True
    assert runtime.has_pending_after(ticket.opened_at) is True
    runtime.commit_send(ticket, sent_at=105.0, message_ids=("bot1",))
    assert runtime.window_open(now=106.0, window_seconds=180) is True
    drained = runtime.drain_pending(after=ticket.opened_at)
    assert [event.message_id for event in drained] == ["m2"]


def test_window_opens_before_generation() -> None:
    runtime = ChannelRuntime("1")
    assert runtime.window_open(now=100.0, window_seconds=180) is False
    runtime.begin_turn(now=100.0, target_author_id="A", reason="addressed")
    assert runtime.window_open(now=100.5, window_seconds=180) is True


def test_abort_restores_previous_anchor() -> None:
    runtime = ChannelRuntime("1")
    runtime.last_bot_at = 50.0
    ticket = runtime.begin_turn(now=100.0, target_author_id="A", reason="addressed")
    assert runtime.last_bot_at == 100.0
    runtime.abort_turn(ticket, error="timeout")
    assert runtime.last_bot_at == 50.0
    assert runtime.window_open(now=300.0, window_seconds=180) is False


def test_external_bot_speak_opens_window() -> None:
    runtime = ChannelRuntime("1")
    runtime.note_external_bot_speak(sent_at=200.0, target_author_id="A", message_id="bot1")
    assert runtime.window_open(now=201.0, window_seconds=180) is True
    assert runtime.participants == {"A"}
    assert runtime.is_answered("bot1") is True


def test_proactive_turn_clears_stale_participants() -> None:
    runtime = ChannelRuntime("1")
    runtime.participants = {"B"}
    runtime.begin_turn(now=300.0, target_author_id="", reason="proactive")
    assert runtime.participants == set()
    assert runtime.window_open(now=301.0, window_seconds=300) is True


def test_external_proactive_speak_clears_stale_participants() -> None:
    runtime = ChannelRuntime("1")
    runtime.participants = {"B"}
    runtime.note_external_bot_speak(sent_at=400.0)
    assert runtime.participants == set()
    assert runtime.window_open(now=401.0, window_seconds=300) is True


def make_config(**overrides: object) -> SimpleNamespace:
    values = dict(
        enabled=True,
        chat_reply_enabled=True,
        active_chat_window_seconds=60,
        followup_window_seconds=900,
        interject_threshold=6,
        interject_score_threshold=7,
        ambient_score_threshold=6,
        interject_cooldown_seconds=120,
        max_replies_per_hour=12,
        max_replies_per_day=240,
        active_hour_start=0,
        active_hour_end=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_short_window_followup_still_replies_without_score() -> None:
    """熱聊窗口 60 秒時，4 分鐘後的續聊仍要走追話寬限，不該卡 lurk_wait。"""
    now = time.time()
    runtime = ChannelRuntime("1")
    runtime.configure_window(60, followup_seconds=900)
    runtime.note_external_bot_speak(sent_at=now - 240.0, target_author_id="A")
    event = make_event("m1", now, author="A", content="我最乖了好ㄇ:>")
    assert runtime.note_human(event) is True

    decision = ReplyPolicy().decide_human([event], runtime, make_config(), now=now)
    assert decision.speak is True
    assert decision.reason == "followup"
    assert decision.continuation is True


def test_proactive_followup_accepts_first_speaker_within_grace() -> None:
    """主動發言沒有指定對象時，寬限期內第一位回話的人就算續聊。"""
    now = time.time()
    runtime = ChannelRuntime("1")
    runtime.configure_window(60, followup_seconds=900)
    runtime.note_external_bot_speak(sent_at=now - 300.0)
    event = make_event("m1", now, author="B", content="什麼蘑菇AI")
    runtime.note_human(event)

    decision = ReplyPolicy().decide_human([event], runtime, make_config(), now=now)
    assert decision.speak is True
    assert decision.reason == "followup"


def test_followup_expires_and_uses_ambient_score() -> None:
    now = time.time()
    runtime = ChannelRuntime("1")
    runtime.configure_window(60, followup_seconds=900)
    runtime.note_external_bot_speak(sent_at=now - 1000.0)
    event = make_event("m1", now, author="B", content="路過")
    runtime.note_human(event)

    decision = ReplyPolicy().decide_human([event], runtime, make_config(), now=now)
    assert decision.speak is True
    assert decision.reason == "ambient_score"
    assert decision.score == 6.0


def test_followup_to_someone_else_does_not_auto_reply() -> None:
    now = time.time()
    runtime = ChannelRuntime("1")
    runtime.configure_window(60, followup_seconds=900)
    runtime.note_external_bot_speak(sent_at=now - 100.0, target_author_id="A")
    event = make_event("m1", now, author="B", content="路過")
    runtime.note_human(event)

    decision = ReplyPolicy().decide_human([event], runtime, make_config(), now=now)
    assert decision.speak is False
    assert decision.reason == "ambient_low_score"
    assert decision.score == 4.0


def test_scoring_disabled_replies_even_to_tiny_message() -> None:
    now = time.time()
    runtime = ChannelRuntime("1")
    runtime.configure_window(60, followup_seconds=900)
    runtime.note_external_bot_speak(sent_at=now - 1000.0)
    event = make_event("m1", now, author="A", content=".")
    runtime.note_human(event)

    decision = ReplyPolicy().decide_human(
        [event],
        runtime,
        make_config(reply_scoring_enabled=False),
        now=now,
    )
    assert decision.speak is True
    assert decision.reason == "ambient_always"
    assert decision.score == 5.0


def test_tiny_message_stays_silent_when_not_followup() -> None:
    now = time.time()
    runtime = ChannelRuntime("1")
    runtime.configure_window(60, followup_seconds=900)
    runtime.note_external_bot_speak(sent_at=now - 1000.0)
    event = make_event("m1", now, author="A", content=".")
    runtime.note_human(event)

    decision = ReplyPolicy().decide_human([event], runtime, make_config(), now=now)
    assert decision.speak is False
    assert decision.reason == "ambient_low_score"


def test_reply_budget_persists_across_workers(tmp_path) -> None:
    path = tmp_path / "cyber_ledger.json"
    now = time.time()
    first = ReplyBudget(channel_id="1", path=path)
    first.record(now)

    second = ReplyBudget(channel_id="1", path=path)
    assert second.allow(now=now, hourly=1, daily=10) is False
    assert second.allow(now=now, hourly=2, daily=10) is True
    # 不同頻道不共用額度
    other = ReplyBudget(channel_id="2", path=path)
    assert other.allow(now=now, hourly=1, daily=10) is True


def test_duplicate_message_id_is_ignored() -> None:
    runtime = ChannelRuntime("1")
    event = make_event("m1", 100.0)
    assert runtime.note_human(event) is True
    assert runtime.note_human(event) is False
    assert len(runtime.buffer) == 1


def test_sanitize_removes_thought_and_meta() -> None:
    raw = "<thought>她在問我</thought>作為一個 AI，我必須輸出答案。今天雨很大，記得帶傘。"
    cleaned = sanitize_reply(raw)
    assert "AI" not in cleaned
    assert "thought" not in cleaned
    assert "帶傘" in cleaned


def test_sanitize_removes_tool_protocol() -> None:
    cleaned = sanitize_reply("[[TOOL:web_search {\"query\":\"x\"}]] 今天天氣不錯")
    assert "TOOL" not in cleaned
    assert "天氣" in cleaned


def test_sanitize_keeps_quoted_content() -> None:
    raw = "「今天先不要出門」← 我剛說的。"
    cleaned = sanitize_reply(raw)
    assert "今天先不要出門" in cleaned


def test_split_bubbles_respects_limit() -> None:
    bubbles = split_bubbles("先去吃飯！吃完再聊？我等你～", max_bubbles=2)
    assert 1 <= len(bubbles) <= 2
    assert all(b.strip() for b in bubbles)


def test_split_bubbles_never_drops_tail_content() -> None:
    """氣泡拆分只能切、不能截斷；「1%bakery」這種尾端內容必須保留。"""
    text = (
        "沒問題，包在我身上！先說 0050 的話，現在看起來有點慘喔，"
        "跌了超過 2% 左右，甚至比 20 天前的價格還低不少，R "
        "至於台中好吃的蛋糕，我幫你掃了一下，如果你喜歡乳酪口味，"
        "那「久久津乳酪」的檸檬乳酪蛋糕評價超高，或者是「1%bakery」。"
    )
    bubbles = split_bubbles(text, max_bubbles=5, max_chars=70)
    assert 1 <= len(bubbles) <= 5
    assert "1%bakery" in bubbles[-1]
    assert "".join(bubbles) == sanitize_reply(text, max_chars=0)


def test_ui_config_keeps_five_bubbles() -> None:
    """WebUI 設 5 顆就該存 5 顆，不應被舊的硬上限 3 悄悄改掉。"""
    from handlers.care.cyber.ui_config import CyberUIConfig

    assert CyberUIConfig.from_dict({"max_bubbles": 5}).max_bubbles == 5
    assert CyberUIConfig.from_dict({"max_bubbles": 99}).max_bubbles == 8
    assert CyberUIConfig.from_dict({"max_bubbles": 0}).max_bubbles == 1


def test_sanitize_removes_dangling_quote_fragment() -> None:
    """模型被 token 上限切在引號中間時，不要送出半截店名。"""
    cleaned = sanitize_reply("我很推「1%bakery")
    assert "1%bakery" not in cleaned
    assert cleaned


def test_looks_like_tool_promise() -> None:
    assert looks_like_tool_promise("我幫你搜尋一下")
    assert looks_like_tool_promise("我先去查一下 0050")
    assert not looks_like_tool_promise("0050 今天收盤 100.5 元，跌了 2%。")
    assert not looks_like_tool_promise("我幫你查了一下，0050 今天 100.5 元。")


def test_build_web_topic_block_includes_topic_and_avoids_report_tone() -> None:
    block = build_web_topic_block(
        {"title": "台中乳酪蛋糕評比", "summary": "1%bakery 拿下高分", "source": "RSS (美食)"}
    )
    assert "台中乳酪蛋糕評比" in block
    assert "1%bakery" in block
    assert "RSS (美食)" in block
    assert "不要說「我在網路上看到」" in block
    assert build_web_topic_block(None) == ""
    assert build_web_topic_block({}) == ""


@pytest.mark.asyncio
async def test_engine_fetches_unread_web_topic() -> None:
    """v2 主動開話題前，要能從既有 CareSurfer 取得一則未讀情報。"""
    from handlers.care.cyber.engine import CyberEngine

    class FakeRepo:
        def __init__(self, enabled: bool) -> None:
            self.enabled = enabled

        def get_surfer_config(self):
            return SimpleNamespace(enabled=self.enabled)

    class FakeSurfer:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_candidates(self):
            self.calls += 1
            return [
                SimpleNamespace(
                    title="台中乳酪蛋糕評比",
                    summary="1%bakery 拿下高分",
                    source="RSS (美食)",
                    url="https://example.com/cake",
                )
            ]

    surfer = FakeSurfer()
    engine = CyberEngine(None, SimpleNamespace(repository=FakeRepo(True)), mode="live")
    engine.surfer = surfer
    topic = await engine._fetch_web_topic()
    assert topic == {
        "title": "台中乳酪蛋糕評比",
        "summary": "1%bakery 拿下高分",
        "source": "RSS (美食)",
        "url": "https://example.com/cake",
    }

    disabled = CyberEngine(None, SimpleNamespace(repository=FakeRepo(False)), mode="live")
    disabled.surfer = surfer
    assert await disabled._fetch_web_topic() is None
    assert await CyberEngine(None, SimpleNamespace(repository=FakeRepo(True)), mode="live")._fetch_web_topic() is None


def test_parse_tool_calls() -> None:
    calls = parse_tool_calls('[[TOOL:web_search {"query":"台北天氣"}]]')
    assert len(calls) == 1
    assert calls[0].name == "SEARCH"
    assert calls[0].args["query"] == "台北天氣"


def test_parse_tool_calls_without_args() -> None:
    calls = parse_tool_calls("[[TOOL:web_search]]")
    assert calls and calls[0].name == "SEARCH"
    assert calls[0].args == {}


def test_parse_tool_calls_tolerates_space_after_colon() -> None:
    """``[[TOOL: SEARCH {...}]]`` 也要執行，不能只留下「我幫你查一下」。"""
    calls = parse_tool_calls('[[TOOL: SEARCH {"query":"0050 股價"}]]')
    assert calls and calls[0].name == "SEARCH"
    assert calls[0].args["query"] == "0050 股價"


def test_parse_tool_calls_tolerates_space_in_closing() -> None:
    """模型把結尾寫成 `] ]` 時仍要執行工具，不能把協議漏到訊息裡。"""
    raw = '[[TOOL:web_search {"query":"0050 股票 最新股價 資訊"}] ]'
    calls = parse_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].name == "SEARCH"
    assert calls[0].args["query"] == "0050 股票 最新股價 資訊"


def test_parse_tool_calls_maps_common_aliases() -> None:
    assert parse_tool_calls('[[TOOL:stock_price {"query":"0050"}]]')[0].name == "STOCK"
    assert parse_tool_calls('[[TOOL:url_summary {"url":"https://x"}]]')[0].name == "SUMMARY"


def test_sanitize_removes_spaced_tool_protocol() -> None:
    raw = '查一下 [[TOOL:web_search {"query":"0050"}] ] 再告訴你'
    cleaned = sanitize_reply(raw)
    assert "TOOL" not in cleaned
    assert "web_search" not in cleaned
    assert "再告訴你" in cleaned


def test_system_prompt_stays_static_across_time() -> None:
    """system prompt 必須完全靜態，才能被 provider 的前綴快取重複利用。"""
    persona = "SOUL 人設內容"
    first = build_system_prompt(persona=persona, tools_enabled=True, mode="ambient")
    second = build_system_prompt(persona=persona, tools_enabled=True, mode="ambient")
    assert first == second
    assert first.startswith(persona)
    assert "【目前時間】" not in first
    assert "<dynamic_context>" not in first
    assert "<memory_context>" not in first
    # 靜態規則要跟在人設後面，而不是被動態內容隔開
    assert first.index("<rules>") > first.index(persona)


def test_user_prompt_carries_all_dynamic_blocks_after_transcript() -> None:
    event = make_event("m1", 100.0, content="0050 多少錢")
    prompt = build_user_prompt(
        transcript="[10:00] Yuuto: 早安",
        events=[event],
        extra="額外指示",
        memory_block="<memory_context>記憶</memory_context>",
        dynamic_block="<dynamic_context>- 現在時間：10:01</dynamic_context>",
        tail_block="⏰【目前時間】：10:01",
    )
    assert "<memory_context>" in prompt
    assert "<dynamic_context>" in prompt
    # 動態與作者備註要靠近生成點，排在近期對話之後
    assert prompt.index("【近期對話】") < prompt.index("<dynamic_context>")
    assert prompt.index("<dynamic_context>") < prompt.index("【任務】")
    assert "⏰【目前時間】" in prompt
    assert prompt.rstrip().endswith("額外指示")


@pytest.mark.asyncio
async def test_generate_passes_reasoning_effort_to_task(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list = []

    class FakeBrain:
        async def enqueue_task(self, task) -> None:
            captured.append(task)
            await task.callback("好")

    class FakeBot:
        def get_cog(self, name):
            return FakeBrain()

    import settings

    monkeypatch.setattr(settings, "PRIMARY_MODEL", "gemini:gemini-3.5-flash-lite")
    client = PrimaryModelClient(FakeBot())
    result = await client.generate(
        system_prompt="s",
        user_prompt="u",
        reasoning_effort="low",
        task_id="t1",
    )
    assert result.ok
    assert captured[0].reasoning_effort == "low"


@pytest.mark.asyncio
async def test_tool_loop_caps_first_decision_round_tokens() -> None:
    class FakeClient(PrimaryModelClient):
        def __init__(self) -> None:
            super().__init__(None)
            self.calls: list[int] = []
            self.responses = [
                '[[TOOL:STOCK {"query":"0050"}]]',
                "0050 大概 100 元。",
            ]

        async def generate(self, **kwargs) -> GenerationResult:
            self.calls.append(int(kwargs.get("max_tokens") or 0))
            return GenerationResult(text=self.responses.pop(0), model="fake")

    class FakeRunner:
        async def run_with_policy(self, policy, name, args) -> str:
            return "0050 收盤 100.5"

    client = FakeClient()
    result = await client.run_tool_loop(
        system_prompt="s",
        user_prompt="u",
        max_rounds=2,
        max_tokens=240,
        decision_max_tokens=96,
        tool_policy=SimpleNamespace(backend="readonly"),
        tool_runner=FakeRunner(),
    )
    assert client.calls == [96, 240]
    assert result.text == "0050 大概 100 元。"
    assert result.tool_rounds == 1


@pytest.mark.asyncio
async def test_tool_loop_executes_aliased_tool_and_returns_final_text() -> None:
    class FakeClient(PrimaryModelClient):
        def __init__(self) -> None:
            super().__init__(None)
            self.responses = [
                '[[TOOL:web_search {"query":"0050 股價"}] ]',
                "0050 現在大概 100 元啦。",
            ]

        async def generate(self, **kwargs) -> GenerationResult:
            return GenerationResult(text=self.responses.pop(0), model="fake-model")

    class FakeRunner:
        def __init__(self) -> None:
            self.calls = []

        async def run_with_policy(self, policy, name, args) -> str:
            self.calls.append((name, args))
            return "0050 收盤 100.5"

    runner = FakeRunner()
    result = await FakeClient().run_tool_loop(
        system_prompt="system",
        user_prompt="查 0050",
        max_rounds=2,
        tool_policy=SimpleNamespace(backend="readonly"),
        tool_runner=runner,
    )
    assert runner.calls == [("SEARCH", {"query": "0050 股價"})]
    assert result.text == "0050 現在大概 100 元啦。"
    assert result.tool_rounds == 1


@pytest.mark.asyncio
async def test_tool_loop_nudges_when_model_only_promises_to_search() -> None:
    """模型只說「我幫你搜尋一下」時，要多要一輪真正的工具呼叫。"""

    class FakeClient(PrimaryModelClient):
        def __init__(self) -> None:
            super().__init__(None)
            self.responses = [
                "我幫你搜尋一下",
                '[[TOOL: SEARCH {"query":"0050"}]]',
                "0050 今天收盤 100.5 元。",
            ]

        async def generate(self, **kwargs) -> GenerationResult:
            return GenerationResult(text=self.responses.pop(0), model="fake")

    class FakeRunner:
        def __init__(self) -> None:
            self.calls = []

        async def run_with_policy(self, policy, name, args) -> str:
            self.calls.append((name, args))
            return "0050 收盤 100.5"

    runner = FakeRunner()
    result = await FakeClient().run_tool_loop(
        system_prompt="s",
        user_prompt="0050 現在多少",
        max_rounds=3,
        max_tokens=800,
        tool_policy=SimpleNamespace(backend="readonly"),
        tool_runner=runner,
    )
    assert runner.calls == [("SEARCH", {"query": "0050"})]
    assert result.text == "0050 今天收盤 100.5 元。"
    assert result.tool_rounds == 1


@pytest.mark.asyncio
async def test_tool_loop_truncates_long_tool_result() -> None:
    """proactive 的工具結果要截斷，避免一次塞進 4,000 字。"""

    class FakeClient(PrimaryModelClient):
        def __init__(self) -> None:
            super().__init__(None)
            self.prompts: list[str] = []
            self.responses = ['[[TOOL:SEARCH {"query":"x"}]]', "好啦。"]

        async def generate(self, **kwargs) -> GenerationResult:
            self.prompts.append(str(kwargs.get("user_prompt", "")))
            return GenerationResult(text=self.responses.pop(0), model="fake")

    class FakeRunner:
        async def run_with_policy(self, policy, name, args) -> str:
            return "長" * 5000

    client = FakeClient()
    result = await client.run_tool_loop(
        system_prompt="s",
        user_prompt="查一下",
        max_rounds=2,
        tool_policy=SimpleNamespace(backend="readonly"),
        tool_runner=FakeRunner(),
        tool_result_max_chars=1200,
    )
    assert result.text == "好啦。"
    assert len(client.prompts) == 2
    assert "…（已截斷）" in client.prompts[1]
    assert "長" * 1201 not in client.prompts[1]


def test_capability_hint_lists_only_allowed() -> None:
    hint = format_capability_hint({"SEARCH", "STOCK", "SUMMARY"})
    available_line = hint.splitlines()[0]
    assert "搜尋" in available_line
    assert "股票" in available_line
    assert "網址摘要" in available_line
    assert "音樂" not in available_line
    assert "不可用" in hint
    assert "音樂" in hint


def test_capability_hint_is_deterministic() -> None:
    allowed = frozenset({"SUMMARY", "SEARCH", "STOCK"})
    values = {format_capability_hint(allowed) for _ in range(10)}
    assert len(values) == 1


def test_capability_hint_omits_unavailable_when_everything_allowed() -> None:
    hint = format_capability_hint({spec.name for spec in TOOL_SPECS})
    assert "本回合可用" in hint
    assert "不可用" not in hint


def test_capability_hint_normalizes_aliases() -> None:
    hint = format_capability_hint({"web_search", "stock_price"})
    assert "搜尋" in hint
    assert "股票" in hint


def test_capability_hint_length_budget() -> None:
    hint = format_capability_hint({"SEARCH", "STOCK", "SUMMARY"})
    assert len(hint) <= 80


def test_system_prompt_adds_capability_hint_only_with_allowed_tools() -> None:
    with_hint = build_system_prompt(
        persona="p",
        tools_enabled=True,
        mode="ambient",
        allowed_tools={"SEARCH", "STOCK", "SUMMARY"},
    )
    without_hint = build_system_prompt(persona="p", tools_enabled=True, mode="ambient")
    disabled = build_system_prompt(
        persona="p",
        tools_enabled=False,
        mode="ambient",
        allowed_tools={"SEARCH"},
    )
    assert "本回合可用" in with_hint
    assert "本回合可用" not in without_hint
    assert "本回合可用" not in disabled


def test_dynamic_context_proactive_includes_motivation() -> None:
    text = build_dynamic_context(
        now=1000.0,
        unanswered=0,
        last_bot_at=0.0,
        last_human_at=0.0,
        phase="idle",
        silence_seconds=1800.0,
        trigger="proactive",
        motivation="無聊累積 82/80",
    )
    assert "你自己想開話題" in text
    assert "無聊累積 82/80" in text


def test_dynamic_context_ambient_has_no_autonomy_line() -> None:
    text = build_dynamic_context(
        now=1000.0,
        unanswered=0,
        last_bot_at=0.0,
        last_human_at=0.0,
        phase="idle",
        silence_seconds=1800.0,
        trigger="",
        motivation="無聊累積 82/80",
    )
    assert "你自己想開話題" not in text
    assert "無聊累積" not in text


def test_dynamic_context_proactive_without_motivation_has_no_empty_parens() -> None:
    text = build_dynamic_context(
        now=1000.0,
        unanswered=0,
        last_bot_at=0.0,
        last_human_at=0.0,
        phase="idle",
        silence_seconds=0.0,
        trigger="proactive",
        motivation="",
    )
    assert "你自己想開話題" in text
    assert "（）" not in text


def test_describe_mind_prefers_boredom() -> None:
    assert CyberEngine._describe_mind(
        boredom=82,
        boredom_th=80,
        social=50,
        social_th=30,
        idle_ready=True,
        silence_minutes=40,
    ) == "無聊累積 82/80"


def test_describe_mind_uses_social_and_idle() -> None:
    assert CyberEngine._describe_mind(
        boredom=20,
        boredom_th=80,
        social=25,
        social_th=30,
        idle_ready=False,
        silence_minutes=0,
    ) == "想聊天（社交滿足 25）"
    assert CyberEngine._describe_mind(
        boredom=20,
        boredom_th=80,
        social=50,
        social_th=30,
        idle_ready=True,
        silence_minutes=32.8,
    ) == "頻道安靜 32 分鐘"


def test_parse_autonomy_silent_and_wait() -> None:
    assert parse_autonomy_reply("[[SILENT]]") == ("silent", 0)
    assert parse_autonomy_reply("  [[ silent ]]  ") == ("silent", 0)
    assert parse_autonomy_reply("[[WAIT:45]]") == ("wait", 45)
    assert parse_autonomy_reply("[[wait:5]]") == ("wait", 30)
    assert parse_autonomy_reply("[[WAIT:300]]") == ("wait", 90)


def test_parse_autonomy_embedded_protocol_is_speech() -> None:
    assert parse_autonomy_reply("我想想 [[SILENT]]") == ("speak", 0)
    assert parse_autonomy_reply("先不要好了") == ("speak", 0)


def test_sanitize_strips_autonomy_protocol() -> None:
    cleaned = sanitize_reply("先這樣 [[SILENT]]")
    assert "SILENT" not in cleaned
    assert cleaned == "先這樣"
    thought_cleaned = sanitize_reply("[[THINK: 不想講]]\n先這樣")
    assert "THINK" not in thought_cleaned
    assert thought_cleaned == "先這樣"


def test_extract_autonomy_thought() -> None:
    thought, rest = extract_autonomy_thought("[[THINK: 大家好像都睡了]]\n[[SILENT]]")
    assert thought == "大家好像都睡了"
    assert rest.strip() == "[[SILENT]]"

    thought, rest = extract_autonomy_thought("[[think: 想吐槽]]\n欸你們看到那個了嗎")
    assert thought == "想吐槽"
    assert rest.strip() == "欸你們看到那個了嗎"

    thought, rest = extract_autonomy_thought("沒有內心戲")
    assert thought == ""
    assert rest == "沒有內心戲"


def test_system_prompt_adds_thought_protocol_when_enabled() -> None:
    with_thought = build_system_prompt(
        persona="p",
        mode="proactive",
        autonomy_enabled=True,
        autonomy_thought_enabled=True,
    )
    without_thought = build_system_prompt(
        persona="p",
        mode="proactive",
        autonomy_enabled=True,
        autonomy_thought_enabled=False,
    )
    assert "[[THINK:" in with_thought
    assert "[[THINK:" not in without_thought


def test_system_prompt_adds_autonomy_only_for_proactive() -> None:
    proactive = build_system_prompt(
        persona="p",
        mode="proactive",
        autonomy_enabled=True,
        autonomy_wait_max_minutes=90,
    )
    ambient = build_system_prompt(
        persona="p",
        mode="ambient",
        autonomy_enabled=True,
    )
    disabled = build_system_prompt(
        persona="p",
        mode="proactive",
        autonomy_enabled=False,
    )
    assert "<autonomy>" in proactive
    assert "[[SILENT]]" in proactive
    assert "30～90" in proactive
    assert "<autonomy>" not in ambient
    assert "<autonomy>" not in disabled


def test_engine_status_payload_includes_autonomy() -> None:
    engine = CyberEngine(
        bot=None,
        care_engine=SimpleNamespace(repository=None),
        mode="shadow",
    )
    payload = engine.status_payload()
    autonomy = payload["autonomy"]
    assert "enabled" in autonomy
    assert "hourly_limit" in autonomy
    assert "daily_limit" in autonomy
    assert autonomy["act"] == ""


def test_resolve_primary_model_reads_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    import settings

    monkeypatch.setattr(settings, "PRIMARY_MODEL", "model-A", raising=False)
    assert resolve_primary_model() == "model-A"
    monkeypatch.setattr(settings, "PRIMARY_MODEL", "model-B", raising=False)
    assert resolve_primary_model() == "model-B"
