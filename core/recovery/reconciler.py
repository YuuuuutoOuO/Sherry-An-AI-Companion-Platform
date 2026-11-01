"""
core/recovery/reconciler.py
===========================
雪莉開機自癒對帳引擎 (BootReconciler)。
負責開機與熱重載後的快照讀取、重入防護、崩潰循環偵測與狀態接續協調。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from core.recovery.base import SnapshotMetadata
from core.recovery.manager import RecoveryManager, get_recovery_manager
from core.recovery.storage import RecoveryStorage
import settings

logger = logging.getLogger(__name__)


class BootReconciler:
    """開機自癒對帳引擎。"""

    def __init__(
        self,
        manager: RecoveryManager | None = None,
        storage: RecoveryStorage | None = None,
    ) -> None:
        self._manager = manager or get_recovery_manager(storage=storage)
        self._storage = storage or self._manager.storage
        self._has_reconciled_boot: bool = False
        self._lock = asyncio.Lock()

    @property
    def has_reconciled_boot(self) -> bool:
        """是否已執行過開機自癒。"""
        return self._has_reconciled_boot

    def reset_boot_guard(self) -> None:
        """重設開機防護旗標（用於熱重載或手動重試）。"""
        self._has_reconciled_boot = False

    def is_enabled(self) -> bool:
        """檢查系統狀態恢復功能是否啟用。"""
        return bool(getattr(settings, "STATE_RECOVERY_ENABLED", True))

    async def reconcile_all(self, bot: Any = None, force: bool = False) -> dict[str, Any]:
        """執行全域開機狀態對帳與自癒。
        
        Args:
            bot: 可選的 Discord Bot 實例（部分 provider 可能需藉此定位頻道與成員）。
            force: 是否忽略 on_ready 單次重入保護強制執行（熱重載時使用）。
            
        Returns:
            dict: 包含對帳結果與各模組處理摘要的報表。
        """
        async with self._lock:
            if self._has_reconciled_boot and not force:
                logger.debug("[BootReconciler] 開機自癒對帳先前已執行完成，略過重入。")
                return {"status": "already_reconciled", "recovered": []}

            self._has_reconciled_boot = True

            # 1. 若功能關閉，封存所有既有快照，防止日後開啟時誤觸發舊狀態
            if not self.is_enabled():
                logger.info("[BootReconciler] STATE_RECOVERY_ENABLED=False，直接歸檔既有未處理快照。")
                archived_count = await self._storage.archive_all_unhandled_async()
                return {"status": "disabled", "archived_count": archived_count, "recovered": []}

            logger.info("[BootReconciler] 開始執行開機狀態對帳...")
            active_snapshots = await self._storage.get_active_snapshots_async()
            if not active_snapshots:
                logger.info("[BootReconciler] 查無待復原的活躍快照，無痛冷啟動。")
                return {"status": "clean_boot", "recovered": []}

            recovered_list: list[str] = []
            quarantined_list: list[str] = []
            failed_list: list[str] = []
            now = time.time()

            for provider_id, snapshot_data, metadata, attempts, quarantined in active_snapshots:
                provider = self._manager.get_provider(provider_id)
                if not provider:
                    logger.warning("[BootReconciler] 快照模組未註冊，略過復原: %s", provider_id)
                    continue

                # 2. 重啟循環防護 (Crash Loop Protection)
                # 若先前已嘗試恢復且短時間內再度重啟 (連續失敗 >= 2 次)，進行隔離防護
                if attempts >= 2 and (now - metadata.snapshot_time) < 120.0:
                    logger.critical(
                        "[BootReconciler] 模組 %s 快照在短時間內連續恢復失敗 %d 次，觸發崩潰防護並隔離快照！",
                        provider_id,
                        attempts,
                    )
                    await self._storage.mark_quarantined_async(provider_id)
                    quarantined_list.append(provider_id)
                    continue

                # 3. 記錄嘗試復原次數
                await self._storage.record_restore_attempt_async(provider_id, now)

                # 4. 調用業務端恢復邏輯
                try:
                    logger.info("[BootReconciler] 正在復原模組狀態: %s (TTL: %.1fs)", provider_id, metadata.ttl_seconds)
                    await provider.restore_from_snapshot(snapshot_data, metadata)
                    await self._storage.mark_restored_async(provider_id)
                    recovered_list.append(provider_id)
                    logger.info("[BootReconciler] 模組 %s 復原成功！", provider_id)
                except Exception as exc:
                    logger.error("[BootReconciler] 模組 %s 復原失敗: %s", provider_id, exc, exc_info=True)
                    failed_list.append(provider_id)
                    # 不在此處標記 mark_restored，以便在非崩潰情況下排查或後續策略處置

            report = {
                "status": "completed",
                "recovered": recovered_list,
                "quarantined": quarantined_list,
                "failed": failed_list,
            }
            logger.info("[BootReconciler] 開機狀態對帳完成: %s", report)
            return report


_GLOBAL_RECONCILER: BootReconciler | None = None


def get_boot_reconciler(
    manager: RecoveryManager | None = None,
    storage: RecoveryStorage | None = None,
) -> BootReconciler:
    """取得或初始化全域 BootReconciler 單例。"""
    global _GLOBAL_RECONCILER
    if _GLOBAL_RECONCILER is None:
        _GLOBAL_RECONCILER = BootReconciler(manager=manager, storage=storage)
    return _GLOBAL_RECONCILER
