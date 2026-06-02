"""Event-driven architecture for DIKIWI.

Events enable async, decoupled communication between stages.
All events include correlation_id for lineage tracking.
"""

from aily.dikiwi.events.models import (
    Event,
    StageCompletedEvent,
    StageRejectedEvent,
    ContentPromotedEvent,
    InsightDiscoveredEvent,
    WisdomSynthesizedEvent,
    ImpactGeneratedEvent,
    MemorialCreatedEvent,
    GateDecisionEvent,
)
from aily.dikiwi.events.bus import (
    EventBus,
    InMemoryEventBus,
    RedisStreamsEventBus,
    create_event_bus,
)

__all__ = [
    "Event",
    "StageCompletedEvent",
    "StageRejectedEvent",
    "ContentPromotedEvent",
    "InsightDiscoveredEvent",
    "WisdomSynthesizedEvent",
    "ImpactGeneratedEvent",
    "MemorialCreatedEvent",
    "GateDecisionEvent",
    "EventBus",
    "InMemoryEventBus",
    "RedisStreamsEventBus",
    "create_event_bus",
]
