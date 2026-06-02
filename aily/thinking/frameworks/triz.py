"""TRIZ (Theory of Inventive Problem Solving) framework analyzer.

TRIZ is a problem-solving methodology that identifies contradictions
and applies 40 inventive principles to resolve them.
"""

from __future__ import annotations

import json
from typing import Any

from aily.thinking.frameworks.base import FrameworkAnalyzer
from aily.thinking.models import (
    Contradiction,
    EvolutionAnalysis,
    FrameworkInsight,
    FrameworkType,
    InsightPriority,
    KnowledgePayload,
    PrincipleRecommendation,
)


class TrizAnalyzer(FrameworkAnalyzer):
    """TRIZ framework analyzer for identifying contradictions and inventive solutions.

    TRIZ (Theory of Inventive Problem Solving) is a methodology developed by
    Genrich Altshuller that analyzes patterns of invention to identify
    contradictions and recommend principles for resolving them.
    """

    framework_type = FrameworkType.TRIZ

    # Key TRIZ principles for reference
    PRINCIPLES = {
        1: "Segmentation",
        2: "Taking out",
        5: "Merging",
        10: "Prior action",
        15: "Dynamics",
        17: "Another dimension",
        25: "Self-service",
        35: "Parameter changes",
        40: "Composite materials",
    }

    async def analyze(self, payload: KnowledgePayload) -> FrameworkInsight:
        """Analyze content using TRIZ methodology.

        Identifies contradictions (technical, physical, administrative),
        recommends applicable TRIZ principles, and analyzes evolutionary
        position on the S-curve.

        Args:
            payload: Knowledge payload containing content to analyze.

        Returns:
            FrameworkInsight with contradictions, principles, and evolution analysis.
        """
        import time

        start_time = time.time()

        # Build the analysis prompt
        user_prompt = self._build_analysis_prompt(payload)

        # Call LLM with structured prompt
        messages = [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "user", "content": user_prompt},
        ]

        try:
            analysis_data = await self.llm_client.chat_json(
                messages=messages,
                temperature=0.3,
            )
        except Exception as exc:
            # Handle LLM errors gracefully
            return FrameworkInsight(
                framework_type=self.framework_type,
                insights=[f"TRIZ analysis failed: {str(exc)}"],
                confidence=0.0,
                priority=InsightPriority.LOW,
                raw_analysis={"error": str(exc)},
                processing_time_ms=int((time.time() - start_time) * 1000),
            )

        # Build structured analysis objects
        contradictions = [
            Contradiction(**c)
            for c in analysis_data.get("contradictions", [])
        ]

        principle_recommendations = [
            PrincipleRecommendation(**p)
            for p in analysis_data.get("principle_recommendations", [])
        ]

        evolution_analysis = None
        if "evolution_analysis" in analysis_data:
            evolution_payload = dict(analysis_data["evolution_analysis"])
            evolution_payload["evolution_trends"] = self._normalize_evolution_trends(
                evolution_payload.get("evolution_trends", [])
            )
            evolution_analysis = EvolutionAnalysis(
                **evolution_payload
            )

        key_insights = analysis_data.get("key_insights", [])

        # Calculate overall confidence
        if principle_recommendations:
            avg_confidence = sum(
                p.confidence for p in principle_recommendations
            ) / len(principle_recommendations)
        else:
            avg_confidence = 0.5
        avg_confidence = analysis_data.get("confidence", avg_confidence)

        # Determine priority based on contradiction severity
        priority = self._determine_priority(contradictions)
        if "priority" in analysis_data:
            priority = self._parse_priority(analysis_data["priority"], priority)

        # Build raw analysis dict
        raw_analysis = {
            "contradictions": contradictions,
            "principle_recommendations": principle_recommendations,
            "evolution_analysis": evolution_analysis,
            "key_insights": key_insights,
        }

        processing_time_ms = int((time.time() - start_time) * 1000)

        return FrameworkInsight(
            framework_type=self.framework_type,
            insights=key_insights,
            confidence=avg_confidence,
            priority=priority,
            raw_analysis=raw_analysis,
            processing_time_ms=processing_time_ms,
        )

    @staticmethod
    def _normalize_evolution_trends(trends: Any) -> list[str]:
        """Coerce model-generated trend objects into readable strings."""
        if not isinstance(trends, list):
            return []

        normalized: list[str] = []
        for item in trends:
            if isinstance(item, str):
                normalized.append(item)
                continue
            if isinstance(item, dict):
                trend = str(item.get("trend", "")).strip()
                description = str(item.get("description", "")).strip()
                if trend and description:
                    normalized.append(f"{trend}: {description}")
                elif trend:
                    normalized.append(trend)
                elif description:
                    normalized.append(description)
                else:
                    normalized.append(json.dumps(item, ensure_ascii=False))
                continue
            normalized.append(str(item))
        return normalized

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

    def _build_analysis_prompt(self, payload: KnowledgePayload) -> str:
        """Build the user prompt for TRIZ analysis.

        Args:
            payload: Knowledge payload with content to analyze.

        Returns:
            Formatted prompt string for LLM.
        """
        prompt_parts = [
            "Analyze the following content using TRIZ methodology:",
            "",
            "=== CONTENT ===",
            payload.content,
            "",
            "=== ANALYSIS REQUIREMENTS ===",
            "1. Identify contradictions (technical, physical, administrative):",
            "   - Technical: When improving one parameter worsens another",
            "   - Physical: When one property must be both present and absent",
            "   - Administrative: When process/management constraints conflict",
            "",
            "2. Recommend TRIZ principles (1-40) with:",
            "   - Principle number and name",
            "   - Specific application to the contradiction",
            "   - Confidence score (0.0-1.0)",
            "",
            "3. Analyze S-curve evolution position:",
            "   - Current position: introduction, growth, maturity, or decline",
            "   - Applicable evolution trends",
            "   - Prediction for next evolution step",
            "",
            "Key TRIZ principles to consider:",
            "1. Segmentation, 2. Taking out, 5. Merging, 10. Prior action,",
            "15. Dynamics, 17. Another dimension, 25. Self-service,",
            "35. Parameter changes, 40. Composite materials",
            "",
        ]

        if payload.metadata:
            prompt_parts.extend([
                "=== CONTEXT ===",
                f"Metadata: {payload.metadata}",
                "",
            ])

        prompt_parts.extend([
            "Respond with a JSON object containing:",
            '- "contradictions": array of contradiction objects',
            '- "principle_recommendations": array of principle recommendations',
            '- "evolution_analysis": object with s_curve_position, evolution_trends, next_generation_prediction',
            '- "key_insights": array of insight strings',
        ])

        return "\n".join(prompt_parts)

    def _determine_priority(
        self, contradictions: list[Contradiction]
    ) -> InsightPriority:
        """Determine insight priority based on contradictions.

        Args:
            contradictions: List of identified contradictions.

        Returns:
            Priority level for the insight.
        """
        if not contradictions:
            return InsightPriority.LOW

        # Count by type
        physical_count = sum(
            1 for c in contradictions
            if c.contradiction_type.lower() == "physical"
        )
        technical_count = sum(
            1 for c in contradictions
            if c.contradiction_type.lower() == "technical"
        )

        # Physical contradictions are typically most severe
        if physical_count > 0:
            return InsightPriority.CRITICAL
        if technical_count > 1:
            return InsightPriority.HIGH
        if len(contradictions) > 1:
            return InsightPriority.MEDIUM

        return InsightPriority.LOW

    def get_system_prompt(self) -> str:
        """Return the TRIZ expert system prompt.

        Returns:
            System prompt string for LLM calls.
        """
        return """You are a TRIZ (Theory of Inventive Problem Solving) expert analyst.

Your role is to analyze problems through the lens of TRIZ methodology developed by
Genrich Altshuller. TRIZ is based on the study of millions of patents and identifies
universal patterns of innovation.

Core TRIZ Concepts:

1. CONTRADICTIONS - The central insight of TRIZ is that problems are contradictions:
   - Technical contradictions: When improving one parameter makes another worse
     Example: Making a car lighter improves speed but worsens safety
   - Physical contradictions: When a property must be both present and absent
     Example: Coffee must be hot to brew but cold to drink immediately
   - Administrative contradictions: Process or management constraints in conflict

2. THE 40 INVENTIVE PRINCIPLES - Universal solution patterns:
   1. Segmentation
   2. Taking out / Extraction
   3. Local quality
   4. Asymmetry
   5. Merging / Consolidation
   6. Universality
   7. Nested doll
   8. Anti-weight / Counterweight
   9. Preliminary anti-action
   10. Prior action
   11. Beforehand cushioning
   12. Equipotentiality
   13. Inversion / 'The other way round'
   14. Spheroidality - Curvature
   15. Dynamics
   16. Partial or excessive actions
   17. Another dimension
   18. Mechanical vibration
   19. Periodic action
   20. Continuity of useful action
   21. Skipping / Rushing through
   22. Blessing in disguise / 'Turn Lemons into Lemonade'
   23. Feedback
   24. Intermediary
   25. Self-service
   26. Copying
   27. Cheap short-living objects
   28. Mechanical interaction substitution
   29. Pneumatics and hydraulics
   30. Flexible shells and thin films
   31. Porous materials
   32. Color changes
   33. Homogeneity
   34. Discarding and recovering
   35. Parameter changes
   36. Phase transitions
   37. Thermal expansion
   38. Strong oxidants
   39. Inert atmosphere
   40. Composite materials

3. S-CURVE EVOLUTION - Technology/systems follow predictable evolution patterns:
   - Introduction: Early stage, few implementations, high uncertainty
   - Growth: Rapid improvement, many patents, increasing adoption
   - Maturity: Incremental improvements, dominant design established
   - Decline: Replacement by newer technology

Evolution Trends to consider:
   - Increasing ideality (more function, less resource)
   - Transition to micro-level
   - Increasing dynamism and controllability
   - Automation and self-service
   - Structure coordination

Analyze thoroughly and provide specific, actionable insights grounded in TRIZ theory."""
