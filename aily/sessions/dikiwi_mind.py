"""DIKIWI Mind - LLM-Powered Knowledge filtration and refinement pipeline.

ARCHITECTURE: LLM-First Design
All stages use LLM reasoning by default
No hardcoded thresholds or heuristics
No keyword fallbacks
Pure semantic understanding through all stages

"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aily.browser.manager import BrowserUseManager
    from aily.graph.db import GraphDB
    from aily.gating.drainage import RainDrop

from aily.llm.llm_router import LLMRouter, LLMConfig
from aily.llm.provider_routes import PrimaryLLMRoute
from aily.llm.prompt_registry import DikiwiPromptRegistry
from aily.processing.markdownize import MarkdownizeProcessor
from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter
from aily.ui.events import emit_ui_event
from aily.ui.telemetry import ui_telemetry_scope
from aily.config import SETTINGS

logger = logging.getLogger(__name__)


class DikiwiStage(Enum):
    """Stages of the DIKIWI hierarchy."""

    DATA = auto()
    INFORMATION = auto()
    KNOWLEDGE = auto()
    INSIGHT = auto()
    WISDOM = auto()
    IMPACT = auto()
    RESIDUAL = auto()


@dataclass
class ConversationMemory:
    """Multi-turn conversation memory for DIKIWI pipeline.

    Kimi API is stateless - we must manually maintain conversation history
    to preserve context across pipeline stages.
    """

    messages: list[dict[str, str]] = field(default_factory=list)
    max_messages: int = 20  # Keep last N messages to stay within token limits

    def add_system(self, content: str) -> None:
        """Add system message."""
        self.messages.append({"role": "system", "content": content})

    def add_user(self, content: str) -> None:
        """Add user message."""
        self.messages.append({"role": "user", "content": content})
        self._truncate_if_needed()

    def add_assistant(self, content: str) -> None:
        """Add assistant response."""
        self.messages.append({"role": "assistant", "content": content})
        self._truncate_if_needed()

    def _truncate_if_needed(self) -> None:
        """Truncate oldest non-system messages to stay within limits."""
        if len(self.messages) > self.max_messages:
            # Keep system messages, truncate oldest user/assistant
            system_msgs = [m for m in self.messages if m["role"] == "system"]
            other_msgs = [m for m in self.messages if m["role"] != "system"]
            # Keep last (max_messages - len(system_msgs)) messages
            keep_count = self.max_messages - len(system_msgs)
            self.messages = system_msgs + other_msgs[-keep_count:]

    def get_messages(self) -> list[dict[str, str]]:
        """Get current conversation history."""
        return self.messages.copy()

    def to_prompt_context(self) -> str:
        """Convert conversation to context string for prompts."""
        context_lines = []
        for msg in self.messages:
            if msg["role"] == "system":
                continue  # Skip system messages in context
            role_label = "User" if msg["role"] == "user" else "Assistant"
            context_lines.append(f"{role_label}: {msg['content'][:500]}")
        return "\n\n".join(context_lines)


@dataclass
class DataPoint:
    """A single extracted fact/claim from raw content."""

    id: str
    content: str
    source: str
    context: str = ""
    confidence: float = 1.0
    concept: str = ""  # Short name for this concept (3-8 words)
    modality: str = "text"
    source_page: int | None = None
    visual_type: str = ""
    asset_embeds: list[str] = field(default_factory=list)
    source_evidence: list[str] = field(default_factory=list)


@dataclass
class InformationNode:
    """A classified and tagged data point."""

    id: str
    data_point_id: str
    content: str
    tags: list[str] = field(default_factory=list)
    info_type: str = ""
    domain: str = ""
    concept: str = ""  # Concept name inherited from data extraction
    data_point_ids: list[str] = field(default_factory=list)
    source_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.8


@dataclass
class KnowledgeLink:
    """A link between information nodes."""

    source_id: str
    target_id: str
    relation_type: str
    strength: float = 0.5
    reasoning: str = ""


@dataclass
class Insight:
    """Pattern recognized from knowledge network."""

    id: str
    insight_type: str
    description: str
    related_nodes: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class Wisdom:
    """Synthesized understanding from insights."""

    id: str
    principle: str
    context: str
    implications: list[str] = field(default_factory=list)


@dataclass
class ZettelkastenNote:
    """A proper Zettelkasten permanent note.

    Not just a fragment - a complete atomic note with:
    - One clear idea (200-500 words)
    - Written in complete thoughts
    - Examples and context
    - Links to related concepts
    - Timeless and reusable
    """

    id: str
    title: str  # Full sentence as title
    content: str  # Complete markdown content (200-500 words)
    tags: list[str] = field(default_factory=list)
    links_to: list[str] = field(default_factory=list)  # Conceptual links
    source_insights: list[str] = field(default_factory=list)  # Source insight IDs
    confidence: float = 0.0

    def to_markdown(self) -> str:
        """Convert to full Zettelkasten markdown format."""
        lines = [
            f"# {self.title}",
            "",
            self.content,
            "",
            "## Tags",
            " ".join(f"#{tag}" for tag in self.tags),
            "",
        ]

        if self.links_to:
            lines.extend([
                "## Related",
                "",
            ])
            for link in self.links_to:
                lines.append(f"- [[{link}]]")
            lines.append("")

        lines.append(f"---\n*Confidence: {self.confidence:.0%}*")

        return "\n".join(lines)


@dataclass
class StageResult:
    """Result of processing a single DIKIWI stage."""

    stage: DikiwiStage
    success: bool
    items_processed: int = 0
    items_output: int = 0
    processing_time_ms: float = 0.0
    error_message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMUsageBudget:
    """Per-source LLM usage budget for DIKIWI."""

    max_calls: int
    stage_round_limit: int
    calls_used: int = 0
    stage_calls: dict[str, int] = field(default_factory=dict)

    def reserve(self, stage: str) -> None:
        if self.calls_used >= self.max_calls:
            raise RuntimeError(
                f"DIKIWI LLM budget exceeded for source job: {self.calls_used}/{self.max_calls} calls used"
            )
        stage_used = self.stage_calls.get(stage, 0)
        if stage_used >= self.stage_round_limit:
            raise RuntimeError(
                f"DIKIWI stage round limit exceeded for {stage}: {stage_used}/{self.stage_round_limit}"
            )
        self.calls_used += 1
        self.stage_calls[stage] = stage_used + 1


@dataclass
class DikiwiResult:
    """Complete result of DIKIWI pipeline processing."""

    input_id: str
    pipeline_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    stage_results: list[StageResult] = field(default_factory=list)

    @property
    def total_time_ms(self) -> float:
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds() * 1000

    @property
    def final_stage_reached(self) -> DikiwiStage | None:
        for stage in reversed(DikiwiStage):
            for result in self.stage_results:
                if result.stage == stage and result.success:
                    return stage
        return None


@dataclass
class DikiwiBatchRun:
    """Batch DIKIWI execution metadata for stage-latched runs."""

    results: list[DikiwiResult] = field(default_factory=list)
    pre_information_nodes: int = 0
    post_information_nodes: int = 0
    incremental_ratio: float = 0.0
    incremental_threshold: float = 0.05
    foundation_only: bool = False
    higher_order_triggered: bool = False
    higher_order_candidates: int = 0
    higher_order_selected: int = 0


class DikiwiMind:
    """DIKIWI filtration pipeline using pure LLM-based processing.

    Unlike the rule-based version, this uses Kimi LLM for ALL stages:
    - No hardcoded thresholds
    - No keyword fallbacks
    - No heuristics
    - Pure semantic understanding
    """

    _MAC_ITERATIONS = 2

    def __init__(
        self,
        graph_db: GraphDB,
        kimi_api_key: str = "",
        enabled: bool = True,
        obsidian_writer: Any | None = None,
        browser_manager: "BrowserUseManager | None" = None,
        model: str = "kimi-k2.5",
        dikiwi_obsidian_writer: DikiwiObsidianWriter | None = None,
        llm_client: Any | None = None,
        llm_client_resolver: Any | None = None,
        queue_db: Any | None = None,
    ) -> None:
        """Initialize LLM-powered DIKIWI mind.

        Args:
            graph_db: Graph database for knowledge storage
            kimi_api_key: Kimi API key (for Standard API)
            enabled: Whether DIKIWI processing is enabled
            obsidian_writer: Optional writer for Obsidian notes (REST API)
            browser_manager: Optional browser manager for URL fetching
            model: Kimi model to use (recommended: kimi-k2.5) - Standard API only
            dikiwi_obsidian_writer: Optional enhanced Obsidian writer (file-based with Dataview)
            llm_client: Pre-configured LLM client (e.g., Coding Plan with kimi-k2.5)
        """
        # Use pre-configured client if provided, otherwise create Standard API client
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            self.llm_client = LLMRouter.standard_kimi(api_key=kimi_api_key, model=model)
        self.llm_client_resolver = llm_client_resolver

        self.graph_db = graph_db
        self.enabled = enabled
        self.obsidian_writer = obsidian_writer
        self.dikiwi_obsidian_writer = dikiwi_obsidian_writer
        self.browser_manager = browser_manager
        self.queue_db = queue_db

        self._markdownizer = MarkdownizeProcessor(browser_manager=browser_manager)

        self._total_inputs = 0
        self._successful_pipelines = 0
        self._failed_pipelines = 0
        self._conversation_memories: dict[str, ConversationMemory] = {}
        self._llm_budgets: dict[str, LLMUsageBudget] = {}

    def _client_for_workload(self, workload: str) -> Any:
        if self.llm_client_resolver is None:
            return self.llm_client
        client = self.llm_client_resolver(workload)
        return client or self.llm_client

    def _client_for_stage(self, stage: DikiwiStage | str) -> Any:
        stage_name = stage.name.lower() if isinstance(stage, DikiwiStage) else str(stage).strip().lower()
        return self._client_for_workload(f"dikiwi.{stage_name}")

    def _get_or_create_memory(self, pipeline_id: str) -> ConversationMemory:
        """Get or create conversation memory for a pipeline."""
        if pipeline_id not in self._conversation_memories:
            memory = ConversationMemory(max_messages=30)
            memory.add_system(
                "You are DIKIWI, an intelligent knowledge refinement system. "
                "You process information through 6 stages: DATA → INFORMATION → KNOWLEDGE → INSIGHT → WISDOM → IMPACT. "
                "Each stage builds on the previous, creating a coherent knowledge pipeline. "
                "Maintain context across stages and look for patterns that emerge from the accumulated understanding."
            )
            self._conversation_memories[pipeline_id] = memory
        return self._conversation_memories[pipeline_id]

    def _cleanup_memory(self, pipeline_id: str) -> None:
        """Clean up conversation memory after pipeline completion."""
        if pipeline_id in self._conversation_memories:
            del self._conversation_memories[pipeline_id]

    def _budget_for_memory(self, memory: ConversationMemory | None) -> LLMUsageBudget | None:
        if memory is None:
            return None
        for pipeline_id, stored_memory in self._conversation_memories.items():
            if stored_memory is memory:
                return self._llm_budgets.get(pipeline_id)
        return None

    @staticmethod
    def _make_pipeline_id(drop: "RainDrop", prefix: str = "dikiwi") -> str:
        return f"{prefix}_{drop.id[:12]}_{int(time.time())}_{uuid.uuid4().hex[:4]}"

    def _build_agent_registry(self) -> dict[DikiwiStage, Any]:
        from aily.dikiwi.agents.data_agent import DataAgent
        from aily.dikiwi.agents.impact_agent import ImpactAgent
        from aily.dikiwi.agents.information_agent import InformationAgent
        from aily.dikiwi.agents.insight_agent import InsightAgent
        from aily.dikiwi.agents.knowledge_agent import KnowledgeAgent
        from aily.dikiwi.agents.wisdom_agent import WisdomAgent

        return {
            DikiwiStage.DATA: DataAgent(),
            DikiwiStage.INFORMATION: InformationAgent(),
            DikiwiStage.KNOWLEDGE: KnowledgeAgent(),
            DikiwiStage.INSIGHT: InsightAgent(),
            DikiwiStage.WISDOM: WisdomAgent(),
            DikiwiStage.IMPACT: ImpactAgent(),
        }

    def _build_agent_context(self, drop: "RainDrop", pipeline_id: str):
        from aily.dikiwi.agents.context import AgentContext

        memory = self._get_or_create_memory(pipeline_id)
        self._llm_budgets[pipeline_id] = LLMUsageBudget(
            max_calls=SETTINGS.dikiwi_max_llm_calls_per_source,
            stage_round_limit=SETTINGS.dikiwi_stage_round_limit,
        )
        return AgentContext(
            pipeline_id=pipeline_id,
            correlation_id=pipeline_id,
            drop=drop,
            memory=memory,
            budget=self._llm_budgets[pipeline_id],
            llm_client=self._client_for_stage(DikiwiStage.DATA),
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )

    @staticmethod
    def _drop_requests_full_dikiwi(drop: "RainDrop") -> bool:
        metadata = getattr(drop, "metadata", {}) or {}
        return bool(
            metadata.get("force_full_dikiwi")
            or metadata.get("manual_iwi_trigger")
            or metadata.get("triggered_synthesis")
            or metadata.get("dikiwi_mode") == "full"
            or metadata.get("workflow_kind") == "business_planning"
        )

    async def _execute_batch_stage(
        self,
        contexts: list[Any],
        *,
        stage: DikiwiStage,
        agent: Any,
        max_concurrency: int,
    ) -> list[Any]:
        if not contexts:
            return []

        batch_ids = sorted(
            {
                str(ctx.drop.metadata.get("batch_id"))
                for ctx in contexts
                if getattr(ctx, "drop", None) is not None and ctx.drop.metadata.get("batch_id")
            }
        )
        batch_id = batch_ids[0] if len(batch_ids) == 1 else None
        await emit_ui_event(
            "batch_stage_started",
            batch_id=batch_id,
            stage=stage.name,
            pipeline_ids=[ctx.pipeline_id for ctx in contexts],
            source_count=len(contexts),
        )
        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def _run(ctx: Any) -> StageResult:
            async with semaphore:
                try:
                    ctx.llm_client = self._client_for_stage(stage)
                    await emit_ui_event(
                        "stage_started",
                        pipeline_id=ctx.pipeline_id,
                        correlation_id=ctx.correlation_id,
                        stage=stage.name,
                        upload_id=ctx.drop.metadata.get("ui_upload_id"),
                        provider=getattr(ctx.llm_client, "_provider_name", lambda: "unknown")(),
                        model=getattr(ctx.llm_client, "model", ""),
                    )
                    with ui_telemetry_scope(
                        pipeline_id=ctx.pipeline_id,
                        upload_id=ctx.drop.metadata.get("ui_upload_id"),
                        stage=stage.name,
                        workload=f"dikiwi.{stage.name.lower()}",
                    ):
                        stage_timeout = float(getattr(SETTINGS, "dikiwi_stage_timeout_seconds", 240.0))
                        if stage_timeout > 0:
                            result = await asyncio.wait_for(agent.execute(ctx), timeout=stage_timeout)
                        else:
                            result = await agent.execute(ctx)
                except asyncio.TimeoutError:
                    logger.warning(
                        "[DIKIWI] Batch stage %s timed out after %.1fs for %s",
                        stage.name,
                        float(getattr(SETTINGS, "dikiwi_stage_timeout_seconds", 240.0)),
                        ctx.pipeline_id,
                    )
                    result = StageResult(
                        stage=stage,
                        success=False,
                        error_message=(
                            f"{stage.name} stage timed out after "
                            f"{float(getattr(SETTINGS, 'dikiwi_stage_timeout_seconds', 240.0)):.1f}s"
                        ),
                    )
                except Exception as exc:
                    logger.exception("[DIKIWI] Batch stage %s failed for %s", stage.name, ctx.pipeline_id)
                    result = StageResult(
                        stage=stage,
                        success=False,
                        error_message=str(exc),
                    )
                ctx.stage_results.append(result)
                await emit_ui_event(
                    "stage_completed" if result.success else "stage_failed",
                    pipeline_id=ctx.pipeline_id,
                    correlation_id=ctx.correlation_id,
                    stage=stage.name,
                    upload_id=ctx.drop.metadata.get("ui_upload_id"),
                    success=result.success,
                    data_keys=sorted(list(result.data.keys())) if isinstance(result.data, dict) else [],
                    error=result.error_message or "",
                )
                return result

        results = await asyncio.gather(*[_run(ctx) for ctx in contexts])
        await emit_ui_event(
            "batch_stage_completed",
            batch_id=batch_id,
            stage=stage.name,
            pipeline_ids=[ctx.pipeline_id for ctx in contexts],
            source_count=len(contexts),
            success_count=sum(1 for result in results if result.success),
            failure_count=sum(1 for result in results if not result.success),
        )
        return results

    @staticmethod
    def _incremental_growth_ratio(pre_count: int, post_count: int) -> float:
        if post_count <= pre_count:
            return 0.0
        if pre_count <= 0:
            return 1.0
        return max(0.0, (post_count - pre_count) / pre_count)

    @staticmethod
    def _batch_lock_active() -> bool:
        try:
            return SETTINGS.dikiwi_batch_lock_path.exists()
        except Exception:
            return False

    async def _chat_json(
        self,
        *,
        stage: str,
        stage_key: str | None = None,
        messages: list[dict[str, str]],
        temperature: float,
        memory: ConversationMemory | None = None,
    ) -> Any:
        budget = self._budget_for_memory(memory)
        reserve_key = stage_key or stage
        if budget is not None:
            budget.reserve(reserve_key)
            logger.info(
                "[DIKIWI] LLM call stage=%s key=%s used=%s/%s stage_used=%s/%s",
                stage,
                reserve_key,
                budget.calls_used,
                budget.max_calls,
                budget.stage_calls.get(reserve_key, 0),
                budget.stage_round_limit,
            )
        return await self.llm_client.chat_json(messages=messages, temperature=temperature)

    async def _multi_agent_json(
        self,
        *,
        stage: str,
        stage_key: str,
        producer_messages: list[dict[str, str]],
        reviewer_messages_factory: Any,
        temperature: float,
        memory: ConversationMemory | None = None,
    ) -> Any:
        draft = await self._chat_json(
            stage=stage,
            stage_key=stage_key,
            messages=producer_messages,
            temperature=temperature,
            memory=memory,
        )

        if not isinstance(draft, dict):
            return draft

        draft_json = json.dumps(draft, ensure_ascii=False, indent=2)
        review_messages = reviewer_messages_factory(draft_json)

        try:
            reviewed = await self._chat_json(
                stage=stage,
                stage_key=stage_key,
                messages=review_messages,
                temperature=max(0.1, temperature - 0.05),
                memory=memory,
            )
            if isinstance(reviewed, dict):
                return reviewed
        except Exception as exc:
            logger.warning("[DIKIWI] Reviewer agent failed for %s (%s): %s", stage, stage_key, exc)

        return draft

    async def process_input(self, drop: "RainDrop") -> DikiwiResult:
        """Process raw input through complete DIKIWI hierarchy using LLM."""
        if not hasattr(drop, "metadata") or getattr(drop, "metadata", None) is None:
            try:
                setattr(drop, "metadata", {})
            except Exception:
                pass
        if not self.enabled:
            return DikiwiResult(
                input_id=drop.id,
                stage_results=[
                    StageResult(
                        stage=DikiwiStage.DATA,
                        success=False,
                        error_message="DIKIWI Mind disabled",
                    )
                ],
            )

        if self._batch_lock_active() and getattr(drop, "source", "") == "chaos_processor":
            logger.warning(
                "[DIKIWI] Suppressing single-drop chaos processing while batch lock is active for %s",
                getattr(drop, "source_id", drop.id),
            )
            return DikiwiResult(
                input_id=drop.id,
                stage_results=[
                    StageResult(
                        stage=DikiwiStage.DATA,
                        success=False,
                        error_message="single-drop chaos processing suppressed while DIKIWI batch lock is active",
                    )
                ],
            )

        if SETTINGS.dikiwi_foundation_only_ingestion and not self._drop_requests_full_dikiwi(drop):
            return await self.process_input_foundation(drop)

        self._total_inputs += 1
        start_time = time.time()
        pipeline_id = f"dikiwi_{drop.id[:12]}_{int(start_time)}"

        logger.info("[DIKIWI] Starting pipeline %s", pipeline_id)

        # Initialize conversation memory for this pipeline
        memory = self._get_or_create_memory(pipeline_id)
        self._llm_budgets[pipeline_id] = LLMUsageBudget(
            max_calls=SETTINGS.dikiwi_max_llm_calls_per_source,
            stage_round_limit=SETTINGS.dikiwi_stage_round_limit,
        )

        result = DikiwiResult(input_id=drop.id, pipeline_id=pipeline_id)

        try:
            # Lazy imports to avoid circular dependency
            from aily.dikiwi.agents.context import AgentContext
            from aily.dikiwi.agents.data_agent import DataAgent
            from aily.dikiwi.agents.information_agent import InformationAgent
            from aily.dikiwi.agents.knowledge_agent import KnowledgeAgent
            from aily.dikiwi.agents.insight_agent import InsightAgent
            from aily.dikiwi.agents.wisdom_agent import WisdomAgent
            from aily.dikiwi.agents.impact_agent import ImpactAgent
            from aily.dikiwi.orchestrator import DikiwiOrchestrator, PipelineConfig
            from aily.dikiwi.stages import DikiwiStage as OrchestratorDikiwiStage

            # Build agent context
            ctx = AgentContext(
                pipeline_id=pipeline_id,
                correlation_id=pipeline_id,
                drop=drop,
                memory=memory,
                budget=self._llm_budgets[pipeline_id],
                llm_client=self._client_for_stage(DikiwiStage.DATA),
                graph_db=self.graph_db,
                obsidian_writer=self.obsidian_writer,
                dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
                markdownizer=self._markdownizer,
            )

            # Create orchestrator and register agents
            orchestrator = DikiwiOrchestrator(
                llm_client=self.llm_client,
                graph_db=self.graph_db,
                llm_client_resolver=self._client_for_stage,
                config=PipelineConfig(
                    require_cvo_for_impact=True,
                    cvo_ttl_hours=0,  # Non-blocking: auto-approve immediately
                ),
            )
            orchestrator.register_agent(OrchestratorDikiwiStage.DATA, DataAgent())
            orchestrator.register_agent(OrchestratorDikiwiStage.INFORMATION, InformationAgent())
            orchestrator.register_agent(OrchestratorDikiwiStage.KNOWLEDGE, KnowledgeAgent())
            orchestrator.register_agent(OrchestratorDikiwiStage.INSIGHT, InsightAgent())
            orchestrator.register_agent(OrchestratorDikiwiStage.WISDOM, WisdomAgent())
            orchestrator.register_agent(OrchestratorDikiwiStage.IMPACT, ImpactAgent())

            # Run pipeline
            pipeline = await orchestrator.run_pipeline(ctx)

            # Impact is the terminal value output. The Residual proposal analyst
            # (and its old Reactor/Entrepreneur consumers) is quarantined — it
            # wrote to a non-layout folder (07-Proposal) and has no consumer in
            # the trimmed product.

            # Transfer results
            result.stage_results = list(ctx.stage_results)
            result.completed_at = datetime.now(timezone.utc)

            # Extract counts for logging
            data_points = ctx.stage_results[0].data.get("data_points", []) if ctx.stage_results else []
            info_nodes = ctx.stage_results[1].data.get("information_nodes", []) if len(ctx.stage_results) > 1 else []
            links = ctx.stage_results[2].data.get("links", []) if len(ctx.stage_results) > 2 else []
            insights = ctx.stage_results[3].data.get("insights", []) if len(ctx.stage_results) > 3 else []
            zettels = ctx.stage_results[4].data.get("zettels", []) if len(ctx.stage_results) > 4 else []
            impacts = ctx.stage_results[5].data.get("impacts", []) if len(ctx.stage_results) > 5 else []

            if memory:
                memory.add_assistant(
                    f"PIPELINE COMPLETE: All 6 stages finished successfully. "
                    f"Generated {len(data_points)} data → {len(info_nodes)} info → "
                    f"{len(insights)} insights → {len(zettels)} zettels → "
                    f"{len(impacts)} impacts. "
                    f"LLM calls used: {self._llm_budgets[pipeline_id].calls_used}"
                )

            self._cleanup_memory(pipeline_id)
            self._llm_budgets.pop(pipeline_id, None)

            if pipeline.status == "completed":
                self._successful_pipelines += 1
                logger.info(
                    "[DIKIWI] Pipeline %s complete: %d data → %d info → %d links → %d insights → %d wisdom → %d impact",
                    pipeline_id,
                    len(data_points),
                    len(info_nodes),
                    len(links),
                    len(insights),
                    len(zettels),
                    len(impacts),
                )
            else:
                self._failed_pipelines += 1

        except Exception as exc:
            result.completed_at = datetime.now(timezone.utc)
            self._failed_pipelines += 1
            logger.exception("[DIKIWI] Pipeline %s failed: %s", pipeline_id, exc)
            self._llm_budgets.pop(pipeline_id, None)

        return result

    async def process_input_foundation(self, drop: "RainDrop") -> DikiwiResult:
        """Process one input only through DATA → INFORMATION → KNOWLEDGE."""
        batch = await self.process_inputs_batched(
            [drop],
            foundation_only=True,
            max_concurrency=1,
        )
        if batch.results:
            return batch.results[0]
        return DikiwiResult(
            input_id=drop.id,
            stage_results=[
                StageResult(
                    stage=DikiwiStage.DATA,
                    success=False,
                    error_message="DIKIWI foundation processing produced no result",
                )
            ],
        )

    async def process_triggered_iwi(
        self,
        *,
        motive: str,
        workflow_run_id: str,
        node_ids: list[str] | None = None,
    ) -> DikiwiResult:
        """Run triggered Insight → Wisdom → Impact over selected Knowledge graph context."""
        from aily.dikiwi.agents.context import AgentContext
        from aily.dikiwi.network_synthesis import SubgraphCandidate
        from aily.gating.drainage import RainDrop, RainType, StreamType

        if not self.enabled:
            return DikiwiResult(
                input_id=workflow_run_id,
                pipeline_id=workflow_run_id,
                stage_results=[
                    StageResult(
                        stage=DikiwiStage.INSIGHT,
                        success=False,
                        error_message="DIKIWI Mind disabled",
                    )
                ],
            )
        if self.graph_db is None:
            return DikiwiResult(
                input_id=workflow_run_id,
                pipeline_id=workflow_run_id,
                stage_results=[
                    StageResult(
                        stage=DikiwiStage.INSIGHT,
                        success=False,
                        error_message="GraphDB unavailable for triggered I/W/I workflow",
                    )
                ],
            )

        effective_node_ids = [node_id for node_id in (node_ids or []) if node_id]
        if effective_node_ids:
            graph_nodes = await self.graph_db.get_nodes_by_ids(effective_node_ids)
        else:
            graph_nodes = await self.graph_db.get_top_information_nodes_by_semantic_edge_count(limit=8)
            effective_node_ids = [str(node["id"]) for node in graph_nodes]

        if len(graph_nodes) < 2:
            return DikiwiResult(
                input_id=workflow_run_id,
                pipeline_id=workflow_run_id,
                stage_results=[
                    StageResult(
                        stage=DikiwiStage.INSIGHT,
                        success=False,
                        error_message="Triggered I/W/I requires at least two information nodes",
                    )
                ],
            )

        edges = await self.graph_db.get_edges_for_nodes(effective_node_ids, limit=40)
        info_nodes = [
            InformationNode(
                id=str(node["id"]),
                data_point_id=str(node["id"]),
                content=str(node.get("label", "")),
                info_type=str(node.get("type", "information")),
                domain="triggered",
                concept=str(node.get("label", ""))[:80],
                tags=["triggered_iwi"],
            )
            for node in graph_nodes
            if str(node.get("type", "information")) == "information"
        ]
        links = [
            KnowledgeLink(
                source_id=str(edge["source_node_id"]),
                target_id=str(edge["target_node_id"]),
                relation_type=str(edge.get("relation_type") or "relates_to"),
                strength=float(edge.get("weight") or 0.5),
                reasoning=f"Triggered by motive: {motive[:160]}",
            )
            for edge in edges
        ]
        candidate = SubgraphCandidate(
            id=f"triggered:{workflow_run_id}",
            anchor_id=info_nodes[0].id,
            anchor_label=info_nodes[0].content,
            anchor_type="information",
            reason=f"Manual I/W/I trigger: {motive}",
            score=max(1.0, float(len(info_nodes) + len(links))),
            nodes=graph_nodes,
            edges=edges,
            changed_node_ids=effective_node_ids,
        )

        drop = RainDrop(
            id=workflow_run_id,
            rain_type=RainType.DOCUMENT,
            content=motive,
            source="manual_iwi_trigger",
            source_id=workflow_run_id,
            stream_type=StreamType.EXTRACT_ANALYZE,
            metadata={
                "workflow_run_id": workflow_run_id,
                "motive": motive,
                "dikiwi_mode": "full",
                "manual_iwi_trigger": True,
                "knowledge_node_ids": effective_node_ids,
            },
        )
        memory = self._get_or_create_memory(workflow_run_id)
        self._llm_budgets[workflow_run_id] = LLMUsageBudget(
            max_calls=SETTINGS.dikiwi_max_llm_calls_per_source,
            stage_round_limit=SETTINGS.dikiwi_stage_round_limit,
        )
        memory.add_user(f"Manual I/W/I workflow motive: {motive}")
        ctx = AgentContext(
            pipeline_id=workflow_run_id,
            correlation_id=workflow_run_id,
            drop=drop,
            memory=memory,
            budget=self._llm_budgets[workflow_run_id],
            llm_client=self._client_for_stage(DikiwiStage.INSIGHT),
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )
        ctx.stage_results.extend(
            [
                StageResult(
                    stage=DikiwiStage.INFORMATION,
                    success=True,
                    items_processed=len(info_nodes),
                    items_output=len(info_nodes),
                    data={"information_nodes": info_nodes, "info_note_ids": []},
                ),
                StageResult(
                    stage=DikiwiStage.KNOWLEDGE,
                    success=True,
                    items_processed=len(info_nodes),
                    items_output=len(links),
                    data={
                        "links": links,
                        "network_nodes": info_nodes,
                        "subgraph_candidates": [candidate],
                        "network_context": candidate.to_prompt_context(),
                        "knowledge_note_ids": [],
                        "network_synthesis_triggered": True,
                        "triggered_iwi": True,
                    },
                ),
            ]
        )
        result = DikiwiResult(input_id=workflow_run_id, pipeline_id=workflow_run_id)
        try:
            agents = self._build_agent_registry()
            for stage in (DikiwiStage.INSIGHT, DikiwiStage.WISDOM, DikiwiStage.IMPACT):
                ctx.llm_client = self._client_for_stage(stage)
                stage_result = await agents[stage].execute(ctx)
                ctx.stage_results.append(stage_result)
                await emit_ui_event(
                    "stage_completed" if stage_result.success else "stage_failed",
                    pipeline_id=workflow_run_id,
                    correlation_id=workflow_run_id,
                    stage=stage.name,
                    workflow_run_id=workflow_run_id,
                    success=stage_result.success,
                    error=stage_result.error_message or "",
                )
                if not stage_result.success:
                    break
            result.stage_results = list(ctx.stage_results)
            result.completed_at = datetime.now(timezone.utc)
            return result
        finally:
            self._cleanup_memory(workflow_run_id)
            self._llm_budgets.pop(workflow_run_id, None)

    async def process_inputs_batched(
        self,
        drops: list["RainDrop"],
        *,
        incremental_threshold: float | None = None,
        max_concurrency: int | None = None,
        foundation_only: bool = False,
    ) -> DikiwiBatchRun:
        """Run DIKIWI as a stage-latched batch over many drops.

        00-Chaos is assumed to be ready already. This method advances the whole
        batch through DATA, then INFORMATION, then KNOWLEDGE. Higher-order
        synthesis only runs for affected contexts when graph growth crosses the
        incremental threshold.
        """
        if not drops:
            return DikiwiBatchRun()

        if not self.enabled:
            return DikiwiBatchRun(
                results=[
                    DikiwiResult(
                        input_id=drop.id,
                        stage_results=[
                            StageResult(
                                stage=DikiwiStage.DATA,
                                success=False,
                                error_message="DIKIWI Mind disabled",
                            )
                        ],
                    )
                    for drop in drops
                ],
                foundation_only=foundation_only,
            )

        threshold = (
            incremental_threshold
            if incremental_threshold is not None
            else SETTINGS.dikiwi_incremental_trigger_ratio
        )
        concurrency = (
            max_concurrency
            if max_concurrency is not None
            else SETTINGS.dikiwi_batch_stage_concurrency
        )

        self._total_inputs += len(drops)
        pre_information_nodes = 0
        if self.graph_db is not None:
            try:
                pre_information_nodes = await self.graph_db.count_nodes_by_type("information")
            except Exception as exc:
                logger.warning("[DIKIWI] Failed to count pre-batch information nodes: %s", exc)

        agents = self._build_agent_registry()
        contexts: list[Any] = []
        results: list[DikiwiResult] = []

        for drop in drops:
            pipeline_id = self._make_pipeline_id(drop, prefix="dikiwi_batch")
            ctx = self._build_agent_context(drop, pipeline_id)
            contexts.append(ctx)
            results.append(DikiwiResult(input_id=drop.id, pipeline_id=pipeline_id))
            await emit_ui_event(
                "pipeline_started",
                pipeline_id=pipeline_id,
                correlation_id=pipeline_id,
                source_id=drop.id,
                source=drop.source,
                upload_id=drop.metadata.get("ui_upload_id"),
                filename=drop.metadata.get("filename"),
                batch=True,
            )

        try:
            stage_contexts = list(contexts)
            await self._execute_batch_stage(
                stage_contexts,
                stage=DikiwiStage.DATA,
                agent=agents[DikiwiStage.DATA],
                max_concurrency=concurrency,
            )
            stage_contexts = [
                ctx for ctx in stage_contexts
                if ctx.stage_results and ctx.stage_results[-1].stage == DikiwiStage.DATA and ctx.stage_results[-1].success
            ]

            if stage_contexts:
                await self._execute_batch_stage(
                    stage_contexts,
                    stage=DikiwiStage.INFORMATION,
                    agent=agents[DikiwiStage.INFORMATION],
                    max_concurrency=concurrency,
                )
            stage_contexts = [
                ctx for ctx in stage_contexts
                if ctx.stage_results and ctx.stage_results[-1].stage == DikiwiStage.INFORMATION and ctx.stage_results[-1].success
            ]

            if stage_contexts:
                await self._execute_batch_stage(
                    stage_contexts,
                    stage=DikiwiStage.KNOWLEDGE,
                    agent=agents[DikiwiStage.KNOWLEDGE],
                    max_concurrency=concurrency,
                )

            knowledge_contexts = [
                ctx
                for ctx in stage_contexts
                if ctx.stage_results
                and ctx.stage_results[-1].stage == DikiwiStage.KNOWLEDGE
                and ctx.stage_results[-1].success
            ]

            post_information_nodes = pre_information_nodes
            if self.graph_db is not None:
                try:
                    post_information_nodes = await self.graph_db.count_nodes_by_type("information")
                except Exception as exc:
                    logger.warning("[DIKIWI] Failed to count post-batch information nodes: %s", exc)

            incremental_ratio = self._incremental_growth_ratio(
                pre_information_nodes,
                post_information_nodes,
            )
            higher_order_triggered = (
                bool(knowledge_contexts)
                and incremental_ratio >= threshold
                and not foundation_only
            )

            if not knowledge_contexts:
                logger.info("[DIKIWI] Batch stopped before graph threshold check: no successful KNOWLEDGE results")
            elif foundation_only:
                logger.info(
                    "[DIKIWI] Foundation-only batch stopped after KNOWLEDGE for %d contexts",
                    len(knowledge_contexts),
                )
            elif not higher_order_triggered:
                logger.info(
                    "[DIKIWI] Batch stopped after KNOWLEDGE: information graph growth %.2f%% below threshold %.2f%%",
                    incremental_ratio * 100.0,
                    threshold * 100.0,
                )
            if knowledge_contexts:
                await emit_ui_event(
                    (
                        "foundation_ingestion_completed"
                        if foundation_only
                        else "threshold_crossed" if higher_order_triggered else "threshold_not_reached"
                    ),
                    stage=DikiwiStage.KNOWLEDGE.name,
                    incremental_ratio=incremental_ratio,
                    incremental_threshold=threshold,
                    foundation_only=foundation_only,
                    higher_order_triggered=higher_order_triggered,
                    pipeline_ids=[ctx.pipeline_id for ctx in knowledge_contexts],
                )

            if higher_order_triggered and knowledge_contexts and not foundation_only:
                await self._attach_batch_network_assessment(knowledge_contexts)

            higher_order_candidates = [
                ctx
                for ctx in knowledge_contexts
                if ctx.stage_results
                and ctx.stage_results[-1].stage == DikiwiStage.KNOWLEDGE
                and ctx.stage_results[-1].success
                and higher_order_triggered
                and not foundation_only
                and ctx.stage_results[-1].data.get("network_synthesis_triggered", False)
            ]
            selected_higher_order_contexts = self._select_higher_order_contexts(higher_order_candidates)

            if higher_order_candidates:
                await emit_ui_event(
                    "higher_order_contexts_selected",
                    stage=DikiwiStage.KNOWLEDGE.name,
                    candidate_count=len(higher_order_candidates),
                    selected_count=len(selected_higher_order_contexts),
                    max_selected=SETTINGS.dikiwi_higher_order_max_contexts,
                    selected_pipeline_ids=[ctx.pipeline_id for ctx in selected_higher_order_contexts],
                )

            if selected_higher_order_contexts:
                higher_order_contexts = selected_higher_order_contexts
                for stage in (DikiwiStage.INSIGHT, DikiwiStage.WISDOM, DikiwiStage.IMPACT):
                    if not higher_order_contexts:
                        break
                    await self._execute_batch_stage(
                        higher_order_contexts,
                        stage=stage,
                        agent=agents[stage],
                        max_concurrency=concurrency,
                    )
                    higher_order_contexts = [
                        ctx for ctx in higher_order_contexts
                        if ctx.stage_results and ctx.stage_results[-1].stage == stage and ctx.stage_results[-1].success
                    ]

            completed_at = datetime.now(timezone.utc)
            for result, ctx in zip(results, contexts):
                result.stage_results = list(ctx.stage_results)
                result.completed_at = completed_at
                if result.stage_results and all(stage_result.success for stage_result in result.stage_results):
                    self._successful_pipelines += 1
                    await emit_ui_event(
                        "pipeline_completed",
                        pipeline_id=ctx.pipeline_id,
                        correlation_id=ctx.correlation_id,
                        upload_id=ctx.drop.metadata.get("ui_upload_id"),
                        batch=True,
                    )
                else:
                    self._failed_pipelines += 1
                    await emit_ui_event(
                        "pipeline_failed",
                        pipeline_id=ctx.pipeline_id,
                        correlation_id=ctx.correlation_id,
                        upload_id=ctx.drop.metadata.get("ui_upload_id"),
                        batch=True,
                    )

            return DikiwiBatchRun(
                results=results,
                pre_information_nodes=pre_information_nodes,
                post_information_nodes=post_information_nodes,
                incremental_ratio=incremental_ratio,
                incremental_threshold=threshold,
                foundation_only=foundation_only,
                higher_order_triggered=higher_order_triggered,
                higher_order_candidates=len(higher_order_candidates),
                higher_order_selected=len(selected_higher_order_contexts),
            )
        finally:
            for ctx in contexts:
                self._cleanup_memory(ctx.pipeline_id)
                self._llm_budgets.pop(ctx.pipeline_id, None)

    def _select_higher_order_contexts(self, contexts: list[Any]) -> list[Any]:
        """Select the strongest graph-change contexts for expensive upper DIKIWI stages."""
        if not contexts:
            return []

        max_contexts = max(1, int(SETTINGS.dikiwi_higher_order_max_contexts or 1))

        def score(ctx: Any) -> tuple[float, int, str]:
            result = ctx.stage_results[-1] if getattr(ctx, "stage_results", None) else None
            data = result.data if result else {}
            graph_score = float(data.get("graph_change_score") or 0.0)
            semantic_edges = int(data.get("graph_semantic_edges") or data.get("semantic_edges") or 0)
            return (graph_score, semantic_edges, getattr(ctx, "pipeline_id", ""))

        return sorted(contexts, key=score, reverse=True)[:max_contexts]

    async def _attach_batch_network_assessment(self, contexts: list[Any]) -> None:
        """Assess the completed batch graph, not only per-document partial graphs.

        Knowledge stages run per input. During a cold batch, each individual
        document can be too small to satisfy network synthesis thresholds even
        though the completed batch creates a synthesis-grade subgraph. This
        method runs one batch-level assessment after all KNOWLEDGE stages have
        persisted their information nodes and semantic links.
        """
        if not contexts:
            return
        if any(
            ctx.stage_results
            and ctx.stage_results[-1].stage == DikiwiStage.KNOWLEDGE
            and ctx.stage_results[-1].data.get("network_synthesis_triggered", False)
            for ctx in contexts
        ):
            return

        try:
            from aily.dikiwi.network_synthesis import (
                NetworkSynthesisSelector,
                candidate_nodes_to_information,
            )

            all_info_nodes: list[InformationNode] = []
            all_links: list[KnowledgeLink] = []
            seen_nodes: set[str] = set()
            seen_links: set[tuple[str, str, str]] = set()

            for ctx in contexts:
                info_result = next(
                    (
                        result
                        for result in ctx.stage_results
                        if result.stage == DikiwiStage.INFORMATION and result.success
                    ),
                    None,
                )
                knowledge_result = ctx.stage_results[-1] if ctx.stage_results else None
                if info_result:
                    for node in info_result.data.get("information_nodes", []):
                        if getattr(node, "id", "") and node.id not in seen_nodes:
                            all_info_nodes.append(node)
                            seen_nodes.add(node.id)
                if knowledge_result and knowledge_result.stage == DikiwiStage.KNOWLEDGE:
                    for link in knowledge_result.data.get("links", []):
                        key = (
                            getattr(link, "source_id", ""),
                            getattr(link, "target_id", ""),
                            getattr(link, "relation_type", ""),
                        )
                        if all(key) and key not in seen_links:
                            all_links.append(link)
                            seen_links.add(key)

            if not all_info_nodes:
                return

            assessment = await NetworkSynthesisSelector().assess(contexts[0], all_info_nodes, all_links)
            if not assessment.triggered:
                logger.info(
                    "[DIKIWI] Batch-level network synthesis not triggered: %s",
                    assessment.reason,
                )
                return

            network_nodes = candidate_nodes_to_information(assessment.candidates)
            network_context = assessment.to_prompt_context()
            target_ctx = contexts[0]
            knowledge_result = target_ctx.stage_results[-1]
            knowledge_result.data.update(
                {
                    "links": all_links,
                    "local_links": all_links,
                    "network_synthesis_triggered": True,
                    "graph_change_assessment": assessment,
                    "graph_change_score": assessment.score,
                    "graph_semantic_edges": sum(
                        int(candidate.metrics.get("semantic_edge_count", 0))
                        for candidate in assessment.candidates
                    ),
                    "subgraph_candidates": assessment.candidates,
                    "network_nodes": network_nodes,
                    "network_context": network_context,
                }
            )
            if target_ctx.memory:
                target_ctx.memory.add_system(
                    "Batch-level network synthesis trigger:\n"
                    f"{assessment.reason}\n\n{network_context[:3000]}"
                )
            await emit_ui_event(
                "batch_network_synthesis_triggered",
                stage=DikiwiStage.KNOWLEDGE.name,
                candidate_count=len(assessment.candidates),
                graph_change_score=assessment.score,
                selected_pipeline_id=target_ctx.pipeline_id,
            )
        except Exception as exc:
            logger.warning("[DIKIWI] Batch-level network synthesis assessment failed: %s", exc)

    async def process_input_incremental(
        self,
        new_file_paths: list[Path],
        *,
        force: bool = False,
    ) -> "IncrementalResult":
        """Process new files through graph-driven incremental DIKIWI pipeline.

        Only processes new content through DATA/INFORMATION stages, then uses
        NetworkSynthesisSelector to detect changed graph neighborhoods. Higher
        stages (INSIGHT/WISDOM/IMPACT) only regenerate for stale notes.

        Args:
            new_file_paths: Markdown files to process
            force: If True, bypass graph threshold and always run higher stages

        Args:
            new_file_paths: List of markdown file paths in 00-Chaos to process

        Returns:
            IncrementalResult with counts of new nodes, affected subgraphs,
            stale notes, and regenerated output.
        """
        from aily.dikiwi.agents.context import AgentContext
        from aily.dikiwi.incremental_orchestrator import IncrementalOrchestrator, IncrementalResult
        from aily.gating.drainage import RainDrop, RainType, StreamType

        if not self.enabled:
            return IncrementalResult(new_files=0, new_data_points=0, new_info_nodes=0,
                                     affected_subgraphs=0, stale_insights=0, stale_wisdom=0,
                                     stale_impacts=0, regenerated_insights=0, regenerated_wisdom=0,
                                     regenerated_impacts=0, skipped_insights=0, skipped_wisdom=0,
                                     skipped_impacts=0, elapsed_seconds=0, error="DIKIWI Mind disabled")

        vault_path = self.dikiwi_obsidian_writer.vault_path if self.dikiwi_obsidian_writer else Path(".")
        orchestrator = IncrementalOrchestrator(vault_path=vault_path)

        self._total_inputs += 1
        pipeline_id = f"dikiwi_incr_{int(time.time())}"
        memory = self._get_or_create_memory(pipeline_id)
        self._llm_budgets[pipeline_id] = LLMUsageBudget(
            max_calls=SETTINGS.dikiwi_max_llm_calls_per_source,
            stage_round_limit=SETTINGS.dikiwi_stage_round_limit,
        )

        drop = RainDrop(
            id="",
            rain_type=RainType.DOCUMENT,
            content="",
            source="incremental_ingest",
            source_id=str(new_file_paths[0]) if new_file_paths else "unknown",
            stream_type=StreamType.EXTRACT_ANALYZE,
        )

        ctx = AgentContext(
            pipeline_id=pipeline_id,
            correlation_id=pipeline_id,
            drop=drop,
            memory=memory,
            budget=self._llm_budgets[pipeline_id],
            llm_client=self.llm_client,
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )

        try:
            return await orchestrator.ingest(new_file_paths, ctx, force=force)
        finally:
            self._cleanup_memory(pipeline_id)
            self._llm_budgets.pop(pipeline_id, None)

    _LONG_DOC_THRESHOLD = 5000   # chars: below this, single-pass extraction
    _CHUNK_SIZE = 4000            # chars per chunk for long documents
    _MAX_CHUNKS = 8               # max chunks to process per pipeline

    @staticmethod
    def _chunk_content(content: str, chunk_size: int = 4000) -> list[str]:
        """Split content into overlapping chunks at paragraph boundaries."""
        if len(content) <= chunk_size:
            return [content]

        paragraphs = content.split("\n\n")
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para) + 2  # account for \n\n separator
            if current_len + para_len > chunk_size and current:
                chunks.append("\n\n".join(current))
                # Overlap: keep last paragraph(s) (~10% of chunk_size) for continuity
                overlap_target = chunk_size // 10
                overlap: list[str] = []
                overlap_len = 0
                for p in reversed(current):
                    if overlap_len + len(p) > overlap_target:
                        break
                    overlap.insert(0, p)
                    overlap_len += len(p) + 2
                current = overlap
                current_len = overlap_len
            current.append(para)
            current_len += para_len

        if current:
            chunks.append("\n\n".join(current))

        return chunks

    async def _llm_extract_chunk(
        self,
        content: str,
        source: str,
        memory: ConversationMemory | None = None,
        chunk_index: int = 0,
        existing_concepts: list[str] | None = None,
    ) -> tuple[list[DataPoint], dict[str, str]]:
        """Extract concept-level data points from a content chunk. Single-pass, no reviewer."""
        memory_context = DikiwiPromptRegistry.render_memory(memory, limit=1200) if memory else ""

        messages = DikiwiPromptRegistry.data_extraction(
            source=source,
            content=content,
            memory_context=memory_context,
            existing_concepts=existing_concepts or [],
        )
        stage_key = f"data:chunk{chunk_index}:{hashlib.sha1(content[:200].encode()).hexdigest()[:8]}"

        try:
            result = await self._chat_json(
                stage="data",
                stage_key=stage_key,
                messages=messages,
                temperature=0.2,
                memory=memory,
            )
        except Exception as exc:
            logger.warning("[DIKIWI] Chunk %d extraction failed: %s", chunk_index, exc)
            return [], {}

        if not isinstance(result, dict):
            return [], {}

        meta: dict[str, str] = {
            "title": str(result.get("title", "")),
            "summary": str(result.get("summary", "")),
        }

        data_points: list[DataPoint] = []
        for i, pd in enumerate(result.get("data_points", [])):
            if not isinstance(pd, dict) or not pd.get("content"):
                continue
            data_points.append(DataPoint(
                id=f"dp_{uuid.uuid4().hex[:8]}_{chunk_index}_{i}",
                content=pd["content"].strip(),
                context=pd.get("context", ""),
                source=source,
                confidence=float(pd.get("confidence", 0.8)),
                concept=str(pd.get("concept", "")),
            ))

        return data_points, meta

    async def _stage_data(
        self, drop: "RainDrop", memory: ConversationMemory | None = None
    ) -> StageResult:
        """Stage 1: DATA - delegate to DataAgent."""
        from aily.dikiwi.agents.context import AgentContext
        from aily.dikiwi.agents.data_agent import DataAgent

        ctx = AgentContext(
            pipeline_id=f"legacy_{id(drop)}",
            correlation_id=f"legacy_{id(drop)}",
            drop=drop,
            memory=memory,
            budget=self._budget_for_memory(memory),
            llm_client=self.llm_client,
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )
        return await DataAgent().execute(ctx)

    async def _llm_extract_data_points(
        self, content: str, source: str, memory: ConversationMemory | None = None
    ) -> list[DataPoint]:
        """Use LLM to extract data points - no hardcoded rules."""
        # Build context from conversation memory
        memory_context = ""
        if memory and len(memory.messages) > 1:
            memory_context = f"\n\nPrevious context:\n{memory.to_prompt_context()[-2000:]}"

        messages = DikiwiPromptRegistry.data_extraction(
            source=source,
            content=content,
            memory_context=memory_context,
        )
        stage_key = f"data:{hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]}"

        try:
            result = await self._multi_agent_json(
                stage="data",
                stage_key=stage_key,
                producer_messages=messages,
                reviewer_messages_factory=lambda draft_json: DikiwiPromptRegistry.review(
                    stage="DATA",
                    reviewer_role="Data Curator",
                    objective="Review extracted data points, remove blended items, and return the cleanest atomic data inventory for downstream processing.",
                    output_contract=DikiwiPromptRegistry.DATA_EXTRACTION_CONTRACT,
                    draft_json=draft_json,
                    memory_context=memory_context,
                    review_focus=(
                        "Split blended data points into atomic units when needed.",
                        "Prefer preserving useful source material over over-compressing it.",
                        "Ensure every returned data point is individually reusable.",
                    ),
                    context_sections=(
                        ("Source", source),
                    ),
                ),
                temperature=0.2,
                memory=memory,
            )
        except Exception as exc:
            logger.warning("[DIKIWI] Data extraction failed: %s", exc)
            # Even on failure, use LLM to summarize rather than hardcoded truncation
            return await self._llm_fallback_extraction(content, source)

        points_data = result.get("data_points", []) if isinstance(result, dict) else []

        data_points: list[DataPoint] = []
        for i, pd in enumerate(points_data):
            if isinstance(pd, dict) and pd.get("content"):
                data_points.append(
                    DataPoint(
                        id=f"dp_{uuid.uuid4().hex[:8]}_{i}",
                        content=pd["content"].strip(),
                        context=pd.get("context", ""),
                        source=source,
                        confidence=pd.get("confidence", 0.8),
                    )
                )

        if not data_points:
            # Use LLM for fallback, not hardcoded truncation
            return await self._llm_fallback_extraction(content, source)

        return data_points

    async def _llm_fallback_extraction(
        self, content: str, source: str
    ) -> list[DataPoint]:
        """LLM-based fallback instead of hardcoded truncation."""
        messages = DikiwiPromptRegistry.fallback_extraction(
            source=source,
            content_preview=f"{content[:2000]}...",
        )

        try:
            result = await self._chat_json(
                stage="data_fallback",
                stage_key=f"data_fallback:{hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]}",
                messages=messages,
                temperature=0.3,
            )

            if isinstance(result, dict):
                summary = result.get("summary") or result.get("key_takeaway", "")
                if summary:
                    return [
                        DataPoint(
                            id=f"dp_{uuid.uuid4().hex[:8]}",
                            content=summary,
                            source=source,
                            confidence=result.get("confidence", 0.5),
                        )
                    ]
        except Exception:
            pass

        # Absolute last resort - but still use LLM to create meaningful content
        return [
            DataPoint(
                id=f"dp_{uuid.uuid4().hex[:8]}",
                content=f"[Content from {source} - extraction failed, manual review needed]",
                source=source,
                confidence=0.0,
            )
        ]

    async def _markdownize_drop(self, drop: "RainDrop") -> "RainDrop":
        """Pre-process drop content - convert URLs to markdown."""
        import re

        if drop.metadata.get("source_type") in {"url_markdown", "chaos_markdown"} or \
           drop.metadata.get("processing_method") == "browser_url_markdown_fetch":
            logger.info(
                "[DIKIWI] Skipping markdownize for pre-normalized content type=%s",
                drop.metadata.get("source_type", "unknown"),
            )
            return drop

        content = drop.content

        url_pattern = r"https?://\S+"
        urls = re.findall(url_pattern, content)

        if not urls:
            return drop

        logger.info("[DIKIWI] Markdownizing %d URLs", len(urls))

        markdown_parts = []
        for url in urls:
            try:
                md_content = await self._markdownizer.process_url(url)
                if md_content.markdown:
                    markdown_parts.append(
                        f"## Content from {url}\n\n{md_content.markdown}"
                    )
                else:
                    markdown_parts.append(f"## {url}\n\n[Could not extract content]")
            except Exception as e:
                logger.warning("[DIKIWI] Failed to markdownize %s: %s", url, e)
                markdown_parts.append(f"## {url}\n\n[Error: {e}]")

        text_without_urls = re.sub(url_pattern, "", content).strip()

        combined = []
        if text_without_urls:
            combined.append(f"## User Message\n\n{text_without_urls}")
        combined.extend(markdown_parts)

        markdownized_content = "\n\n---\n\n".join(combined)

        from dataclasses import replace

        return replace(drop, content=markdownized_content)

    @staticmethod
    def _clean_domain(domain: str) -> str:
        """Take the first segment of pipe-separated domain strings returned by the LLM."""
        return domain.split("|")[0].strip() if "|" in domain else domain.strip()

    async def _llm_classify_batch(
        self,
        data_points: list[DataPoint],
        source: str,
        memory: ConversationMemory | None = None,
    ) -> list[dict[str, Any]]:
        """Classify all data points in a single LLM call. Returns list aligned with data_points."""
        if not data_points:
            return []

        memory_context = DikiwiPromptRegistry.render_memory(memory, limit=1200)
        messages = DikiwiPromptRegistry.classification_batch(
            data_points=data_points,
            source=source,
            memory_context=memory_context,
        )
        stage_key = f"information:batch:{hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]}"

        fallback = [
            {"tags": [], "info_type": "fact", "domain": "general", "confidence": 0.8}
        ] * len(data_points)

        try:
            result = await self._chat_json(
                stage="information",
                stage_key=stage_key,
                messages=messages,
                temperature=0.2,
                memory=memory,
            )
        except Exception as exc:
            logger.warning("[DIKIWI] Batch classification failed: %s", exc)
            return fallback

        if not isinstance(result, dict):
            return fallback

        raw_list = result.get("classifications", [])
        index_map: dict[int, dict] = {}
        for item in raw_list:
            if isinstance(item, dict):
                idx = item.get("index")
                if isinstance(idx, int):
                    index_map[idx] = item

        return [
            {
                "tags": index_map.get(i, {}).get("tags", [])[:5],
                "info_type": index_map.get(i, {}).get("info_type", "fact"),
                "domain": self._clean_domain(index_map.get(i, {}).get("domain", "general")),
                "confidence": float(index_map.get(i, {}).get("confidence", 0.8)),
            }
            for i in range(len(data_points))
        ]

    async def _stage_information(
        self,
        data_points: list[DataPoint],
        source: str,
        memory: ConversationMemory | None = None,
    ) -> StageResult:
        """Stage 2: INFORMATION - delegate to InformationAgent."""
        from aily.dikiwi.agents.context import AgentContext
        from aily.dikiwi.agents.information_agent import InformationAgent

        class _MockDrop:
            def __init__(self):
                self.id = "legacy"
                self.source = source
                self.metadata = {}
        ctx = AgentContext(
            pipeline_id=f"legacy_info_{source}",
            correlation_id=f"legacy_info_{source}",
            drop=_MockDrop(),  # type: ignore[arg-type]
            memory=memory,
            budget=self._budget_for_memory(memory),
            llm_client=self.llm_client,
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )
        ctx.stage_results.append(
            StageResult(
                stage=DikiwiStage.DATA,
                success=True,
                items_processed=len(data_points),
                items_output=len(data_points),
                data={"data_points": data_points, "doc_title": "", "doc_summary": ""},
            )
        )
        return await InformationAgent().execute(ctx)

    async def _llm_classify_and_tag(
        self,
        data_point: DataPoint,
        memory: ConversationMemory | None = None,
    ) -> dict[str, Any]:
        """LLM-based classification - no keyword fallbacks."""
        memory_context = DikiwiPromptRegistry.render_memory(memory, limit=1200)
        messages = DikiwiPromptRegistry.classification(
            content=data_point.content,
            context=data_point.context,
            source=data_point.source,
            memory_context=memory_context,
        )
        stage_key = f"information:{data_point.id}"

        try:
            result = await self._multi_agent_json(
                stage="information",
                stage_key=stage_key,
                producer_messages=messages,
                reviewer_messages_factory=lambda draft_json: DikiwiPromptRegistry.review(
                    stage="INFORMATION",
                    reviewer_role="Taxonomy Editor",
                    objective="Review the classification draft and return cleaner tags and domain labels that improve future Zettelkasten linking.",
                    output_contract=DikiwiPromptRegistry.CLASSIFICATION_CONTRACT,
                    draft_json=draft_json,
                    memory_context=memory_context,
                    review_focus=(
                        "Remove vague tags and keep the most link-worthy terminology.",
                        "Prefer content-centered labels over bookkeeping labels.",
                        "Preserve the source meaning while tightening categorization.",
                    ),
                ),
                temperature=0.2,
                memory=memory,
            )

            if isinstance(result, dict):
                return {
                    "tags": result.get("tags", [])[:5],
                    "info_type": result.get("info_type", "fact"),
                    "domain": result.get("domain", "general"),
                    "confidence": result.get("confidence", 0.8),
                }
        except Exception as exc:
            logger.warning("[DIKIWI] Classification failed: %s", exc)

        # Even fallback uses LLM minimal classification
        return {"tags": [], "info_type": "fact", "domain": "general", "confidence": 0.0}

    async def _store_node_metadata(self, node: InformationNode) -> None:
        """Store node tags in GraphDB."""
        for tag in node.tags:
            await self.graph_db.insert_node(
                node_id=f"tag_{tag}", node_type="tag", label=tag, source="dikiwi"
            )
            await self.graph_db.insert_edge(
                edge_id=f"edge_{uuid.uuid4().hex[:8]}",
                source_node_id=node.id,
                target_node_id=f"tag_{tag}",
                relation_type="has_tag",
                source="dikiwi",
                weight=1.0,
            )

    async def _stage_knowledge(
        self,
        info_nodes: list[InformationNode],
        source: str,
        memory: ConversationMemory | None = None,
    ) -> StageResult:
        """Stage 3: KNOWLEDGE - delegate to KnowledgeAgent."""
        from aily.dikiwi.agents.context import AgentContext
        from aily.dikiwi.agents.knowledge_agent import KnowledgeAgent

        class _MockDrop:
            def __init__(self):
                self.id = "legacy"
                self.source = source
                self.metadata = {}
        ctx = AgentContext(
            pipeline_id=f"legacy_knowledge_{source}",
            correlation_id=f"legacy_knowledge_{source}",
            drop=_MockDrop(),  # type: ignore[arg-type]
            memory=memory,
            budget=self._budget_for_memory(memory),
            llm_client=self.llm_client,
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )
        ctx.stage_results.append(
            StageResult(
                stage=DikiwiStage.INFORMATION,
                success=True,
                items_processed=len(info_nodes),
                items_output=len(info_nodes),
                data={"information_nodes": info_nodes, "info_note_ids": {}},
            )
        )
        return await KnowledgeAgent().execute(ctx)

    async def _llm_map_relations_batch(
        self,
        info_nodes: list[InformationNode],
        source: str,
        memory: ConversationMemory | None = None,
    ) -> list[KnowledgeLink]:
        """Map relationships across ALL nodes in one batched LLM call.

        Returns only the strongest, most meaningful links (strength > 0.5).
        """
        memory_context = DikiwiPromptRegistry.render_memory(memory, limit=1200)
        messages = DikiwiPromptRegistry.relation_batch(
            nodes=info_nodes,
            memory_context=memory_context,
        )
        stage_key = f"knowledge:batch:{hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]}"

        id_map = {i: n.id for i, n in enumerate(info_nodes)}

        try:
            result = await self._chat_json(
                stage="knowledge",
                stage_key=stage_key,
                messages=messages,
                temperature=0.2,
                memory=memory,
            )

            if not isinstance(result, dict):
                return []

            links: list[KnowledgeLink] = []
            for raw in result.get("links", []):
                if not isinstance(raw, dict):
                    continue
                a_idx = raw.get("node_a_index")
                b_idx = raw.get("node_b_index")
                relation = raw.get("relation_type", "none")
                strength = float(raw.get("strength", 0.0))
                if (
                    a_idx is None or b_idx is None
                    or relation == "none"
                    or strength <= 0.5
                    or a_idx not in id_map
                    or b_idx not in id_map
                    or a_idx == b_idx
                ):
                    continue
                links.append(KnowledgeLink(
                    source_id=id_map[a_idx],
                    target_id=id_map[b_idx],
                    relation_type=relation,
                    strength=strength,
                    reasoning=str(raw.get("reasoning", "")),
                ))
            return links

        except Exception as exc:
            logger.warning("[DIKIWI] Batch relation mapping failed: %s", exc)
            return []

    async def _stage_insight(
        self,
        info_nodes: list[InformationNode],
        links: list[KnowledgeLink],
        drop: "RainDrop",
        memory: ConversationMemory | None = None,
    ) -> StageResult:
        """Stage 4: INSIGHT - delegate to InsightAgent."""
        from aily.dikiwi.agents.context import AgentContext
        from aily.dikiwi.agents.insight_agent import InsightAgent

        ctx = AgentContext(
            pipeline_id=f"legacy_insight_{id(drop)}",
            correlation_id=f"legacy_insight_{id(drop)}",
            drop=drop,
            memory=memory,
            budget=self._budget_for_memory(memory),
            llm_client=self.llm_client,
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )
        ctx.stage_results.append(
            StageResult(
                stage=DikiwiStage.INFORMATION,
                success=True,
                items_processed=len(info_nodes),
                items_output=len(info_nodes),
                data={"information_nodes": info_nodes, "info_note_ids": {}},
            )
        )
        ctx.stage_results.append(
            StageResult(
                stage=DikiwiStage.KNOWLEDGE,
                success=True,
                items_processed=len(info_nodes),
                items_output=len(links),
                data={"links": links, "knowledge_note_ids": []},
            )
        )
        return await InsightAgent().execute(ctx)

    async def _llm_detect_patterns(
        self,
        info_nodes: list[InformationNode],
        links: list[KnowledgeLink],
        memory: ConversationMemory | None = None,
    ) -> list[Insight]:
        """LLM-based pattern detection - no hardcoded rules."""
        # Prepare network description
        nodes_desc = "\n".join(
            f"{i+1}. [{n.domain}] {n.content[:150]}"
            for i, n in enumerate(info_nodes[:15])
        )

        links_desc = "\n".join(
            f"- {l.source_id[:8]}... {l.relation_type} {l.target_id[:8]}... (strength: {l.strength:.2f})"
            for l in links[:10]
        )

        # Include conversation memory context
        memory_context = ""
        if memory and len(memory.messages) > 2:
            memory_context = f"\n\nProcessing context:\n{memory.to_prompt_context()[-1500:]}\n\n"

        messages = DikiwiPromptRegistry.insight(
            nodes_desc=nodes_desc,
            links_desc=links_desc,
            memory_context=memory_context.strip(),
        )
        stage_key = f"insight:{hashlib.sha1((nodes_desc + links_desc).encode('utf-8')).hexdigest()[:8]}"

        try:
            result = await self._multi_agent_json(
                stage="insight",
                stage_key=stage_key,
                producer_messages=messages,
                reviewer_messages_factory=lambda draft_json: DikiwiPromptRegistry.review(
                    stage="INSIGHT",
                    reviewer_role="Pattern Editor",
                    objective="Review the draft insights and keep only non-obvious patterns that deserve long-term note space.",
                    output_contract=DikiwiPromptRegistry.INSIGHT_CONTRACT,
                    draft_json=draft_json,
                    memory_context=memory_context.strip(),
                    review_focus=(
                        "Remove restatements of single facts.",
                        "Prefer tensions, mechanisms, gaps, and recurring patterns.",
                        "Keep the insight list compact but meaningful.",
                    ),
                    context_sections=(
                        ("Information Nodes", nodes_desc or "No nodes available."),
                        ("Relationships", links_desc or "No explicit relationships available."),
                    ),
                ),
                temperature=0.4,
                memory=memory,
            )

            if not isinstance(result, dict):
                return []

            insights_data = result.get("insights", [])
            insights: list[Insight] = []

            for p in insights_data:
                if isinstance(p, dict) and p.get("description"):
                    insights.append(
                        Insight(
                            id=f"insight_{uuid.uuid4().hex[:8]}",
                            insight_type=p.get("type", "pattern"),
                            description=p.get("description", ""),
                            related_nodes=[
                                f"node_{i}" for i in p.get("related_node_indices", [])
                            ],
                            confidence=p.get("confidence", 0.5),
                        )
                    )

            return insights

        except Exception as exc:
            logger.debug("[DIKIWI] Pattern detection failed: %s", exc)
            return []

    async def _write_insight_notes(self, insights: list[Insight], drop: "RainDrop") -> None:
        """Write insight notes to Obsidian."""
        try:
            # Use enhanced DikiwiObsidianWriter if available
            if self.dikiwi_obsidian_writer and insights:
                await self.dikiwi_obsidian_writer.write_insights(
                    message_id=drop.id,
                    insights=insights,
                    source_title=drop.source,
                )
                logger.info("[DIKIWI] Wrote %d insights to enhanced Obsidian", len(insights))

            # Fallback to legacy REST API writer
            if self.obsidian_writer:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                markdown = f"""# Insights from {drop.source}

**Date**: {date_str}
**Source**: {drop.source}
**Method**: LLM-powered pattern detection (Kimi)

## Detected Patterns

"""
                for insight in insights:
                    emoji = {
                        "theme": "🎯",
                        "contradiction": "⚡",
                        "opportunity": "💡",
                        "gap": "🔗",
                        "pattern": "🔍",
                        "tension": "↔️",
                    }.get(insight.insight_type, "📌")

                    markdown += f"""### {emoji} {insight.insight_type.replace('_', ' ').title()}

{insight.description}

**Confidence**: {insight.confidence:.0%}

---

"""

                await self.obsidian_writer.write_note(
                    title=f"Insight-LLM: {date_str} {drop.id[:8]}",
                    markdown=markdown,
                    source_url=f"aily://insight/{drop.id}",
                )

        except Exception as e:
            logger.warning("[DIKIWI] Failed to write insight notes: %s", e)

    async def _stage_wisdom(
        self,
        insights: list[Insight],
        info_nodes: list[InformationNode],
        drop: "RainDrop",
        memory: ConversationMemory | None = None,
    ) -> StageResult:
        """Stage 5: WISDOM - delegate to WisdomAgent."""
        from aily.dikiwi.agents.context import AgentContext
        from aily.dikiwi.agents.wisdom_agent import WisdomAgent

        ctx = AgentContext(
            pipeline_id=f"legacy_wisdom_{id(drop)}",
            correlation_id=f"legacy_wisdom_{id(drop)}",
            drop=drop,
            memory=memory,
            budget=self._budget_for_memory(memory),
            llm_client=self.llm_client,
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )
        ctx.stage_results.append(
            StageResult(
                stage=DikiwiStage.INFORMATION,
                success=True,
                items_processed=len(info_nodes),
                items_output=len(info_nodes),
                data={"information_nodes": info_nodes, "info_note_ids": {}},
            )
        )
        ctx.stage_results.append(
            StageResult(
                stage=DikiwiStage.INSIGHT,
                success=True,
                items_processed=len(insights),
                items_output=len(insights),
                data={"insights": insights, "insight_note_ids": []},
            )
        )
        return await WisdomAgent().execute(ctx)

    async def _llm_synthesize_wisdom(
        self,
        insights: list[Insight],
        info_nodes: list[InformationNode],
        memory: ConversationMemory | None = None,
    ) -> list[ZettelkastenNote]:
        """LLM-based synthesis into proper Zettelkasten permanent notes.

        NOT fragments - complete atomic notes (200-500 words) with:
        - One clear, timeless idea
        - Examples and context
        - Written as complete thoughts
        - Links to related concepts
        """
        insights_desc = "\n".join(
            f"- [{i.insight_type}] {i.description[:140]}"
            for i in insights[:4]
        )

        info_samples = "\n".join(
            f"- [{n.domain}] {n.content[:140]}" for n in info_nodes[:8]
        )

        memory_context = DikiwiPromptRegistry.render_memory(memory, limit=700)
        messages = DikiwiPromptRegistry.wisdom(
            insights_desc=insights_desc,
            info_samples=info_samples,
            memory_context=memory_context,
        )
        stage_key = f"wisdom:{hashlib.sha1((insights_desc + info_samples).encode('utf-8')).hexdigest()[:8]}"

        try:
            if SETTINGS.dikiwi_wisdom_review_enabled:
                result = await self._multi_agent_json(
                    stage="wisdom",
                    stage_key=stage_key,
                    producer_messages=messages,
                    reviewer_messages_factory=lambda draft_json: DikiwiPromptRegistry.review(
                        stage="WISDOM",
                        reviewer_role="Slip-Box Editor",
                        objective="Review the draft permanent notes and return a cleaner set of atomic, source-grounded zettels for long-term use.",
                        output_contract=DikiwiPromptRegistry.WISDOM_CONTRACT,
                        draft_json=draft_json,
                        memory_context=memory_context,
                        review_focus=(
                            "Reject summary-shaped notes and preserve distinct durable ideas instead.",
                            "Split mechanisms, workflows, constraints, tradeoffs, and examples into separate notes when the source supports them.",
                            "Keep titles readable, human, and conceptually precise.",
                        ),
                        context_sections=(
                            ("Insights", insights_desc or "No insights available."),
                            ("Knowledge Base", info_samples or "No information samples available."),
                        ),
                    ),
                    temperature=0.5,
                    memory=memory,
                )
            else:
                result = await self._chat_json(
                    stage="wisdom",
                    stage_key=stage_key,
                    messages=messages,
                    temperature=0.45,
                    memory=memory,
                )

            if not isinstance(result, dict):
                return []

            zettels_data = result.get("zettels", [])
            return [
                ZettelkastenNote(
                    id=f"z{uuid.uuid4().hex[:6]}",
                    title=z.get("title", "Untitled"),
                    content=z.get("content", ""),
                    tags=z.get("tags", []),
                    links_to=z.get("links_to", []),
                    confidence=z.get("confidence", 0.5),
                )
                for z in zettels_data
                if isinstance(z, dict) and z.get("title") and len(z.get("content", "")) > 100
            ]

        except Exception as exc:
            logger.debug("[DIKIWI] Wisdom synthesis failed: %s", exc)
            return []

    async def _write_wisdom_notes(
        self, wisdom_items: list[Wisdom], insights: list[Insight], drop: "RainDrop"
    ) -> None:
        """Write wisdom notes to Obsidian."""
        try:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            markdown = f"""# Wisdom Synthesis: {drop.source}

**Date**: {date_str}
**Source**: {drop.source}
**Stage**: DIKIWI WISDOM (LLM-Powered)

## Derived Principles

"""
            for i, wisdom in enumerate(wisdom_items, 1):
                markdown += f"""### {i}. {wisdom.principle}

**Principle**: {wisdom.principle}

**Context**: {wisdom.context}

**Implications**:
"""
                for imp in wisdom.implications:
                    markdown += f"- {imp}\n"
                markdown += "\n---\n\n"

            markdown += """## Source Insights

"""
            for insight in insights[:3]:
                markdown += f"- **{insight.insight_type}**: {insight.description}\n"

            await self.obsidian_writer.write_note(
                title=f"Wisdom-LLM: {date_str} {drop.id[:8]}",
                markdown=markdown,
                source_url=f"aily://wisdom/{drop.id}",
            )

        except Exception as e:
            logger.warning("[DIKIWI] Failed to write wisdom notes: %s", e)

    async def _write_zettelkasten_notes(
        self, zettels: list[ZettelkastenNote], drop: "RainDrop"
    ) -> None:
        """Write Zettelkasten permanent notes to Obsidian."""
        try:
            if not self.dikiwi_obsidian_writer:
                return

            for zettel in zettels:
                source_paths = drop.metadata.get("source_paths", [])
                await self.dikiwi_obsidian_writer.write_zettel(
                    zettel_id=zettel.id,
                    title=zettel.title,
                    content=zettel.content,
                    tags=zettel.tags,
                    links_to=zettel.links_to,
                    source=drop.source,
                    source_paths=source_paths,
                    dikiwi_level="wisdom",
                )

                logger.info(
                    "[DIKIWI] Wrote Zettelkasten note: %s (%d words)",
                    zettel.title[:40],
                    len(zettel.content.split()),
                )

        except Exception as e:
            logger.warning("[DIKIWI] Failed to write Zettelkasten notes: %s", e)

    def _drop_source_paths(self, drop: "RainDrop") -> list[str]:
        return list(drop.metadata.get("source_paths", []))

    def _drop_run_tags(self, drop: "RainDrop") -> list[str]:
        run_id = str(drop.metadata.get("evidence_run_id") or "").strip()
        if not run_id:
            return []
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in run_id).strip("-_")
        return [f"evidence-run-{safe}"] if safe else []

    def _short_hash(self, text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]

    def _title_from_text(self, text: str, fallback: str) -> str:
        cleaned = " ".join(text.replace("\n", " ").split()).strip(" -:")
        if not cleaned:
            return fallback
        sentence = cleaned.split(".")[0].split("。")[0].strip()
        if 12 <= len(sentence) <= 110:
            return sentence[0].upper() + sentence[1:] if sentence else fallback
        if len(cleaned) > 110:
            cleaned = cleaned[:107].rstrip() + "..."
        return cleaned or fallback

    async def _write_data_zettels(self, data_points: list[DataPoint], drop: "RainDrop") -> None:
        if not self.dikiwi_obsidian_writer:
            return
        for dp in data_points:
            title = self._title_from_text(dp.content, "Source Data Point")
            content = "\n".join([
                f"This note preserves a source-grounded data fragment captured from `{drop.source}`.",
                "",
                "## Data",
                dp.content,
                "",
                "## Context",
                dp.context or "No extra context was preserved.",
                "",
                "## Source Grounding",
                f"- Confidence: {dp.confidence:.0%}",
                f"- Source: {drop.source}",
            ])
            await self.dikiwi_obsidian_writer.write_zettel(
                zettel_id=f"d{self._short_hash(dp.id + dp.content)}",
                title=title,
                content=content,
                tags=self._drop_run_tags(drop),
                links_to=[],
                source=drop.source,
                source_paths=self._drop_source_paths(drop),
                dikiwi_level="data",
            )

    async def _write_information_zettels(self, info_nodes: list[InformationNode], drop: "RainDrop") -> None:
        if not self.dikiwi_obsidian_writer:
            return
        for node in info_nodes:
            title = self._title_from_text(node.content, f"{node.domain.title()} Information")
            content = "\n".join([
                "This note condenses one classified information unit from the source material.",
                "",
                "## Information",
                node.content,
                "",
                "## Classification",
                f"- Domain: {node.domain or 'general'}",
                f"- Type: {node.info_type or 'fact'}",
                f"- Tags: {', '.join(node.tags) if node.tags else 'None'}",
            ])
            await self.dikiwi_obsidian_writer.write_zettel(
                zettel_id=f"i{self._short_hash(node.id + node.content)}",
                title=title,
                content=content,
                tags=[*node.tags, *self._drop_run_tags(drop)],
                links_to=[],
                source=drop.source,
                source_paths=self._drop_source_paths(drop),
                dikiwi_level="information",
            )

    async def _write_knowledge_zettels(
        self,
        links: list[KnowledgeLink],
        info_nodes: list[InformationNode],
        drop: "RainDrop",
    ) -> None:
        if not self.dikiwi_obsidian_writer:
            return
        node_map = {node.id: node for node in info_nodes}
        for link in links:
            source_node = node_map.get(link.source_id)
            target_node = node_map.get(link.target_id)
            if not source_node or not target_node:
                continue
            title = self._title_from_text(
                f"{source_node.content} {link.relation_type.replace('_', ' ')} {target_node.content}",
                "Knowledge Relationship",
            )
            content = "\n".join([
                "This note preserves a meaningful relationship discovered between two information units.",
                "",
                "## Relationship",
                f"{source_node.content}",
                "",
                f"**{link.relation_type.replace('_', ' ').title()}**",
                "",
                f"{target_node.content}",
                "",
                "## Interpretation",
                f"- Relation type: {link.relation_type}",
                f"- Strength: {link.strength:.2f}",
            ])
            await self.dikiwi_obsidian_writer.write_zettel(
                zettel_id=f"k{self._short_hash(link.source_id + link.target_id + link.relation_type)}",
                title=title,
                content=content,
                tags=[
                    link.relation_type,
                    *(source_node.tags[:2]),
                    *(target_node.tags[:2]),
                    *self._drop_run_tags(drop),
                ],
                links_to=[
                    self._title_from_text(source_node.content, "Source Information"),
                    self._title_from_text(target_node.content, "Target Information"),
                ],
                source=drop.source,
                source_paths=self._drop_source_paths(drop),
                dikiwi_level="knowledge",
            )

    async def _write_insight_zettels(
        self,
        insights: list[Insight],
        info_nodes: list[InformationNode],
        drop: "RainDrop",
    ) -> None:
        if not self.dikiwi_obsidian_writer:
            return
        for insight in insights:
            title = self._title_from_text(insight.description, f"{insight.insight_type.title()} Insight")
            content = "\n".join([
                "This note records an insight that emerges from multiple information units.",
                "",
                "## Insight",
                insight.description,
                "",
                "## Significance",
                f"- Type: {insight.insight_type}",
                f"- Confidence: {insight.confidence:.0%}",
            ])
            await self.dikiwi_obsidian_writer.write_zettel(
                zettel_id=f"s{self._short_hash(insight.id + insight.description)}",
                title=title,
                content=content,
                tags=[insight.insight_type, *self._drop_run_tags(drop)],
                links_to=[],
                source=drop.source,
                source_paths=self._drop_source_paths(drop),
                dikiwi_level="insight",
            )

    async def _stage_impact(
        self,
        zettels: list[ZettelkastenNote],
        insights: list[Insight],
        drop: "RainDrop",
        memory: ConversationMemory | None = None,
    ) -> StageResult:
        """Stage 6: IMPACT - delegate to ImpactAgent."""
        from aily.dikiwi.agents.context import AgentContext
        from aily.dikiwi.agents.impact_agent import ImpactAgent

        ctx = AgentContext(
            pipeline_id=f"legacy_impact_{id(drop)}",
            correlation_id=f"legacy_impact_{id(drop)}",
            drop=drop,
            memory=memory,
            budget=self._budget_for_memory(memory),
            llm_client=self.llm_client,
            graph_db=self.graph_db,
            obsidian_writer=self.obsidian_writer,
            dikiwi_obsidian_writer=self.dikiwi_obsidian_writer,
            markdownizer=self._markdownizer,
        )
        ctx.stage_results.append(
            StageResult(
                stage=DikiwiStage.WISDOM,
                success=True,
                items_processed=len(insights),
                items_output=len(zettels),
                data={"zettels": zettels, "wisdom_note_ids": []},
            )
        )
        if insights:
            ctx.stage_results.append(
                StageResult(
                    stage=DikiwiStage.INSIGHT,
                    success=True,
                    items_processed=len(insights),
                    items_output=len(insights),
                    data={"insights": insights, "insight_note_ids": []},
                )
            )
        return await ImpactAgent().execute(ctx)

    async def _llm_generate_impacts(
        self,
        zettels: list[ZettelkastenNote],
        insights: list[Insight],
        memory: ConversationMemory | None = None,
    ) -> list[dict]:
        """LLM-based impact generation from Zettelkasten notes."""
        zettels_desc = "\n".join(
            f"- {z.title[:100]}" for z in zettels[:3]
        )
        memory_context = DikiwiPromptRegistry.render_memory(memory, limit=1200)
        messages = DikiwiPromptRegistry.impact(
            zettels_desc=zettels_desc,
            memory_context=memory_context,
        )
        stage_key = f"impact:{hashlib.sha1(zettels_desc.encode('utf-8')).hexdigest()[:8]}"

        try:
            result = await self._multi_agent_json(
                stage="impact",
                stage_key=stage_key,
                producer_messages=messages,
                reviewer_messages_factory=lambda draft_json: DikiwiPromptRegistry.review(
                    stage="IMPACT",
                    reviewer_role="Action Editor",
                    objective="Review the proposed impacts and keep only actions that faithfully follow from the extracted knowledge.",
                    output_contract=DikiwiPromptRegistry.IMPACT_CONTRACT,
                    draft_json=draft_json,
                    memory_context=memory_context,
                    review_focus=(
                        "Remove generic actions that are not grounded in the notes.",
                        "Prefer concrete next steps that compound the knowledge system.",
                    ),
                    context_sections=(
                        ("Zettelkasten Principles", zettels_desc or "No zettels available."),
                    ),
                ),
                temperature=0.5,
                memory=memory,
            )

            if not isinstance(result, dict):
                return []

            impacts = result.get("impacts", [])
            return [
                {
                    "type": imp.get("type", "action"),
                    "description": imp.get("description", ""),
                    "priority": imp.get("priority", "medium"),
                    "rationale": imp.get("rationale", ""),
                    "effort_estimate": imp.get("effort_estimate", "medium"),
                }
                for imp in impacts
                if isinstance(imp, dict)
            ]

        except Exception as exc:
            logger.debug("[DIKIWI] Impact generation failed: %s", exc)
            return []

    async def _write_impact_notes(
        self, impacts: list[dict], zettels: list[ZettelkastenNote], drop: "RainDrop"
    ) -> None:
        """Write impact notes to Obsidian based on Zettelkasten principles."""
        try:
            if not self.obsidian_writer:
                return

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            markdown = f"""# Impact: Actionable Outcomes

**Date**: {date_str}
**Source**: {drop.source}
**Stage**: DIKIWI IMPACT (LLM-Powered)

## Source Principles
"""
            for zettel in zettels[:3]:
                markdown += f"- [[{zettel.title}]]\n"

            markdown += """
## Proposed Actions

"""
            for imp in impacts:
                emoji = {
                    "innovation": "💡",
                    "opportunity": "🚀",
                    "action": "✅",
                    "research": "🔬",
                    "exploration": "🔭",
                }.get(imp.get("type", ""), "📌")

                priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                    imp.get("priority", ""), "⚪"
                )

                markdown += f"""### {emoji} {imp.get('type', 'action').title()}

{imp.get('description', '')}

**Priority**: {priority_emoji} {imp.get('priority', 'medium').title()}
**Effort**: {imp.get('effort_estimate', 'medium')}
**Rationale**: {imp.get('rationale', '')}

---

"""

            await self.obsidian_writer.write_note(
                title=f"Impact-LLM: {date_str} {drop.id[:8]}",
                markdown=markdown,
                source_url=f"aily://impact/{drop.id}",
            )

        except Exception as e:
            logger.warning("[DIKIWI] Failed to write impact notes: %s", e)

    def get_metrics(self) -> dict[str, Any]:
        """Get processing metrics."""
        return {
            "total_inputs": self._total_inputs,
            "successful_pipelines": self._successful_pipelines,
            "failed_pipelines": self._failed_pipelines,
            "success_rate": self._successful_pipelines / max(self._total_inputs, 1),
            "enabled": self.enabled,
            "mode": "llm-powered (Kimi)",
        }
