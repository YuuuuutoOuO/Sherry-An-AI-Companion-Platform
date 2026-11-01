"""tests/unit/test_spark_web.py - Spark Web REST API 與視圖單元測試。

遵循 AAA (Arrange / Act / Assert) 原則。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from quart import Quart

from handlers.spark.web import spark_bp


@pytest.fixture
def anyio_backend() -> str:
    """設定 anyio 測試後端為 asyncio。"""
    return "asyncio"


@pytest.fixture
def app_with_spark(tmp_path: Path) -> Quart:
    """提供掛載 spark_bp 的測試 Quart App。"""
    app = Quart(__name__)
    app.register_blueprint(spark_bp)
    app.config["TESTING"] = True
    return app


@pytest.mark.anyio
async def test_web_view_endpoint(app_with_spark: Quart) -> None:
    """測試 /view 視圖片段端點。"""
    # Arrange & Act
    async with app_with_spark.test_client() as client:
        res = await client.get("/view")

    # Assert
    assert res.status_code == 200
    text = await res.get_data(as_text=True)
    assert "view-spark" in text
    assert "Spark 泛用條件推播主控台" in text


@pytest.mark.anyio
async def test_web_crud_endpoints(app_with_spark: Quart) -> None:
    """測試 REST API CRUD 流程。"""
    async with app_with_spark.test_client() as client:
        # 1. 建立 Trigger
        create_payload = {
            "user_id": 12345,
            "channel_id": 67890,
            "trigger_type": "schedule",
            "condition": {"mode": "interval", "interval_minutes": 15},
            "message": "測試定時推播",
            "cooldown_seconds": 900,
        }
        res_create = await client.post(
            "/api/spark/triggers",
            json=create_payload,
        )
        assert res_create.status_code == 200
        data_create = await res_create.get_json()
        assert data_create["status"] == "success"
        trigger_id = data_create["trigger"]["trigger_id"]

        # 2. 查詢列表
        res_list = await client.get("/api/spark/triggers")
        assert res_list.status_code == 200
        data_list = await res_list.get_json()
        assert data_list["status"] == "success"
        assert any(t["trigger_id"] == trigger_id for t in data_list["triggers"])

        # 3. 測試切換狀態 (Toggle)
        res_toggle = await client.post(f"/api/spark/triggers/{trigger_id}/toggle")
        assert res_toggle.status_code == 200
        data_toggle = await res_toggle.get_json()
        assert data_toggle["status"] == "success"
        assert data_toggle["is_active"] is False

        # 4. 測試手動檢查 (Test)
        res_test = await client.post(f"/api/spark/triggers/{trigger_id}/test")
        assert res_test.status_code == 200
        data_test = await res_test.get_json()
        assert data_test["status"] == "success"
        assert "should_fire" in data_test

        # 5. 測試統計端點 (Stats)
        res_stats = await client.get("/api/spark/stats")
        assert res_stats.status_code == 200
        data_stats = await res_stats.get_json()
        assert data_stats["status"] == "success"
        assert "stats" in data_stats

        # 6. 刪除 Trigger
        res_del = await client.delete(f"/api/spark/triggers/{trigger_id}")
        assert res_del.status_code == 200
        data_del = await res_del.get_json()
        assert data_del["status"] == "success"


@pytest.mark.anyio
async def test_web_error_hides_exception_details(app_with_spark: Quart) -> None:
    """R2：端點內部例外不得外洩細節（str(exc)），回應需改帶 error_id（報告 §5.3.6）。"""
    from unittest.mock import patch

    marker = "SPARK-LEAK-MARKER-777"
    async with app_with_spark.test_client() as client:
        with patch("handlers.spark.web._get_service", side_effect=RuntimeError(marker)):
            res = await client.get("/api/spark/stats")

    assert res.status_code == 500
    text = await res.get_data(as_text=True)
    assert marker not in text
    data = await res.get_json()
    assert data.get("error_id")
