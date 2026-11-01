"""
core/autonomy_guard.py
======================
全域自主性熔斷 (Autonomy Kill Switch) 與行動預算管理器 (AutonomyBudgetManager)。

提供：
1. `GLOBAL_AUTONOMY_KILL_SWITCH`: 一鍵緊急阻斷所有主動行為 (SPARK 推播、主動插話、排程任務)。
2. `AutonomyBudgetManager`: 滑動時間窗預算控制，防止失控迴圈 (Runaway Loops) 造成 Token 浪費或洗頻。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Callable

from core.risk_engine import ActionRiskLevel, RiskPolicyEngine
from core.system_state import SystemHealthLevel, SystemMetrics, SystemStateNormalizer

logger = logging.getLogger(__name__)

ActionGate = Callable[[str, dict[str, object]], tuple[bool, str]]


@dataclass(frozen=True)
class GuardDecision:
    """治理閘門的單次判定結果。

    Attributes:
        allowed: 是否允許本次自主行動。
        action_type: 送審的行動代碼。
        risk_level: 風險引擎判定之等級。
        health_level: 判定當下的系統健康等級。
        reason: 允許或拒絕的可讀原因。
    """

    allowed: bool
    action_type: str
    risk_level: ActionRiskLevel
    health_level: SystemHealthLevel
    reason: str


class AutonomyGuard:
    """自主性防護與預算管理器。"""

    def __init__(
        self,
        max_actions_per_hour: int = 15,
        max_actions_per_day: int = 100,
        risk_engine: RiskPolicyEngine | None = None,
        state_normalizer: SystemStateNormalizer | None = None,
    ) -> None:
        """初始化防護與預算設定。

        Args:
            max_actions_per_hour: 每小時最大自主行動上限。
            max_actions_per_day: 每日最大自主行動上限。
            risk_engine: 三級風險策略引擎（預設建立新實例）。
            state_normalizer: 系統健康狀態聚合器（預設建立新實例）。
        """
        if max_actions_per_hour < 0 or max_actions_per_day < 0:
            raise ValueError("自主行動預算上限不可為負數")
        self.max_actions_per_hour = max_actions_per_hour
        self.max_actions_per_day = max_actions_per_day
        self._risk_engine = risk_engine or RiskPolicyEngine()
        self._state_normalizer = state_normalizer or SystemStateNormalizer()
        self._health_level: SystemHealthLevel = SystemHealthLevel.HEALTHY
        self._lock = threading.Lock()
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str = ""
        self._kill_switch_triggered_at: str | None = None
        self._action_timestamps: deque[float] = deque(maxlen=max(10, max_actions_per_day * 2))
        # R19：可插拔的領域閘門（例如主動關懷的 opt-in 同意）；名稱重複時覆蓋。
        self._gates: dict[str, ActionGate] = {}

    def register_gate(self, name: str, gate: ActionGate) -> None:
        """註冊領域閘門；``gate(action_type, params)`` 回傳 (是否允許, 原因)。

        閘門在風險／健康檢查之後、全域預算之前執行；例外視為拒絕
        （fail-closed），避免治理元件故障時放行自主行為。
        """
        with self._lock:
            self._gates[str(name)] = gate

    def unregister_gate(self, name: str) -> None:
        with self._lock:
            self._gates.pop(str(name), None)

    @property
    def is_kill_switch_active(self) -> bool:
        """取得全域自主熔斷狀態。"""
        return self._kill_switch_active

    @property
    def kill_switch_reason(self) -> str:
        """取得熔斷觸發原因。"""
        return self._kill_switch_reason

    @property
    def health_level(self) -> SystemHealthLevel:
        """取得最近一次評估的系統健康等級。"""
        return self._health_level

    def update_system_metrics(self, metrics: SystemMetrics) -> SystemHealthLevel:
        """以最新系統指標刷新健康等級，供閘門降級判斷。

        Args:
            metrics: 系統當前指標快照。

        Returns:
            SystemHealthLevel: 聚合後之健康等級。
        """
        level = self._state_normalizer.evaluate_health(metrics)
        with self._lock:
            self._health_level = level
        if level in (SystemHealthLevel.WARNING, SystemHealthLevel.INCIDENT):
            logger.warning("[AutonomyGuard] 系統健康等級: %s，自主行動將受限", level.value)
        return level

    def check(
        self,
        action_type: str,
        params: dict[str, object] | None = None,
        description: str = "",
    ) -> GuardDecision:
        """治理閘門唯一入口：風險分級 → 系統健康 → 領域閘門 → 熔斷／預算。

        這是 ADR-0002 所述「強制實施」的實際落點，供 SPARK 行動派發與
        BotBrain 提交前呼叫。未註冊的行動代碼由風險引擎預設為 HIGH（fail-closed）。

        Args:
            action_type: 行動代碼（如 ``spark_push``）。
            params: 行動參數，供風險引擎動態升級判斷。
            description: 行動說明，僅用於日誌。

        Returns:
            GuardDecision: 判定結果與原因。
        """
        risk_level = self._risk_engine.classify_risk(action_type, dict(params or {}))
        label = description or action_type

        if risk_level is ActionRiskLevel.HIGH:
            reason = f"HIGH 風險行動需人工審批，拒絕自主執行: {label}"
            logger.warning("[AutonomyGuard] %s", reason)
            return GuardDecision(False, action_type, risk_level, self._health_level, reason)

        with self._lock:
            health_level = self._health_level
        if health_level is SystemHealthLevel.INCIDENT and risk_level is not ActionRiskLevel.LOW:
            reason = f"系統處於 INCIDENT，暫停中高風險自主行動: {label}"
            logger.warning("[AutonomyGuard] %s", reason)
            return GuardDecision(False, action_type, risk_level, health_level, reason)

        if risk_level is ActionRiskLevel.MEDIUM and health_level is SystemHealthLevel.WARNING:
            reason = f"系統處於 WARNING，暫停中風險自主行動: {label}"
            logger.warning("[AutonomyGuard] %s", reason)
            return GuardDecision(False, action_type, risk_level, health_level, reason)

        # R19：領域閘門（如 opt-in 同意）。先複製快照，避免持鎖執行 DB I/O。
        with self._lock:
            gates = list(self._gates.items())
        for gate_name, gate in gates:
            try:
                gate_allowed, gate_reason = gate(action_type, dict(params or {}))
            except Exception as exc:
                reason = f"治理閘門 {gate_name} 例外，拒絕行動: {label} ({exc})"
                logger.error("[AutonomyGuard] %s", reason)
                return GuardDecision(False, action_type, risk_level, health_level, reason)
            if not gate_allowed:
                reason = gate_reason or f"治理閘門 {gate_name} 拒絕行動: {label}"
                logger.warning("[AutonomyGuard] %s", reason)
                return GuardDecision(False, action_type, risk_level, health_level, reason)

        allowed, budget_reason = self.check_and_consume_budget(action_type)
        return GuardDecision(allowed, action_type, risk_level, health_level, budget_reason)

    def trigger_kill_switch(self, reason: str = "手動觸發緊急停止") -> None:
        """啟用全域自主性熔斷，立即阻斷所有主動與背景排程行為。

        Args:
            reason: 觸發熔斷的原因描述。
        """
        with self._lock:
            self._kill_switch_active = True
            self._kill_switch_reason = reason
            self._kill_switch_triggered_at = datetime.now(timezone.utc).isoformat()
        logger.critical("[AutonomyGuard] 🚨 全域自主性熔斷 (KILL SWITCH) 已啟動！原因: %s", reason)

    def release_kill_switch(self) -> None:
        """解除全域自主性熔斷，恢復正常自主行為。"""
        with self._lock:
            self._kill_switch_active = False
            self._kill_switch_reason = ""
            self._kill_switch_triggered_at = None
        logger.info("[AutonomyGuard] ✅ 全域自主性熔斷已解除，恢復自主與排程運作。")

    def _cleanup_old_timestamps(self, now: float) -> None:
        """清除超過 24 小時的歷史時間戳。"""
        one_day_ago = now - 86400.0
        while self._action_timestamps and self._action_timestamps[0] < one_day_ago:
            self._action_timestamps.popleft()

    def check_and_consume_budget(self, action_name: str) -> tuple[bool, str]:
        """檢查並消耗自主行動預算。

        Args:
            action_name: 動作名稱。

        Returns:
            tuple[bool, str]: (是否允許執行, 拒絕原因或成功提示)。
        """
        with self._lock:
            if self._kill_switch_active:
                return False, f"全域自主熔斷已啟動 ({self._kill_switch_reason})，阻斷自主行動: {action_name}"

            now = time.time()
            self._cleanup_old_timestamps(now)

            # 檢查每小時預算
            one_hour_ago = now - 3600.0
            actions_last_hour = sum(1 for ts in self._action_timestamps if ts >= one_hour_ago)
            if actions_last_hour >= self.max_actions_per_hour:
                reason = f"已達每小時自主行動上限 ({actions_last_hour}/{self.max_actions_per_hour})"
                logger.warning("[AutonomyGuard] %s，拒絕: %s", reason, action_name)
                return False, reason

            # 檢查每日預算
            actions_last_day = len(self._action_timestamps)
            if actions_last_day >= self.max_actions_per_day:
                reason = f"已達每日自主行動上限 ({actions_last_day}/{self.max_actions_per_day})"
                logger.warning("[AutonomyGuard] %s，拒絕: %s", reason, action_name)
                return False, reason

            # 扣減預算 (記錄時間戳)
            self._action_timestamps.append(now)
            logger.debug("[AutonomyGuard] 自主行動核准: %s (近1hr: %d, 近24hr: %d)", action_name, actions_last_hour + 1, actions_last_day + 1)
            return True, "預算核准"

    def get_budget_status(self) -> dict[str, object]:
        """取得當前自主行動預算與熔斷狀態摘要。"""
        with self._lock:
            now = time.time()
            self._cleanup_old_timestamps(now)
            one_hour_ago = now - 3600.0
            actions_last_hour = sum(1 for ts in self._action_timestamps if ts >= one_hour_ago)
            actions_last_day = len(self._action_timestamps)
            return {
                "kill_switch_active": self._kill_switch_active,
                "kill_switch_reason": self._kill_switch_reason,
                "kill_switch_triggered_at": self._kill_switch_triggered_at,
                "health_level": self._health_level.value,
                "actions_last_hour": actions_last_hour,
                "max_actions_per_hour": self.max_actions_per_hour,
                "actions_last_day": actions_last_day,
                "max_actions_per_day": self.max_actions_per_day,
                "active_gates": list(self._gates.keys()),
            }


# 系統單例實例
global_autonomy_guard = AutonomyGuard()
