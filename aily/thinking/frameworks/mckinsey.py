"""McKinsey framework analyzer.

Applies MECE structuring, hypothesis-driven problem solving, and business
frameworks (7S, 3C, Porter 5 Forces) to analyze strategic business problems.
"""

from __future__ import annotations

from typing import Any

from aily.thinking.frameworks.base import FrameworkAnalyzer
from aily.thinking.models import (
    FrameworkApplication,
    FrameworkInsight,
    FrameworkType,
    HypothesisTree,
    InsightPriority,
    KnowledgePayload,
    MeceStructure,
)


class McKinseyAnalyzer(FrameworkAnalyzer):
    """McKinsey framework analyzer for strategic business problem solving.

    Applies MECE structuring, hypothesis-driven analysis, and classic business
    frameworks (7S, 3C, Porter 5 Forces) to break down complex problems and
    generate actionable insights.
    """

    framework_type = FrameworkType.MCKINSEY

    async def analyze(self, payload: KnowledgePayload) -> FrameworkInsight:
        """Analyze content using McKinsey frameworks.

        Args:
            payload: The knowledge payload containing content to analyze.

        Returns:
            FrameworkInsight containing MECE structures, hypothesis trees,
            framework applications, and key insights.
        """
        import time

        start_time = time.time()

        system_prompt = self.get_system_prompt()

        try:
            # Single consolidated LLM call for efficiency
            analysis_prompt = self._build_analysis_prompt(payload)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": analysis_prompt},
            ]

            analysis_data = await self.llm_client.chat_json(
                messages=messages,
                temperature=0.3,
            )

            # Parse the structured response
            mece_structures = self._parse_mece_structures(
                analysis_data.get("mece_structure", {})
            )
            hypothesis_trees = self._parse_hypothesis_trees(
                analysis_data.get("hypothesis_tree", {})
            )
            framework_applications = self._parse_framework_applications(
                analysis_data.get("framework_applications", [])
            )
            key_insights = analysis_data.get("key_insights", [])
            confidence = analysis_data.get("confidence", 0.75)

        except Exception as exc:
            # Handle LLM errors gracefully
            return FrameworkInsight(
                framework_type=self.framework_type,
                insights=[f"McKinsey analysis failed: {str(exc)}"],
                confidence=0.0,
                priority=InsightPriority.LOW,
                raw_analysis={"error": str(exc)},
                processing_time_ms=int((time.time() - start_time) * 1000),
            )

        processing_time = int((time.time() - start_time) * 1000)

        raw_analysis = {
            "mece_structures": [m.model_dump() for m in mece_structures],
            "hypothesis_trees": [h.model_dump() for h in hypothesis_trees],
            "framework_applications": [f.model_dump() for f in framework_applications],
            "key_insights": key_insights,
        }

        priority = self._determine_priority(key_insights)
        if "priority" in analysis_data:
            priority = self._parse_priority(analysis_data["priority"], priority)

        return FrameworkInsight(
            framework_type=self.framework_type,
            insights=key_insights,
            confidence=confidence,
            priority=priority,
            raw_analysis=raw_analysis,
            processing_time_ms=processing_time,
        )

    def get_system_prompt(self) -> str:
        """Return the McKinsey consultant system prompt."""
        return (
            "You are a McKinsey & Company partner with 20+ years of experience "
            "in strategic consulting. You approach problems with rigorous "
            "MECE structuring (Mutually Exclusive, Collectively Exhaustive), "
            "hypothesis-driven analysis, and deep expertise in business frameworks.\n\n"
            "Your analytical approach:\n"
            "1. Frame the problem clearly with a crisp problem statement\n"
            "2. Apply MECE structuring to break complex problems into components\n"
            "3. Develop testable hypotheses and prioritize by importance\n"
            "4. Apply relevant business frameworks (7S, 3C, Porter 5 Forces)\n"
            "5. Synthesize actionable insights with clear recommendations\n\n"
            "Guiding principles:\n"
            "- Focus on the 'so what' - what action should be taken?\n"
            "- Be hypothesis-driven, not data-driven (know what you're proving)\n"
            "- Use the 80/20 rule: focus on the vital few drivers\n"
            "- Structure thinking to enable clarity and communication\n"
            "- Always consider implementation feasibility"
        )

    def _build_analysis_prompt(self, payload: KnowledgePayload) -> str:
        """Build comprehensive analysis prompt for McKinsey framework.

        Args:
            payload: The knowledge payload to analyze.

        Returns:
            Formatted prompt string for LLM.
        """
        metadata_context = ""
        if payload.metadata:
            metadata_context = f"\nAdditional context: {payload.metadata}"

        if payload.source_title:
            metadata_context += f"\nSource: {payload.source_title}"

        return f"""Analyze the following content using McKinsey consulting frameworks.

Content to analyze:
---
{payload.content}
---{metadata_context}

Provide a structured analysis with the following JSON format:

{{
    "mece_structure": {{
        "problem_statement": "Clear problem statement",
        "categories": ["Category 1", "Category 2", "Category 3"],
        "subcategories": {{
            "Category 1": ["Sub 1", "Sub 2"],
            "Category 2": ["Sub 1", "Sub 2"]
        }}
    }},
    "hypothesis_tree": {{
        "root_hypothesis": "Main hypothesis to test",
        "sub_hypotheses": ["Sub-hypothesis 1", "Sub-hypothesis 2"],
        "testable_questions": ["Question 1?", "Question 2?"],
        "priority_order": [0, 1]
    }},
    "framework_applications": [
        {{
            "framework_name": "7S|3C|Porter5Forces|ValueChain|BCG",
            "application_context": "How this applies",
            "key_insights": ["Insight 1", "Insight 2"],
            "recommendations": ["Recommendation 1", "Recommendation 2"]
        }}
    ],
    "priorities": [
        {{
            "issue": "Issue description",
            "impact": "high|medium|low",
            "effort": "high|medium|low",
            "quadrant": "quick_win|strategic|fill_in|avoid"
        }}
    ],
    "key_insights": ["3-5 key strategic insights"],
    "confidence": 0.0-1.0
}}

Apply MECE structuring (Mutually Exclusive, Collectively Exhaustive) and hypothesis-driven analysis. Focus on actionable business insights."""

    def _parse_mece_structures(self, response: Any) -> list[MeceStructure]:
        """Parse LLM response into MeceStructure objects."""
        structures = []
        try:
            content = response
            if hasattr(response, 'content'):
                content = response.content
            elif isinstance(response, dict):
                content = response.get('content', response)

            if isinstance(content, dict):
                structure = MeceStructure(
                    problem_statement=content.get('problem_statement', 'Unknown problem'),
                    categories=content.get('categories', []),
                    subcategories=content.get('subcategories', {}),
                )
                structures.append(structure)
            elif isinstance(content, list):
                for item in content:
                    structure = MeceStructure(
                        problem_statement=item.get('problem_statement', 'Unknown problem'),
                        categories=item.get('categories', []),
                        subcategories=item.get('subcategories', {}),
                    )
                    structures.append(structure)
        except Exception:
            structures.append(MeceStructure(
                problem_statement="Failed to parse MECE structure",
                categories=[],
                subcategories={},
            ))
        return structures

    def _parse_hypothesis_trees(self, response: Any) -> list[HypothesisTree]:
        """Parse LLM response into HypothesisTree objects."""
        trees = []
        try:
            content = response
            if hasattr(response, 'content'):
                content = response.content
            elif isinstance(response, dict):
                content = response.get('content', response)

            if isinstance(content, dict):
                tree = HypothesisTree(
                    root_hypothesis=content.get('root_hypothesis', 'Unknown hypothesis'),
                    sub_hypotheses=content.get('sub_hypotheses', []),
                    testable_questions=content.get('testable_questions', []),
                    priority_order=content.get('priority_order', []),
                )
                trees.append(tree)
            elif isinstance(content, list):
                for item in content:
                    tree = HypothesisTree(
                        root_hypothesis=item.get('root_hypothesis', 'Unknown hypothesis'),
                        sub_hypotheses=item.get('sub_hypotheses', []),
                        testable_questions=item.get('testable_questions', []),
                        priority_order=item.get('priority_order', []),
                    )
                    trees.append(tree)
        except Exception:
            trees.append(HypothesisTree(
                root_hypothesis="Failed to parse hypothesis tree",
                sub_hypotheses=[],
                testable_questions=[],
                priority_order=[],
            ))
        return trees

    def _parse_framework_applications(self, response: Any) -> list[FrameworkApplication]:
        """Parse LLM response into FrameworkApplication objects."""
        applications = []
        try:
            content = response
            if hasattr(response, 'content'):
                content = response.content
            elif isinstance(response, dict):
                content = response.get('content', response)

            if isinstance(content, dict):
                app = FrameworkApplication(
                    framework_name=content.get('framework_name', 'Unknown'),
                    application_context=content.get('application_context', ''),
                    key_insights=content.get('key_insights', []),
                    recommendations=content.get('recommendations', []),
                )
                applications.append(app)
            elif isinstance(content, list):
                for item in content:
                    app = FrameworkApplication(
                        framework_name=item.get('framework_name', 'Unknown'),
                        application_context=item.get('application_context', ''),
                        key_insights=item.get('key_insights', []),
                        recommendations=item.get('recommendations', []),
                    )
                    applications.append(app)
        except Exception:
            pass
        return applications

    def _parse_key_insights(self, response: Any) -> list[str]:
        """Parse LLM response into list of key insights."""
        try:
            content = response
            if hasattr(response, 'content'):
                content = response.content
            elif isinstance(response, dict):
                content = response.get('content', response)

            if isinstance(content, list):
                return [str(item) for item in content]
            elif isinstance(content, dict):
                return content.get('insights', []) or content.get('key_insights', [])
            elif isinstance(content, str):
                return [line.strip() for line in content.split('\n') if line.strip()]
        except Exception:
            pass
        return []

    @staticmethod
    def _parse_priority(
        priority_value: str,
        fallback: InsightPriority = InsightPriority.MEDIUM,
    ) -> InsightPriority:
        """Parse a priority string into an InsightPriority enum."""
        try:
            return InsightPriority[priority_value.upper()]
        except (KeyError, AttributeError):
            return fallback

    def _determine_priority(self, key_insights: list[str]) -> InsightPriority:
        """Determine priority level based on insights."""
        if not key_insights:
            return InsightPriority.MEDIUM

        critical_keywords = ['critical', 'urgent', 'must', 'immediate', 'crisis']
        high_keywords = ['important', 'significant', 'major', 'key', 'strategic']

        text = ' '.join(key_insights).lower()

        if any(kw in text for kw in critical_keywords):
            return InsightPriority.CRITICAL
        elif any(kw in text for kw in high_keywords):
            return InsightPriority.HIGH
        elif len(key_insights) >= 4:
            return InsightPriority.MEDIUM
        else:
            return InsightPriority.LOW
