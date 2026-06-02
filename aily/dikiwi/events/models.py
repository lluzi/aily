"""Event dataclasses for DIKIWI event bus.

All events include correlation_id for full lineage tracking.
Events are immutable dataclasses (frozen=True).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from aily.dikiwi.stages import DikiwiStage


class EventType(Enum):
    """Types of events in the DIKIWI system."""

    STAGE_COMPLETED = auto()
    STAGE_REJECTED = auto()
    CONTENT_PROMOTED = auto()
    INSIGHT_DISCOVERED = auto()
    WISDOM_SYNTHESIZED = auto()
    IMPACT_GENERATED = auto()
    GATE_DECISION = auto()
    MEMORIAL_CREATED = auto()


@dataclass(frozen=True)
class Event:
    """Base event class.

    All events are immutable and include:
    - event_id: Unique identifier
    - correlation_id: Links to lineage chain
    - timestamp: When event occurred
    - event_type: For routing
    """

    event_id: str = field(default_factory=lambda: str(uuid4())[:8])
    correlation_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: EventType = field(default=EventType.STAGE_COMPLETED)

    def with_correlation(self, correlation_id: str) -> Event:
        """Create new event with correlation ID."""
        return self.__class__(
            event_id=self.event_id,
            correlation_id=correlation_id,
            timestamp=self.timestamp,
            event_type=self.event_type,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to dictionary."""
        result: dict[str, Any] = {
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.name,
        }

        # Add subclass fields
        for key, value in self.__dict__.items():
            if key not in result:
                if isinstance(value, Enum):
                    result[key] = value.name
                elif isinstance(value, datetime):
                    result[key] = value.isoformat()
                else:
                    result[key] = value

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        """Deserialize event from dictionary."""
        # Base implementation - subclasses should override for specific types
        return cls(
            event_id=data.get("event_id", str(uuid4())[:8]),
            correlation_id=data.get("correlation_id", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.now(timezone.utc),
            event_type=EventType[data.get("event_type", "STAGE_COMPLETED")],
        )


@dataclass(frozen=True)
class StageCompletedEvent(Event):
    """Emitted when a stage successfully completes.

    Triggers next stage or gate review.
    """

    stage: DikiwiStage | None = None
    input_content_ids: list[str] = field(default_factory=list)
    output_content_ids: list[str] = field(default_factory=list)
    processing_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "event_type", EventType.STAGE_COMPLETED)


@dataclass(frozen=True)
class StageRejectedEvent(Event):
    """Emitted when content fails a quality gate.

    Causes content to loop back to previous stage.
    This is the 封驳 (rejection) mechanism from 门下省.
    """

    stage: DikiwiStage | None = None
    content_ids: list[str] = field(default_factory=list)
    rejected_by: str = ""  # Agent ID that rejected
    reason: str = ""  # Why it was rejected
    send_back_to: DikiwiStage | None = None  # Where to send it
    rejection_count: int = 0  # How many times rejected

    def __post_init__(self):
        object.__setattr__(self, "event_type", EventType.STAGE_REJECTED)


@dataclass(frozen=True)
class ContentPromotedEvent(Event):
    """Emitted when content is promoted to next stage.

    Memorial is created for this promotion.
    """

    from_stage: DikiwiStage | None = None
    to_stage: DikiwiStage | None = None
    content_ids: list[str] = field(default_factory=list)
    gate_decision: str = "approved"  # approved, auto_approved, modified
    decision_reason: str = ""

    def __post_init__(self):
        object.__setattr__(self, "event_type", EventType.CONTENT_PROMOTED)


@dataclass(frozen=True)
class InsightDiscoveredEvent(Event):
    """Emitted when an insight is detected in the knowledge network."""

    insight_id: str = ""
    insight_type: str = ""  # pattern, contradiction, gap, opportunity
    description: str = ""
    related_content_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def __post_init__(self):
        object.__setattr__(self, "event_type", EventType.INSIGHT_DISCOVERED)


@dataclass(frozen=True)
class WisdomSynthesizedEvent(Event):
    """Emitted when wisdom is synthesized from insights."""

    wisdom_id: str = ""
    principle: str = ""
    context: str = ""
    source_insight_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        object.__setattr__(self, "event_type", EventType.WISDOM_SYNTHESIZED)


@dataclass(frozen=True)
class ImpactGeneratedEvent(Event):
    """Emitted when actionable impact is generated from wisdom."""

    impact_id: str = ""
    impact_type: str = ""  # innovation, opportunity, action, research
    description: str = ""
    priority: str = "medium"  # high, medium, low
    source_wisdom_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        object.__setattr__(self, "event_type", EventType.IMPACT_GENERATED)


@dataclass(frozen=True)
class GateDecisionEvent(Event):
    """Emitted when a gate makes a decision.

    Used for both 门下省 and CVO gates.
    """

    gate_name: str = ""  # "menxia", "cvo"
    decision: str = ""  # approve, reject, modify, pending
    content_ids: list[str] = field(default_factory=list)
    reasoning: str = ""
    requires_human: bool = False

    def __post_init__(self):
        object.__setattr__(self, "event_type", EventType.GATE_DECISION)


@dataclass(frozen=True)
class MemorialCreatedEvent(Event):
    """Emitted when a memorial (audit record) is created."""

    memorial_id: str = ""
    pipeline_id: str = ""
    stage: DikiwiStage | None = None
    gate_decision: str = ""

    def __post_init__(self):
        object.__setattr__(self, "event_type", EventType.MEMORIAL_CREATED)