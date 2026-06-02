"""CVO (Chief Vision Officer) gate - Human approval for high-impact decisions.

The CVO gate requires human approval at the WISDOM → IMPACT transition.
If no human response within TTL, auto-approves.

Inspired by Clowder AI's human-in-the-loop design.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)


class ApprovalDecisionType(Enum):
    """Possible CVO decisions."""

    APPROVED = auto()
    AUTO_APPROVED = auto()  # TTL expired
    REJECTED = auto()
    PENDING = auto()


@dataclass
class ApprovalDecision:
    """Decision from CVO gate."""

    decision: ApprovalDecisionType
    reasoning: str = ""
    approved_by: str = ""  # Human identifier or "system"
    approved_at: datetime | None = None


@dataclass
class PendingApproval:
    """A pending approval request."""

    approval_id: str
    content_id: str
    content_preview: str
    wisdom_summary: str
    impact_proposal: dict[str, Any]
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_hours: int = 24

    def is_expired(self) -> bool:
        """Check if TTL has expired."""
        elapsed = datetime.now(timezone.utc) - self.requested_at
        return elapsed > timedelta(hours=self.ttl_hours)


class CVOGate:
    """Chief Vision Officer gate - Human approval for WISDOM → IMPACT.

    The CVO (human) is a co-creator, not a manager:
    - Express vision at WISDOM → IMPACT gate
    - Make decisions on high-impact proposals
    - Shape culture through feedback on quality
    - System runs autonomously if human absent (TTL auto-approve)

    Implementation:
    - Queue for human review with TTL
    - Auto-approve after TTL expires
    - Human can approve/reject/modify
    """

    DEFAULT_TTL_HOURS = 24

    def __init__(self, ttl_hours: int = DEFAULT_TTL_HOURS) -> None:
        self.ttl_hours = ttl_hours
        self._pending: dict[str, PendingApproval] = {}
        self._completed: list[ApprovalDecision] = []
        self._approval_count = 0
        self._auto_approval_count = 0
        self._rejection_count = 0

    async def request_approval(
        self,
        approval_id: str,
        content_id: str,
        content_preview: str,
        wisdom_summary: str,
        impact_proposal: dict[str, Any],
    ) -> PendingApproval:
        """Queue content for CVO approval.

        Args:
            approval_id: Unique approval request ID
            content_id: Content being approved
            content_preview: Short preview for human review
            wisdom_summary: Summary of wisdom derived
            impact_proposal: Proposed impact/action items

        Returns:
            PendingApproval that tracks the request
        """
        pending = PendingApproval(
            approval_id=approval_id,
            content_id=content_id,
            content_preview=content_preview,
            wisdom_summary=wisdom_summary,
            impact_proposal=impact_proposal,
            ttl_hours=self.ttl_hours,
        )

        self._pending[approval_id] = pending

        logger.info(
            "[CVO] Approval requested for %s (TTL: %dh)",
            approval_id[:8],
            self.ttl_hours,
        )

        return pending

    async def await_approval(
        self,
        approval_id: str,
        check_interval_seconds: float = 5.0,
    ) -> ApprovalDecision:
        """Wait for human approval or TTL expiry using asyncio.Event.

        Args:
            approval_id: The approval request ID
            check_interval_seconds: How often to check for decision

        Returns:
            ApprovalDecision (approved, rejected, or auto-approved)
        """
        pending = self._pending.get(approval_id)
        if not pending:
            return ApprovalDecision(
                decision=ApprovalDecisionType.REJECTED,
                reasoning="Approval request not found",
            )

        # Create an event to signal human decision
        decision_event = asyncio.Event()
        decision_result: ApprovalDecision | None = None

        # Store the event so approve/reject can signal it
        pending._decision_event = decision_event  # type: ignore

        # Wait for human decision or TTL
        start_time = datetime.now(timezone.utc)
        timeout_seconds = self.ttl_hours * 3600

        try:
            # Wait for either the decision event or TTL timeout
            await asyncio.wait_for(
                decision_event.wait(),
                timeout=timeout_seconds,
            )

            # Decision was made (via approve/reject)
            decision = getattr(pending, '_decision_result', None)
            if decision:
                self._pending.pop(approval_id, None)
                self._completed.append(decision)

                if decision.decision == ApprovalDecisionType.APPROVED:
                    self._approval_count += 1
                elif decision.decision == ApprovalDecisionType.REJECTED:
                    self._rejection_count += 1

                return decision

        except asyncio.TimeoutError:
            # TTL expired
            logger.info(
                "[CVO] TTL expired for %s, auto-approving",
                approval_id[:8],
            )
            self._auto_approval_count += 1
            self._pending.pop(approval_id, None)

            decision = ApprovalDecision(
                decision=ApprovalDecisionType.AUTO_APPROVED,
                reasoning=f"Auto-approved after TTL ({self.ttl_hours}h)",
                approved_by="system",
                approved_at=datetime.now(timezone.utc),
            )
            self._completed.append(decision)
            return decision

        # Fallback: check for any decision that was recorded
        decision = getattr(pending, '_decision_result', None)
        if decision:
            return decision

        # No decision found
        return ApprovalDecision(
            decision=ApprovalDecisionType.REJECTED,
            reasoning="Approval process completed without decision",
        )

    async def _check_human_decision(
        self,
        approval_id: str,
    ) -> ApprovalDecision | None:
        """Check if human has made a decision.

        In a real implementation, this would check:
        - Database for human response
        - API endpoint
        - Message queue

        For now, returns None (no decision yet).
        """
        # Placeholder - would check external system
        return None

    def approve(
        self,
        approval_id: str,
        approved_by: str = "human",
        reasoning: str = "",
    ) -> ApprovalDecision | None:
        """Record human approval.

        Call this when human approves via UI/API.

        Args:
            approval_id: The approval request
            approved_by: Who approved it
            reasoning: Optional reasoning

        Returns:
            The decision if found, None otherwise
        """
        if approval_id not in self._pending:
            return None

        pending = self._pending[approval_id]

        decision = ApprovalDecision(
            decision=ApprovalDecisionType.APPROVED,
            reasoning=reasoning or "Approved by CVO",
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc),
        )

        # Store decision and signal the waiting task
        pending._decision_result = decision  # type: ignore
        if hasattr(pending, '_decision_event'):
            pending._decision_event.set()

        self._approval_count += 1
        self._completed.append(decision)
        del self._pending[approval_id]

        logger.info("[CVO] %s approved by %s", approval_id[:8], approved_by)
        return decision

    def reject(
        self,
        approval_id: str,
        rejected_by: str = "human",
        reasoning: str = "",
    ) -> ApprovalDecision | None:
        """Record human rejection.

        Args:
            approval_id: The approval request
            rejected_by: Who rejected it
            reasoning: Why it was rejected

        Returns:
            The decision if found, None otherwise
        """
        if approval_id not in self._pending:
            return None

        pending = self._pending[approval_id]

        decision = ApprovalDecision(
            decision=ApprovalDecisionType.REJECTED,
            reasoning=reasoning or "Rejected by CVO",
            approved_by=rejected_by,
            approved_at=datetime.now(timezone.utc),
        )

        # Store decision and signal the waiting task
        pending._decision_result = decision  # type: ignore
        if hasattr(pending, '_decision_event'):
            pending._decision_event.set()

        self._rejection_count += 1
        self._completed.append(decision)
        del self._pending[approval_id]

        logger.info("[CVO] %s rejected by %s: %s", approval_id[:8], rejected_by, reasoning)
        return decision

    def get_pending(self) -> list[PendingApproval]:
        """Get all pending approvals."""
        return list(self._pending.values())

    def get_metrics(self) -> dict[str, Any]:
        """Get CVO gate metrics."""
        total = self._approval_count + self._auto_approval_count + self._rejection_count
        return {
            "approved": self._approval_count,
            "auto_approved": self._auto_approval_count,
            "rejected": self._rejection_count,
            "pending": len(self._pending),
            "total_completed": total,
            "human_engagement_rate": (
                self._approval_count / max(total, 1)
            ),
            "ttl_hours": self.ttl_hours,
        }
