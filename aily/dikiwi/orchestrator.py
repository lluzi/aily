"""DIKIWI orchestrator - coordinates stages via event bus.

This is the platform layer that enables:
- Event-driven stage coordination
- Institutional review gates (门下省, CVO)
- Audit trail through memorials
- Three-layer architecture (model/tool/platform)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from aily.dikiwi.events import (
    EventBus,
    InMemoryEventBus,
    StageCompletedEvent,
    StageRejectedEvent,
    ContentPromotedEvent,
    InsightDiscoveredEvent,
    WisdomSynthesizedEvent,
    ImpactGeneratedEvent,
    GateDecisionEvent,
)
from aily.dikiwi.gates import CVOGate, MenxiaGate, ReviewDecisionType
from aily.dikiwi.stages import (
    DikiwiStage,
    StageContext,
    StageState,
    StageStateMachine,
    can_transition,
)
from aily.ui.events import emit_ui_event
from aily.ui.telemetry import ui_telemetry_scope

if TYPE_CHECKING:
    from aily.dikiwi.agents.base import DikiwiAgent
    from aily.dikiwi.agents.context import AgentContext
    from aily.llm.client import LLMClient
    from aily.graph.db import GraphDB

logger = logging.getLogger(__name__)


def _workload_for_stage(stage: DikiwiStage) -> str:
    return f"dikiwi.{stage.name.lower()}"


@dataclass
class PipelineConfig:
    """Configuration for DIKIWI pipeline."""

    # Quality gates
    menxia_quality_threshold: float = 0.6  # Min quality to pass 门下省
    cvo_ttl_hours: int = 24  # Auto-approve after TTL

    # Retry limits
    max_rejections: int = 3  # Max times content can be rejected

    # Gate decisions
    require_cvo_for_impact: bool = True  # Require human approval for WISDOM->IMPACT


@dataclass
class ProcessingPipeline:
    """A running pipeline processing content through DIKIWI."""

    pipeline_id: str
    correlation_id: str
    context: StageContext
    config: PipelineConfig
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    status: str = "running"  # running, completed, failed, rejected

    def __post_init__(self) -> None:
        self._completion_event: asyncio.Event = asyncio.Event()
        self._inflight_tasks: list[asyncio.Task] = []


class DikiwiOrchestrator:
    """Orchestrates the DIKIWI multi-agent system.

    Responsibilities (Platform Layer):
    - Event bus coordination
    - Stage state machine enforcement
    - Gate review scheduling
    - Memorial creation
    - Metrics collection

    Does NOT do (Model Layer):
    - Content classification
    - Pattern detection
    - Wisdom synthesis

    Does NOT do (Tool Layer):
    - LLM calls
    - GraphDB operations
    - File I/O
    """

    def __init__(
        self,
        llm_client: LLMClient,
        graph_db: GraphDB,
        event_bus: EventBus | None = None,
        config: PipelineConfig | None = None,
        llm_client_resolver: Callable[[DikiwiStage], Any] | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.graph_db = graph_db
        self.event_bus = event_bus or InMemoryEventBus()
        self.config = config or PipelineConfig()
        self.llm_client_resolver = llm_client_resolver

        # State management
        self.state_machine = StageStateMachine(
            max_rejections=self.config.max_rejections,
        )
        self._pipelines: dict[str, ProcessingPipeline] = {}
        self._agent_contexts: dict[str, AgentContext] = {}
        self.agent_registry: dict[DikiwiStage, DikiwiAgent] = {}

        # Gates
        self.menxia_gate = MenxiaGate(
            llm_client=llm_client,
            quality_threshold=self.config.menxia_quality_threshold,
        )
        self.cvo_gate = CVOGate(ttl_hours=self.config.cvo_ttl_hours)

        # Metrics
        self._metrics = {
            "pipelines_started": 0,
            "pipelines_completed": 0,
            "pipelines_failed": 0,
            "stage_rejections": 0,
        }

        # Setup event handlers
        self._setup_event_handlers()

    def _setup_event_handlers(self) -> None:
        """Subscribe to events for coordination."""
        # Stage completion triggers gate review or next stage
        self.event_bus.subscribe(StageCompletedEvent, self._on_stage_completed)

        # Stage rejection triggers fallback
        self.event_bus.subscribe(StageRejectedEvent, self._on_stage_rejected)

        # Gate decisions trigger promotion or rejection
        self.event_bus.subscribe(GateDecisionEvent, self._on_gate_decision)

        logger.info("DikiwiOrchestrator event handlers registered")

    def register_agent(self, stage: DikiwiStage, agent: DikiwiAgent) -> None:
        """Register a stage agent."""
        self.agent_registry[stage] = agent
        logger.info("Registered agent for stage %s", stage.name)

    async def run_pipeline(
        self,
        agent_ctx: AgentContext,
    ) -> ProcessingPipeline:
        """Start a DIKIWI pipeline using a pre-built agent context.

        This is the adapter entry-point used by DikiwiMind.
        """
        from aily.dikiwi.stages import StageContext

        context = StageContext(
            context_id=agent_ctx.pipeline_id,
            correlation_id=agent_ctx.correlation_id or agent_ctx.pipeline_id,
            content_id=agent_ctx.drop.id,
            source=agent_ctx.drop.source,
        )
        self.state_machine._contexts[context.context_id] = context

        pipeline = ProcessingPipeline(
            pipeline_id=context.context_id,
            correlation_id=context.correlation_id,
            context=context,
            config=self.config,
        )
        self._pipelines[pipeline.pipeline_id] = pipeline
        self._agent_contexts[pipeline.pipeline_id] = agent_ctx
        self._metrics["pipelines_started"] += 1

        logger.info(
            "[DIKIWI] Pipeline %s started for content %s",
            pipeline.pipeline_id,
            agent_ctx.drop.id,
        )
        await emit_ui_event(
            "pipeline_started",
            pipeline_id=pipeline.pipeline_id,
            correlation_id=pipeline.correlation_id,
            source_id=agent_ctx.drop.id,
            source=agent_ctx.drop.source,
            upload_id=agent_ctx.drop.metadata.get("ui_upload_id"),
            filename=agent_ctx.drop.metadata.get("filename"),
        )

        # Execute DATA agent first, then emit stage completed to trigger downstream
        agent = self.agent_registry.get(DikiwiStage.DATA)
        if agent:
            try:
                if self.llm_client_resolver is not None:
                    agent_ctx.llm_client = self.llm_client_resolver(_workload_for_stage(DikiwiStage.DATA))
                await emit_ui_event(
                    "stage_started",
                    pipeline_id=pipeline.pipeline_id,
                    correlation_id=pipeline.correlation_id,
                    stage=DikiwiStage.DATA.name,
                    upload_id=agent_ctx.drop.metadata.get("ui_upload_id"),
                    provider=getattr(agent_ctx.llm_client, "_provider_name", lambda: "unknown")(),
                    model=getattr(agent_ctx.llm_client, "model", ""),
                )
                with ui_telemetry_scope(
                    pipeline_id=pipeline.pipeline_id,
                    upload_id=agent_ctx.drop.metadata.get("ui_upload_id"),
                    stage=DikiwiStage.DATA.name,
                    workload=f"dikiwi.{DikiwiStage.DATA.name.lower()}",
                ):
                    result = await agent.execute(agent_ctx)
                agent_ctx.stage_results.append(result)
                await emit_ui_event(
                    "stage_completed" if result.success else "stage_failed",
                    pipeline_id=pipeline.pipeline_id,
                    correlation_id=pipeline.correlation_id,
                    stage=DikiwiStage.DATA.name,
                    upload_id=agent_ctx.drop.metadata.get("ui_upload_id"),
                    success=result.success,
                    data_keys=sorted(list(result.data.keys())) if isinstance(result.data, dict) else [],
                    error=result.error_message or "",
                )
                if not result.success:
                    await self._fail_pipeline(pipeline, result.error_message or "DATA stage failed")
                    return pipeline
            except Exception as exc:
                logger.exception("[DIKIWI] Agent execution failed for DATA")
                await self._fail_pipeline(pipeline, str(exc))
                return pipeline

        await self.event_bus.publish(
            StageCompletedEvent(
                correlation_id=pipeline.correlation_id,
                stage=DikiwiStage.DATA,
                output_content_ids=[agent_ctx.drop.id],
            )
        )

        # Wait for full pipeline completion (all event-driven stages)
        try:
            await asyncio.wait_for(pipeline._completion_event.wait(), timeout=300.0)
        except asyncio.TimeoutError:
            logger.error("[DIKIWI] Pipeline %s timed out waiting for completion", pipeline.pipeline_id)
            for task in pipeline._inflight_tasks:
                if not task.done():
                    task.cancel()
            await self._fail_pipeline(pipeline, "Pipeline completion timeout")

        return pipeline

    async def start_pipeline(
        self,
        content_id: str,
        source: str,
    ) -> ProcessingPipeline:
        """Start a new DIKIWI pipeline for content.

        Args:
            content_id: Unique identifier for the content
            source: Where the content came from

        Returns:
            Pipeline tracking object
        """
        # Create stage context
        context = self.state_machine.create_context(content_id, source)

        # Create pipeline
        pipeline = ProcessingPipeline(
            pipeline_id=context.context_id,
            correlation_id=context.correlation_id,
            context=context,
            config=self.config,
        )
        self._pipelines[pipeline.pipeline_id] = pipeline
        self._metrics["pipelines_started"] += 1

        logger.info(
            "[DIKIWI] Pipeline %s started for content %s",
            pipeline.pipeline_id,
            content_id,
        )

        # Emit initial event to trigger DATA agent execution
        await self.event_bus.publish(
            StageCompletedEvent(
                correlation_id=pipeline.correlation_id,
                stage=DikiwiStage.DATA,
                output_content_ids=[content_id],
            )
        )

        return pipeline

    async def _on_stage_completed(self, event: StageCompletedEvent) -> None:
        """Handle stage completion - trigger gate or next stage."""
        if not event.stage:
            return

        # Get pipeline
        pipeline = self._get_pipeline_by_correlation(event.correlation_id)
        if not pipeline:
            logger.warning("Pipeline not found for correlation %s", event.correlation_id[:8])
            return

        # Mark stage completed in state machine
        self.state_machine.complete_stage(pipeline.context)

        # Determine next step based on stage
        if event.stage == DikiwiStage.DATA:
            # Data complete → auto-promote to INFORMATION
            await self._promote_to_stage(pipeline, DikiwiStage.INFORMATION)

        elif event.stage == DikiwiStage.INFORMATION:
            # Information complete → 门下省 review for KNOWLEDGE promotion
            await self._schedule_menxia_review(pipeline, event)

        elif event.stage == DikiwiStage.KNOWLEDGE:
            ctx = self._agent_contexts.get(pipeline.pipeline_id)
            knowledge_result = None
            if ctx:
                knowledge_result = next(
                    (
                        result
                        for result in reversed(ctx.stage_results)
                        if getattr(result.stage, "name", None) == DikiwiStage.KNOWLEDGE.name
                    ),
                    None,
                )
            if knowledge_result and not knowledge_result.data.get("network_synthesis_triggered", True):
                logger.info(
                    "[DIKIWI] Pipeline %s stopped at KNOWLEDGE: %s",
                    pipeline.pipeline_id,
                    knowledge_result.data.get("graph_change_assessment", "network threshold not reached"),
                )
                await self._complete_pipeline(pipeline)
                return
            # Knowledge complete with a synthesis-grade subgraph -> INSIGHT
            await self._promote_to_stage(pipeline, DikiwiStage.INSIGHT)

        elif event.stage == DikiwiStage.INSIGHT:
            # Insight complete → auto-promote to WISDOM
            await self._promote_to_stage(pipeline, DikiwiStage.WISDOM)

        elif event.stage == DikiwiStage.WISDOM:
            # Wisdom complete → CVO review for IMPACT promotion
            await self._schedule_cvo_review(pipeline, event)

        elif event.stage == DikiwiStage.IMPACT:
            # Pipeline complete
            await self._complete_pipeline(pipeline)

    async def _on_stage_rejected(self, event: StageRejectedEvent) -> None:
        """Handle stage rejection - send content back."""
        pipeline = self._get_pipeline_by_correlation(event.correlation_id)
        if not pipeline:
            return

        # Record rejection in state machine
        if event.stage:
            self.state_machine.reject_stage(pipeline.context, event.reason)

        self._metrics["stage_rejections"] += 1

        # Check max rejections
        if not pipeline.context.can_retry(
            event.stage or DikiwiStage.INFORMATION,
            self.config.max_rejections,
        ):
            logger.error(
                "Pipeline %s failed: max rejections reached",
                pipeline.pipeline_id,
            )
            await self._fail_pipeline(pipeline, "Max rejections reached")
            return

        # Send back to specified stage
        if event.send_back_to:
            await self._promote_to_stage(pipeline, event.send_back_to, is_rejection=True)

    async def _on_gate_decision(self, event: GateDecisionEvent) -> None:
        """Handle gate decision - approve or reject."""
        pipeline = self._get_pipeline_by_correlation(event.correlation_id)
        if not pipeline:
            return

        if event.decision == "approve":
            # Determine next stage based on current stage
            next_stage_map = {
                "menxia": DikiwiStage.KNOWLEDGE,
                "cvo": DikiwiStage.IMPACT,
            }
            next_stage = next_stage_map.get(event.gate_name)
            if next_stage:
                await self._promote_to_stage(pipeline, next_stage)

        elif event.decision == "reject":
            # Send back
            rejection_map = {
                "menxia": DikiwiStage.INFORMATION,
                "cvo": DikiwiStage.INSIGHT,
            }
            send_back_to = rejection_map.get(event.gate_name)
            if send_back_to:
                await self.event_bus.publish(
                    StageRejectedEvent(
                        correlation_id=event.correlation_id,
                        rejected_by=event.gate_name,
                        reason=event.reasoning,
                        send_back_to=send_back_to,
                    )
                )

    async def _schedule_menxia_review(
        self,
        pipeline: ProcessingPipeline,
        event: StageCompletedEvent,
    ) -> None:
        """Schedule 门下省 review for INFORMATION → KNOWLEDGE transition."""
        logger.info(
            "[DIKIWI] Scheduling 门下省 review for pipeline %s",
            pipeline.pipeline_id,
        )

        # Build review content from INFORMATION stage result
        ctx = self._agent_contexts.get(pipeline.pipeline_id)
        info_result = None
        if ctx:
            info_result = next(
                (
                    result
                    for result in reversed(ctx.stage_results)
                    if getattr(result.stage, "name", None) == DikiwiStage.INFORMATION.name
                ),
                None,
            )

        if info_result and info_result.data:
            info_nodes = info_result.data.get("information_nodes", [])
            content_parts: list[str] = []
            for node in info_nodes[:10]:
                node_content = getattr(node, "content", str(node))
                node_domain = getattr(node, "domain", "general")
                content_parts.append(f"[{node_domain}] {node_content}")
            content = "\n".join(content_parts) if content_parts else pipeline.context.content_id
            metadata = {
                "tags": info_result.data.get("keywords", []),
                "classification": info_result.data.get("domain", "general"),
            }
        else:
            content = pipeline.context.content_id
            metadata = {}

        decision = await self.menxia_gate.review(content, metadata)

        if decision.decision in (ReviewDecisionType.APPROVE, ReviewDecisionType.MODIFY):
            await self.event_bus.publish(
                GateDecisionEvent(
                    correlation_id=pipeline.correlation_id,
                    gate_name="menxia",
                    decision="approve",
                    content_ids=event.output_content_ids,
                    requires_human=False,
                    reasoning=decision.reason,
                )
            )
        else:
            await self.event_bus.publish(
                GateDecisionEvent(
                    correlation_id=pipeline.correlation_id,
                    gate_name="menxia",
                    decision="reject",
                    content_ids=event.output_content_ids,
                    requires_human=False,
                    reasoning=decision.reason,
                )
            )

    async def _schedule_cvo_review(
        self,
        pipeline: ProcessingPipeline,
        event: StageCompletedEvent,
    ) -> None:
        """Schedule CVO review for WISDOM → IMPACT transition."""
        logger.info(
            "[DIKIWI] Scheduling CVO review for pipeline %s",
            pipeline.pipeline_id,
        )

        # Build wisdom summary from WISDOM stage result
        ctx = self._agent_contexts.get(pipeline.pipeline_id)
        wisdom_summary = ""
        if ctx:
            wisdom_result = next(
                (
                    result
                    for result in reversed(ctx.stage_results)
                    if getattr(result.stage, "name", None) == DikiwiStage.WISDOM.name
                ),
                None,
            )
            if wisdom_result and wisdom_result.data:
                zettels = wisdom_result.data.get("zettels", [])
                wisdom_summary = "; ".join(
                    getattr(z, "title", str(z))[:100] for z in zettels[:5]
                )

        approval_id = f"cvo_{pipeline.pipeline_id}"
        await self.cvo_gate.request_approval(
            approval_id=approval_id,
            content_id=pipeline.context.content_id,
            content_preview=pipeline.context.source or "",
            wisdom_summary=wisdom_summary,
            impact_proposal={},
        )

        if self.config.require_cvo_for_impact:
            # Queue for human review with TTL
            await self.event_bus.publish(
                GateDecisionEvent(
                    correlation_id=pipeline.correlation_id,
                    gate_name="cvo",
                    decision="pending",
                    content_ids=event.output_content_ids,
                    requires_human=True,
                )
            )

            # Start TTL timer
            asyncio.create_task(
                self._cvo_ttl_timer(pipeline, event.output_content_ids, approval_id)
            )
        else:
            # Auto-approve via CVO gate
            self.cvo_gate.approve(
                approval_id,
                approved_by="system",
                reasoning="Auto-approved",
            )
            await self._promote_to_stage(pipeline, DikiwiStage.IMPACT)

    async def _cvo_ttl_timer(
        self,
        pipeline: ProcessingPipeline,
        content_ids: list[str],
        approval_id: str,
    ) -> None:
        """Auto-approve CVO gate after TTL expires."""
        await asyncio.sleep(self.config.cvo_ttl_hours * 3600)

        # Check if still pending
        if pipeline.status == "running" and pipeline.context.current_stage == DikiwiStage.WISDOM:
            logger.info(
                "[DIKIWI] CVO TTL expired for pipeline %s, auto-approving",
                pipeline.pipeline_id,
            )
            self.cvo_gate.approve(
                approval_id,
                approved_by="system",
                reasoning=f"Auto-approved after TTL ({self.config.cvo_ttl_hours}h)",
            )
            await self.event_bus.publish(
                GateDecisionEvent(
                    correlation_id=pipeline.correlation_id,
                    gate_name="cvo",
                    decision="approve",
                    content_ids=content_ids,
                    reasoning=f"Auto-approved after TTL ({self.config.cvo_ttl_hours}h)",
                )
            )

    async def _promote_to_stage(
        self,
        pipeline: ProcessingPipeline,
        to_stage: DikiwiStage,
        is_rejection: bool = False,
    ) -> None:
        """Promote content to next stage and dispatch the stage agent."""
        from_stage = pipeline.context.current_stage
        success, message = self.state_machine.transition(pipeline.context, to_stage)

        if not success:
            logger.error(
                "[DIKIWI] Transition failed for pipeline %s: %s",
                pipeline.pipeline_id,
                message,
            )
            await self._fail_pipeline(pipeline, message)
            return

        # Emit promotion event
        await self.event_bus.publish(
            ContentPromotedEvent(
                correlation_id=pipeline.correlation_id,
                from_stage=from_stage,
                to_stage=to_stage,
                gate_decision="rejected_back" if is_rejection else "approved",
            )
        )

        logger.info(
            "[DIKIWI] Pipeline %s promoted to %s %s",
            pipeline.pipeline_id,
            to_stage.name,
            f"(rejection loop)" if is_rejection else "",
        )

        # Dispatch agent for the target stage
        agent = self.agent_registry.get(to_stage)
        if agent:
            ctx = self._agent_contexts.get(pipeline.pipeline_id)
            if ctx:
                async def _execute_agent() -> bool:
                    if self.llm_client_resolver is not None:
                        ctx.llm_client = self.llm_client_resolver(_workload_for_stage(to_stage))
                    await emit_ui_event(
                        "stage_started",
                        pipeline_id=pipeline.pipeline_id,
                        correlation_id=pipeline.correlation_id,
                        stage=to_stage.name,
                        upload_id=ctx.drop.metadata.get("ui_upload_id"),
                        provider=getattr(ctx.llm_client, "_provider_name", lambda: "unknown")(),
                        model=getattr(ctx.llm_client, "model", ""),
                    )
                    with ui_telemetry_scope(
                        pipeline_id=pipeline.pipeline_id,
                        upload_id=ctx.drop.metadata.get("ui_upload_id"),
                        stage=to_stage.name,
                        workload=_workload_for_stage(to_stage),
                    ):
                        result = await agent.execute(ctx)
                    ctx.stage_results.append(result)
                    await emit_ui_event(
                        "stage_completed" if result.success else "stage_failed",
                        pipeline_id=pipeline.pipeline_id,
                        correlation_id=pipeline.correlation_id,
                        stage=to_stage.name,
                        upload_id=ctx.drop.metadata.get("ui_upload_id"),
                        success=result.success,
                        data_keys=sorted(list(result.data.keys())) if isinstance(result.data, dict) else [],
                        error=result.error_message or "",
                    )
                    if not result.success:
                        await self._fail_pipeline(pipeline, result.error_message or f"{to_stage.name} stage failed")
                        return False
                    return True

                task: asyncio.Task = asyncio.create_task(_execute_agent())
                pipeline._inflight_tasks.append(task)
                try:
                    success = await task
                    if not success:
                        return
                except asyncio.CancelledError:
                    logger.warning(
                        "[DIKIWI] Agent task for %s cancelled in pipeline %s",
                        to_stage.name, pipeline.pipeline_id,
                    )
                    return
                except Exception as exc:
                    logger.exception("[DIKIWI] Agent execution failed for %s", to_stage.name)
                    await self._fail_pipeline(pipeline, str(exc))
                    return
                finally:
                    if task in pipeline._inflight_tasks:
                        pipeline._inflight_tasks.remove(task)
            else:
                logger.warning("[DIKIWI] No agent context found for pipeline %s", pipeline.pipeline_id)

        # Emit stage completed to trigger next coordination step
        await self.event_bus.publish(
            StageCompletedEvent(
                correlation_id=pipeline.correlation_id,
                stage=to_stage,
                output_content_ids=[pipeline.context.content_id],
            )
        )

    async def _complete_pipeline(self, pipeline: ProcessingPipeline) -> None:
        """Mark pipeline as completed."""
        pipeline.status = "completed"
        pipeline.completed_at = datetime.now(timezone.utc)
        self._metrics["pipelines_completed"] += 1
        pipeline._completion_event.set()

        logger.info(
            "[DIKIWI] Pipeline %s completed in %.2fs",
            pipeline.pipeline_id,
            (pipeline.completed_at - pipeline.started_at).total_seconds(),
        )
        ctx = self._agent_contexts.get(pipeline.pipeline_id)
        await emit_ui_event(
            "pipeline_completed",
            pipeline_id=pipeline.pipeline_id,
            correlation_id=pipeline.correlation_id,
            upload_id=ctx.drop.metadata.get("ui_upload_id") if ctx else None,
            duration_ms=round((pipeline.completed_at - pipeline.started_at).total_seconds() * 1000, 2),
        )

    async def _fail_pipeline(self, pipeline: ProcessingPipeline, reason: str) -> None:
        """Mark pipeline as failed."""
        pipeline.status = "failed"
        pipeline.completed_at = datetime.now(timezone.utc)
        self._metrics["pipelines_failed"] += 1
        pipeline._completion_event.set()

        logger.error(
            "[DIKIWI] Pipeline %s failed: %s",
            pipeline.pipeline_id,
            reason,
        )
        ctx = self._agent_contexts.get(pipeline.pipeline_id)
        await emit_ui_event(
            "pipeline_failed",
            pipeline_id=pipeline.pipeline_id,
            correlation_id=pipeline.correlation_id,
            upload_id=ctx.drop.metadata.get("ui_upload_id") if ctx else None,
            error=reason,
        )

    def _get_pipeline_by_correlation(self, correlation_id: str) -> ProcessingPipeline | None:
        """Find pipeline by correlation ID."""
        for pipeline in self._pipelines.values():
            if pipeline.correlation_id == correlation_id:
                return pipeline
        return None

    def _get_pipeline_id(self, correlation_id: str) -> str:
        """Get pipeline ID from correlation ID."""
        pipeline = self._get_pipeline_by_correlation(correlation_id)
        return pipeline.pipeline_id if pipeline else ""

    def get_metrics(self) -> dict[str, Any]:
        """Get orchestrator metrics."""
        return {
            **self._metrics,
            "active_pipelines": sum(
                1 for p in self._pipelines.values() if p.status == "running"
            ),
        }

    async def close(self) -> None:
        """Close the orchestrator."""
        await self.event_bus.close()
        logger.info("DikiwiOrchestrator closed")
