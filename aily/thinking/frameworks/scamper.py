"""SCAMPER innovation framework analyzer.

SCAMPER is a creative thinking technique developed by Bob Eberle (1971).
It provides 7 action verbs to spark innovation:
Substitute, Combine, Adapt, Modify, Put to other uses, Eliminate, Reverse
"""

from __future__ import annotations

import logging
from typing import Any

from aily.sessions.reactor_scheduler import InnovationMethod, MethodResult
from aily.sessions.models import Proposal, ProposalType, ProposalStatus

logger = logging.getLogger(__name__)


SCAMPER_ACTIONS = {
    "S": {
        "name": "Substitute",
        "question": "What can be substituted? (materials, people, processes, components)",
        "prompt": "Think about substituting parts, materials, people, or processes. What alternatives could work?",
    },
    "C": {
        "name": "Combine",
        "question": "What can be combined? (features, functions, products)",
        "prompt": "Consider merging features, combining functions, or integrating with other products.",
    },
    "A": {
        "name": "Adapt",
        "question": "What can be adapted? (from other contexts, industries, nature)",
        "prompt": "Look for similar solutions in other contexts, industries, or nature that could be adapted.",
    },
    "M": {
        "name": "Modify/Magnify/Minify",
        "question": "What can be modified? (attributes, scale, frequency)",
        "prompt": "Consider changing size, color, shape, or other attributes. What if you magnify or minify aspects?",
    },
    "P": {
        "name": "Put to other uses",
        "question": "What other uses are possible? (new contexts, users, applications)",
        "prompt": "Think about new contexts, different users, or alternative applications for this.",
    },
    "E": {
        "name": "Eliminate",
        "question": "What can be eliminated? (simplify, remove, streamline)",
        "prompt": "Consider simplification. What parts, steps, or features could be removed?",
    },
    "R": {
        "name": "Reverse/Rearrange",
        "question": "What can be reversed or rearranged? (order, layout, process)",
        "prompt": "Think about reversing the order, rearranging components, or doing the opposite.",
    },
}


class ScamperAnalyzer:
    """SCAMPER creative thinking analyzer."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def analyze(self, context: dict[str, Any]) -> MethodResult:
        """Apply SCAMPER methodology to generate innovations."""
        proposals = []
        focus = context.get("focus_areas", ["general"])
        recent_insights = context.get("recent_insights", [])

        # Run each SCAMPER action
        for letter, action in SCAMPER_ACTIONS.items():
            try:
                proposal = await self._generate_for_action(
                    letter, action, focus, recent_insights
                )
                if proposal:
                    proposals.append(proposal)
            except Exception as e:
                logger.warning(f"SCAMPER {letter} failed: {e}")

        # Calculate overall confidence
        avg_confidence = sum(p.confidence for p in proposals) / len(proposals) if proposals else 0

        return MethodResult(
            method=InnovationMethod.SCAMPER,
            proposals=proposals,
            confidence=avg_confidence,
            metadata={"actions_applied": len(proposals)},
        )

    async def _generate_for_action(
        self,
        letter: str,
        action: dict,
        focus: list[str],
        insights: list,
    ) -> Proposal | None:
        """Generate a proposal for a specific SCAMPER action."""
        prompt = f"""Apply SCAMPER action "{letter} - {action['name']}" to generate an innovation.

Context:
- Focus areas: {', '.join(focus)}
- Recent insights: {len(insights)} items

Action: {action['question']}
Guidance: {action['prompt']}

Generate:
1. A specific innovation idea using this action
2. Title (concise, clear)
3. Description (2-3 sentences)
4. Expected impact (high/medium/low)
5. Feasibility (high/medium/low)
6. Novelty score (0.0-1.0)

Format as JSON:
{{
    "title": "...",
    "description": "...",
    "impact": "high",
    "feasibility": "high",
    "novelty": 0.8
}}"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "You are a SCAMPER innovation expert. Generate concrete, actionable ideas."},
                {"role": "user", "content": prompt},
            ])

            llm_confidence = float(response.get("novelty", 0.7))
            return Proposal(
                title=f"[{letter}] {response.get('title', 'Untitled')}",
                content=response.get("description", ""),
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=llm_confidence,
                metadata={
                    "scamper_action": letter,
                    "impact": response.get("impact", "medium"),
                    "feasibility": response.get("feasibility", "medium"),
                    "novelty": response.get("novelty", 0.5),
                    "framework": "SCAMPER",
                },
            )
        except Exception as e:
            logger.warning(f"Failed to generate {letter} proposal: {e}")
            return None
