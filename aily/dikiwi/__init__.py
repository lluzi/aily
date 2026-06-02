"""DIKIWI - Event-driven knowledge pipeline with institutional review.

Active runtime:
- Event-driven stage coordination
- Institutional review gates (门下省 Menxia, CVO)
- Stage agents used by `DikiwiMind`

Usage:
    from aily.dikiwi import DikiwiOrchestrator, PipelineConfig

    orchestrator = DikiwiOrchestrator(llm_client, graph_db)
    pipeline = await orchestrator.start_pipeline(content_id, source)
"""

from aily.dikiwi.events import (
    ContentPromotedEvent,
    Event,
    EventBus,
    GateDecisionEvent,
    ImpactGeneratedEvent,
    InMemoryEventBus,
    InsightDiscoveredEvent,
    MemorialCreatedEvent,
    StageCompletedEvent,
    StageRejectedEvent,
    WisdomSynthesizedEvent,
)
from aily.dikiwi.gates import (
    ApprovalDecision,
    ApprovalDecisionType,
    CVOGate,
    MenxiaGate,
    PendingApproval,
    ReviewDecision,
    ReviewDecisionType,
)
from aily.dikiwi.orchestrator import (
    DikiwiOrchestrator,
    PipelineConfig,
    ProcessingPipeline,
)
from aily.dikiwi.incremental_orchestrator import (
    IncrementalOrchestrator,
    IncrementalResult,
)
from aily.dikiwi.stages import (
    DikiwiStage,
    StageContext,
    StageState,
    StageStateMachine,
    can_transition,
)

# Agents (v2 agent system)
from aily.dikiwi.agents import (
    DikiwiAgent,
    AgentContext,
    ProducerAgent,
    ReviewerAgent,
    DataAgent,
    InformationAgent,
    KnowledgeAgent,
    InsightAgent,
    WisdomAgent,
    ImpactAgent,
    ResidualAgent,
    ObsidianCLI,
)

__all__ = [
    # Core orchestrator
    "DikiwiOrchestrator",
    "PipelineConfig",
    "ProcessingPipeline",
    # Incremental orchestrator
    "IncrementalOrchestrator",
    "IncrementalResult",
    # Stages
    "DikiwiStage",
    "StageContext",
    "StageState",
    "StageStateMachine",
    "can_transition",
    # Events
    "Event",
    "EventBus",
    "InMemoryEventBus",
    "StageCompletedEvent",
    "StageRejectedEvent",
    "ContentPromotedEvent",
    "InsightDiscoveredEvent",
    "WisdomSynthesizedEvent",
    "ImpactGeneratedEvent",
    "MemorialCreatedEvent",
    "GateDecisionEvent",
    # Gates
    "MenxiaGate",
    "ReviewDecision",
    "ReviewDecisionType",
    "CVOGate",
    "ApprovalDecision",
    "ApprovalDecisionType",
    "PendingApproval",
    # Agents
    "DikiwiAgent",
    "AgentContext",
    "ProducerAgent",
    "ReviewerAgent",
    "DataAgent",
    "InformationAgent",
    "KnowledgeAgent",
    "InsightAgent",
    "WisdomAgent",
    "ImpactAgent",
    "ResidualAgent",
    "ObsidianCLI",
]

__version__ = "2.0.0"
