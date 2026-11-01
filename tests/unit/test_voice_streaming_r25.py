"""tests/unit/test_voice_streaming_r25.py
=======================================
R25: 多模態 M3（低延遲對話與串流管線）單元測試。
驗證：
1. SentenceStreamSplitter 句子級即時切分與殘留處理；
2. BargeInController 使用者開口打斷與任務取消；
3. LowLatencyVoicePipeline 串流即刻 TTS 管線；
4. 出口驗收指標：端到端 P50 < 1.5s 延遲驗證。
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
import pytest

from handlers.voice.stream_pipeline import (
    BargeInController,
    LowLatencyVoicePipeline,
    SentenceStreamSplitter,
)


# ---------------------------------------------------------------------------
# 1. 句子級即時切分器
# ---------------------------------------------------------------------------

def test_sentence_stream_splitter_punctuations():
    splitter = SentenceStreamSplitter(min_sentence_len=4)

    # 模擬 token 逐批抵達
    s1 = splitter.feed("你好呀！")
    assert s1 == ["你好呀！"]

    s2 = splitter.feed("今天")
    assert s2 == []

    s3 = splitter.feed("天氣真好呢。")
    assert s3 == ["今天天氣真好呢。"]

    # 二級標點在超過 12 字時觸發切句
    long_chunk = "我剛才在街角看到一隻好可愛的小黑貓，"
    s4 = splitter.feed(long_chunk)
    assert len(s4) == 1
    assert "小黑貓，" in s4[0]

    # 殘留字串 flush
    splitter.feed("快點過來看")
    residual = splitter.flush()
    assert residual == "快點過來看"


def test_sentence_stream_splitter_edge_cases():
    """邊界測試：驗證短感嘆詞不卡死串流、二級標點前導短詞不遺失、長無標點文本截斷。"""
    splitter = SentenceStreamSplitter(min_sentence_len=4, max_sentence_len=30)

    # 1. 極短感嘆詞（len 2 < 4）：不應卡死後續句子切分
    out1 = splitter.feed("好！")
    assert out1 == []  # 暫存等待
    out2 = splitter.feed("今天天氣真好。")
    assert out2 == ["好！今天天氣真好。"]  # 順利與後續句子合併輸出，不卡死！
    assert splitter.flush() is None

    # 2. 二級標點前導短詞（如 "好，"）：切句時不得遺失
    s2 = SentenceStreamSplitter(min_sentence_len=4, max_sentence_len=50)
    out_sec = s2.feed("好，這是一段超過十二個字且只有逗號的句子，後面還有")
    assert len(out_sec) == 1
    assert out_sec[0] == "好，這是一段超過十二個字且只有逗號的句子，"
    assert s2.flush() == "後面還有"

    # 3. 連續標點
    s3 = SentenceStreamSplitter(min_sentence_len=4)
    out_excl = s3.feed("天啊！！！！！！太棒了！！")
    assert len(out_excl) >= 1
    assert "天啊！！！！！！" in out_excl[0]

    # 4. 長無標點文本：強制在 max_sentence_len 截斷
    s4 = SentenceStreamSplitter(min_sentence_len=4, max_sentence_len=20)
    out_long = s4.feed("A" * 50)
    assert len(out_long) == 2
    assert len(out_long[0]) == 20
    assert len(out_long[1]) == 20
    assert s4.flush() == "A" * 10


# ---------------------------------------------------------------------------
# 2. Barge-in (使用者插話打斷)
# ---------------------------------------------------------------------------

def test_barge_in_controller():
    stop_called = False

    def on_stop():
        nonlocal stop_called
        stop_called = True

    barge_in = BargeInController(stop_playback_callback=on_stop)

    # 1. 閒置狀態下開口 -> 不觸發中斷
    assert not barge_in.on_user_speech_detected()
    assert not stop_called

    # 2. 播音中開口 -> 觸發中斷
    barge_in.is_speaking = True
    assert barge_in.on_user_speech_detected()
    assert stop_called
    assert not barge_in.is_speaking
    assert barge_in._barge_in_count == 1


# ---------------------------------------------------------------------------
# 3. 低延遲串流管線與 P50 < 1.5s 指標驗收
# ---------------------------------------------------------------------------

def test_streaming_voice_pipeline_latency_and_p50_benchmark():
    """模擬真實 LLM Token 串流與即刻 TTS 合成：
    驗證首音延遲 (TTFA: Time to First Audio) 端到端 P50 < 1.5s。
    """
    pipeline = LowLatencyVoicePipeline()

    async def _run_single_session() -> float:
        # 模擬 LLM 串流：每 30ms 產生一個 token
        async def mock_token_stream():
            tokens = ["好", "呀", "！", "我", "剛", "才", "正", "好", "想", "出", "門", "呢", "。", "你", "想", "去", "哪", "裡", "？"]
            for t in tokens:
                await asyncio.sleep(0.02)  # 20ms 生成延遲
                yield t

        # 模擬句子級 TTS 合成（約 80ms 快速合成首句）
        async def mock_synthesize(sentence: str) -> bytes:
            await asyncio.sleep(0.08)
            return b"WAV_AUDIO_DATA"

        audios_received: list[bytes] = []

        async def mock_on_audio(audio: bytes, sentence: str) -> None:
            audios_received.append(audio)

        stats = await pipeline.run_streaming_conversation(
            mock_token_stream(),
            mock_synthesize,
            mock_on_audio,
        )
        assert len(audios_received) >= 1
        return stats["ttfa_ms"]

    async def _run_benchmark():
        # 執行 10 輪模擬對話以計算 P50
        for _ in range(10):
            ttfa = await _run_single_session()
            assert ttfa > 0

        stats = pipeline.get_latency_stats()
        print(f"\n[Voice Pipeline Latency] Count: {stats['count']}, P50: {stats['p50_ms']}ms, P95: {stats['p95_ms']}ms")

        # 出口門檻：端到端 P50 < 1500ms (1.5s)
        assert stats["p50_ms"] < 1500.0, f"端到端 P50 延遲超標: {stats['p50_ms']}ms >= 1500ms"
        assert stats["count"] == 10

    asyncio.run(_run_benchmark())
