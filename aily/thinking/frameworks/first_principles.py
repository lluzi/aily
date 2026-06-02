"""First Principles Thinking framework analyzer.

Break down problems to fundamental truths and build up from there.
Popularized by Aristotle, used by physicists and innovators like Elon Musk.

Process:
1. Identify current assumptions
2. Break down to fundamental truths
3. Examine each component
4. Build alternative solutions
5. Synthesize new approach
"""

from __future__ import annotations

import logging
from typing import Any

from aily.sessions.reactor_scheduler import InnovationMethod, MethodResult
from aily.sessions.models import Proposal, ProposalType, ProposalStatus

logger = logging.getLogger(__name__)


class FirstPrinciplesAnalyzer:
    """First Principles thinking analyzer."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def analyze(self, context: dict[str, Any]) -> MethodResult:
        """Apply First Principles thinking methodology."""
        focus = context.get("focus_areas", ["general"])
        recent_insights = context.get("recent_insights", [])

        # Step 1: Identify assumptions
        assumptions = await self._identify_assumptions(focus, recent_insights)

        # Step 2: Deconstruct to fundamentals
        fundamentals = await self._deconstruct_to_fundamentals(assumptions, focus)

        # Step 3: Generate proposals from fundamentals
        proposals = []
        for i, fundamental in enumerate(fundamentals[:5]):  # Limit to 5
            try:
                proposal = await self._build_from_fundamentals(
                    fundamental, assumptions, focus
                )
                if proposal:
                    proposals.append(proposal)
            except Exception as e:
                logger.warning(f"Fundamental building failed: {e}")

        # Step 4: Synthesis
        try:
            synthesis = await self._synthesize_first_principles(proposals, fundamentals)
            if synthesis:
                proposals.append(synthesis)
        except Exception as e:
            logger.warning(f"Synthesis failed: {e}")

        avg_confidence = sum(p.confidence for p in proposals) / len(proposals) if proposals else 0

        return MethodResult(
            method=InnovationMethod.FIRST_PRINCIPLES,
            proposals=proposals,
            confidence=avg_confidence,
            metadata={
                "assumptions_challenged": len(assumptions),
                "fundamentals_identified": len(fundamentals),
            },
        )

    async def _identify_assumptions(
        self, focus: list[str], insights: list
    ) -> list[str]:
        """Identify current assumptions in the domain."""
        insights_summary = "\n".join(
            f"- {i.get('label', '')}" for i in insights[:8] if isinstance(i, dict)
        ) or "No recent insights provided."
        prompt = f"""Identify assumptions in the current approach to these domains.

Domains: {', '.join(focus)}
Recent evidence:
{insights_summary}

What are the industry standard assumptions?
What does everyone take for granted?
What "best practices" are actually just inherited traditions?

List 5-7 key assumptions as a JSON array:
[
    "Assumption 1...",
    "Assumption 2...",
    ...
]"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "Identify hidden assumptions and industry dogmas."},
                {"role": "user", "content": prompt},
            ])

            if isinstance(response, list):
                return response[:7]
            elif isinstance(response, dict) and "assumptions" in response:
                return response["assumptions"][:7]
            return []

        except Exception as e:
            logger.warning(f"Failed to identify assumptions: {e}")
            return []

    async def _deconstruct_to_fundamentals(
        self, assumptions: list[str], focus: list[str]
    ) -> list[dict]:
        """Break assumptions down to fundamental truths."""
        fundamentals = []

        for assumption in assumptions[:5]:  # Process first 5
            prompt = f"""Deconstruct this assumption to first principles.

Assumption: "{assumption}"
Domain: {', '.join(focus)}

Ask:
1. Is this actually true? What is the evidence?
2. What are the fundamental physics/economics/logic behind this?
3. If we strip away all inherited knowledge, what's the core truth?

Format as JSON:
{{
    "assumption": "...",
    "is_true": true/false,
    "fundamental_truth": "...",
    "evidence": "...",
    "deconstruction": "..."
}}"""

            try:
                response = await self.llm_client.chat_json([
                    {"role": "system", "content": "Deconstruct assumptions to fundamental truths using Socratic questioning."},
                    {"role": "user", "content": prompt},
                ])

                fundamentals.append({
                    "assumption": assumption,
                    "fundamental_truth": response.get("fundamental_truth", ""),
                    "deconstruction": response.get("deconstruction", ""),
                    "is_true": response.get("is_true", True),
                })

            except Exception as e:
                logger.warning(f"Failed to deconstruct assumption: {e}")

        return fundamentals

    async def _build_from_fundamentals(
        self,
        fundamental: dict,
        all_assumptions: list[str],
        focus: list[str],
    ) -> Proposal | None:
        """Build innovation from fundamental truth."""
        prompt = f"""Build a new solution from first principles.

Fundamental Truth: {fundamental['fundamental_truth']}
Original Assumption: {fundamental['assumption']}
Deconstruction: {fundamental['deconstruction']}
Domain: {', '.join(focus)}

Starting from this fundamental truth, rebuild:
1. If we ignore traditional approaches, what becomes possible?
2. What is the most direct way to solve this?
3. What constraints are actually imaginary?
4. Who feels this pain first in the workflow?
5. What first proof artifact would convince a skeptical adopter?

Generate:
1. Innovation title
2. Description explaining the first principles approach
3. Target user
4. Economic buyer
5. Current workaround
6. Workflow insertion point
7. Adoption wedge
8. Proof artifact
9. Why this breaks from tradition
10. Impact, feasibility, novelty scores

Format as JSON:
{{
    "title": "...",
    "description": "...",
    "target_user": "...",
    "economic_buyer": "...",
    "current_workaround": "...",
    "workflow_insertion": "...",
    "adoption_wedge": "...",
    "proof_artifact": "...",
    "breaks_from_tradition": "...",
    "impact": "high/medium/low",
    "feasibility": "high/medium/low",
    "novelty": 0.8
}}"""

        try:
            response = await self.llm_client.chat_json([
                {
                    "role": "system",
                    "content": (
                        "Build innovation from fundamental truths, ignoring traditional approaches. "
                        "Prefer concrete deep-tech or enterprise hypotheses over broad platform language."
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
                f"Workflow insertion: {response.get('workflow_insertion', 'unknown')}",
                f"Adoption wedge: {response.get('adoption_wedge', 'unknown')}",
                f"Proof artifact: {response.get('proof_artifact', 'unknown')}",
            ]).strip()

            return Proposal(
                title=f"[1st Principles] {response.get('title', 'Fundamental Innovation')}",
                content=content,
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=float(response.get("novelty", 0.75)),
                metadata={
                    "fundamental_truth": fundamental['fundamental_truth'],
                    "challenged_assumption": fundamental['assumption'],
                    "framework": "First Principles",
                    "target_user": response.get("target_user", ""),
                    "economic_buyer": response.get("economic_buyer", ""),
                    "current_workaround": response.get("current_workaround", ""),
                    "workflow_insertion": response.get("workflow_insertion", ""),
                    "adoption_wedge": response.get("adoption_wedge", ""),
                    "proof_artifact": response.get("proof_artifact", ""),
                    "breaks_from_tradition": response.get("breaks_from_tradition", ""),
                    "impact": response.get("impact", "medium"),
                    "feasibility": response.get("feasibility", "medium"),
                    "novelty": response.get("novelty", 0.8),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to build from fundamentals: {e}")
            return None

    async def _synthesize_first_principles(
        self,
        proposals: list[Proposal],
        fundamentals: list[dict],
    ) -> Proposal | None:
        """Synthesize first principles insights."""
        if not proposals:
            return None

        key_truths = [f['fundamental_truth'] for f in fundamentals[:3]]

        prompt = f"""Synthesize first principles thinking into strategic insight.

Key Fundamental Truths:
{chr(10).join(f"- {t}" for t in key_truths)}

Generated Proposals: {len(proposals)}

Synthesize:
1. What core insight emerges from first principles analysis?
2. What are the strategic implications?
3. Recommended next steps

Format as JSON:
{{
    "synthesis": "...",
    "strategic_implications": "...",
    "next_steps": ["...", "..."]
}}"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "Synthesize first principles analysis into strategic direction."},
                {"role": "user", "content": prompt},
            ])

            return Proposal(
                title="[1st Principles] Strategic Synthesis",
                content=response.get("synthesis", ""),
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=0.8,  # Synthesis — qualitative scoring
                metadata={
                    "framework": "First Principles",
                    "is_synthesis": True,
                    "strategic_implications": response.get("strategic_implications", ""),
                    "next_steps": response.get("next_steps", []),
                    "novelty": 0.7,
                },
            )
        except Exception as e:
            logger.warning(f"Synthesis failed: {e}")
            return None
