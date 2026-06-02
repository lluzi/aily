"""Six Thinking Hats framework analyzer (Edward de Bono).

Parallel thinking methodology using 6 cognitive "hats":
- White: Facts, data, objective information
- Red: Emotions, intuition, gut feelings
- Black: Caution, risks, critical judgment
- Yellow: Optimism, benefits, advantages
- Green: Creativity, alternatives, new ideas
- Blue: Process control, meta-thinking, summary
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aily.sessions.reactor_scheduler import InnovationMethod, MethodResult
from aily.sessions.models import Proposal, ProposalType, ProposalStatus

logger = logging.getLogger(__name__)


SIX_HATS = {
    "white": {
        "name": "White Hat",
        "color": "⚪",
        "focus": "Facts & Information",
        "question": "What do we know? What information is missing?",
        "prompt": "Focus on objective facts, data, and information. No interpretations or opinions.",
    },
    "red": {
        "name": "Red Hat",
        "color": "🔴",
        "focus": "Feelings & Emotions",
        "question": "What are my gut feelings? What's my intuition?",
        "prompt": "Express emotions, intuitions, and gut reactions without justification.",
    },
    "black": {
        "name": "Black Hat",
        "color": "⚫",
        "focus": "Critical Judgment",
        "question": "What could go wrong? What are the risks?",
        "prompt": "Identify risks, problems, weaknesses, and why something might not work.",
    },
    "yellow": {
        "name": "Yellow Hat",
        "color": "🟡",
        "focus": "Optimism & Benefits",
        "question": "What are the benefits? Why will this work?",
        "prompt": "Focus on positives, benefits, and why ideas have merit.",
    },
    "green": {
        "name": "Green Hat",
        "color": "🟢",
        "focus": "Creativity & Alternatives",
        "question": "What new ideas are possible? What alternatives exist?",
        "prompt": "Generate creative alternatives, new ideas, and fresh perspectives.",
    },
    "blue": {
        "name": "Blue Hat",
        "color": "🔵",
        "focus": "Process & Summary",
        "question": "What's the big picture? What should we prioritize?",
        "prompt": "Synthesize all perspectives and provide meta-analysis and recommendations.",
    },
}


class SixHatsAnalyzer:
    """Six Thinking Hats parallel thinking analyzer."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def analyze(self, context: dict[str, Any]) -> MethodResult:
        """Apply Six Thinking Hats methodology."""
        focus = context.get("focus_areas", ["general"])
        recent_insights = context.get("recent_insights", [])

        # Run all 6 hats in parallel
        tasks = []
        for hat_key, hat in SIX_HATS.items():
            task = self._think_with_hat(hat_key, hat, focus, recent_insights)
            tasks.append(task)

        hat_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect proposals (Green hat generates them, others provide context)
        proposals = []
        for result in hat_results:
            if isinstance(result, Proposal):
                proposals.append(result)

        # Generate synthesis proposal from Blue hat perspective
        try:
            synthesis = await self._synthesize_hats(hat_results, focus)
            if synthesis:
                proposals.append(synthesis)
        except Exception as e:
            logger.warning(f"Blue hat synthesis failed: {e}")

        avg_confidence = sum(p.confidence for p in proposals) / len(proposals) if proposals else 0

        return MethodResult(
            method=InnovationMethod.SIX_HATS,
            proposals=proposals,
            confidence=avg_confidence,
            metadata={"hats_applied": 6, "green_hat_proposals": len([p for p in proposals if "Green" not in p.title])},
        )

    async def _think_with_hat(
        self,
        hat_key: str,
        hat: dict,
        focus: list[str],
        insights: list,
    ) -> Proposal | str:
        """Think from the perspective of one hat."""
        prompt = f"""Put on the {hat['name']} {hat['color']} and think about innovation.

Context:
- Focus areas: {', '.join(focus)}
- Recent insights: {len(insights)} items

Your Focus: {hat['focus']}
Question: {hat['question']}
Guidance: {hat['prompt']}

Generate:
1. Your perspective on potential innovations
2. Key insights from this thinking mode
3. For Green Hat: Generate a specific innovation proposal
4. For other hats: Provide analysis and considerations

Format as JSON:
{{
    "perspective": "...",
    "key_insights": ["...", "..."],
    "innovation_title": "..." (only for Green Hat),
    "innovation_description": "..." (only for Green Hat)
}}"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": f"You are wearing the {hat['name']}. {hat['prompt']}"},
                {"role": "user", "content": prompt},
            ])

            # Green hat generates actual proposals
            if hat_key == "green":
                return Proposal(
                    title=f"[{hat['name']}] {response.get('innovation_title', 'Creative Idea')}",
                    content=response.get("innovation_description", response.get("perspective", "")),
                    proposal_type=ProposalType.INNOVATION,
                    status=ProposalStatus.PROPOSED,
                    confidence=float(response.get("novelty", 0.7)),
                    metadata={
                        "hat": hat_key,
                        "perspective": hat['focus'],
                        "framework": "Six Thinking Hats",
                        "key_insights": response.get("key_insights", []),
                        "novelty": float(response.get("novelty", 0.8)),
                    },
                )

            # Other hats return their analysis
            return response.get("perspective", "")

        except Exception as e:
            logger.warning(f"{hat['name']} thinking failed: {e}")
            return ""

    async def _synthesize_hats(
        self,
        hat_results: list,
        focus: list[str],
    ) -> Proposal | None:
        """Synthesize all hat perspectives (Blue Hat)."""
        # Extract non-empty perspectives
        perspectives = [r for r in hat_results if isinstance(r, str) and r]
        green_proposals = [r for r in hat_results if isinstance(r, Proposal)]

        prompt = f"""As the Blue Hat (Process Control), synthesize all thinking into a strategic recommendation.

Focus areas: {', '.join(focus)}

Other hat perspectives:
{chr(10).join(f"- {p[:200]}..." for p in perspectives)}

Green Hat innovations: {len(green_proposals)}

Generate:
1. Strategic synthesis - the big picture
2. Priority recommendation
3. Title for the meta-insight
4. Description with actionable next steps

Format as JSON:
{{
    "title": "...",
    "description": "...",
    "priority": "high/medium/low"
}}"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "You are the Blue Hat. Provide meta-analysis and strategic direction."},
                {"role": "user", "content": prompt},
            ])

            llm_priority = response.get("priority", "medium")
            synth_confidence = 0.9 if llm_priority == "high" else 0.8
            return Proposal(
                title=f"[Blue Hat - Synthesis] {response.get('title', 'Strategic Direction')}",
                content=response.get("description", ""),
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=synth_confidence,
                metadata={
                    "hat": "blue",
                    "perspective": "Process & Synthesis",
                    "framework": "Six Thinking Hats",
                    "priority": response.get("priority", "medium"),
                    "is_synthesis": True,
                },
            )
        except Exception as e:
            logger.warning(f"Blue hat synthesis failed: {e}")
            return None
