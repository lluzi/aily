"""Synthesis skill - combines insights into wisdom principles."""

from __future__ import annotations

import logging
from typing import Any

from aily.dikiwi.skills.base import Skill, SkillContext, SkillResult

logger = logging.getLogger(__name__)


class SynthesisSkill(Skill):
    """Synthesize insights into actionable wisdom principles.

    Used in WISDOM stage to:
    - Combine multiple insights into principles
    - Create decision frameworks
    - Generate "how-to" knowledge
    """

    name = "synthesis"
    description = "Synthesize insights into wisdom principles"
    version = "1.0.0"
    target_stages = ["wisdom"]
    content_types = ["*"]

    requires_llm = True
    requires_graph_db = False

    async def execute(self, context: SkillContext) -> SkillResult:
        """Synthesize content into principles."""
        if not context.llm_client:
            return SkillResult.error_result(
                self.name,
                "LLM client required for synthesis",
            )

        prompt = f"""Synthesize the following content into actionable wisdom principles.

Content:
{context.content[:3000]}

Create:
1. Core principles (3-5 key takeaways)
2. When to apply (context/conditions)
3. How to apply (actionable steps)
4. Common pitfalls to avoid
5. Related principles (if applicable)

Format as structured principles that can be applied to similar situations."""

        try:
            response = await context.llm_client.complete(prompt)

            principles = self._parse_principles(response)

            return SkillResult.success_result(
                skill_name=self.name,
                output=principles,
                processing_time_ms=0.0,
                metadata={
                    "content_id": context.content_id,
                    "principle_count": len(principles.get("principles", [])),
                },
                output_content=[
                    {
                        "type": "principles",
                        "data": principles,
                    }
                ],
            )
        except Exception as e:
            logger.exception("Synthesis failed")
            return SkillResult.error_result(self.name, str(e))

    def _parse_principles(self, response: str) -> dict[str, Any]:
        """Parse synthesis response into structured principles."""
        lines = response.split("\n")

        principles = []
        current_principle = {}

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Look for numbered principles or headers
            if line[0].isdigit() and "." in line[:3]:
                if current_principle:
                    principles.append(current_principle)
                current_principle = {
                    "title": line.split(".", 1)[1].strip(),
                    "description": "",
                }
            elif line.lower().startswith("when"):
                current_principle["when_to_apply"] = line.split(":", 1)[1].strip() if ":" in line else ""
            elif line.lower().startswith("how"):
                current_principle["how_to_apply"] = line.split(":", 1)[1].strip() if ":" in line else ""
            elif line.lower().startswith("pitfall"):
                if "pitfalls" not in current_principle:
                    current_principle["pitfalls"] = []
                current_principle["pitfalls"].append(line.split(":", 1)[1].strip() if ":" in line else line)
            else:
                if current_principle:
                    current_principle["description"] += line + " "

        if current_principle:
            principles.append(current_principle)

        return {
            "principles": principles,
            "raw_analysis": response,
        }
