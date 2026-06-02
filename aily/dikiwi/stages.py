"""DIKIWI stage definitions and state machine.

Implements the hard rails: stage transitions are strictly controlled.
Permission matrix enforced at code level.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from aily.dikiwi.events.models import Event

logger = logging.getLogger(__name__)


class DikiwiStage(Enum):
    """Stages of the DIKIWI hierarchy.

    Mapped to 三省六部 governance structure:
    - DATA → INFORMATION: 中书省 (Zhongshu) - Planning/Classification
    - INFORMATION → KNOWLEDGE: 门下省 (Menxia) - Review/Veto
    - KNOWLEDGE → INSIGHT: 尚书省 (Shangshu) - Dispatch/Execution
    - INSIGHT → WISDOM: 吏部 (Libu) - Quality/Grading
    - WISDOM → IMPACT: 工部 (Gongbu) - Execution/Output
    """

    DATA = auto()         # Raw → Data points
    INFORMATION = auto()  # Data points → Tagged, classified (中书省)
    KNOWLEDGE = auto()    # Information → Linked network (门下省 gate)
    INSIGHT = auto()      # Network → Pattern recognition (尚书省)
    WISDOM = auto()       # Insights → Applied understanding (吏部/CVO gate)
    IMPACT = auto()       # Wisdom → Actionable outcomes (工部)
    RESIDUAL = auto()     # Post-pipeline vault analysis and proposal drafting (残差)

    def __str__(self) -> str:
        return self.name

    @property
    def chinese_name(self) -> str:
        """Get the Chinese governance name."""
        names = {
            DikiwiStage.DATA: "原始",
            DikiwiStage.INFORMATION: "中书省",
            DikiwiStage.KNOWLEDGE: "门下省",
            DikiwiStage.INSIGHT: "尚书省",
            DikiwiStage.WISDOM: "吏部",
            DikiwiStage.IMPACT: "工部",
            DikiwiStage.RESIDUAL: "残差",
        }
        return names.get(self, "未知")

    @property
    def has_veto_power(self) -> bool:
        """Check if this stage has institutional review (封驳) power."""
        return self in (DikiwiStage.KNOWLEDGE, DikiwiStage.WISDOM)


class StageState(Enum):
    """Processing state within a stage."""

    PENDING = auto()      # Waiting to start
    PROCESSING = auto()   # Currently being processed
    AWAITING_REVIEW = auto()  # Waiting for gate review (门下省/吏部)
    REJECTED = auto()     # Failed review, send back
    COMPLETED = auto()    # Successfully completed
    FAILED = auto()       # Processing failed


class StageTransition(Enum):
    """Valid transitions between stages (permission matrix).

    Enforced by the state machine. Invalid transitions raise errors.
    """

    # Normal forward flow
    DATA_TO_INFORMATION = (DikiwiStage.DATA, DikiwiStage.INFORMATION)
    INFORMATION_TO_KNOWLEDGE = (DikiwiStage.INFORMATION, DikiwiStage.KNOWLEDGE)
    KNOWLEDGE_TO_INSIGHT = (DikiwiStage.KNOWLEDGE, DikiwiStage.INSIGHT)
    INSIGHT_TO_WISDOM = (DikiwiStage.INSIGHT, DikiwiStage.WISDOM)
    WISDOM_TO_IMPACT = (DikiwiStage.WISDOM, DikiwiStage.IMPACT)
    IMPACT_TO_RESIDUAL = (DikiwiStage.IMPACT, DikiwiStage.RESIDUAL)

    # Review rejection loops (封驳)
    KNOWLEDGE_REJECT_TO_INFORMATION = (DikiwiStage.KNOWLEDGE, DikiwiStage.INFORMATION)
    WISDOM_REJECT_TO_INSIGHT = (DikiwiStage.WISDOM, DikiwiStage.INSIGHT)

    # Retry same stage
    RETRY_INFORMATION = (DikiwiStage.INFORMATION, DikiwiStage.INFORMATION)
    RETRY_KNOWLEDGE = (DikiwiStage.KNOWLEDGE, DikiwiStage.KNOWLEDGE)

    def __init__(self, from_stage: DikiwiStage, to_stage: DikiwiStage) -> None:
        self.from_stage = from_stage
        self.to_stage = to_stage

    @classmethod
    def is_valid(cls, from_stage: DikiwiStage, to_stage: DikiwiStage) -> bool:
        """Check if a transition is valid."""
        for transition in cls:
            if transition.from_stage == from_stage and transition.to_stage == to_stage:
                return True
        return False

    @classmethod
    def get_next_stages(cls, from_stage: DikiwiStage) -> list[DikiwiStage]:
        """Get all valid next stages from a given stage."""
        return [t.to_stage for t in cls if t.from_stage == from_stage]


# Permission matrix as explicit mapping
# from_stage: [allowed_to_stages]
PERMISSION_MATRIX: dict[DikiwiStage, list[DikiwiStage]] = {
    DikiwiStage.DATA: [DikiwiStage.INFORMATION],
    DikiwiStage.INFORMATION: [DikiwiStage.KNOWLEDGE],
    DikiwiStage.KNOWLEDGE: [DikiwiStage.INSIGHT, DikiwiStage.INFORMATION],  # Can reject back
    DikiwiStage.INSIGHT: [DikiwiStage.WISDOM],
    DikiwiStage.WISDOM: [DikiwiStage.IMPACT, DikiwiStage.INSIGHT],  # CVO can reject back
    DikiwiStage.IMPACT: [DikiwiStage.RESIDUAL],  # Can proceed to Residual analysis
    DikiwiStage.RESIDUAL: [],  # Terminal stage
}


def can_transition(from_stage: DikiwiStage, to_stage: DikiwiStage) -> bool:
    """Check if a stage transition is allowed.

    This is the hard rail enforcement.
    """
    allowed = PERMISSION_MATRIX.get(from_stage, [])
    return to_stage in allowed


@dataclass
class StageContext:
    """Context for a content item moving through DIKIWI stages.

    Tracks the full lineage and state of a content item.
    """

    context_id: str = field(default_factory=lambda: str(uuid4())[:12])
    correlation_id: str = ""
    content_id: str = ""
    source: str = ""

    # Current state
    current_stage: DikiwiStage = DikiwiStage.DATA
    stage_state: StageState = StageState.PENDING

    # History
    stage_history: list[dict[str, Any]] = field(default_factory=list)
    rejection_count: dict[DikiwiStage, int] = field(default_factory=dict)

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize context to dictionary."""
        return {
            "context_id": self.context_id,
            "correlation_id": self.correlation_id,
            "content_id": self.content_id,
            "source": self.source,
            "current_stage": self.current_stage.name,
            "stage_state": self.stage_state.name,
            "stage_history": self.stage_history,
            "rejection_count": {k.name: v for k, v in self.rejection_count.items()},
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageContext:
        """Deserialize context from dictionary."""
        from datetime import datetime as dt

        context = cls(
            context_id=data["context_id"],
            correlation_id=data["correlation_id"],
            content_id=data["content_id"],
            source=data.get("source", ""),
            current_stage=DikiwiStage[data["current_stage"]],
            stage_state=StageState[data["stage_state"]],
            stage_history=data.get("stage_history", []),
            rejection_count={DikiwiStage[k]: v for k, v in data.get("rejection_count", {}).items()},
        )
        if "created_at" in data:
            context.created_at = dt.fromisoformat(data["created_at"])
        if "updated_at" in data:
            context.updated_at = dt.fromisoformat(data["updated_at"])
        return context

    def record_stage_entry(self, stage: DikiwiStage) -> None:
        """Record entering a stage."""
        self.current_stage = stage
        self.stage_state = StageState.PROCESSING
        self.updated_at = datetime.now(timezone.utc)
        self.stage_history.append({
            "stage": stage.name,
            "state": "entered",
            "timestamp": self.updated_at.isoformat(),
        })

    def record_stage_completion(self, stage: DikiwiStage) -> None:
        """Record successful stage completion."""
        self.stage_state = StageState.COMPLETED
        self.updated_at = datetime.now(timezone.utc)
        self.stage_history.append({
            "stage": stage.name,
            "state": "completed",
            "timestamp": self.updated_at.isoformat(),
        })

    def record_rejection(self, stage: DikiwiStage, reason: str) -> None:
        """Record stage rejection (封驳)."""
        self.stage_state = StageState.REJECTED
        self.rejection_count[stage] = self.rejection_count.get(stage, 0) + 1
        self.updated_at = datetime.now(timezone.utc)
        self.stage_history.append({
            "stage": stage.name,
            "state": "rejected",
            "reason": reason,
            "timestamp": self.updated_at.isoformat(),
        })

    def can_retry(self, stage: DikiwiStage, max_retries: int = 3) -> bool:
        """Check if content can be retried at a stage."""
        return self.rejection_count.get(stage, 0) < max_retries


class StageStateMachine:
    """State machine for enforcing valid stage transitions.

    This is the hard rail. Invalid transitions are rejected.
    """

    def __init__(self, context: StageContext | None = None, max_rejections: int = 3) -> None:
        self.context = context
        self.max_rejections = max_rejections
        self._contexts: dict[str, StageContext] = {}
        self._history: list[dict[str, Any]] = []
        if context:
            self._contexts[context.context_id] = context

    @property
    def current_stage(self) -> DikiwiStage | None:
        """Get current stage from context."""
        return self.context.current_stage if self.context else None

    def can_transition_to(self, to_stage: DikiwiStage) -> bool:
        """Check if transition to stage is allowed."""
        if not self.context:
            return False
        return can_transition(self.context.current_stage, to_stage)

    def transition_to(self, to_stage: DikiwiStage, reason: str = "") -> None:
        """Transition to a new stage, raising on invalid transition."""
        if not self.context:
            raise ValueError("No context set")

        from_stage = self.context.current_stage
        success, message = self.transition(self.context, to_stage)
        if not success:
            raise ValueError(message)

        # Record in history
        self._history.append({
            "from": from_stage.name,
            "to": to_stage.name,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def is_max_rejections_reached(self) -> bool:
        """Check if max rejections reached for current stage."""
        if not self.context:
            return False
        return not self.context.can_retry(self.context.current_stage, self.max_rejections)

    def get_history(self) -> list[dict[str, Any]]:
        """Get transition history."""
        return list(self._history)

    def create_context(self, content_id: str, source: str) -> StageContext:
        """Create a new stage context for content."""
        context = StageContext(
            correlation_id=str(uuid4())[:16],
            content_id=content_id,
            source=source,
        )
        self._contexts[context.context_id] = context
        return context

    def get_context(self, context_id: str) -> StageContext | None:
        """Get context by ID."""
        return self._contexts.get(context_id)

    def transition(
        self,
        context: StageContext,
        to_stage: DikiwiStage,
    ) -> tuple[bool, str]:
        """Attempt to transition to a new stage.

        Returns:
            (success, message)
        """
        from_stage = context.current_stage

        # Check permission matrix
        if not can_transition(from_stage, to_stage):
            return False, f"Invalid transition: {from_stage} -> {to_stage}"

        # Check if this is a rejection loop
        if to_stage.value < from_stage.value:  # Going backwards
            if not context.can_retry(from_stage, self.max_rejections):
                return False, f"Max rejections ({self.max_rejections}) reached at {from_stage}"
            context.record_rejection(from_stage, reason="封驳 - stage rejection")

        # Record the transition
        context.record_stage_entry(to_stage)

        logger.info(
            "Stage transition: %s -> %s (context: %s)",
            from_stage.name,
            to_stage.name,
            context.context_id,
        )

        return True, f"Transitioned to {to_stage.name}"

    def complete_stage(self, context: StageContext) -> None:
        """Mark current stage as completed."""
        context.record_stage_completion(context.current_stage)

    def reject_stage(self, context: StageContext, reason: str) -> None:
        """Mark current stage as rejected (封驳)."""
        context.record_rejection(context.current_stage, reason)

        # Determine where to send back
        rejection_map = {
            DikiwiStage.KNOWLEDGE: DikiwiStage.INFORMATION,
            DikiwiStage.WISDOM: DikiwiStage.INSIGHT,
        }
        send_back_to = rejection_map.get(context.current_stage)

        if send_back_to:
            logger.info(
                "Stage %s rejected (%s), sending back to %s",
                context.current_stage.name,
                reason,
                send_back_to.name,
            )
