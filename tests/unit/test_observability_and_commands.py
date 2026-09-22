"""
tests/unit/test_observability_and_commands.py
=============================================
測試全系統日誌、Trace ID 與指令/工具可觀測性。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import pytest

from handlers.social.observability import (
    TraceIdFilter,
    flush_observability_logs,
    get_trace_id,
    log_command,
    log_error,
    log_event,
    log_inference,
    log_tool_exec,
    make_timer,
    new_trace_id,
    set_trace_id,
)


def test_trace_id_generation_and_contextvar() -> None:
    """測試 Trace ID 生成與 ContextVar 設定讀取。"""
    # 預設狀態或生成新 ID
    tid1 = new_trace_id("cmd")
    assert tid1.startswith("cmd-")
    assert len(tid1) == 12

    # 設定 ContextVar
    set_trace_id(tid1)
    assert get_trace_id() == tid1

    # 自動生成設定
    tid2 = set_trace_id()
    assert tid2.startswith("tr-")
    assert get_trace_id() == tid2


def test_trace_id_filter() -> None:
    """測試 TraceIdFilter 是否正確將 trace_id 寫入 LogRecord。"""
    filter_instance = TraceIdFilter()
    set_trace_id("test-trace-1234")

    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Hello World",
        args=(),
        exc_info=None,
    )
    result = filter_instance.filter(record)
    assert result is True
    assert getattr(record, "trace_id", None) == "test-trace-1234"


def test_log_event_and_log_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 log_event 與 log_error 寫入 JSONL 檔案。"""
    runtime_file = tmp_path / "runtime_log.jsonl"
    error_file = tmp_path / "error_log.jsonl"

    monkeypatch.setattr("handlers.social.observability._RUNTIME_LOG_PATH", runtime_file)
    monkeypatch.setattr("handlers.social.observability._ERROR_LOG_PATH", error_file)

    set_trace_id("tr-event-test")

    # 1. 寫入普通事件
    log_event(
        "chat_reply",
        user="TestUser",
        channel_id=12345,
        raw_input="Hello Sherry",
        intent="CHAT",
        latency_ms=150,
        tools_invoked=["CHAT"],
    )

    flush_observability_logs()
    assert runtime_file.exists()
    content = runtime_file.read_text(encoding="utf-8")
    assert "chat_reply" in content
    assert "tr-event-test" in content
    assert "TestUser" in content
    assert "150" in content

    # 2. 寫入錯誤事件
    log_error(
        event="command_error",
        task_id="task-999",
        error_type="ValueError",
        message="Invalid parameter passed",
        extra={"detail": "debug info"},
    )

    flush_observability_logs()
    assert error_file.exists()
    err_content = error_file.read_text(encoding="utf-8")
    assert "command_error" in err_content
    assert "ValueError" in err_content
    assert "Invalid parameter passed" in err_content


def test_log_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 log_command 記錄 Slash 指令與按鈕點擊事件。"""
    runtime_file = tmp_path / "runtime_log.jsonl"
    monkeypatch.setattr("handlers.social.observability._RUNTIME_LOG_PATH", runtime_file)

    set_trace_id("cmd-slash-001")

    # 測試 Slash 指令
    log_command(
        command_type="slash",
        command_name="vibe",
        user="AdminUser (admin)",
        user_id=112233,
        guild_name="TestGuild",
        guild_id=998877,
        channel_name="general",
        channel_id=12345,
        options={"action": "show"},
        latency_ms=45,
        status="success",
    )

    flush_observability_logs()
    content = runtime_file.read_text(encoding="utf-8")
    data = json.loads(content.strip())
    assert data["event"] == "command_exec"
    assert data["trace_id"] == "cmd-slash-001"
    assert data["intent"] == "SLASH: vibe"
    assert data["extra"]["command_name"] == "vibe"
    assert data["extra"]["options"] == {"action": "show"}
    assert data["extra"]["user_id"] == 112233


def test_log_tool_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 log_tool_exec 記錄工具執行輸入、輸出與耗時。"""
    runtime_file = tmp_path / "runtime_log.jsonl"
    monkeypatch.setattr("handlers.social.observability._RUNTIME_LOG_PATH", runtime_file)

    set_trace_id("tool-trace-002")

    log_tool_exec(
        tool_name="SEARCH",
        tool_input="2026 AI news",
        observation="Found 3 articles about AI agents.",
        latency_ms=320,
        status="success",
        user="Alice",
        channel_id=1001,
        message_id=2002,
    )

    flush_observability_logs()
    content = runtime_file.read_text(encoding="utf-8")
    data = json.loads(content.strip())
    assert data["event"] == "tool_exec"
    assert data["trace_id"] == "tool-trace-002"
    assert data["intent"] == "TOOL: SEARCH"
    assert data["extra"]["tool_name"] == "SEARCH"
    assert "Found 3 articles" in data["extra"]["observation"]
    assert data["latency_ms"] == 320


def test_log_inference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 log_inference 記錄 LLM 推論任務。"""
    runtime_file = tmp_path / "runtime_log.jsonl"
    monkeypatch.setattr("handlers.social.observability._RUNTIME_LOG_PATH", runtime_file)

    set_trace_id("llm-trace-003")

    log_inference(
        task_id="vision-9988",
        model="qwen2.5:7b",
        prompt_length=450,
        completion_length=120,
        queue_latency_ms=25,
        inference_latency_ms=850,
        status="success",
        prompt_preview="Describe this image in detail",
        completion_preview="A blue haired detective girl",
    )

    flush_observability_logs()
    content = runtime_file.read_text(encoding="utf-8")
    data = json.loads(content.strip())
    assert data["event"] == "inference_done"
    assert data["trace_id"] == "llm-trace-003"
    assert data["model_used"] == "qwen2.5:7b"
    assert data["extra"]["prompt_length"] == 450
    assert data["extra"]["queue_latency_ms"] == 25
    assert data["latency_ms"] == 850


def test_make_timer() -> None:
    """測試 make_timer 耗時回傳。"""
    import time
    timer = make_timer()
    time.sleep(0.02)
    elapsed = timer()
    assert elapsed >= 0


def test_format_thinking_steps_block_structured() -> None:
    """測試 format_thinking_steps_block 針對結構化日誌輸出包含模型、耗時與 1~4 步驟。"""
    from handlers.social.utils import format_thinking_steps_block

    thought_log = (
        "【Parallel 任務排程思考】\n"
        "分析意圖為查詢股市與閒聊\n\n"
        "【工具並行回傳數據】\n"
        "- STOCK: 2330.TW 收盤價 980 元 (+15)\n\n"
        "【雪莉內心思考】\n"
        "用傲嬌名偵探語氣回答"
    )

    block = format_thinking_steps_block(
        thought_log=thought_log,
        intent_name="PARALLEL_AGENT",
        model="Qwen2.5-7B",
        latency_ms=720,
        prompt="查台積電",
    )

    assert "> 💡 **雪莉思維推演與執行步驟 (Thinking Process)**" in block
    assert "⚡ **模型**: `Qwen2.5-7B`" in block
    assert "**總耗時**: `720 ms`" in block
    assert "**意圖**: `PARALLEL_AGENT`" in block
    assert "1️⃣ 📋 **意圖拆解 [Plan]**" in block
    assert "2️⃣ 🛠️ **工具與觀測 [Action & Obs]**" in block
    assert "3️⃣ 💭 **角色心智 [Persona]**" in block
    assert "4️⃣ ✨ **完成輸出 [Done]**" in block


def test_format_thinking_steps_block_plain_and_empty() -> None:
    """測試 format_thinking_steps_block 針對純 CoT 與空思考之容錯處理。"""
    from handlers.social.utils import format_thinking_steps_block

    # 1. 純 CoT 思維鏈
    block_cot = format_thinking_steps_block(
        thought_log="這是一個純文字思考鏈，我要給出有禮貌的回覆。",
        intent_name="CHAT",
        model="deepseek-r1:7b",
        latency_ms=350,
    )
    assert "1️⃣ 🧠 **思維鏈推演 [CoT]**" in block_cot
    assert "2️⃣ ✨ **完成輸出 [Done]**" in block_cot
    assert "`deepseek-r1:7b`" in block_cot
    assert "`350 ms`" in block_cot

    # 2. 空思考日誌
    block_empty = format_thinking_steps_block(
        thought_log="",
        intent_name="CHAT",
        model="qwen:7b",
        latency_ms=120,
    )
    assert "1️⃣ ⚡ **直接生成 [Direct]**" in block_empty
    assert "2️⃣ ✨ **完成輸出 [Done]**" in block_empty


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_web_ui_sqlite_log_viewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anyio_backend: str) -> None:
    """測試 Web UI /api/logs/files 與 /api/logs 能正確列出與查詢 SQLite 資料表日誌。"""
    import settings
    from core.db import db_transaction
    from web_ui.server import app

    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", test_data_dir)

    # 建立測試 chat_history.db 與 runtime_events 表
    chat_db = test_data_dir / "chat_history.db"
    with db_transaction(chat_db) as cur:
        cur.execute("""
            CREATE TABLE runtime_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                trace_id TEXT,
                event TEXT NOT NULL,
                user TEXT,
                channel_id TEXT,
                latency_ms INTEGER DEFAULT 0,
                data_json TEXT NOT NULL
            )
        """)
        cur.execute("""
            INSERT INTO runtime_events (timestamp, trace_id, event, user, channel_id, latency_ms, data_json)
            VALUES ('2026-08-30 12:00:00', 'tr-1234', 'chat_reply', 'Yuuto', '123', 50, '{"event":"chat_reply","reply_preview":"Hello!"}')
        """)

    client = app.test_client()

    # 1. 測試 GET /api/logs/files
    resp_files = await client.get("/api/logs/files")
    assert resp_files.status_code == 200
    files_data = await resp_files.get_json()
    assert any("chat_history.db" in f["name"] for f in files_data)

    # 2. 測試 GET /api/logs?file=chat_history.db [runtime_events]
    resp_logs = await client.get("/api/logs?file=chat_history.db%20[runtime_events]&page=1&size=10")
    assert resp_logs.status_code == 200
    logs_data = await resp_logs.get_json()
    assert logs_data["total"] == 1
    assert len(logs_data["entries"]) == 1
    assert logs_data["entries"][0]["event"] == "chat_reply"
    assert logs_data["entries"][0]["reply_preview"] == "Hello!"


def test_admin_audit_manager_sqlite(tmp_path: Path) -> None:
    """測試 AdminAuditManager 的 SQLite 寫入與審計歷程讀取。"""
    from mod.audit_manager import AdminAuditManager

    test_log = tmp_path / "admin_audit.jsonl"
    test_db = tmp_path / "chat_history.db"

    mgr = AdminAuditManager(log_path=test_log, db_path=test_db)

    # 1. 記錄登入
    rec = mgr.record_login("sess_123", "admin", "127.0.0.1", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0")
    assert rec["username"] == "admin"
    assert rec["device"] == "Windows / Chrome"

    # 2. 記錄操作
    mgr.record_action("sess_123", "系統設定修改", "更新 PREFIX 為 !")

    # 3. 記錄登出
    mgr.record_logout("sess_123")

    # 4. 讀取歷史
    history = mgr.get_audit_history(limit=10)
    assert len(history) >= 1
    assert history[0]["session_id"] == "sess_123"
    assert history[0]["username"] == "admin"


def test_observability_batch_flush() -> None:
    """測試 observability 的多筆事件批次排空與寫入。"""
    from handlers.social.observability import _RUNTIME_LOG_PATH, _WRITE_QUEUE

    for i in range(10):
        log_event(
            event="batch_test_event",
            data={"index": i, "status": "ok"},
            user=f"user_{i}",
            channel_id=1000 + i,
            latency_ms=10 + i,
        )
    flush_observability_logs()
    assert _WRITE_QUEUE.empty()
    assert _RUNTIME_LOG_PATH.exists()
    content = _RUNTIME_LOG_PATH.read_text(encoding="utf-8")
    assert "batch_test_event" in content
    assert "user_9" in content

