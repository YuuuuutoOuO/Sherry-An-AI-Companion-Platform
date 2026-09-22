"""
tests/unit/test_dependency_and_architecture_fixes.py
===================================================
針對 06 依賴套件與第三方函式庫檢查進行之單元測試：
1. STT 惰性載入與 ImportError 容錯保護
2. Card Scraper 移除 webdriver-manager 改用原生 Selenium 管理
3. DataFetcher 以 aiohttp 替換同步 requests
4. requirements.txt 相依性規範校驗
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.voice.stt import SpeechToText
from mod.market_plugin.card_scraper import fetch_snkrdunk_grades_sync
from mod.market_plugin.data_fetcher import DataFetcher


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# =============================================================================
# 1. STT 惰性載入與防禦保護測試
# =============================================================================

@pytest.mark.anyio
async def test_stt_import_error_resilience() -> None:
    """驗證當缺少 transformers 或 torch 時，SpeechToText 啟動不崩潰且能優雅降級。"""
    # Arrange: 模擬環境未安裝 transformers / torch
    with patch.dict("sys.modules", {"transformers": None, "torch": None}):
        with patch("handlers.voice.stt.logger.warning") as mock_log_warn:
            # Act
            stt = SpeechToText()

            # Assert
            assert stt._model is None
            mock_log_warn.assert_called()

            # Act & Assert: 轉譯應回傳空字串且不崩潰
            result = await stt.transcribe(b"dummy_wav_data")
            assert result == ""

            # Act & Assert: 卸載模型不報錯
            stt.unload()
            assert stt._model is None


@pytest.mark.anyio
async def test_stt_transcribe_success_and_exception() -> None:
    """驗證 STT 模型在正常辨識與內部推論錯誤時的行為。"""
    # Arrange
    mock_transformers = MagicMock()
    mock_pipeline_inst = MagicMock()
    mock_pipeline_inst.return_value = {"text": " 你好世界 "}
    mock_transformers.pipeline.return_value = mock_pipeline_inst

    mock_hf = MagicMock()
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False

    with patch.dict(
        "sys.modules",
        {
            "transformers": mock_transformers,
            "huggingface_hub": mock_hf,
            "torch": mock_torch,
        },
    ):
        with patch("handlers.voice.stt.os.path.exists", return_value=True), \
             patch("handlers.voice.stt.os.listdir", return_value=["model.bin"]):
            stt = SpeechToText()
            assert stt._model is not None

            # Act (成功案例)
            text = await stt.transcribe(b"fake_audio_bytes")

            # Assert
            assert text == "你好世界"

            # Act (推論拋出異常時安全回退)
            mock_pipeline_inst.side_effect = RuntimeError("GPU Out of Memory")
            err_text = await stt.transcribe(b"fake_audio_bytes")
            assert err_text == ""


# =============================================================================
# 2. Card Scraper 原生 Selenium 測試
# =============================================================================

def test_card_scraper_selenium_unavailable_fallback() -> None:
    """驗證 Selenium 未安裝時，卡片爬蟲安全回退並標記 N/A。"""
    # Arrange
    test_dict = {"噴火龍": "https://snkrdunk.com/en/trading-cards/12345"}
    target_grades = ["A", "PSA 10", "BGS 9.5"]

    with patch("mod.market_plugin.card_scraper._SELENIUM_AVAILABLE", False):
        # Act
        results = fetch_snkrdunk_grades_sync(test_dict, target_grades)

        # Assert
        assert len(results) == 1
        assert results[0]["Card Name"] == "噴火龍"
        assert results[0]["A"] == "N/A"
        assert results[0]["PSA 10"] == "N/A"
        assert results[0]["BGS 9.5"] == "N/A"


def test_card_scraper_native_selenium_invocation() -> None:
    """驗證卡片爬蟲呼叫原生 webdriver.Chrome(options=...) 且不調用 webdriver_manager。"""
    # Arrange
    test_dict = {"噴火龍": "https://snkrdunk.com/products/9999"}
    target_grades = ["A", "PSA 10"]

    mock_driver = MagicMock()
    mock_driver.page_source = """
    <div>
        <div>Listed Items</div>
        <div>US $100</div>
        <div>PSA 10</div>
        <div>US $350</div>
    </div>
    """

    with patch("mod.market_plugin.card_scraper._SELENIUM_AVAILABLE", True), \
         patch("mod.market_plugin.card_scraper.webdriver.Chrome", return_value=mock_driver) as mock_chrome, \
         patch("mod.market_plugin.card_scraper.WebDriverWait"), \
         patch("mod.market_plugin.card_scraper.time.sleep"):

        # Act
        results = fetch_snkrdunk_grades_sync(test_dict, target_grades)

        # Assert: 驗證使用 options 參數初始化原生 Chrome driver
        mock_chrome.assert_called_once()
        _, kwargs = mock_chrome.call_args
        assert "options" in kwargs
        assert "service" not in kwargs

        assert len(results) == 1
        assert results[0]["Card Name"] == "噴火龍"
        assert results[0]["A"] == 100
        assert results[0]["PSA 10"] == 350
        mock_driver.quit.assert_called_once()


# =============================================================================
# 3. DataFetcher aiohttp 重構與博弈數據測試
# =============================================================================

def test_data_fetcher_no_synchronous_requests_session() -> None:
    """驗證 DataFetcher 不再包含 requests.Session 執行個體。"""
    # Arrange & Act
    fetcher = DataFetcher()

    # Assert
    assert not hasattr(fetcher, "_session")


@pytest.mark.anyio
async def test_data_fetcher_crypto_advanced_metrics_async() -> None:
    """驗證非同步 get_crypto_advanced_metrics_async 正常解析 Fear&Greed 與 Binance 數據。"""
    # Arrange
    fetcher = DataFetcher()

    mock_fng_resp = AsyncMock()
    mock_fng_resp.status = 200
    mock_fng_resp.json = AsyncMock(return_value={"data": [{"value": "80", "value_classification": "Extreme Greed"}]})

    mock_premium_resp = AsyncMock()
    mock_premium_resp.status = 200
    mock_premium_resp.json = AsyncMock(return_value={"lastFundingRate": "0.00015"})

    mock_ls_resp = AsyncMock()
    mock_ls_resp.status = 200
    mock_ls_resp.json = AsyncMock(return_value=[{"longShortRatio": "2.45"}])

    mock_session = MagicMock()

    class MockContextManager:
        def __init__(self, resp: Any) -> None:
            self.resp = resp

        async def __aenter__(self) -> Any:
            return self.resp

        async def __aexit__(self, *args: Any) -> None:
            pass

    def mock_get(url: str, **kwargs: Any) -> MockContextManager:
        if "alternative.me" in url:
            return MockContextManager(mock_fng_resp)
        elif "premiumIndex" in url:
            return MockContextManager(mock_premium_resp)
        elif "globalLongShortAccountRatio" in url:
            return MockContextManager(mock_ls_resp)
        raise ValueError(f"Unknown URL {url}")

    mock_session.get = mock_get
    mock_session.closed = False

    # Act
    metrics = await fetcher.get_crypto_advanced_metrics_async("BTC-USD", session=mock_session)

    # Assert
    assert metrics["fng_value"] == "80"
    assert metrics["fng_class"] == "Extreme Greed"
    assert metrics["funding_rate"] == "0.0150%"
    assert metrics["funding_signal"] == "中性"
    assert metrics["long_short_ratio"] == 2.45
    assert metrics["ls_signal"] == "🔴 散戶做多"


@pytest.mark.anyio
async def test_data_fetcher_crypto_metrics_sync_wrapper() -> None:
    """驗證同步包裝方法 get_crypto_advanced_metrics 在事件迴圈內外皆能安全執行。"""
    # Arrange
    fetcher = DataFetcher()
    fake_metrics = {
        "fng_value": "50",
        "fng_class": "Neutral",
        "funding_rate": "0.0100%",
        "funding_signal": "中性",
        "long_short_ratio": 1.0,
        "ls_signal": "中性",
    }

    with patch.object(fetcher, "get_crypto_advanced_metrics_async", new=AsyncMock(return_value=fake_metrics)):
        # Act: 在異步事件迴圈內部呼叫同步方法 (測試 ThreadPoolExecutor 橋接)
        res_inside_loop = fetcher.get_crypto_advanced_metrics("BTC-USD")

        # Assert
        assert res_inside_loop == fake_metrics

    # Act: 在無事件迴圈之一般執行緒中呼叫同步方法 (測試 asyncio.run 橋接)
    def _sync_worker() -> dict[str, Any]:
        with patch.object(fetcher, "get_crypto_advanced_metrics_async", new=AsyncMock(return_value=fake_metrics)):
            return fetcher.get_crypto_advanced_metrics("BTC-USD")

    res_outside_loop = await asyncio.to_thread(_sync_worker)
    assert res_outside_loop == fake_metrics


@pytest.mark.anyio
async def test_data_fetcher_crypto_metrics_error_resilience() -> None:
    """驗證網路異常或 HTTP 錯誤時，get_crypto_advanced_metrics_async 正確回退至預設值。"""
    # Arrange
    fetcher = DataFetcher()

    mock_session = MagicMock()

    class ErrorContextManager:
        async def __aenter__(self) -> Any:
            raise ConnectionResetError("Connection refused")

        async def __aexit__(self, *args: Any) -> None:
            pass

    mock_session.get = MagicMock(return_value=ErrorContextManager())
    mock_session.closed = False

    # Act
    metrics = await fetcher.get_crypto_advanced_metrics_async("BTC-USD", session=mock_session)

    # Assert
    assert metrics["fng_value"] == "N/A"
    assert metrics["fng_class"] == "N/A"
    assert metrics["funding_rate"] == "N/A"
    assert metrics["funding_signal"] == "無數據"
    assert metrics["long_short_ratio"] == "N/A"
    assert metrics["ls_signal"] == "無數據"


@pytest.mark.anyio
async def test_data_fetcher_crypto_funding_rate_and_ls_signals() -> None:
    """驗證多頭擁擠、空頭擁擠以及散戶看空之特殊訊號判斷邏輯。"""
    # Arrange
    fetcher = DataFetcher()

    mock_fng_resp = AsyncMock()
    mock_fng_resp.status = 200
    mock_fng_resp.json = AsyncMock(return_value={"data": [{"value": "20", "value_classification": "Extreme Fear"}]})

    # 1. 測試空頭擁擠 (< 0) 與散戶看空 (< 0.8)
    mock_premium_resp = AsyncMock()
    mock_premium_resp.status = 200
    mock_premium_resp.json = AsyncMock(return_value={"lastFundingRate": "-0.0005"})

    mock_ls_resp = AsyncMock()
    mock_ls_resp.status = 200
    mock_ls_resp.json = AsyncMock(return_value=[{"longShortRatio": "0.65"}])

    mock_session = MagicMock()

    class MockContextManager:
        def __init__(self, resp: Any) -> None:
            self.resp = resp

        async def __aenter__(self) -> Any:
            return self.resp

        async def __aexit__(self, *args: Any) -> None:
            pass

    def mock_get(url: str, **kwargs: Any) -> MockContextManager:
        if "alternative.me" in url:
            return MockContextManager(mock_fng_resp)
        elif "premiumIndex" in url:
            return MockContextManager(mock_premium_resp)
        elif "globalLongShortAccountRatio" in url:
            return MockContextManager(mock_ls_resp)
        raise ValueError(f"Unknown URL {url}")

    mock_session.get = mock_get
    mock_session.closed = False

    # Act
    metrics = await fetcher.get_crypto_advanced_metrics_async("ETH-USD", session=mock_session)

    # Assert
    assert metrics["funding_signal"] == "🔥 空頭擁擠"
    assert metrics["ls_signal"] == "🟢 散戶看空"

    # 2. 測試多頭擁擠 (> 0.01)
    mock_premium_resp.json = AsyncMock(return_value={"lastFundingRate": "0.015"})
    mock_ls_resp.json = AsyncMock(return_value=[])  # 空清單時安全回退
    metrics_crowded = await fetcher.get_crypto_advanced_metrics_async("ETH-USD", session=mock_session)
    assert metrics_crowded["funding_signal"] == "⚠️ 多頭擁擠"
    assert metrics_crowded["long_short_ratio"] == "N/A"
    assert metrics_crowded["ls_signal"] == "N/A"


def test_card_scraper_empty_input() -> None:
    """驗證當輸入空的 card_dict 時，回傳空列表。"""
    # Arrange & Act
    results = fetch_snkrdunk_grades_sync({}, ["A", "PSA 10"])

    # Assert
    assert results == []


# =============================================================================
# 4. requirements.txt 相依性規範審查測試
# =============================================================================

def test_requirements_file_specifications() -> None:
    """驗證 requirements.txt 符合依賴套件與第三方函式庫審查規範。"""
    # Arrange
    req_path = Path(__file__).resolve().parent.parent.parent / "requirements.txt"
    assert req_path.exists(), f"找不到 requirements.txt: {req_path}"

    content = req_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]

    # 1. 驗證遺漏套件已補入
    assert any(line.startswith("Pillow>=12.3.0") for line in lines), "缺少 Pillow>=12.3.0"
    assert any(line.startswith("cryptography>=50.0.1") for line in lines), "缺少 cryptography>=50.0.1"
    assert any(line.startswith("trafilatura>=2.2.0") for line in lines), "缺少 trafilatura>=2.2.0"
    assert any(line.startswith("imageio-ffmpeg>=0.6.0") for line in lines), "缺少 imageio-ffmpeg>=0.6.0"

    # 2. 驗證孤兒相依已移除
    for orphan in ["pydantic", "librosa", "soundfile", "torchaudio", "webdriver-manager"]:
        assert not any(line.startswith(orphan) for line in lines), f"未移除孤兒套件: {orphan}"

    # 3. 驗證 CVE 升級版本已生效
    assert any(line.startswith("aiohttp>=3.14.3") for line in lines), "aiohttp 未升級至 >=3.14.3"
    assert any(line.startswith("python-dotenv>=1.2.3") for line in lines), "python-dotenv 未升級至 >=1.2.3"
    assert any(line.startswith("PyNaCl>=1.6.2") for line in lines), "PyNaCl 未升級至 >=1.6.2"

    # 4. 驗證 google-genai 與 beautifulsoup4 不在可略過選用註解區塊下方
    # 分割檢查
    parts = content.split("# --- Optional Modules Dependencies (mod/market_plugin) ---")
    assert len(parts) == 2
    optional_section = parts[1]
    assert "google-genai" not in optional_section, "google-genai 不應置於可略過選用區塊"
    assert "beautifulsoup4" not in optional_section, "beautifulsoup4 不應置於可略過選用區塊"
