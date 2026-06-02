"""SIT (Systematic Inventive Thinking) framework analyzer.

SIT is a simplified, practical evolution of TRIZ developed by Genady Filkovsky (1995).
It uses 5 core thinking patterns instead of TRIZ's 40 principles.

The 5 SIT Operators:
1. Subtraction - Remove an essential component
2. Multiplication - Add a copy, but change it
3. Division - Divide in time or space
4. Task Unification - Assign new tasks to existing resources
5. Attribute Dependency - Create/remove dependencies between attributes
"""

from __future__ import annotations

import logging
from typing import Any

from aily.sessions.reactor_scheduler import InnovationMethod, MethodResult
from aily.sessions.models import Proposal, ProposalType, ProposalStatus

logger = logging.getLogger(__name__)


SIT_OPERATORS = {
    "subtraction": {
        "name": "Subtraction",
        "description": "Remove an essential component and see what remains",
        "closed_world": True,
        "prompt": "Identify an essential component of the system/product. Remove it completely. What remains? Can the remaining components compensate for the missing one?",
    },
    "multiplication": {
        "name": "Multiplication",
        "description": "Add a copy of an existing component, but change it",
        "closed_world": True,
        "prompt": "Identify a component. Add a copy of it, but with a slight variation (size, material, location, etc.). How does this create new value?",
    },
    "division": {
        "name": "Division",
        "description": "Divide product/process in time or space",
        "closed_world": True,
        "prompt": "Divide the system either spatially (into parts) or temporally (in time). Rearrange the pieces. What new possibilities emerge?",
    },
    "task_unification": {
        "name": "Task Unification",
        "description": "Assign new tasks to existing resources",
        "closed_world": True,
        "prompt": "Look at existing components/resources. What additional tasks could they perform? Can one component do the job of two or more?",
    },
    "attribute_dependency": {
        "name": "Attribute Dependency",
        "description": "Create or remove dependencies between attributes",
        "closed_world": True,
        "prompt": "Identify two independent attributes. Create a dependency between them (as A changes, B changes). Or break an existing dependency.",
    },
}


class SITAnalyzer:
    """Systematic Inventive Thinking analyzer."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def analyze(self, context: dict[str, Any]) -> MethodResult:
        """Apply SIT methodology to generate innovations."""
        proposals = []
        focus = context.get("focus_areas", ["general"])
        recent_insights = context.get("recent_insights", [])

        # Apply each SIT operator
        for operator_key, operator in SIT_OPERATORS.items():
            try:
                proposal = await self._generate_for_operator(
                    operator_key, operator, focus, recent_insights
                )
                if proposal:
                    proposals.append(proposal)
            except Exception as e:
                logger.warning(f"SIT {operator_key} failed: {e}")

        avg_confidence = sum(p.confidence for p in proposals) / len(proposals) if proposals else 0

        return MethodResult(
            method=InnovationMethod.SIT,
            proposals=proposals,
            confidence=avg_confidence,
            metadata={"operators_applied": len(proposals)},
        )

    async def _generate_for_operator(
        self,
        operator_key: str,
        operator: dict,
        focus: list[str],
        insights: list,
    ) -> Proposal | None:
        """Generate a proposal for a specific SIT operator."""
        prompt = f"""Apply SIT operator "{operator['name']}" to generate an innovation.

Context:
- Focus areas: {', '.join(focus)}
- Recent insights: {len(insights)} items

Operator: {operator['description']}
Guidance: {operator['prompt']}

Key Principle: Closed World - use only existing components, don't add new ones from outside.

Generate:
1. A specific innovation idea using this operator
2. Title (concise, clear)
3. Description (2-3 sentences explaining the innovation)
4. Target user
5. Current workaround
6. Workflow insertion point
7. Adoption wedge
8. Proof artifact
9. Expected impact (high/medium/low)
10. Feasibility (high/medium/low)
11. Novelty score (0.0-1.0)
12. Which existing component was modified/removed

Format as JSON:
{{
    "title": "...",
    "description": "...",
    "target_user": "...",
    "current_workaround": "...",
    "workflow_insertion": "...",
    "adoption_wedge": "...",
    "proof_artifact": "...",
    "impact": "high",
    "feasibility": "high",
    "novelty": 0.8,
    "modified_component": "..."
}}"""

        try:
            response = await self.llm_client.chat_json([
                {
                    "role": "system",
                    "content": (
                        "You are a SIT innovation expert. Apply Closed World principle strictly. "
                        "Prefer concrete workflow innovations over vague platform concepts."
                    ),
                },
                {"role": "user", "content": prompt},
            ])

            content = "\n".join([
                response.get("description", ""),
                "",
                f"Target user: {response.get('target_user', 'unknown')}",
                f"Current workaround: {response.get('current_workaround', 'unknown')}",
                f"Workflow insertion: {response.get('workflow_insertion', 'unknown')}",
                f"Adoption wedge: {response.get('adoption_wedge', 'unknown')}",
                f"Proof artifact: {response.get('proof_artifact', 'unknown')}",
            ]).strip()

            return Proposal(
                title=f"[SIT-{operator['name']}] {response.get('title', 'Untitled')}",
                content=content,
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=float(response.get("novelty", 0.75)),
                metadata={
                    "sit_operator": operator_key,
                    "target_user": response.get("target_user", ""),
                    "current_workaround": response.get("current_workaround", ""),
                    "workflow_insertion": response.get("workflow_insertion", ""),
                    "adoption_wedge": response.get("adoption_wedge", ""),
                    "proof_artifact": response.get("proof_artifact", ""),
                    "impact": response.get("impact", "medium"),
                    "feasibility": response.get("feasibility", "medium"),
                    "novelty": response.get("novelty", 0.5),
                    "framework": "SIT",
                    "closed_world": True,
                    "modified_component": response.get("modified_component", "unknown"),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to generate {operator_key} proposal: {e}")
            return None
