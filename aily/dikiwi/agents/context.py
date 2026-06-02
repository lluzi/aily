"""Agent context - pipeline-scoped state passed to every agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aily.gating.drainage import RainDrop
    from aily.graph.db import GraphDB
    from aily.llm.client import LLMClient
    from aily.processing.markdownize import MarkdownizeProcessor
    from aily.sessions.dikiwi_mind import (
        ConversationMemory,
        LLMUsageBudget,
        StageResult,
    )
    from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter


@dataclass
class AgentContext:
    """Pipeline-scoped context passed to every agent execution.

    Carries all transient state for a single pipeline run, plus
    references to long-lived infrastructure.
    """

    pipeline_id: str
    correlation_id: str
    drop: "RainDrop"
    memory: "ConversationMemory | None" = None
    budget: "LLMUsageBudget | None" = None
    stage_results: list["StageResult"] = field(default_factory=list)
    artifact_store: dict[str, Any] = field(default_factory=dict)

    # Infrastructure references (injected by the orchestrator/adapter)
    llm_client: "LLMClient | None" = None
    graph_db: "GraphDB | None" = None
    obsidian_writer: "Any | None" = None
    dikiwi_obsidian_writer: "DikiwiObsidianWriter | None" = None
    markdownizer: "MarkdownizeProcessor | None" = None
