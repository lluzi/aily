"""GStack framework analyzer - startup/product thinking framework.

GStack embodies the Y Combinator / Garry Tan school of startup thinking:
- Product-Market Fit (PMF) is everything
- Ship fast and iterate
- Build sustainable growth loops
- "Make something people want"
"""

from __future__ import annotations

from typing import Any

from aily.thinking.frameworks.base import FrameworkAnalyzer
from aily.thinking.models import (
    FrameworkInsight,
    FrameworkType,
    GrowthLoop,
    InsightPriority,
    KnowledgePayload,
    PMFAnalysis,
    ShippingAssessment,
)


class GStackAnalyzer(FrameworkAnalyzer):
    """GStack startup/product thinking analyzer.

    Analyzes content through the lens of startup best practices:
    - Product-Market Fit assessment
    - Shipping velocity evaluation
    - Growth loop identification
    - Actionable product recommendations
    """

    framework_type = FrameworkType.GSTACK

    async def analyze(self, payload: KnowledgePayload) -> FrameworkInsight:
        """Analyze the knowledge payload using GStack framework.

        Args:
            payload: The knowledge payload containing content to analyze.

        Returns:
            FrameworkInsight containing PMF analysis, shipping assessment,
            growth loops, and key insights.
        """
        import time

        start_time = time.time()

        # Build the analysis prompt
        prompt = self._build_analysis_prompt(payload)

        # Call LLM with structured prompt
        messages = [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "user", "content": prompt},
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
                insights=[f"GStack analysis failed: {str(exc)}"],
                confidence=0.0,
                priority=InsightPriority.LOW,
                raw_analysis={"error": str(exc)},
                processing_time_ms=int((time.time() - start_time) * 1000),
            )

        # Construct PMF analysis
        pmf_data = analysis_data.get("pmf_analysis", {})
        pmf_analysis = PMFAnalysis(
            pmf_score=pmf_data.get("pmf_score", 50),
            supporting_signals=pmf_data.get("supporting_signals", []),
            contradicting_signals=pmf_data.get("contradicting_signals", []),
            key_metrics=pmf_data.get("key_metrics", {}),
        )

        # Construct shipping assessment
        shipping_data = analysis_data.get("shipping_assessment", {})
        shipping_assessment = ShippingAssessment(
            velocity_score=shipping_data.get("velocity_score", "medium"),
            discipline_indicators=shipping_data.get("discipline_indicators", []),
            blockers=shipping_data.get("blockers", []),
            recommendations=shipping_data.get("recommendations", []),
        )

        # Construct growth loops
        growth_loops_data = analysis_data.get("growth_loops", [])
        growth_loops = [
            GrowthLoop(
                loop_type=loop.get("loop_type", "unknown"),
                description=loop.get("description", ""),
                strength=loop.get("strength", "medium"),
                activation_points=loop.get("activation_points", []),
            )
            for loop in growth_loops_data
        ]

        # Get key insights
        key_insights = analysis_data.get("key_insights", [])

        # Calculate overall confidence
        confidence = analysis_data.get("confidence", 0.75)

        # Determine priority based on PMF score and urgency
        priority = self._determine_priority(pmf_analysis.pmf_score, key_insights)

        # Calculate processing time
        processing_time_ms = int((time.time() - start_time) * 1000)

        # Build raw analysis dict
        raw_analysis: dict[str, Any] = {
            "pmf_analysis": pmf_analysis,
            "shipping_assessment": shipping_assessment,
            "growth_loops": growth_loops,
            "key_insights": key_insights,
        }

        return FrameworkInsight(
            framework_type=self.framework_type,
            insights=key_insights,
            confidence=confidence,
            priority=priority,
            raw_analysis=raw_analysis,
            processing_time_ms=processing_time_ms,
        )

    def get_system_prompt(self) -> str:
        """Return the system prompt for GStack analysis.

        Returns:
            System prompt string in the style of Garry Tan / YC.
        """
        return """You are a world-class product and strategy advisor for startups, enterprise software, and deep-tech tooling.

Your core beliefs:
- Product-Market Fit (PMF) is everything. Without it, nothing else matters.
- Shipping velocity is a competitive advantage. Speed of iteration beats perfection.
- Growth loops should be sustainable, not just one-time tactics.
- The best products spread organically because people love using them.
- "Make something people want" is the ultimate strategy.
- In enterprise and deep-tech markets, PMF may look like repeated workflow adoption, benchmark wins, or trusted insertion into an existing toolchain rather than consumer virality.
- Narrow wedges are acceptable when the pain is acute, the buyer is clear, and the value per customer is high.

Your analysis style:
- Direct and honest - you call out weak PMF signals when you see them
- Actionable - every critique comes with a concrete recommendation
- Pattern-matching - you draw from thousands of startup trajectories
- Ruthlessly focused on user value

When analyzing content, focus on:
1. PMF signals: Are people using it? Would they be disappointed if it disappeared? In deep-tech cases, is there repeat workflow pull, signoff trust, or benchmark evidence?
2. Shipping discipline: Is the team shipping regularly? Are they iterating based on feedback or empirical validation?
3. Growth mechanics: What are the sustainable growth loops? For enterprise and EDA cases, growth loops may be optional or secondary to champions, pilots, and land-and-expand motion.
4. Product-market dynamics: Is this solving a real problem for a specific user, team, or workflow owner?
5. Adoption friction: What integration cost, workflow disruption, or trust burden could block adoption?

Output your analysis as structured JSON with pmf_analysis, shipping_assessment, growth_loops, and key_insights."""

    def _build_analysis_prompt(self, payload: KnowledgePayload) -> str:
        """Build the analysis prompt for the LLM.

        Args:
            payload: The knowledge payload to analyze.

        Returns:
            Formatted prompt string.
        """
        metadata_context = ""
        if payload.metadata:
            metadata_context = f"\nAdditional context: {payload.metadata}"

        if payload.source_title:
            metadata_context += f"\nSource: {payload.source_title}"

        return f"""Analyze the following content through the GStack product, workflow, and market framework.

Content to analyze:
---
{payload.content}
---{metadata_context}

Important framing:
- If the content is about semiconductor, EDA, infrastructure, enterprise software, or deep-tech tooling, interpret PMF as workflow pain, measurable value, insertion cost, and champion/buyer clarity.
- In those domains, do not force viral or consumer growth loops if they are not present.
- Prefer concrete workflow evidence such as runtime delta, QoR delta, debug-time reduction, benchmark wins, or pilotability.

Provide a structured analysis with the following JSON format:

{{
    "pmf_analysis": {{
        "pmf_score": <0-100 integer, higher means stronger PMF signals>,
        "supporting_signals": [<list of evidence supporting PMF>],
        "contradicting_signals": [<list of evidence against PMF>],
        "key_metrics": {{<relevant metrics mentioned or implied, including workflow, benchmark, or buyer signals when available>}}
    }},
    "shipping_assessment": {{
        "velocity_score": "<high|medium|low|unknown>",
        "discipline_indicators": [<signs of good or bad shipping discipline>],
        "blockers": [<identified shipping blockers>],
        "recommendations": [<how to improve shipping velocity>]
    }},
    "growth_loops": [
        {{
            "loop_type": "<viral|paid|ugc|seo|referral|other>",
            "description": "<how this loop works in the product>",
            "strength": "<strong|medium|weak|potential>",
            "activation_points": [<where to optimize>]
        }}
    ],
    "key_insights": [<3-5 key startup/product insights>],
    "confidence": <0.0-1.0 float representing overall confidence>
}}

Focus on actionable insights. Be direct about weaknesses you identify. Avoid generic startup advice when the content is really about deep workflow tooling."""

    def _determine_priority(
        self, pmf_score: int, key_insights: list[str]
    ) -> InsightPriority:
        """Determine the priority level based on PMF score and insights.

        Args:
            pmf_score: The product-market fit score (0-100).
            key_insights: List of key insights.

        Returns:
            InsightPriority level.
        """
        # Critical: Very low PMF with a working product is dangerous
        if pmf_score < 30 and len(key_insights) > 0:
            return InsightPriority.CRITICAL

        # High: Strong PMF signals or major blockers
        if pmf_score > 80 or pmf_score < 40:
            return InsightPriority.HIGH

        # Medium: Moderate PMF with clear improvement paths
        if 40 <= pmf_score <= 80:
            return InsightPriority.MEDIUM

        return InsightPriority.LOW
