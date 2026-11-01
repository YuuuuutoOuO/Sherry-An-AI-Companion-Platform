"""
core/recovery
=============
雪莉系統狀態快照與開機自癒恢復核心框架 (Sherry Resilience & State Recovery Framework)。
"""

from __future__ import annotations

from core.recovery.base import Recoverable, SnapshotMetadata
from core.recovery.manager import RecoveryManager, get_recovery_manager
from core.recovery.reconciler import BootReconciler, get_boot_reconciler
from core.recovery.storage import RecoveryStorage

__all__ = [
    "Recoverable",
    "SnapshotMetadata",
    "RecoveryStorage",
    "RecoveryManager",
    "get_recovery_manager",
    "BootReconciler",
    "get_boot_reconciler",
]
