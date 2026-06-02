"""Blue Ocean Strategy framework analyzer (Kim & Mauborgne, 2005).

Create uncontested market space (blue ocean) instead of competing
in existing markets (red ocean). Focus on Value Innovation:
simultaneous differentiation AND low cost.

Key Tools:
- Four Actions Framework: Eliminate, Reduce, Raise, Create
- Strategy Canvas: Visualize value curves
- Six Paths Framework: Cross industry, strategic groups, etc.
"""

from __future__ import annotations

import logging
from typing import Any

from aily.sessions.reactor_scheduler import InnovationMethod, MethodResult
from aily.sessions.models import Proposal, ProposalType, ProposalStatus

logger = logging.getLogger(__name__)


FOUR_ACTIONS = {
    "eliminate": {
        "question": "Which factors that the industry takes for granted should be eliminated?",
        "prompt": "What are competitors doing that's no longer valuable to customers? What can you completely remove?",
    },
    "reduce": {
        "question": "Which factors should be reduced well below industry standard?",
        "prompt": "Where are competitors over-delivering? What can be simplified or minimized?",
    },
    "raise": {
        "question": "Which factors should be raised well above industry standard?",
        "prompt": "What do customers really value that competitors are neglecting? Where can you excel?",
    },
    "create": {
        "question": "Which factors should be created that the industry has never offered?",
        "prompt": "What unmet needs exist? What new value can you create that doesn't exist in the industry?",
    },
}

SIX_PATHS = [
    {"path": "cross_industry", "description": "Look at alternative industries"},
    {"path": "strategic_groups", "description": "Look at strategic groups within industry"},
    {"path": "buyer_chain", "description": "Redefine the buyer group"},
    {"path": "complementary", "description": "Look at complementary products/services"},
    {"path": "functional_emotional", "description": "Redefine functional/emotional orientation"},
    {"path": "time", "description": "Look at trends over time"},
]


class BlueOceanAnalyzer:
    """Blue Ocean Strategy analyzer for market creation."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def analyze(self, context: dict[str, Any]) -> MethodResult:
        """Apply Blue Ocean Strategy methodology."""
        focus = context.get("focus_areas", ["general"])
        recent_insights = context.get("recent_insights", [])

        proposals = []

        # Four Actions Framework
        for action_key, action in FOUR_ACTIONS.items():
            try:
                proposal = await self._analyze_four_actions(
                    action_key, action, focus, recent_insights
                )
                if proposal:
                    proposals.append(proposal)
            except Exception as e:
                logger.warning(f"Four Actions {action_key} failed: {e}")

        # Six Paths exploration
        for path_info in SIX_PATHS[:3]:  # Limit to first 3 paths
            try:
                proposal = await self._explore_path(path_info, focus)
                if proposal:
                    proposals.append(proposal)
            except Exception as e:
                logger.warning(f"Six Paths {path_info['path']} failed: {e}")

        # Strategy Canvas synthesis
        try:
            synthesis = await self._create_strategy_canvas(proposals, focus)
            if synthesis:
                proposals.append(synthesis)
        except Exception as e:
            logger.warning(f"Strategy canvas failed: {e}")

        avg_confidence = sum(p.confidence for p in proposals) / len(proposals) if proposals else 0

        return MethodResult(
            method=InnovationMethod.BLUE_OCEAN,
            proposals=proposals,
            confidence=avg_confidence,
            metadata={
                "four_actions_applied": 4,
                "six_paths_explored": 3,
                "has_strategy_canvas": synthesis is not None,
            },
        )

    async def _analyze_four_actions(
        self,
        action_key: str,
        action: dict,
        focus: list[str],
        insights: list,
    ) -> Proposal | None:
        """Analyze one of the four actions."""
        prompt = f"""Apply Blue Ocean Strategy's "{action_key.upper()}" action.

Domain: {', '.join(focus)}

Question: {action['question']}
Guidance: {action['prompt']}

Analyze:
1. What specific factors should be {action_key}d?
2. Why is this a blue ocean opportunity?
3. What value does this create?

Generate:
1. Innovation title focusing on this action
2. Detailed description
3. Target user
4. Economic buyer
5. Current workaround
6. Adoption wedge
7. Proof artifact
8. Impact on differentiation
9. Impact on cost
10. Risk assessment

Format as JSON:
{{
    "title": "...",
    "description": "...",
    "target_user": "...",
    "economic_buyer": "...",
    "current_workaround": "...",
    "adoption_wedge": "...",
    "proof_artifact": "...",
    "differentiation_impact": "high/medium/low",
    "cost_impact": "increase/reduce/neutral",
    "risk": "high/medium/low",
    "novelty": 0.8
}}"""

        try:
            response = await self.llm_client.chat_json([
                {
                    "role": "system",
                    "content": (
                        "You are a Blue Ocean Strategy expert. Focus on value innovation. "
                        "Prefer specific wedges, workflow shifts, and buyer clarity over broad category claims."
                    ),
                },
                {"role": "user", "content": prompt},
            ])

            content = "\n".join([
                response.get("description", ""),
                "",
                f"Target user: {response.get('target_user', 'unknown')}",
                f"Economic buyer: {response.get('economic_buyer', 'unknown')}",
                f"Current workaround: {response.get('current_workaround', 'unknown')}",
                f"Adoption wedge: {response.get('adoption_wedge', 'unknown')}",
                f"Proof artifact: {response.get('proof_artifact', 'unknown')}",
            ]).strip()

            llm_confidence = float(response.get("novelty", 0.75))
            return Proposal(
                title=f"[Blue Ocean - {action_key.title()}] {response.get('title', 'Untitled')}",
                content=content,
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=llm_confidence,
                metadata={
                    "blue_ocean_action": action_key,
                    "framework": "Blue Ocean Strategy",
                    "target_user": response.get("target_user", ""),
                    "economic_buyer": response.get("economic_buyer", ""),
                    "current_workaround": response.get("current_workaround", ""),
                    "adoption_wedge": response.get("adoption_wedge", ""),
                    "proof_artifact": response.get("proof_artifact", ""),
                    "differentiation_impact": response.get("differentiation_impact", "medium"),
                    "cost_impact": response.get("cost_impact", "neutral"),
                    "risk": response.get("risk", "medium"),
                    "novelty": response.get("novelty", 0.6),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to generate {action_key} proposal: {e}")
            return None

    async def _explore_path(
        self,
        path_info: dict,
        focus: list[str],
    ) -> Proposal | None:
        """Explore one of the six paths."""
        prompt = f"""Explore Blue Ocean Strategy's Six Paths framework.

Path: {path_info['path']}
Description: {path_info['description']}
Domain: {', '.join(focus)}

Analyze:
1. What insights does this path reveal?
2. What blue ocean opportunity emerges?
3. What unmet needs become visible?
4. Who would adopt this first?
5. What proof artifact would make the opportunity credible?

Format as JSON:
{{
    "title": "...",
    "description": "...",
    "path_insights": "...",
    "target_user": "...",
    "proof_artifact": "...",
    "novelty": 0.7
}}"""

        try:
            response = await self.llm_client.chat_json([
                {
                    "role": "system",
                    "content": (
                        "You are exploring new market spaces using the Six Paths framework. "
                        "Prefer credible deep-tech or enterprise wedges over generic market expansion claims."
                    ),
                },
                {"role": "user", "content": prompt},
            ])

            content = "\n".join([
                response.get("description", ""),
                "",
                f"Target user: {response.get('target_user', 'unknown')}",
                f"Proof artifact: {response.get('proof_artifact', 'unknown')}",
            ]).strip()

            llm_confidence = float(response.get("novelty", 0.7))
            return Proposal(
                title=f"[Six Paths - {path_info['path']}] {response.get('title', 'Untitled')}",
                content=content,
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=llm_confidence,
                metadata={
                    "six_paths": path_info['path'],
                    "framework": "Blue Ocean Strategy",
                    "path_insights": response.get("path_insights", ""),
                    "target_user": response.get("target_user", ""),
                    "proof_artifact": response.get("proof_artifact", ""),
                    "novelty": response.get("novelty", 0.6),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to explore path {path_info['path']}: {e}")
            return None

    async def _create_strategy_canvas(
        self,
        proposals: list[Proposal],
        focus: list[str],
    ) -> Proposal | None:
        """Create a strategy canvas synthesis."""
        actions_summary = [
            f"- {p.metadata.get('blue_ocean_action', 'unknown')}: {p.title}"
            for p in proposals if 'blue_ocean_action' in p.metadata
        ]

        prompt = f"""Create a Blue Ocean Strategy Canvas synthesis.

Domain: {', '.join(focus)}

Four Actions Summary:
{chr(10).join(actions_summary[:4])}

Synthesize:
1. Current industry value curve (what to compete less on)
2. New value curve (blue ocean offering)
3. Strategic sequence for implementation
4. Key success factors

Format as JSON:
{{
    "title": "Blue Ocean Strategy Canvas",
    "synthesis": "...",
    "implementation_sequence": ["...", "..."],
    "success_factors": ["...", "..."]
}}"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "Create a strategic synthesis using the Strategy Canvas framework."},
                {"role": "user", "content": prompt},
            ])

            return Proposal(
                title="[Strategy Canvas] Blue Ocean Synthesis",
                content=response.get("synthesis", ""),
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=0.8,  # Synthesis is qualitative — fixed confidence is appropriate
                metadata={
                    "framework": "Blue Ocean Strategy",
                    "is_synthesis": True,
                    "implementation_sequence": response.get("implementation_sequence", []),
                    "success_factors": response.get("success_factors", []),
                    "novelty": 0.7,
                },
            )
        except Exception as e:
            logger.warning(f"Strategy canvas creation failed: {e}")
            return None
