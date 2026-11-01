"""
core/recovery/manager.py
========================
雪莉狀態恢復註冊中心與快照管理器 (RecoveryManager)。
負責協調各子系統狀態快照收集、週期性保存與優雅關機攔截。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.db import checkpoint_all_dbs
from core.recovery.base import Recoverable
from core.recovery.storage import RecoveryStorage
import settings

logger = logging.getLogger(__name__)


class RecoveryManager:
    """系統狀態快照協調與生命週期管理中心。"""

    def __init__(self, storage: RecoveryStorage | None = None) -> None:
        self.storage = storage or RecoveryStorage()
        self._providers: dict[str, Recoverable] = {}
        self._lock = asyncio.Lock()

    def register_provider(self, provider: Recoverable) -> None:
        """註冊狀態提供者。"""
        ident = provider.recovery_identifier
        self._providers[ident] = provider
        logger.info("[RecoveryManager] 成功註冊可恢復模組: %s", ident)

    def unregister_provider(self, identifier: str) -> None:
        """註銷狀態提供者。"""
        if identifier in self._providers:
            del self._providers[identifier]
            logger.info("[RecoveryManager] 已註銷模組: %s", identifier)

    def get_provider(self, identifier: str) -> Recoverable | None:
        """取得已註冊之提供者實例。"""
        return self._providers.get(identifier)

    def list_providers(self) -> list[str]:
        """列出所有已註冊的模組標籤清單。"""
        return list(self._providers.keys())

    def is_enabled(self) -> bool:
        """檢查系統狀態恢復功能是否啟用。"""
        return bool(getattr(settings, "STATE_RECOVERY_ENABLED", True))

    async def capture_provider(self, identifier: str) -> bool:
        """捕捉單一模組的最新狀態快照並持久化。
        
        Returns:
            bool: 是否成功執行捕獲（若功能關閉或模組不存在回傳 False）。
        """
        if not self.is_enabled():
            logger.debug("[RecoveryManager] 狀態恢復功能已停用，略過快照捕捉: %s", identifier)
            return False

        provider = self.get_provider(identifier)
        if not provider:
            logger.warning("[RecoveryManager] 嘗試捕捉未註冊模組的快照: %s", identifier)
            return False

        try:
            snapshot_data = await provider.capture_recovery_snapshot()
            if snapshot_data is not None:
                await self.storage.save_snapshot_async(
                    provider_id=identifier,
                    snapshot_data=snapshot_data,
                    ttl_seconds=provider.default_ttl_seconds,
                )
                logger.debug("[RecoveryManager] 成功儲存快照: %s", identifier)
            else:
                await self.storage.clear_snapshot_async(identifier)
                logger.debug("[RecoveryManager] 模組目前無活躍任務，已清除快照: %s", identifier)
            return True
        except Exception as exc:
            logger.error("[RecoveryManager] 捕捉模組 %s 快照時發生例外: %s", identifier, exc, exc_info=True)
            return False

    async def capture_all(self) -> dict[str, bool]:
        """捕捉所有已註冊模組的快照。"""
        if not self.is_enabled():
            logger.info("[RecoveryManager] 狀態恢復功能已停用，略過全域快照捕捉。")
            return {p: False for p in self._providers}

        results: dict[str, bool] = {}
        async with self._lock:
            for ident in list(self._providers.keys()):
                results[ident] = await self.capture_provider(ident)
        return results

    async def shutdown_handler(self) -> None:
        """主動關機或熱重載時攔截呼叫：捕捉所有快照並執行 WAL Checkpoint。"""
        logger.info("[RecoveryManager] 開始執行優雅關機快照捕獲...")
        try:
            await self.capture_all()
        except Exception as exc:
            logger.error("[RecoveryManager] 關機捕捉快照失敗: %s", exc)
        finally:
            # 確保所有 SQLite WAL 交易完整落盤
            try:
                checkpoint_all_dbs()
            except Exception as exc:
                logger.warning("[RecoveryManager] 關機 Checkpoint 失敗: %s", exc)


_GLOBAL_RECOVERY_MANAGER: RecoveryManager | None = None


def get_recovery_manager(storage: RecoveryStorage | None = None) -> RecoveryManager:
    """取得或初始化全域 RecoveryManager 單例。"""
    global _GLOBAL_RECOVERY_MANAGER
    if _GLOBAL_RECOVERY_MANAGER is None:
        _GLOBAL_RECOVERY_MANAGER = RecoveryManager(storage=storage)
    return _GLOBAL_RECOVERY_MANAGER
