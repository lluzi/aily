"""Pattern detection skill - finds themes and patterns in knowledge network."""

from __future__ import annotations

import logging
from typing import Any

from aily.dikiwi.skills.base import Skill, SkillContext, SkillResult

logger = logging.getLogger(__name__)


class PatternDetectionSkill(Skill):
    """Detect patterns and themes across the knowledge network.

    Used in INSIGHT stage to identify:
    - Recurring themes
    - Connections between seemingly unrelated items
    - Emerging trends
    """

    name = "pattern_detection"
    description = "Detect patterns and themes in knowledge network"
    version = "1.0.0"
    target_stages = ["insight"]
    content_types = ["*"]

    requires_llm = True
    requires_graph_db = True

    async def execute(self, context: SkillContext) -> SkillResult:
        """Detect patterns related to current content."""
        if not context.llm_client:
            return SkillResult.error_result(
                self.name,
                "LLM client required for pattern detection",
            )

        if not context.graph_db:
            return SkillResult.error_result(
                self.name,
                "GraphDB required for pattern detection",
            )

        # Get related knowledge from graph
        related = []
        if context.content_id:
            try:
                # Query for related nodes
                related = await context.graph_db.query(
                    """
                    MATCH (n {id: $content_id})-[:RELATED|SIMILAR|SUPPORTS|CONTRADICTS*1..2]-(m)
                    RETURN DISTINCT m.content as content, m.tags as tags
                    LIMIT 10
                    """,
                    {"content_id": context.content_id},
                )
            except Exception as e:
                logger.warning("Failed to query graph: %s", e)

        # Build context for LLM
        related_text = ""
        if related:
            related_text = "\n\nRelated knowledge items:\n"
            for item in related:
                content = item.get("content", "")[:500]
                tags = item.get("tags", [])
                related_text += f"- [{', '.join(tags)}]: {content}\n"

        prompt = f"""Analyze the following content and related knowledge to identify patterns.

Current content:
{context.content[:1500]}
{related_text}

Identify:
1. Recurring themes across items
2. Connections or contradictions
3. Emerging patterns
4. Gaps in the knowledge

Respond with a structured analysis."""

        try:
            response = await context.llm_client.complete(prompt)

            patterns = self._parse_patterns(response)

            return SkillResult.success_result(
                skill_name=self.name,
                output=patterns,
                processing_time_ms=0.0,
                metadata={
                    "content_id": context.content_id,
                    "related_items_count": len(related),
                },
                output_content=[
                    {
                        "type": "patterns",
                        "data": patterns,
                    }
                ],
            )
        except Exception as e:
            logger.exception("Pattern detection failed")
            return SkillResult.error_result(self.name, str(e))

    def _parse_patterns(self, response: str) -> dict[str, Any]:
        """Parse pattern detection response."""
        return {
            "analysis": response,
            "themes": [],
            "connections": [],
            "contradictions": [],
            "gaps": [],
        }
