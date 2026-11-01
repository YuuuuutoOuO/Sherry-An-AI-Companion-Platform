"""
tests/unit/test_live_thinking.py
=================================
測試 LiveThinkingSession 即時動態編輯思考推演生命週期。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import settings
from handlers.social.streaming_thinking import LiveThinkingSession
from handlers.social.utils import format_thinking_steps_block


def test_live_thinking_session_disabled_by_config() -> None:
    """測試關閉 LIVE_THINKING_EDIT 與 DEBUG_DETAIL 時，維持正常一次性發送行為。"""
    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock()
    target_msg.channel = MagicMock(spec=discord.TextChannel)
    target_msg.channel.send = AsyncMock()
    target_msg.add_reaction = AsyncMock()

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", False):
            with patch.object(settings, "DEBUG_DETAIL", False):
                session = LiveThinkingSession(target_msg)
                assert session.is_enabled is False

                await session.start("📋 意圖解析中")
                assert session.placeholder_msg is None

                session.push_step("plan", "拆解任務")
                assert len(session.steps) == 0

                sent = await session.finish("這是最終回覆！", intent_name="CHAT")
                assert len(sent) == 1
                target_msg.reply.assert_called_once()
                assert target_msg.reply.call_args[0][0] == "這是最終回覆！"

    asyncio.run(_run())


def test_live_thinking_session_streaming_thought_chunks() -> None:
    """測試接收 LLM 串流 Token 時即時動態抽取 <thought> 思考推演。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 12345
    placeholder_mock.edit = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            session = LiveThinkingSession(target_msg)
            await session.start("📋 意圖解析中")

            # 模擬 LLM 逐步產出 Token (思考中階段)
            await session.on_stream_chunk("<thought>使用者稱讚我很可愛")
            assert session._live_thought == "使用者稱讚我很可愛"
            rendered_in_progress = session._render_in_progress_block()
            assert "心智推演中 [Thinking]" in rendered_in_progress
            assert "使用者稱讚我很可愛" in rendered_in_progress

            # 模擬 LLM 思考閉合並開始正文輸出 (思考完成階段)
            await session.on_stream_chunk("，我要害羞但傲嬌地回覆</thought>嘿嘿！")
            assert session._live_thought == "使用者稱讚我很可愛，我要害羞但傲嬌地回覆"

            rendered = session._render_in_progress_block()
            assert "思維鏈推演 [Thinking]" in rendered
            assert "使用者稱讚我很可愛" in rendered

    asyncio.run(_run())


def test_live_thinking_session_enabled_lifecycle() -> None:
    """測試開啟 LIVE_THINKING_EDIT 時的完整即時佔位 ➔ 原地編輯 ➔ 最終完成生命週期。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 998877
    placeholder_mock.edit = AsyncMock()
    placeholder_mock.add_reaction = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)
    target_msg.channel.send = AsyncMock()
    target_msg.add_reaction = AsyncMock()

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            with patch.object(settings, "DEBUG_DETAIL", True):
                session = LiveThinkingSession(target_msg)
                assert session.is_enabled is True

                # 1. 初始佔位
                await session.start("📋 意圖解析中")
                assert session.placeholder_msg is placeholder_mock
                target_msg.reply.assert_called_once()
                assert "雪莉思維推演與執行步驟" in target_msg.reply.call_args[0][0]

                # 2. 推送階段步驟
                session.push_step("plan", "查詢台積電股價")
                session.push_step("obs", "STOCK: 2330.TW 收盤價 980 元")
                assert len(session.steps) == 2

                # 3. 最終完成 (DEBUG_DETAIL=True 保留推演欄)
                sent = await session.finish(
                    "台積電收盤 980 元喔！",
                    thought_log="【Parallel 任務排程思考】\n查詢股市\n\n【工具並行回傳數據】\nSTOCK: 980",
                    intent_name="PARALLEL_AGENT",
                    model="Qwen2.5-7B",
                    latency_ms=650,
                )
                assert len(sent) == 1
                placeholder_mock.edit.assert_called()
                final_edited_text = placeholder_mock.edit.call_args.kwargs["content"]
                assert "💡 **雪莉思維推演與執行步驟" in final_edited_text
                assert "台積電收盤 980 元喔！" in final_edited_text

    asyncio.run(_run())


def test_live_thinking_session_finish_without_debug_detail() -> None:
    """測試 DEBUG_DETAIL=False 時，最終完成會將思考推演欄擦除，只保留純淨回覆。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 112233
    placeholder_mock.edit = AsyncMock()
    placeholder_mock.add_reaction = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            with patch.object(settings, "DEBUG_DETAIL", False):
                session = LiveThinkingSession(target_msg)
                assert session.is_enabled is True

                await session.start("📋 意圖解析中")
                session.push_step("plan", "日常閒聊對話")

                # 生成期間包含思考推演
                rendered = session._render_in_progress_block()
                assert "雪莉思維推演與執行步驟" in rendered
                assert "日常閒聊對話" in rendered

                # 完成後 DEBUG_DETAIL=False 會抹除思考推演欄
                sent = await session.finish(
                    "今天天氣真好呢！",
                    thought_log="【Parallel 任務排程思考】\n純日常閒聊",
                    intent_name="PARALLEL_AGENT",
                    model="Gemma-4-26B",
                    latency_ms=800,
                )
                assert len(sent) == 1
                placeholder_mock.edit.assert_called()
                final_edited_text = placeholder_mock.edit.call_args.kwargs["content"]
                assert "雪莉思維推演與執行步驟" not in final_edited_text
                assert final_edited_text == "今天天氣真好呢！"

    asyncio.run(_run())


def test_live_thinking_session_render_in_progress_block() -> None:
    """測試 _render_in_progress_block 渲染進行中步驟格式與耗時。"""
    target_msg = MagicMock(spec=discord.Message)
    session = LiveThinkingSession(target_msg)
    session._prefill_ms = 120
    session._thought_ms = 350
    session._live_thought = "思考使用者需求"
    session.steps = [
        ("plan", "分析使用者提問", 50),
        ("action", "調用 STOCK(2330.TW)", 200),
        ("obs", "取得台積電 980 元", 200),
    ]
    rendered = session._render_in_progress_block()
    assert "> 💡 **雪莉思維推演與執行步驟 (Thinking Process)**" in rendered
    assert "1️⃣ 📋 **意圖拆解 [Plan]**：`50 ms` ➔ 分析使用者提問" in rendered
    assert "2️⃣ 🛠️ **工具與觀測 [Action & Obs]**：`200 ms` ➔ 調用 STOCK(2330.TW)" in rendered
    assert "3️⃣ 🛠️ **工具與觀測 [Action & Obs]**：`200 ms` ➔ 取得台積電 980 元" in rendered
    assert "4️⃣ ⚡ **PREFILL [首字生成]**：`120 ms`" in rendered
    assert "5️⃣ 🧠 **思維鏈推演 [Thinking]**：`350 ms` ➔ 思考使用者需求" in rendered


def test_live_thinking_session_fallback_on_edit_failure() -> None:
    """測試當原地 edit 失敗時（如原訊息被刪除），自動 fallback 重新發送新訊息。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "Not Found"))

    new_msg_mock = MagicMock(spec=discord.Message)
    new_msg_mock.add_reaction = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(side_effect=[placeholder_mock, new_msg_mock])
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            session = LiveThinkingSession(target_msg)
            await session.start("📋 意圖解析中")
            assert session.placeholder_msg is placeholder_mock

            sent = await session.finish("備援發送的回覆內容")
            assert len(sent) == 1
            assert sent[0] is new_msg_mock

    asyncio.run(_run())


def test_live_thinking_detailed_step_timings_in_finish() -> None:
    """測試 finish 輸出包含 PREFILL、思維鏈、工具與生成的詳細耗時指標。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 556677
    placeholder_mock.edit = AsyncMock()
    placeholder_mock.add_reaction = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            with patch.object(settings, "DEBUG_DETAIL", True):
                session = LiveThinkingSession(target_msg)
                await session.start("📋 意圖解析中")

                # 模擬串流首個 chunk 與思考
                await session.on_stream_chunk("<thought>分析台積電營收與走勢")
                await session.on_stream_chunk("，準備以專業口吻回覆</thought>台積電今天表現強勁！")

                session.push_step("action", "調用 STOCK(2330.TW)", duration_ms=250)
                session.push_step("obs", "台積電收盤 980 元", duration_ms=250)

                sent = await session.finish(
                    "台積電今天表現強勁！",
                    thought_log="分析台積電營收與走勢，準備以專業口吻回覆",
                    intent_name="STOCK_AGENT",
                    model="Qwen2.5-7B",
                    latency_ms=1200,
                )
                assert len(sent) == 1
                placeholder_mock.edit.assert_called()
                content = placeholder_mock.edit.call_args.kwargs["content"]
                assert "PREFILL [首字生成]" in content
                assert "思維鏈推演 [Thinking]" in content
                assert "工具與觀測 [Action & Obs]" in content
                assert "生成回覆 [Generation]" in content
                assert "台積電今天表現強勁！" in content

    asyncio.run(_run())


def test_live_thinking_generation_non_freezing_and_char_progress() -> None:
    """測試思考閉合或無思考標籤時，正文生成期間持續動態更新字數，不凍結死鎖。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 778899
    placeholder_mock.edit = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            session = LiveThinkingSession(target_msg)
            await session.start("💭 正在思考中")

            # 模擬 LLM 產出帶思考標籤並閉合
            await session.on_stream_chunk("<thought>思考一二三</thought>")
            assert session._pass_thought_ms is not None

            # 模擬正文串流持續湧入
            await session.on_stream_chunk("這是一段很長很長的正式回覆文本內容。")
            assert session._generated_chars > 0

            rendered = session._render_in_progress_block()
            assert "正在生成回覆中 (已輸出" in rendered

    asyncio.run(_run())


def test_live_thinking_multi_stage_prepare_synthesis_pass() -> None:
    """測試多階段調度中呼叫 prepare_synthesis_pass 清理串流緩衝，準確量測 Pass 2。"""
    target_msg = MagicMock(spec=discord.Message)
    session = LiveThinkingSession(target_msg)

    # 模擬 Pass 1: 輸出工具調用思考（已閉合思考標籤，計時狀態齊全）
    session._stream_buffer = "<thought>Pass 1 初始思考</thought><action>tool: test</action>"
    session._live_thought = "Pass 1 初始思考"
    session._pass_prefill_ms = 400
    session._pass_thought_ms = 800
    session._thought_end_time = 100.0
    session._generation_ms = 500

    # 執行重置以準備 Pass 2
    session.prepare_synthesis_pass()
    assert session._stream_buffer == ""
    assert session._live_thought == ""
    assert session._pass_prefill_ms is None
    assert session._pass_thought_ms is None
    assert session._thought_end_time is None
    assert session._generation_ms is None
    assert session._generated_chars == 0


def test_format_thinking_steps_block_cleans_duplicate_tool_obs() -> None:
    """測試 format_thinking_steps_block 萃取純思考，排除已在工具步驟印出的觀測數據。"""
    composite_thought = (
        "【雪莉初始思考】\n先查詢台北天氣狀況\n\n"
        "【工具執行回傳數據】\n- weather: 台北 晴天 28度\n\n"
        "【雪莉最終思考】\n天氣很好，以元氣滿滿口吻回覆"
    )
    tools = [("obs", "weather: 台北 晴天 28度", 300)]
    block = format_thinking_steps_block(
        thought_log=composite_thought,
        intent_name="INTEGRATED_ACTION",
        model="Qwen2.5-7B",
        latency_ms=1500,
        prefill_ms=120,
        thought_ms=250,
        tool_steps=tools,
        generation_ms=830,
    )
    # 驗證時序：先工具步驟，再 PREFILL，再思考，再生成
    assert "1️⃣ 🛠️ **工具與觀測 [Action & Obs]**：`300 ms` ➔ weather: 台北 晴天 28度" in block
    assert "2️⃣ ⚡ **PREFILL [首字生成]**：`120 ms`" in block
    assert "3️⃣ 🧠 **思維鏈推演 [Thinking]**：`250 ms` ➔ 先查詢台北天氣狀況 ➔ 天氣很好，以元氣滿滿口吻回覆" in block
    assert "4️⃣ ✨ **生成回覆 [Generation]**：`830 ms`" in block
    # 驗證觀測數據未在思考推演欄被重複打印
    assert "工具執行回傳數據" not in block


def test_live_thinking_two_pass_thought_timing_remeasured() -> None:
    """測試兩段式流程 Pass 2 能重新量測思考與生成計時（Pass 1 已閉合思考之回歸）。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 665544
    placeholder_mock.edit = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            session = LiveThinkingSession(target_msg)
            await session.start("💭 正在思考中")

            # 模擬 Pass 1（INTEGRATED 第一階段）串流並閉合思考標籤
            await session.on_stream_chunk("<thought>Pass 1 工具調度思考</thought>")
            assert session._thought_end_time is not None
            assert session._pass_thought_ms is not None

            # 進入第二階段合成前重置
            session.prepare_synthesis_pass()
            assert session._thought_end_time is None
            assert session._generation_start_time is None
            assert session._pass_thought_ms is None

            # 模擬 Pass 2 合成串流再次產出思考並閉合
            await session.on_stream_chunk("<thought>Pass 2 合成回覆思考</thought>明天一起去爬山！")
            assert session._pass_thought_ms is not None
            assert session._generation_start_time is not None
            assert session._generated_chars == len("明天一起去爬山！")

    asyncio.run(_run())


def test_live_thinking_debounce_finish_race_guard() -> None:
    """測試防競態：finish 設 _is_finished 後，卡在鎖前的節流編輯任務不會覆蓋最終回覆。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 332211
    placeholder_mock.edit = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            session = LiveThinkingSession(target_msg)
            await session.start("📋 意圖解析中")

            # 讓節流任務跳過 sleep，直接進入編輯流程
            session._last_edit_time = time.monotonic() - session._min_edit_interval - 1.0

            # 外部先握住鎖，模擬 finish 正在處理中才釋放的競態窗口
            await session._lock.acquire()
            task = asyncio.create_task(session._debounced_edit_step())
            await asyncio.sleep(0.05)

            session._is_finished = True
            session._lock.release()
            await task

            # 完成後舊的節流編輯不得再覆蓋最終回覆
            placeholder_mock.edit.assert_not_awaited()

    asyncio.run(_run())


def test_live_thinking_finish_cancels_and_awaits_pending_task() -> None:
    """測試 finish 會確實取消並等待在途的 pending task，最終訊息保證為 final reply。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 445566
    placeholder_mock.edit = AsyncMock()
    placeholder_mock.add_reaction = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            with patch.object(settings, "DEBUG_DETAIL", False):
                session = LiveThinkingSession(target_msg)
                await session.start("📋 意圖解析中")

                # 模擬串流中觸發了背景節流任務
                await session.on_stream_chunk("部分思考內容...")
                assert session._pending_task is not None
                assert not session._pending_task.done()

                # 立即呼叫 finish
                sent = await session.finish("這是最終正式回覆！")
                assert len(sent) == 1
                assert session._pending_task is None
                assert session._is_finished is True

                # 確認 placeholder_mock 的最後一次 edit 是最終回覆，而非推演中途區塊
                last_call_kwargs = placeholder_mock.edit.call_args.kwargs
                assert last_call_kwargs["content"] == "這是最終正式回覆！"
                assert "雪莉思維推演與執行步驟" not in last_call_kwargs["content"]

    asyncio.run(_run())


def test_live_thinking_finish_retries_on_rate_limit_429() -> None:
    """測試 finish 遭遇 Discord 429 RateLimit 時能自動退避重試並成功更新。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 778899
    placeholder_mock.add_reaction = AsyncMock()

    mock_resp_429 = MagicMock()
    mock_resp_429.status = 429
    rate_limit_exc = discord.HTTPException(response=mock_resp_429, message={"message": "You are being rate limited.", "retry_after": 0.05})
    setattr(rate_limit_exc, "status", 429)
    setattr(rate_limit_exc, "retry_after", 0.05)

    # 第一次調用報 429，第二次重試成功
    placeholder_mock.edit = AsyncMock(side_effect=[rate_limit_exc, None])

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            with patch.object(settings, "DEBUG_DETAIL", False):
                session = LiveThinkingSession(target_msg)
                await session.start("📋 意圖解析中")

                sent = await session.finish("這是 429 重試後成功輸出的回覆！")
                assert len(sent) == 1
                assert placeholder_mock.edit.call_count == 2
                assert placeholder_mock.edit.call_args.kwargs["content"] == "這是 429 重試後成功輸出的回覆！"

    asyncio.run(_run())


def test_live_thinking_session_fine_grained_steps_and_speed() -> None:
    """測試細緻化前置步驟 (intent/memory/vision) 與串流生成速率 (字/秒) 指標。"""
    placeholder_mock = MagicMock(spec=discord.Message)
    placeholder_mock.id = 556677
    placeholder_mock.edit = AsyncMock()

    target_msg = MagicMock(spec=discord.Message)
    target_msg.reply = AsyncMock(return_value=placeholder_mock)
    target_msg.channel = MagicMock(spec=discord.TextChannel)

    async def _run() -> None:
        with patch.object(settings, "LIVE_THINKING_EDIT", True):
            with patch.object(settings, "DEBUG_DETAIL", True):
                session = LiveThinkingSession(target_msg)
                await session.start("💭 正在思考中")

                # 1. 批量推送細緻前置步驟 (方案 A)
                session.push_steps([
                    ("intent", "感知話題氛圍與脈絡 (長度: 15 字)"),
                    ("memory", "檢索群友 [Yuuto] 專屬互動記憶 (3 筆相關事實與親密度)"),
                    ("vision", "載入多模態圖像線索與名偵探特徵感知"),
                ])
                assert len(session.steps) == 3

                # 2. 模擬首字生成到達
                session._pass_prefill_ms = 450

                # 3. 模擬生成中並計算速率 (方案 B)
                session._generation_start_time = time.monotonic() - 2.0
                session._generated_chars = 72

                rendered = session._render_in_progress_block()
                assert "1️⃣ 📋 **對話語境與意圖 [Context & Intent]**：感知話題氛圍與脈絡" in rendered
                assert "2️⃣ 🧠 **長期記憶檢索 [Memory]**：檢索群友 [Yuuto] 專屬互動記憶" in rendered
                assert "3️⃣ 👁️ **多模態視覺感知 [Vision]**：載入多模態圖像線索與名偵探特徵感知" in rendered
                assert "4️⃣ ⚡ **PREFILL [首字生成]**：`450 ms`" in rendered
                assert "正在生成回覆中 (已輸出 72 字 • " in rendered
                assert "字/秒" in rendered

                # 4. 呼叫 finish，驗證最終區塊包含生成字數與速率
                final_text = "這是一段總共二十幾個字的測試回覆內容！"
                sent = await session.finish(
                    final_text,
                    intent_name="INTEGRATED_CHAT",
                    model="Qwen2.5-7B",
                    latency_ms=2500,
                )
                assert len(sent) == 1
                final_content = placeholder_mock.edit.call_args.kwargs["content"]
                assert "✨ **生成回覆 [Generation]**：" in final_content
                assert "字 • " in final_content
                assert "字/秒" in final_content
                assert final_text in final_content

    asyncio.run(_run())
