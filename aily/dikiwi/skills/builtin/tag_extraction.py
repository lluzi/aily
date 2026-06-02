"""Tag extraction skill - extracts domain and topic tags from content."""

from __future__ import annotations

import logging
from typing import Any

from aily.dikiwi.skills.base import Skill, SkillContext, SkillResult

logger = logging.getLogger(__name__)


class TagExtractionSkill(Skill):
    """Extract domain and topic tags from content.

    Used in INFORMATION stage to classify content for later linking.
    """

    name = "tag_extraction"
    description = "Extract domain and topic tags from content"
    version = "1.0.0"
    target_stages = ["information"]
    content_types = ["*"]  # All content types

    requires_llm = True
    requires_graph_db = False

    async def execute(self, context: SkillContext) -> SkillResult:
        """Extract tags from content."""
        if not context.llm_client:
            return SkillResult.error_result(
                self.name,
                "LLM client required for tag extraction",
            )

        prompt = f"""Analyze the following content and extract:
1. Domain tags (broad categories like "AI", "Business", "Technology", "Health")
2. Topic tags (specific topics mentioned)
3. Key entities (people, organizations, products, concepts)

Content:
{context.content[:2000]}  # Limit content length

Respond in this format:
Domains: tag1, tag2, tag3
Topics: topic1, topic2, topic3
Entities: entity1, entity2, entity3
"""

        try:
            response = await context.llm_client.complete(prompt)

            # Parse response
            tags = self._parse_tags(response)

            return SkillResult.success_result(
                skill_name=self.name,
                output=tags,
                processing_time_ms=0.0,  # Will be set by run()
                metadata={
                    "content_id": context.content_id,
                    "source": context.source,
                },
                output_content=[
                    {
                        "type": "tags",
                        "data": tags,
                    }
                ],
            )
        except Exception as e:
            logger.exception("Tag extraction failed")
            return SkillResult.error_result(self.name, str(e))

    def _parse_tags(self, response: str) -> dict[str, list[str]]:
        """Parse LLM response into structured tags."""
        tags = {
            "domains": [],
            "topics": [],
            "entities": [],
        }

        current_key = None
        for line in response.split("\n"):
            line = line.strip()
            if line.lower().startswith("domains:"):
                current_key = "domains"
                tags["domains"] = [
                    t.strip()
                    for t in line.split(":", 1)[1].split(",")
                    if t.strip()
                ]
            elif line.lower().startswith("topics:"):
                current_key = "topics"
                tags["topics"] = [
                    t.strip()
                    for t in line.split(":", 1)[1].split(",")
                    if t.strip()
                ]
            elif line.lower().startswith("entities:"):
                current_key = "entities"
                tags["entities"] = [
                    t.strip()
                    for t in line.split(":", 1)[1].split(",")
                    if t.strip()
                ]

        return tags
