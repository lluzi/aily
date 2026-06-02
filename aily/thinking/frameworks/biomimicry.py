"""Biomimicry innovation framework analyzer.

Innovation inspired by nature's 3.8 billion years of R&D.
Asks: "How would nature solve this?"

Levels of biomimicry:
- Organism: Mimic specific organisms (e.g., gecko feet → adhesive)
- Behavior: Mimic processes (e.g., termite mounds → HVAC)
- Ecosystem: Mimic systems (e.g., coral reef → circular economy)

Strategies from nature:
- Adaptation to environment
- Resource efficiency
- Multifunctionality
- Self-organization
- Resilience
- Symbiosis
"""

from __future__ import annotations

import logging
from typing import Any

from aily.sessions.reactor_scheduler import InnovationMethod, MethodResult
from aily.sessions.models import Proposal, ProposalType, ProposalStatus

logger = logging.getLogger(__name__)


BIOMIMICRY_LEVELS = {
    "organism": "Mimic specific organisms (e.g., gecko feet → adhesive, lotus leaf → self-cleaning)",
    "behavior": "Mimic processes and behaviors (e.g., termite mounds → HVAC, birds → aerodynamics)",
    "ecosystem": "Mimic system-level patterns (e.g., coral reef → circular economy, forest → nutrient cycling)",
}

BIOMIMICRY_STRATEGIES = [
    {"strategy": "adaptation", "description": "How does nature adapt to changing conditions?"},
    {"strategy": "resource_efficiency", "description": "How does nature maximize resource use?"},
    {"strategy": "multifunctionality", "description": "How does nature design for multiple functions?"},
    {"strategy": "self_organization", "description": "How does nature create order without centralized control?"},
    {"strategy": "resilience", "description": "How does nature recover from disturbance?"},
    {"strategy": "symbiosis", "description": "How does nature create mutual benefit relationships?"},
]

# Example biological analogs by domain
BIOLOGICAL_ANALOGS = {
    "structural": ["nautilus shell", "spider silk", "bone structure", "diatom shells"],
    "thermal": ["termite mounds", "elephant ears", "polar bear fur", "penguin feathers"],
    "adhesion": ["gecko feet", "mussel byssus", "burdock burrs", "tree frog toes"],
    "fluid": ["shark skin", "kingfisher beak", "lotus leaf", "butterfly wings"],
    "energy": ["photosynthesis", "bioluminescence", "muscle fibers", "ATP cycle"],
    "information": ["ant pheromones", "bee dances", "whale songs", "mycelial networks"],
}


class BiomimicryAnalyzer:
    """Nature-inspired innovation analyzer."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def analyze(self, context: dict[str, Any]) -> MethodResult:
        """Apply biomimicry methodology to generate innovations."""
        focus = context.get("focus_areas", ["general"])
        recent_insights = context.get("recent_insights", [])

        proposals = []

        # Step 1: Abstract the human challenge
        challenge_type = await self._classify_challenge(focus, recent_insights)

        # Step 2: Find biological analogs
        biological_models = await self._find_nature_solutions(challenge_type, ", ".join(focus))

        # Step 3: Generate innovations for each level and strategy
        for level_key, level_desc in BIOMIMICRY_LEVELS.items():
            for strategy_info in BIOMIMICRY_STRATEGIES:
                try:
                    proposal = await self._generate_biomimetic_innovation(
                        level_key, level_desc,
                        strategy_info,
                        biological_models,
                        focus,
                    )
                    if proposal:
                        proposals.append(proposal)
                except Exception as e:
                    logger.warning(f"Biomimicry {level_key}/{strategy_info['strategy']} failed: {e}")

        # Limit to top proposals
        proposals = proposals[:8]  # Too many can overwhelm
        avg_confidence = sum(p.confidence for p in proposals) / len(proposals) if proposals else 0

        return MethodResult(
            method=InnovationMethod.BIOMIMICRY,
            proposals=proposals,
            confidence=avg_confidence,
            metadata={
                "nature_models_referenced": len(biological_models),
                "challenge_type": challenge_type,
            },
        )

    async def _classify_challenge(self, focus: list[str], insights: list) -> str:
        """Classify what type of challenge we're solving."""
        # Simple heuristic - could be LLM-based
        focus_str = " ".join(focus).lower()

        if any(word in focus_str for word in ["structure", "material", "build"]):
            return "structural"
        elif any(word in focus_str for word in ["heat", "cool", "thermal", "temperature"]):
            return "thermal"
        elif any(word in focus_str for word in ["flow", "fluid", "air", "water"]):
            return "fluid"
        elif any(word in focus_str for word in ["energy", "power", "battery", "solar"]):
            return "energy"
        elif any(word in focus_str for word in ["stick", "attach", "adhesive", "glue"]):
            return "adhesion"
        elif any(word in focus_str for word in ["communicate", "signal", "sense", "detect"]):
            return "information"
        else:
            return "general"

    async def _find_nature_solutions(self, challenge_type: str, focus: str) -> list[str]:
        """Find biological models for the challenge domain using LLM."""
        prompt = f"""Suggest relevant biological models for biomimetic innovation.

Challenge domain: {challenge_type}
Focus areas: {focus}

What biological organisms, systems, or processes in nature are most relevant for inspiring solutions in this domain?
Return 4-6 specific biological models as a JSON array of strings.
Focus on well-documented organisms with clear functional principles."""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "You are a biomimicry expert. Suggest biological models relevant to the challenge domain."},
                {"role": "user", "content": prompt},
            ])
            if isinstance(response, list):
                return [str(item) for item in response[:6] if item]
            elif isinstance(response, dict) and "models" in response:
                return [str(item) for item in response["models"][:6] if item]
            # Fallback to hardcoded analogs
            return BIOLOGICAL_ANALOGS.get(challenge_type, BIOLOGICAL_ANALOGS["structural"])
        except Exception as e:
            logger.warning(f"Failed to find nature solutions via LLM: {e}")
            return BIOLOGICAL_ANALOGS.get(challenge_type, BIOLOGICAL_ANALOGS["structural"])

    async def _generate_biomimetic_innovation(
        self,
        level_key: str,
        level_desc: str,
        strategy_info: dict,
        biological_models: list[str],
        focus: list[str],
    ) -> Proposal | None:
        """Generate a biomimetic innovation."""
        prompt = f"""Generate a biomimetic innovation using nature as inspiration.

Biomimicry Level: {level_key}
Level Description: {level_desc}

Nature Strategy: {strategy_info['strategy']}
Strategy Question: {strategy_info['description']}

Biological Models to Consider: {', '.join(biological_models)}

Human Challenge Domain: {', '.join(focus)}

Process:
1. Select one biological model that exemplifies the strategy
2. Abstract the principle from nature
3. Apply to human challenge domain
4. Describe the innovation

Generate:
1. Innovation title (mention the organism and application)
2. Description: What does nature do? How can we apply this?
3. Biological model used
4. Principle abstracted from nature
5. Impact, feasibility, novelty scores

Format as JSON:
{{
    "title": "...",
    "description": "...",
    "biological_model": "...",
    "nature_principle": "...",
    "impact": "high/medium/low",
    "feasibility": "high/medium/low",
    "novelty": 0.8
}}"""

        try:
            response = await self.llm_client.chat_json([
                {"role": "system", "content": "You are a biomimicry expert. Study nature's solutions and translate to human innovation."},
                {"role": "user", "content": prompt},
            ])

            return Proposal(
                title=f"[Bio-{level_key.title()}] {response.get('title', 'Nature-Inspired Solution')}",
                content=response.get("description", ""),
                proposal_type=ProposalType.INNOVATION,
                status=ProposalStatus.PROPOSED,
                confidence=float(response.get("novelty", 0.7)),
                metadata={
                    "biomimicry_level": level_key,
                    "strategy": strategy_info['strategy'],
                    "biological_model": response.get("biological_model", "unknown"),
                    "nature_principle": response.get("nature_principle", ""),
                    "framework": "Biomimicry",
                    "impact": response.get("impact", "medium"),
                    "feasibility": response.get("feasibility", "medium"),
                    "novelty": response.get("novelty", 0.6),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to generate biomimetic innovation: {e}")
            return None
