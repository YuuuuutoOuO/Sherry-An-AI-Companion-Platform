"""
core/risk_engine.py
===================
雪莉受控自主性行動三級風險策略引擎 (Risk Engine & Action Policy)。

將 AI 自主性行動分類為：
- LOW: 低風險 (唯讀、自動清理過期暫存、指標採集)，允許完全自主執行。
- MEDIUM: 中風險 (主動社交插話、SPARK 條件推播、快取刷新)，需依據預算與冷卻受控執行。
- HIGH: 高風險 (刪除記憶/資料庫、修改系統設定、全域廣播)，強制需要管理員審批 (Human Approval)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class ActionRiskLevel(str, Enum):
    """行動風險分級枚舉。"""

    LOW = "LOW"  # 低風險：完全自主
    MEDIUM = "MEDIUM"  # 中風險：受控自主 (需預算/冷卻限制)
    HIGH = "HIGH"  # 高風險：強制人工審查確認


class ApprovalStatus(str, Enum):
    """審批流程狀態枚舉。"""

    PENDING = "PENDING"  # 等待審批
    APPROVED = "APPROVED"  # 已核准
    REJECTED = "REJECTED"  # 已拒絕
    EXECUTED = "EXECUTED"  # 已執行
    EXPIRED = "EXPIRED"  # 已過期逾時


@dataclass
class ActionRequest:
    """自主行動請求實體。"""

    action_id: str
    action_type: str
    description: str
    risk_level: ActionRiskLevel
    params: dict[str, Any] = field(default_factory=dict)
    requester: str = "SherryAutonomyWorker"
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    approver_id: int | None = None
    approval_comment: str = ""


class RiskPolicyEngine:
    """三級風險策略引擎。"""

    # 預設動作風險映射表
    DEFAULT_RISK_MAPPING: dict[str, ActionRiskLevel] = {
        # 低風險：唯讀、診斷、暫存清理
        "sys_inspect": ActionRiskLevel.LOW,
        "cache_cleanup": ActionRiskLevel.LOW,
        "metric_collect": ActionRiskLevel.LOW,
        "vibe_infer": ActionRiskLevel.LOW,
        # 中風險：主動插話、排程提醒、SPARK 股票/RSS 觸發、背景推論任務
        "interject_speak": ActionRiskLevel.MEDIUM,
        "spark_push": ActionRiskLevel.MEDIUM,
        "proactive_care": ActionRiskLevel.MEDIUM,
        "proactive_vision_reaction": ActionRiskLevel.MEDIUM,
        "bot_brain_autonomous": ActionRiskLevel.MEDIUM,
        "schedule_notify": ActionRiskLevel.MEDIUM,
        "world_memory_archive": ActionRiskLevel.MEDIUM,
        # 高風險：刪除記憶、修改全域設定、資料庫清空、重啟服務
        "clear_memory": ActionRiskLevel.HIGH,
        "update_global_config": ActionRiskLevel.HIGH,
        "db_drop_table": ActionRiskLevel.HIGH,
        "system_restart": ActionRiskLevel.HIGH,
        "ban_user": ActionRiskLevel.HIGH,
    }

    def __init__(self, custom_mapping: dict[str, ActionRiskLevel] | None = None) -> None:
        """初始化風險策略引擎。"""
        self._mapping = dict(self.DEFAULT_RISK_MAPPING)
        if custom_mapping:
            self._mapping.update(custom_mapping)
        self._pending_requests: dict[str, ActionRequest] = {}

    def classify_risk(self, action_type: str, params: dict[str, Any] | None = None) -> ActionRiskLevel:
        """根據動作類型與參數判定風險等級。

        Args:
            action_type: 動作識別代碼。
            params: 動作攜帶參數。

        Returns:
            ActionRiskLevel: 判定之風險等級 (未定義預設為 HIGH 以策安全)。
        """
        level = self._mapping.get(action_type, ActionRiskLevel.HIGH)

        # 動態提升：若清理動作包含強制抹除所有資料，升級為 HIGH
        if action_type == "cache_cleanup" and params:
            purge_val = params.get("purge_all", False)
            is_purge = purge_val is True or str(purge_val).strip().lower() in ("true", "1", "yes")
            if is_purge:
                return ActionRiskLevel.HIGH

        return level

    def create_request(
        self,
        action_type: str,
        description: str,
        params: dict[str, Any] | None = None,
        requester: str = "SherryAutonomyWorker",
    ) -> ActionRequest:
        """建立新的自主行動請求。

        Args:
            action_type: 動作代碼。
            description: 動作說明。
            params: 執行參數。
            requester: 請求來源。

        Returns:
            ActionRequest 物件。
        """
        req_params = params or {}
        risk_level = self.classify_risk(action_type, req_params)
        action_id = f"act-{uuid.uuid4().hex[:8]}"

        req = ActionRequest(
            action_id=action_id,
            action_type=action_type,
            description=description,
            risk_level=risk_level,
            params=req_params,
            requester=requester,
            status=ApprovalStatus.APPROVED if risk_level == ActionRiskLevel.LOW else ApprovalStatus.PENDING,
        )

        if risk_level != ActionRiskLevel.LOW:
            self._pending_requests[action_id] = req

        return req

    def can_auto_execute(self, req: ActionRequest) -> bool:
        """判斷該請求是否符合直接自主執行條件 (無須審查)。

        Args:
            req: 行動請求。

        Returns:
            bool: 是否允許直接執行。
        """
        return req.risk_level == ActionRiskLevel.LOW and req.status == ApprovalStatus.APPROVED

    def approve_action(self, action_id: str, approver_id: int, comment: str = "") -> bool:
        """人工審批核准高風險請求。

        Args:
            action_id: 請求 ID。
            approver_id: 管理員 ID。
            comment: 審批附註。

        Returns:
            bool: 審批是否成功。
        """
        req = self._pending_requests.get(action_id)
        if not req:
            logger.warning("[RiskEngine] 找不到待審批請求: %s", action_id)
            return False

        if req.status != ApprovalStatus.PENDING:
            logger.warning("[RiskEngine] 請求 %s 當前狀態為 %s，無法重復審批", action_id, req.status)
            return False

        req.status = ApprovalStatus.APPROVED
        req.approver_id = approver_id
        req.approval_comment = comment
        logger.info("[RiskEngine] 請求 %s 已由管理員 %d 核准: %s", action_id, approver_id, comment)
        return True

    def reject_action(self, action_id: str, approver_id: int, reason: str = "") -> bool:
        """人工駁回高風險請求。

        Args:
            action_id: 請求 ID。
            approver_id: 管理員 ID。
            reason: 駁回理由。

        Returns:
            bool: 駁回是否成功。
        """
        req = self._pending_requests.get(action_id)
        if not req:
            return False

        if req.status != ApprovalStatus.PENDING:
            logger.warning("[RiskEngine] 請求 %s 當前狀態為 %s，無法重複駁回或逆轉", action_id, req.status)
            return False

        req.status = ApprovalStatus.REJECTED
        req.approver_id = approver_id
        req.approval_comment = reason
        logger.info("[RiskEngine] 請求 %s 已由管理員 %d 駁回: %s", action_id, approver_id, reason)
        return True

    def get_pending_requests(self) -> list[ActionRequest]:
        """取得所有等待審批中的請求。"""
        return [r for r in self._pending_requests.values() if r.status == ApprovalStatus.PENDING]
