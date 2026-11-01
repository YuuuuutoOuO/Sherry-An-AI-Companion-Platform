"""tools/drill_recovery.py
========================
災難還原與降級演練腳本 (Disaster Recovery Drill Runner).

支援情境：
  - D1: 單一雲端供應商故障 (Single Provider Outage & Failover)
  - D2: 全雲端 LLM 癱瘓 (All Provider Failure & Safe Offline Mock Fallback)
  - D3: SQLite 資料庫損毀與備份還原 (Database Corruption & Recovery)

執行範例：
  python tools/drill_recovery.py --all
  python tools/drill_recovery.py --scenario d3
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile

# 確保當前目錄在 sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.autonomy_guard import global_autonomy_guard
from core.db import backup_database, restore_database, verify_database_integrity, get_db_connection
from handlers.social.observability import log_event, redact_pii

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DrillRecovery")


def run_d1_drill() -> dict[str, object]:
    """演練 D1：單一供應商故障與路由切換。"""
    logger.info("==> 執行演練 D1: 單一雲端供應商故障與容錯回退")
    start_time = datetime.now(timezone.utc).isoformat()
    
    # 模擬供應商失敗情境與重試切換
    class MockProviderError(RuntimeError):
        pass

    primary_called = False
    secondary_called = False

    def primary_provider() -> str:
        nonlocal primary_called
        primary_called = True
        raise MockProviderError("HTTP 503: Primary Provider Unavailable")

    def secondary_provider() -> str:
        nonlocal secondary_called
        secondary_called = True
        return "雪莉收到你的訊息囉！目前為備用通道回覆。"

    # 模擬派發器的容錯鏈路
    result_text = ""
    error_logged = False
    try:
        result_text = primary_provider()
    except MockProviderError as err:
        log_event("drill_d1_provider_failover", extra={"error": str(err)})
        error_logged = True
        result_text = secondary_provider()

    passed = primary_called and secondary_called and bool(result_text) and error_logged
    return {
        "scenario": "D1_single_provider_outage",
        "passed": passed,
        "started_at": start_time,
        "details": {
            "primary_called": primary_called,
            "secondary_called": secondary_called,
            "result_text": result_text,
            "error_logged": error_logged,
        },
    }


def run_d2_drill() -> dict[str, object]:
    """演練 D2：全雲端 LLM 癱瘓、自主熔斷與安全離線模式。"""
    logger.info("==> 執行演練 D2: 全雲端 LLM 癱瘓與自主熔斷")
    start_time = datetime.now(timezone.utc).isoformat()

    # 1. 觸發自主性熔斷 (Kill Switch)
    global_autonomy_guard.trigger_kill_switch(reason="Drill D2: All LLM Providers Offline")
    decision_before = global_autonomy_guard.check("spark_push", {})
    blocked = not decision_before.allowed

    # 2. 模擬離線安全回覆 (Safe Offline Mock Persona)
    offline_canned_reply = "嗚……雪莉現在腦袋有點暈暈的，暫時連不上思考中樞，稍後再跟我聊天好嗎？"
    safe_response = redact_pii(offline_canned_reply)

    # 3. 解除熔斷
    global_autonomy_guard.release_kill_switch()
    decision_after = global_autonomy_guard.check("spark_push", {})
    recovered = decision_after.allowed

    passed = blocked and recovered and (safe_response == offline_canned_reply)
    return {
        "scenario": "D2_all_llm_outage_safe_mode",
        "passed": passed,
        "started_at": start_time,
        "details": {
            "kill_switch_blocked_background_tasks": blocked,
            "kill_switch_recovered": recovered,
            "offline_reply_safe": bool(safe_response),
        },
    }


def run_d3_drill(temp_dir: Path) -> dict[str, object]:
    """演練 D3：SQLite 資料庫損毀、偵測與線上備份還原。"""
    logger.info("==> 執行演練 D3: SQLite 資料庫損毀與線上備份還原")
    start_time = datetime.now(timezone.utc).isoformat()

    live_db = temp_dir / "live_chat.db"
    backup_db = temp_dir / "backup_chat.db"

    # 1. 初始化資料庫並寫入基準資料
    conn = get_db_connection(live_db)
    try:
        conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, content TEXT, created_at TEXT);")
        conn.execute("INSERT INTO test_data (content, created_at) VALUES ('雪莉核心記憶測試', datetime('now'));")
        conn.execute("INSERT INTO test_data (content, created_at) VALUES ('使用者偏好紀錄', datetime('now'));")
        conn.commit()
    finally:
        conn.close()

    # 2. 建立線上備份
    bkp_success = backup_database(live_db, backup_db)
    bkp_valid, bkp_msg = verify_database_integrity(backup_db)

    # 3. 故意損毀線上資料庫 (覆蓋檔案頭部使其非合法 SQLite)
    with open(live_db, "wb") as f:
        f.write(b"CORRUPTED_DATABASE_HEADER_DATA_INJECTION_TEST_1234567890\x00\xff" * 20)

    # 4. 驗證損毀偵測
    corrupt_valid, corrupt_msg = verify_database_integrity(live_db)
    corruption_detected = not corrupt_valid

    # 5. 從備份執行原子還原
    restore_success = restore_database(backup_db, live_db)
    restore_valid, restore_msg = verify_database_integrity(live_db)

    # 6. 查驗還原後資料筆數與內容
    row_count = 0
    if restore_success and restore_valid:
        conn = get_db_connection(live_db)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM test_data;")
            row_count = cur.fetchone()[0]
        finally:
            conn.close()

    passed = bkp_success and bkp_valid and corruption_detected and restore_success and restore_valid and (row_count == 2)
    return {
        "scenario": "D3_database_corruption_and_restore",
        "passed": passed,
        "started_at": start_time,
        "details": {
            "backup_success": bkp_success,
            "backup_valid": bkp_valid,
            "corruption_detected": corruption_detected,
            "restore_success": restore_success,
            "restore_valid": restore_valid,
            "restored_rows": row_count,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sherry Disaster Recovery Drill Runner")
    parser.add_argument("--scenario", choices=["d1", "d2", "d3", "all"], default="all", help="Scenario to run")
    parser.add_argument("--all", action="store_true", help="Run all disaster recovery scenarios")
    parser.add_argument("--report-dir", type=str, default=str(_ROOT / "data" / "drills"), help="Directory for reports")
    args = parser.parse_args()

    scenario = "all" if args.all else args.scenario
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td)
        if scenario in ("d1", "all"):
            results.append(run_d1_drill())
        if scenario in ("d2", "all"):
            results.append(run_d2_drill())
        if scenario in ("d3", "all"):
            results.append(run_d3_drill(tpath))

    all_passed = all(bool(r.get("passed")) for r in results)

    # 匯出結構化報告
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = report_dir / f"drill_report_{timestamp_str}.json"
    full_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "all_passed": all_passed,
        "scenarios_executed": len(results),
        "results": results,
    }
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(full_report, f, ensure_ascii=False, indent=2)

    logger.info("==========================================")
    logger.info("災難還原演練結果摘要 (Drill Summary):")
    for r in results:
        status_sym = "✅ 通過 (PASS)" if r.get("passed") else "❌ 失敗 (FAIL)"
        logger.info("  - %s: %s", r.get("scenario"), status_sym)
    logger.info("演練完整報告已儲存至: %s", report_file)
    logger.info("==========================================")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
