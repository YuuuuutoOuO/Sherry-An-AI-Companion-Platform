"""
tests/unit/test_recovery_framework.py
=====================================
Unit tests for the Sherry State Recovery & Resilience Core Framework (Phase 1).
"""

import time
from pathlib import Path
from typing import Any
import pytest

from core.recovery.base import Recoverable, SnapshotMetadata
from core.recovery.storage import RecoveryStorage
from core.recovery.manager import RecoveryManager
from core.recovery.reconciler import BootReconciler
import settings


class MockProvider(Recoverable):
    def __init__(self, identifier: str = "test_provider", ttl: float = 300.0) -> None:
        self._identifier = identifier
        self._ttl = ttl
        self.current_state: dict[str, Any] | None = {"foo": "bar", "count": 42}
        self.restored_payloads: list[tuple[dict[str, Any], SnapshotMetadata]] = []

    @property
    def recovery_identifier(self) -> str:
        return self._identifier

    @property
    def default_ttl_seconds(self) -> float:
        return self._ttl

    async def capture_recovery_snapshot(self) -> dict[str, Any] | None:
        return self.current_state

    async def restore_from_snapshot(
        self, snapshot_data: dict[str, Any], metadata: SnapshotMetadata
    ) -> None:
        self.restored_payloads.append((snapshot_data, metadata))


@pytest.fixture
def temp_storage(tmp_path: Path) -> RecoveryStorage:
    db_file = tmp_path / "test_recovery.db"
    return RecoveryStorage(db_path=db_file)


@pytest.mark.asyncio
async def test_storage_save_and_get(temp_storage: RecoveryStorage) -> None:
    data = {"guild_id": 12345, "queue": ["song1", "song2"]}
    await temp_storage.save_snapshot_async(
        provider_id="music_player",
        snapshot_data=data,
        ttl_seconds=180.0,
        version=2,
    )

    loaded_data, meta = await temp_storage.get_snapshot_async("music_player")
    assert loaded_data == data
    assert meta is not None
    assert meta.provider_name == "music_player"
    assert meta.ttl_seconds == 180.0
    assert meta.version == 2
    assert time.time() - meta.snapshot_time < 5.0


@pytest.mark.asyncio
async def test_storage_active_snapshots_and_restore(temp_storage: RecoveryStorage) -> None:
    await temp_storage.save_snapshot_async("p1", {"k": 1}, 100.0)
    await temp_storage.save_snapshot_async("p2", {"k": 2}, 200.0)

    actives = await temp_storage.get_active_snapshots_async()
    assert len(actives) == 2
    ids = {item[0] for item in actives}
    assert ids == {"p1", "p2"}

    await temp_storage.mark_restored_async("p1")
    actives_after = await temp_storage.get_active_snapshots_async()
    assert len(actives_after) == 1
    assert actives_after[0][0] == "p2"


@pytest.mark.asyncio
async def test_storage_archive_all_unhandled(temp_storage: RecoveryStorage) -> None:
    await temp_storage.save_snapshot_async("p1", {"k": 1}, 100.0)
    await temp_storage.save_snapshot_async("p2", {"k": 2}, 200.0)

    archived = await temp_storage.archive_all_unhandled_async()
    assert archived == 2

    actives = await temp_storage.get_active_snapshots_async()
    assert len(actives) == 0


@pytest.mark.asyncio
async def test_storage_clear_snapshot(temp_storage: RecoveryStorage) -> None:
    await temp_storage.save_snapshot_async("p1", {"k": 1}, 100.0)
    await temp_storage.clear_snapshot_async("p1")

    data, meta = await temp_storage.get_snapshot_async("p1")
    assert data is None
    assert meta is None


@pytest.mark.asyncio
async def test_manager_registration_and_capture(temp_storage: RecoveryStorage) -> None:
    manager = RecoveryManager(storage=temp_storage)
    provider = MockProvider("test_mod")

    manager.register_provider(provider)
    assert manager.list_providers() == ["test_mod"]
    assert manager.get_provider("test_mod") is provider

    # Test capture
    success = await manager.capture_provider("test_mod")
    assert success is True

    data, meta = await temp_storage.get_snapshot_async("test_mod")
    assert data == {"foo": "bar", "count": 42}
    assert meta.provider_name == "test_mod"

    # Test when provider returns None (idle), snapshot should be cleared
    provider.current_state = None
    success = await manager.capture_provider("test_mod")
    assert success is True

    data_idle, _ = await temp_storage.get_snapshot_async("test_mod")
    assert data_idle is None

    # Test unregister
    manager.unregister_provider("test_mod")
    assert manager.list_providers() == []


@pytest.mark.asyncio
async def test_manager_disabled(temp_storage: RecoveryStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = RecoveryManager(storage=temp_storage)
    provider = MockProvider("test_disabled")
    manager.register_provider(provider)

    monkeypatch.setattr(settings, "STATE_RECOVERY_ENABLED", False)

    assert manager.is_enabled() is False
    res = await manager.capture_provider("test_disabled")
    assert res is False

    data, _ = await temp_storage.get_snapshot_async("test_disabled")
    assert data is None


@pytest.mark.asyncio
async def test_reconciler_success_flow(temp_storage: RecoveryStorage) -> None:
    manager = RecoveryManager(storage=temp_storage)
    provider = MockProvider("reconcile_mod")
    manager.register_provider(provider)

    # Save a snapshot
    await temp_storage.save_snapshot_async(
        "reconcile_mod",
        {"step": "inference_in_progress"},
        ttl_seconds=60.0,
    )

    reconciler = BootReconciler(manager=manager, storage=temp_storage)
    report = await reconciler.reconcile_all()

    assert report["status"] == "completed"
    assert "reconcile_mod" in report["recovered"]
    assert len(provider.restored_payloads) == 1
    restored_data, meta = provider.restored_payloads[0]
    assert restored_data == {"step": "inference_in_progress"}
    assert meta.provider_name == "reconcile_mod"

    # Verify marked as restored
    actives = await temp_storage.get_active_snapshots_async()
    assert len(actives) == 0


@pytest.mark.asyncio
async def test_reconciler_idempotency_guard(temp_storage: RecoveryStorage) -> None:
    manager = RecoveryManager(storage=temp_storage)
    reconciler = BootReconciler(manager=manager, storage=temp_storage)

    report1 = await reconciler.reconcile_all()
    assert report1["status"] in ("completed", "clean_boot")

    # Second call should be prevented by idempotency guard
    report2 = await reconciler.reconcile_all()
    assert report2["status"] == "already_reconciled"

    # Force call should bypass guard
    report3 = await reconciler.reconcile_all(force=True)
    assert report3["status"] in ("completed", "clean_boot")


@pytest.mark.asyncio
async def test_reconciler_disabled_archives_all(
    temp_storage: RecoveryStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = RecoveryManager(storage=temp_storage)
    provider = MockProvider("p_disabled")
    manager.register_provider(provider)

    await temp_storage.save_snapshot_async("p_disabled", {"active": True}, 100.0)

    monkeypatch.setattr(settings, "STATE_RECOVERY_ENABLED", False)

    reconciler = BootReconciler(manager=manager, storage=temp_storage)
    report = await reconciler.reconcile_all()

    assert report["status"] == "disabled"
    assert report["archived_count"] == 1
    assert len(provider.restored_payloads) == 0

    # Ensure no active snapshots remain
    actives = await temp_storage.get_active_snapshots_async()
    assert len(actives) == 0


@pytest.mark.asyncio
async def test_reconciler_crash_loop_quarantine(temp_storage: RecoveryStorage) -> None:
    manager = RecoveryManager(storage=temp_storage)
    provider = MockProvider("crash_mod")
    manager.register_provider(provider)

    # Save snapshot and simulate 2 previous failed restore attempts within short time
    await temp_storage.save_snapshot_async("crash_mod", {"boom": True}, 100.0)
    await temp_storage.record_restore_attempt_async("crash_mod", time.time())
    await temp_storage.record_restore_attempt_async("crash_mod", time.time())

    reconciler = BootReconciler(manager=manager, storage=temp_storage)
    report = await reconciler.reconcile_all()

    assert "crash_mod" in report["quarantined"]
    assert len(provider.restored_payloads) == 0

    # Ensure it's not active anymore
    actives = await temp_storage.get_active_snapshots_async()
    assert len(actives) == 0
