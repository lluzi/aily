"""Insight Dam - the gating system for high-impact insights.

The dam holds back weak insights. Only content with enough
force (confidence, novelty, impact) breaks through.

Breakthrough triggers output flow to Feishu and Obsidian.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Optional

from aily.gating.reservoir import River
from aily.thinking.orchestrator import ThinkingOrchestrator
from aily.thinking.models import ThinkingResult, InsightPriority

logger = logging.getLogger(__name__)


class GateType(Enum):
    """Types of gates in the dam."""

    CONFIDENCE = auto()  # Minimum confidence threshold
    NOVELTY = auto()  # Must be novel enough
    IMPACT = auto()  # Must have actionable insights
    SYNTHESIS = auto()  # Must combine multiple perspectives


@dataclass
class GateThreshold:
    """Threshold configuration for a gate."""

    gate_type: GateType
    minimum: float  # 0.0 to 1.0
    weight: float = 1.0  # How much this gate matters

    def check(self, value: float) -> bool:
        """Check if value passes this gate."""
        return value >= self.minimum


@dataclass
class DamBreakthrough:
    """Record of content breaking through the dam."""

    id: str
    river_id: str
    result: ThinkingResult
    gates_passed: list[GateType]
    breakthrough_force: float  # How strongly it broke through
    created_at: datetime = field(default_factory=datetime.utcnow)
    output_channels: list[str] = field(default_factory=list)


class InsightDam:
    """The gating system for Aily outputs.

    The dam ensures only high-quality, impactful insights
    flow out to users. Low-quality content is held back.

    Gates:
    - Confidence gate: Result confidence > threshold
    - Novelty gate: Content is sufficiently new
    - Impact gate: Has actionable recommendations
    - Synthesis gate: Combines multiple frameworks
    """

    def __init__(
        self,
        orchestrator: ThinkingOrchestrator,
        output_handler: Any | None = None,
        confidence_threshold: float = 0.6,
        novelty_threshold: float = 0.3,
        impact_threshold: float = 0.5,
    ) -> None:
        """Initialize the dam with gate thresholds.

        Args:
            orchestrator: For running ARMY analysis
            output_handler: For delivering outputs
            confidence_threshold: Min confidence (0-1)
            novelty_threshold: Min novelty (0-1)
            impact_threshold: Min impact score (0-1)
        """
        self.orchestrator = orchestrator
        self.output_handler = output_handler

        # Define gates
        self.gates: list[GateThreshold] = [
            GateThreshold(GateType.CONFIDENCE, confidence_threshold, weight=2.0),
            GateThreshold(GateType.NOVELTY, novelty_threshold, weight=1.0),
            GateThreshold(GateType.IMPACT, impact_threshold, weight=1.5),
            GateThreshold(GateType.SYNTHESIS, 0.5, weight=1.0),
        ]

        self.breakthroughs: list[DamBreakthrough] = []
        self._held_back: list[River] = []  # Rivers that didn't break through

    async def receive_river(self, river: River) -> Optional[DamBreakthrough]:
        """Receive a river and attempt breakthrough.

        Args:
            river: River from reservoir with content

        Returns:
            DamBreakthrough if gates passed, None if held back
        """
        logger.info(
            "[Dam] River %s approaching dam (momentum: %.2f)",
            river.id[:12],
            river.momentum,
        )

        # Run ARMY analysis on river content
        result = await self._analyze_content(river)

        if not result:
            logger.warning("[Dam] Analysis failed for river %s", river.id[:12])
            self._held_back.append(river)
            return None

        # Check each gate
        gates_passed = []
        gate_scores = []

        # Confidence gate
        if self._check_confidence_gate(result):
            gates_passed.append(GateType.CONFIDENCE)
            gate_scores.append(result.confidence_score * 2.0)  # Weighted

        # Novelty gate
        novelty = river.metadata.get("novelty_score", 0.5)
        if self._check_novelty_gate(novelty):
            gates_passed.append(GateType.NOVELTY)
            gate_scores.append(novelty)

        # Impact gate
        impact = self._calculate_impact(result)
        if self._check_impact_gate(impact):
            gates_passed.append(GateType.IMPACT)
            gate_scores.append(impact * 1.5)  # Weighted

        # Synthesis gate
        if self._check_synthesis_gate(result):
            gates_passed.append(GateType.SYNTHESIS)
            gate_scores.append(0.5)

        # Calculate breakthrough force
        breakthrough_force = sum(gate_scores) / sum(g.weight for g in self.gates)

        # Determine if breakthrough occurs
        # Need at least 3 gates passed AND sufficient force
        if len(gates_passed) >= 3 and breakthrough_force >= 0.6:
            breakthrough = await self._create_breakthrough(
                river, result, gates_passed, breakthrough_force
            )
            return breakthrough
        else:
            logger.info(
                "[Dam] River %s held back (gates: %d/4, force: %.2f)",
                river.id[:12],
                len(gates_passed),
                breakthrough_force,
            )
            self._held_back.append(river)
            return None

    async def _analyze_content(self, river: River) -> Optional[ThinkingResult]:
        """Run ARMY OF TOP MINDS analysis on river content."""
        from aily.thinking.models import KnowledgePayload

        try:
            payload = KnowledgePayload(
                content=river.content,
                source_url=river.metadata.get("source_url"),
                source_title=river.metadata.get("source_title"),
                metadata={
                    "keywords": river.metadata.get("keywords", []),
                    "context_nodes": river.metadata.get("context_nodes", []),
                },
            )

            result = await self.orchestrator.think(payload)

            logger.info(
                "[Dam] Analysis complete: %d frameworks, %d insights, %.0f%% confidence",
                len(result.framework_insights),
                len(result.top_insights),
                result.confidence_score * 100,
            )

            return result

        except Exception as e:
            logger.error("[Dam] Analysis failed: %s", e)
            return None

    def _check_confidence_gate(self, result: ThinkingResult) -> bool:
        """Check if result confidence passes gate."""
        gate = next((g for g in self.gates if g.gate_type == GateType.CONFIDENCE), None)
        if not gate:
            return True
        passed = gate.check(result.confidence_score)
        logger.debug("[Dam] Confidence gate: %.2f >= %.2f = %s",
                    result.confidence_score, gate.minimum, passed)
        return passed

    def _check_novelty_gate(self, novelty: float) -> bool:
        """Check if novelty passes gate."""
        gate = next((g for g in self.gates if g.gate_type == GateType.NOVELTY), None)
        if not gate:
            return True
        passed = gate.check(novelty)
        logger.debug("[Dam] Novelty gate: %.2f >= %.2f = %s",
                    novelty, gate.minimum, passed)
        return passed

    def _check_impact_gate(self, impact: float) -> bool:
        """Check if impact passes gate."""
        gate = next((g for g in self.gates if g.gate_type == GateType.IMPACT), None)
        if not gate:
            return True
        passed = gate.check(impact)
        logger.debug("[Dam] Impact gate: %.2f >= %.2f = %s",
                    impact, gate.minimum, passed)
        return passed

    def _check_synthesis_gate(self, result: ThinkingResult) -> bool:
        """Check if synthesis quality passes gate."""
        # Must have insights from multiple frameworks
        frameworks_used = len(set(
            fi.framework_type for fi in result.framework_insights
        ))
        passed = frameworks_used >= 2 and len(result.synthesized_insights) >= 1
        logger.debug("[Dam] Synthesis gate: %d frameworks, %d insights = %s",
                    frameworks_used, len(result.synthesized_insights), passed)
        return passed

    def _calculate_impact(self, result: ThinkingResult) -> float:
        """Calculate impact score from result."""
        if not result.top_insights:
            return 0.0

        # Factors:
        # - Priority of insights (CRITICAL=1.0, HIGH=0.75, etc)
        # - Number of action items
        # - Confidence

        priority_scores = {
            InsightPriority.CRITICAL: 1.0,
            InsightPriority.HIGH: 0.75,
            InsightPriority.MEDIUM: 0.5,
            InsightPriority.LOW: 0.25,
        }

        avg_priority = sum(
            priority_scores.get(i.priority, 0.5) for i in result.top_insights
        ) / len(result.top_insights)

        action_count = sum(
            len(i.action_items) for i in result.top_insights
        )

        # Normalize action count (cap at 10 actions = full score)
        action_score = min(action_count / 5, 1.0)

        impact = (avg_priority * 0.5) + (action_score * 0.3) + (result.confidence_score * 0.2)
        return impact

    async def _create_breakthrough(
        self,
        river: River,
        result: ThinkingResult,
        gates_passed: list[GateType],
        force: float,
    ) -> DamBreakthrough:
        """Create a breakthrough and trigger output."""
        breakthrough = DamBreakthrough(
            id=f"bt_{river.id}_{int(datetime.utcnow().timestamp())}",
            river_id=river.id,
            result=result,
            gates_passed=gates_passed,
            breakthrough_force=force,
        )

        self.breakthroughs.append(breakthrough)

        logger.info(
            "[Dam] 🌊 BREAKTHROUGH! River %s breached dam (force: %.2f, gates: %s)",
            river.id[:12],
            force,
            ", ".join(g.name for g in gates_passed),
        )

        # Trigger output flow
        await self._flow_outputs(breakthrough, river)

        return breakthrough

    async def _flow_outputs(self, breakthrough: DamBreakthrough, river: River) -> None:
        """Flow breakthrough content to output channels."""
        if not self.output_handler:
            logger.warning("[Dam] No output handler configured")
            return

        try:
            # Prepare delivery options
            delivery_options = {
                "output_format": "both",
                "open_id": river.metadata.get("open_id", ""),
                "message_id": river.metadata.get("message_id", ""),
                "breakthrough_force": breakthrough.breakthrough_force,
                "gates_passed": [g.name for g in breakthrough.gates_passed],
            }

            # Deliver
            result = await self.output_handler.deliver(
                breakthrough.result,
                delivery_options,
            )

            # Record output channels
            if result.obsidian_success:
                breakthrough.output_channels.append("obsidian")
            if result.feishu_success:
                breakthrough.output_channels.append("feishu")

            logger.info(
                "[Dam] Output flowed to: %s",
                ", ".join(breakthrough.output_channels) if breakthrough.output_channels else "NOWHERE"
            )

        except Exception as e:
            logger.error("[Dam] Output flow failed: %s", e)

    def get_stats(self) -> dict[str, Any]:
        """Get dam statistics."""
        return {
            "total_breakthroughs": len(self.breakthroughs),
            "held_back": len(self._held_back),
            "avg_force": sum(b.breakthrough_force for b in self.breakthroughs) / max(len(self.breakthroughs), 1),
            "gates": {
                g.gate_type.name: {"min": g.minimum, "weight": g.weight}
                for g in self.gates
            },
        }
