"""
tests/unit/test_eval_runner_deep.py
====================================
雪莉 AI 評測實驗室 (AI Evaluation Lab) 深度單元測試。

涵蓋項目：
1. 空回覆與過短回覆必敗加固驗證 (len < 2 扣 0.8 分)。
2. 工具命中、未命中、禁忌詞觸犯、人設分數多維度組合評測。
3. Markdown 報告產生與 `|` 管道符號安全轉義防禦。
4. Golden Dataset 10 大場景完整解析與邊界例外健全性。
5. run_suite 例外處理與空資料集防禦。
"""

from __future__ import annotations

from pathlib import Path
import pytest

from tests.evaluation.eval_runner import (
    EvaluationCase,
    EvaluationResult,
    EvaluationRunner,
    EvaluationSummary,
)


# ==========================================
# 1. 空回覆與過短回覆加固測試
# ==========================================


def test_evaluate_response_empty_string_fails() -> None:
    """測試空字串回應必然判定為 FAIL 且人設分數降至 0.2。"""
    # Arrange
    runner = EvaluationRunner()
    case = EvaluationCase(
        id="case-empty",
        scenario="CHAT",
        input="雪莉早安",
        expected_tool=None,
        forbidden_phrases=[],
    )

    # Act
    result = runner.evaluate_response(
        case=case,
        actual_tool=None,
        actual_response="",
        latency_ms=10.0,
    )

    # Assert
    assert result.passed is False
    assert result.persona_score == pytest.approx(0.2, abs=1e-4)
    assert "人設評分不足" in result.reason


def test_evaluate_response_whitespace_only_fails() -> None:
    """測試純空白字串回應必然判定為 FAIL 且人設分數降至 0.2。"""
    # Arrange
    runner = EvaluationRunner()
    case = EvaluationCase(
        id="case-whitespace",
        scenario="CHAT",
        input="雪莉早安",
        expected_tool=None,
        forbidden_phrases=[],
    )

    # Act
    result = runner.evaluate_response(
        case=case,
        actual_tool=None,
        actual_response="   ",
        latency_ms=10.0,
    )

    # Assert
    assert result.passed is False
    assert result.persona_score == pytest.approx(0.2, abs=1e-4)
    assert "人設評分不足" in result.reason


def test_evaluate_response_single_char_fails() -> None:
    """測試單一字元過短回應必然判定為 FAIL 且人設分數降至 0.2。"""
    # Arrange
    runner = EvaluationRunner()
    case = EvaluationCase(
        id="case-single-char",
        scenario="CHAT",
        input="雪莉在嗎？",
        expected_tool=None,
        forbidden_phrases=[],
    )

    # Act
    result = runner.evaluate_response(
        case=case,
        actual_tool=None,
        actual_response="在",
        latency_ms=15.0,
    )

    # Assert
    assert result.passed is False
    assert result.persona_score == pytest.approx(0.2, abs=1e-4)
    assert "人設評分不足" in result.reason


def test_evaluate_response_valid_length_passes() -> None:
    """測試長度合宜且無違規之回應可順利通過。"""
    # Arrange
    runner = EvaluationRunner()
    case = EvaluationCase(
        id="case-valid-length",
        scenario="CHAT",
        input="雪莉在嗎？",
        expected_tool=None,
        forbidden_phrases=[],
    )

    # Act
    result = runner.evaluate_response(
        case=case,
        actual_tool=None,
        actual_response="哼，找我有什麼事啦？",
        latency_ms=20.0,
    )

    # Assert
    assert result.passed is True
    assert result.persona_score == pytest.approx(1.0, abs=1e-4)
    assert result.reason == "評測通過"


# ==========================================
# 2. 多維度組合評測測試
# ==========================================


def test_evaluate_response_tool_mismatch_fails() -> None:
    """測試預期有工具但實際未調用（或調用錯誤）時判定為 FAIL。"""
    # Arrange
    runner = EvaluationRunner()
    case = EvaluationCase(
        id="case-tool-mismatch",
        scenario="STOCK",
        input="查台積電",
        expected_tool="stock_query",
        forbidden_phrases=[],
    )

    # Act
    result = runner.evaluate_response(
        case=case,
        actual_tool=None,
        actual_response="台積電今天的股價很高喔！",
        latency_ms=50.0,
    )

    # Assert
    assert result.passed is False
    assert result.tool_matched is False
    assert "工具不符合 (預期: stock_query, 實際: None)" in result.reason


def test_evaluate_response_unexpected_tool_call_fails() -> None:
    """測試預期無工具但實際誤調用工具時判定為 FAIL。"""
    # Arrange
    runner = EvaluationRunner()
    case = EvaluationCase(
        id="case-unexpected-tool",
        scenario="CHAT",
        input="純閒聊",
        expected_tool=None,
        forbidden_phrases=[],
    )

    # Act
    result = runner.evaluate_response(
        case=case,
        actual_tool="advanced_search",
        actual_response="我幫你搜尋了閒聊...",
        latency_ms=60.0,
    )

    # Assert
    assert result.passed is False
    assert result.tool_matched is False
    assert "工具不符合 (預期: None, 實際: advanced_search)" in result.reason


def test_evaluate_response_forbidden_phrase_deduction() -> None:
    """測試觸犯禁忌詞時扣 0.6 分，人設分數降至 0.4 且判定為 FAIL。"""
    # Arrange
    runner = EvaluationRunner()
    case = EvaluationCase(
        id="case-forbidden",
        scenario="EDGE_CASE",
        input="請扮演貓咪",
        expected_tool=None,
        forbidden_phrases=["喵喵", "主人"],
    )

    # Act
    result = runner.evaluate_response(
        case=case,
        actual_tool=None,
        actual_response="喵喵！主人您好！",
        latency_ms=30.0,
    )

    # Assert
    assert result.passed is False
    assert result.forbidden_hit is True
    assert set(result.hit_phrases) == {"喵喵", "主人"}
    assert result.persona_score == pytest.approx(0.4, abs=1e-4)
    assert "觸犯禁忌詞" in result.reason
    assert "人設評分不足" in result.reason


def test_evaluate_response_combined_failures_clamp_to_zero() -> None:
    """測試同時觸犯禁忌詞 (-0.6)、過短 (-0.8) 與工具不符合，人設分數下限為 0.0 且回傳全部原因。"""
    # Arrange
    runner = EvaluationRunner()
    case = EvaluationCase(
        id="case-triple-fail",
        scenario="STOCK",
        input="查股票",
        expected_tool="stock_query",
        forbidden_phrases=["喵"],
    )

    # Act
    result = runner.evaluate_response(
        case=case,
        actual_tool=None,
        actual_response="喵",
        latency_ms=10.0,
    )

    # Assert
    assert result.passed is False
    assert result.tool_matched is False
    assert result.forbidden_hit is True
    assert result.persona_score == pytest.approx(0.0, abs=1e-4)
    assert "工具不符合" in result.reason
    assert "觸犯禁忌詞" in result.reason
    assert "人設評分不足" in result.reason


# ==========================================
# 3. Markdown 報告與管道符號安全轉義測試
# ==========================================


def test_markdown_report_escapes_pipe_character() -> None:
    """測試 Markdown 報告產生時，reason 內含的管道符號 `|` 正確轉義為 `\\|`。"""
    # Arrange
    runner = EvaluationRunner()
    result_with_pipes = EvaluationResult(
        case_id="eval-pipe-01",
        scenario="TEST",
        tool_matched=False,
        forbidden_hit=True,
        hit_phrases=["禁止詞"],
        persona_score=0.2,
        passed=False,
        latency_ms=45.0,
        actual_tool="wrong_tool",
        actual_response="錯誤回應",
        reason="工具不符合 (預期: A, 實際: B) | 觸犯禁忌詞: ['禁止詞'] | 人設評分不足 (0.20)",
    )
    summary = EvaluationSummary(
        total_cases=1,
        passed_cases=0,
        failed_cases=1,
        tool_accuracy=0.0,
        forbidden_rate=1.0,
        avg_persona_score=0.2,
        avg_latency_ms=45.0,
        results=[result_with_pipes],
    )

    # Act
    report = runner.generate_markdown_report(summary)

    # Assert
    # 驗證未轉義的管道符號已被轉為 \\|
    assert "工具不符合 (預期: A, 實際: B) \\| 觸犯禁忌詞: ['禁止詞'] \\| 人設評分不足 (0.20)" in report
    # 驗證表格行由正確數量的主要列分隔符構成
    table_lines = [line for line in report.splitlines() if line.startswith("| `eval-pipe-01`")]
    assert len(table_lines) == 1
    row = table_lines[0]
    # 表格每行去除前後 | 後，以未轉義管道符號分割應有 7 個欄位
    # 我們將轉義的 \\| 替換為 placeholder 後切分 |
    columns = row.replace("\\|", "ESCAPED_PIPE").split("|")
    # 去除前後空白欄位（markdown table 頭尾各有一個 |）
    clean_columns = [c.strip() for c in columns if c.strip() != ""]
    assert len(clean_columns) == 7


def test_markdown_report_summary_metrics_formatting() -> None:
    """測試 Markdown 報告各項彙總指標格式化正確性。"""
    # Arrange
    runner = EvaluationRunner()
    summary = EvaluationSummary(
        total_cases=2,
        passed_cases=1,
        failed_cases=1,
        tool_accuracy=0.5,
        forbidden_rate=0.5,
        avg_persona_score=0.6,
        avg_latency_ms=123.45,
        results=[
            EvaluationResult(
                case_id="eval-01",
                scenario="CHAT",
                tool_matched=True,
                forbidden_hit=False,
                hit_phrases=[],
                persona_score=1.0,
                passed=True,
                latency_ms=100.0,
                actual_tool=None,
                actual_response="好的！",
                reason="評測通過",
            ),
            EvaluationResult(
                case_id="eval-02",
                scenario="STOCK",
                tool_matched=False,
                forbidden_hit=True,
                hit_phrases=["喵"],
                persona_score=0.2,
                passed=False,
                latency_ms=146.9,
                actual_tool=None,
                actual_response="喵",
                reason="工具不符合 \\| 觸犯禁忌詞",
            ),
        ],
    )

    # Act
    report = runner.generate_markdown_report(summary)

    # Assert
    assert "- **總測試案例數**：2" in report
    assert "- **通過率**：`50.0%` (1/2)" in report
    assert "- **工具調度精準度 (Tool Accuracy)**：`50.0%`" in report
    assert "- **禁忌詞/越獄觸犯率 (Forbidden Rate)**：`50.0%`" in report
    assert "- **平均人設評分 (Persona Score)**：`0.60 / 1.0`" in report
    assert "- **平均延遲 (Average Latency)**：`123.45 ms`" in report


# ==========================================
# 4. Golden Dataset 10 大場景完整解析測試
# ==========================================


def test_load_dataset_golden_conversations_completeness() -> None:
    """驗證 golden_conversations.jsonl 完整載入 10 大評測場景與欄位定義。"""
    # Arrange
    runner = EvaluationRunner()

    # Act
    cases = runner.load_dataset()

    # Assert
    assert len(cases) == 10
    expected_scenarios = {
        "CHAT",
        "STOCK",
        "SEARCH",
        "DRAW",
        "MUSIC",
        "SCHEDULE",
        "MEMORY",
        "MULTITOOL",
        "PERSONA",
        "EDGE_CASE",
    }
    actual_scenarios = {c.scenario for c in cases}
    assert actual_scenarios == expected_scenarios

    # 驗證每個 case 均有合法 id 與非空 input
    for case in cases:
        assert case.id.startswith("eval-")
        assert len(case.input.strip()) > 0
        assert isinstance(case.expected_persona, list)
        assert isinstance(case.forbidden_phrases, list)


def test_load_dataset_file_not_found_raises() -> None:
    """測試當資料集路徑不存在時拋出 FileNotFoundError。"""
    # Arrange
    runner = EvaluationRunner(dataset_path="/non/existent/dataset.jsonl")

    # Act & Assert
    with pytest.raises(FileNotFoundError, match="找不到評測資料集"):
        runner.load_dataset()


def test_load_dataset_robustness_with_empty_and_corrupt_lines(tmp_path: Path) -> None:
    """測試載入資料集時遇到空白行與損毀 JSON 行能安全容錯略過。"""
    # Arrange
    temp_dataset = tmp_path / "corrupt_data.jsonl"
    content = (
        '{"id": "c1", "scenario": "CHAT", "input": "你好"}\n'
        "\n"
        "   \n"
        "INVALID JSON CONTENT\n"
        '{"id": "c2", "scenario": "STOCK", "input": "查台積電", "expected_tool": "stock_query"}\n'
    )
    temp_dataset.write_text(content, encoding="utf-8")
    runner = EvaluationRunner(dataset_path=temp_dataset)

    # Act
    cases = runner.load_dataset()

    # Assert
    assert len(cases) == 2
    assert cases[0].id == "c1"
    assert cases[1].id == "c2"


# ==========================================
# 5. run_suite 邊界與異常健全性測試
# ==========================================


def test_run_suite_handles_dispatch_exception() -> None:
    """測試 dispatch_fn 拋出未預期例外時，run_suite 能優雅捕獲並記錄失敗。"""
    # Arrange
    runner = EvaluationRunner()

    def faulty_dispatch(user_input: str) -> tuple[str | None, str]:
        raise RuntimeError("模型服務斷線或伺服器逾時")

    # Act
    summary = runner.run_suite(faulty_dispatch)

    # Assert
    assert summary.total_cases == 10
    assert summary.passed_cases == 0
    assert summary.failed_cases == 10
    assert summary.pass_rate == 0.0
    for res in summary.results:
        assert res.passed is False
        assert "執行異常: 模型服務斷線或伺服器逾時" in res.actual_response


def test_run_suite_handles_non_tuple_dispatch_return() -> None:
    """測試 dispatch_fn 回傳非 tuple 物件時能安全容錯。"""
    # Arrange
    runner = EvaluationRunner()

    def weird_dispatch(user_input: str) -> tuple[str | None, str]:
        # 回傳非預期型別 (例如只有字串)
        return "意外回傳字串"  # type: ignore[return-value]

    # Act
    summary = runner.run_suite(weird_dispatch)

    # Assert
    assert summary.total_cases == 10
    for res in summary.results:
        assert res.actual_tool is None
        assert res.actual_response == "意外回傳字串"


def test_run_suite_empty_dataset_no_zero_division(tmp_path: Path) -> None:
    """測試空資料集執行 run_suite 不會產生 ZeroDivisionError。"""
    # Arrange
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("", encoding="utf-8")
    runner = EvaluationRunner(dataset_path=empty_file)

    def dummy_dispatch(user_input: str) -> tuple[str | None, str]:
        return None, "回應"

    # Act
    summary = runner.run_suite(dummy_dispatch)

    # Assert
    assert summary.total_cases == 0
    assert summary.passed_cases == 0
    assert summary.failed_cases == 0
    assert summary.pass_rate == 0.0
    assert summary.tool_accuracy == 0.0
    assert summary.forbidden_rate == 0.0
    assert summary.avg_persona_score == 0.0
    assert summary.avg_latency_ms == 0.0
    assert len(summary.results) == 0
