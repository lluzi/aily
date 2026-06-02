"""Morphological Analysis framework analyzer (Fritz Zwicky, 1940s).

Systematic exploration of all possible combinations of problem parameters.
Uses the "Zwicky Box" - an n-dimensional matrix to exhaustively search
for solutions.

Process:
1. Decompose problem into dimensions (parameters)
2. List options/values for each dimension
3. Generate all valid combinations
4. Evaluate each configuration
5. Select optimal solutions
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

from aily.sessions.reactor_scheduler import InnovationMethod, MethodResult
from aily.sessions.models import Proposal, ProposalType, ProposalStatus

logger = logging.getLogger(__name__)


class MorphologicalAnalyzer:
    """Zwicky Box morphological analysis analyzer."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def analyze(self, context: dict[str, Any]) -> MethodResult:
        """Apply morphological analysis methodology."""
        focus = context.get("focus_areas", ["general"])
        recent_insights = context.get("recent_insights", [])

        # Step 1: Decompose problem into dimensions
        dimensions = await self._identify_dimensions(focus, recent_insights)

        # Step 2: Generate configurations (sample of full space)
        configurations = self._generate_configurations(dimensions)

        # Step 3: Evaluate top configurations
        proposals = []
        for config in configurations[:10]:  # Limit evaluations
            try:
                proposal = await self._evaluate_configuration(config, focus)
                if proposal:
                    proposals.append(proposal)
            except Exception as e:
                logger.warning(f"Configuration evaluation failed: {e}")

        # Sort by score and take top
        proposals.sort(key=lambda p: p.confidence, reverse=True)
        proposals = proposals[:5]

        avg_confidence = sum(p.confidence for p in proposals) / len(proposals) if proposals else 0

        return MethodResult(
            method=InnovationMethod.MORPHOLOGICAL,
            proposals=proposals,
            confidence=avg_confidence,
            metadata={
                "dimensions": len(dimensions),
                "configurations_generated": len(configurations),
                "configurations_evaluated": min(len(configurations), 10),
            },
        )

    async def _identify_dimensions(
        self, focus: list[str], insights: list
    ) -> dict[str, list[str]]:
        """Identify problem dimensions and their options."""
        prompt = f"""For a morphological analysis (Zwicky Box), identify dimensions and options.

Focus areas: {', '.join(focus)}

Identify 3-4 key dimensions (parameters) for innovation in this domain.
For each dimension, provide 3-4 possible values/options.

Example dimensions for product innovation:
- Material: [plastic, metal, composite, biological]
- Power Source: [battery, solar, kinetic, wireless]
- Interface: [touch, voice, gesture, neural]

Generate dimensions and options as JSON:
{{
    "dimension_name_1": ["option1", "option2", "option3"],
    "dimension_name_2": ["optionA", "optionB", "optionC"],
    ...
}}"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "You are a morphological analysis expert. Define clear, orthogonal dimensions."},
                {"role": "user", "content": prompt},
            ])

            # Parse dimensions from response
            dimensions = {}
            for key, value in response.items():
                if isinstance(value, list) and len(value) >= 2:
                    dimensions[key] = value[:4]  # Max 4 options per dimension

            return dimensions if dimensions else self._default_dimensions()

        except Exception as e:
            logger.warning(f"Failed to identify dimensions: {e}")
            return self._default_dimensions()

    def _default_dimensions(self) -> dict[str, list[str]]:
        """Default dimensions if LLM fails."""
        return {
            "approach": ["incremental", "radical", "disruptive", "adjacent"],
            "technology": ["ai", "materials", "biotech", "energy"],
            "market": ["existing", "new", "niche", "mass"],
        }

    def _generate_configurations(
        self, dimensions: dict[str, list[str]]
    ) -> list[dict[str, str]]:
        """Generate all valid combinations of dimension options."""
        if not dimensions:
            return []

        keys = list(dimensions.keys())
        values = [dimensions[k] for k in keys]

        # Generate cartesian product
        configurations = []
        for combo in itertools.product(*values):
            config = dict(zip(keys, combo))
            configurations.append(config)

        return configurations

    async def _evaluate_configuration(
        self, config: dict[str, str], focus: list[str]
    ) -> Proposal | None:
        """Evaluate a specific configuration and create a proposal."""
        config_str = ", ".join(f"{k}={v}" for k, v in config.items())

        prompt = f"""Evaluate this morphological configuration as an innovation.

Configuration: {config_str}
Domain: {', '.join(focus)}

Analyze this specific combination:
1. What does this configuration represent?
2. What innovation does it enable?
3. What are the advantages?
4. What are the risks/challenges?
5. Rate: novelty (0-1), feasibility (0-1), impact (high/medium/low)

Format as JSON:
{{
    "title": "...",
    "description": "...",
    "advantages": ["...", "..."],
    "risks": ["...", "..."],
    "novelty": 0.7,
    "feasibility": 0.6,
    "impact": "medium"
}}"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "You are evaluating innovation configurations systematically."},
                {"role": "user", "content": prompt},
            ])

            # Calculate composite confidence score
            novelty = response.get("novelty", 0.5)
            feasibility = response.get("feasibility", 0.5)
            impact_map = {"high": 1.0, "medium": 0.6, "low": 0.3}
            impact = impact_map.get(response.get("impact", "medium"), 0.5)

            confidence = (novelty + feasibility + impact) / 3

            return Proposal(
                title=f"[Morph] {response.get('title', 'Config')}",
                content=response.get("description", ""),
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=confidence,
                metadata={
                    "configuration": config,
                    "framework": "Morphological Analysis",
                    "novelty": novelty,
                    "feasibility": feasibility,
                    "impact": response.get("impact", "medium"),
                    "advantages": response.get("advantages", []),
                    "risks": response.get("risks", []),
                },
            )
        except Exception as e:
            logger.warning(f"Configuration evaluation failed: {e}")
            return None
