"""Pydantic models for the ARMY OF TOP MINDS thinking system.

This module defines all data structures used across the thinking pipeline,
from input payload to final output.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class FrameworkType(str, Enum):
    """Enumeration of supported thinking frameworks."""

    TRIZ = "triz"
    MCKINSEY = "mckinsey"
    GSTACK = "gstack"


class InsightPriority(int, Enum):
    """Priority levels for insights, ordered by importance."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class KnowledgePayload(BaseModel):
    """Input data structure for thinking analysis.

    Attributes:
        content: The primary text content to analyze.
        source_url: Optional URL where the content originated.
        source_title: Optional title of the source document.
        metadata: Additional context about the content.
        context_nodes: Related node IDs from GraphDB for context enrichment.
        timestamp: When the payload was created.
    """

    content: str = Field(..., min_length=1, description="Primary text content to analyze")
    source_url: Optional[str] = Field(None, description="Source URL if applicable")
    source_title: Optional[str] = Field(None, description="Source document title")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional context")
    context_nodes: list[str] = Field(default_factory=list, description="Related GraphDB node IDs")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        """Ensure content is not just whitespace."""
        if not v.strip():
            raise ValueError("Content cannot be empty or whitespace only")
        return v


class Contradiction(BaseModel):
    """TRIZ contradiction representation.

    Attributes:
        contradiction_type: Type of contradiction (technical, physical, administrative).
        description: Human-readable description of the contradiction.
        improving_parameter: What we want to improve.
        worsening_parameter: What gets worse as a side effect.
    """

    contradiction_type: str = Field(..., description="Type: technical, physical, or administrative", alias="type")
    description: str = Field(..., description="Contradiction description")
    improving_parameter: str = Field(default="", description="Parameter being improved")
    worsening_parameter: str = Field(default="", description="Parameter being worsened")

    model_config = {"populate_by_name": True}


class PrincipleRecommendation(BaseModel):
    """TRIZ principle recommendation.

    Attributes:
        principle_number: TRIZ principle number (1-40).
        principle_name: Name of the principle.
        application: How to apply this principle to the contradiction.
        confidence: Confidence score (0.0-1.0).
    """

    principle_number: int = Field(..., ge=1, le=40, description="TRIZ principle number (1-40)")
    principle_name: str = Field(default="", description="Name of the TRIZ principle")
    application: str = Field(..., description="Application guidance")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence score", alias="confidence_score")

    model_config = {"populate_by_name": True}


class EvolutionAnalysis(BaseModel):
    """TRIZ evolution trend analysis.

    Attributes:
        s_curve_position: Current position on S-curve (introduction, growth, maturity, decline).
        evolution_trends: List of applicable evolution trends.
        next_generation_prediction: Prediction of next evolution step.
    """

    s_curve_position: str = Field(..., description="S-curve position")
    evolution_trends: list[str] = Field(default_factory=list, description="Applicable trends")
    next_generation_prediction: Optional[str] = Field(None, description="Next evolution prediction")


class MeceStructure(BaseModel):
    """McKinsey MECE (Mutually Exclusive, Collectively Exhaustive) structure.

    Attributes:
        problem_statement: The problem being decomposed.
        categories: Top-level categories.
        subcategories: Nested breakdown structure.
    """

    problem_statement: str = Field(..., description="Problem being analyzed")
    categories: list[str] = Field(..., description="Top-level MECE categories")
    subcategories: dict[str, list[str]] = Field(
        default_factory=dict, description="Category to subcategories mapping"
    )


class HypothesisTree(BaseModel):
    """McKinsey hypothesis-driven analysis tree.

    Attributes:
        root_hypothesis: The main hypothesis being tested.
        sub_hypotheses: Supporting or refuting sub-hypotheses.
        testable_questions: Questions to validate each hypothesis.
        priority_order: Hypotheses ordered by importance to test.
    """

    root_hypothesis: str = Field(..., description="Main hypothesis")
    sub_hypotheses: list[str] = Field(default_factory=list, description="Sub-hypotheses")
    testable_questions: list[str] = Field(default_factory=list, description="Validation questions")
    priority_order: list[int] = Field(default_factory=list, description="Priority indices")


class FrameworkApplication(BaseModel):
    """Application of a specific business framework.

    Attributes:
        framework_name: Name of framework (7S, 3C, Porter 5 Forces, etc.).
        application_context: How the framework applies to this problem.
        key_insights: Insights derived from this framework.
        recommendations: Actionable recommendations.
    """

    framework_name: str = Field(..., description="Business framework name")
    application_context: str = Field(..., description="Context of application")
    key_insights: list[str] = Field(default_factory=list, description="Derived insights")
    recommendations: list[str] = Field(default_factory=list, description="Recommendations")


class PMFAnalysis(BaseModel):
    """Product-Market Fit analysis (GStack).

    Attributes:
        pmf_score: PMF score from 0-100.
        supporting_signals: Evidence supporting the score.
        contradicting_signals: Evidence against the score.
        key_metrics: Relevant PMF metrics mentioned.
    """

    pmf_score: int = Field(..., ge=0, le=100, description="PMF score (0-100)")
    supporting_signals: list[str] = Field(default_factory=list, description="Supporting evidence")
    contradicting_signals: list[str] = Field(default_factory=list, description="Contradicting evidence")
    key_metrics: dict[str, Any] = Field(default_factory=dict, description="PMF metrics")


class ShippingAssessment(BaseModel):
    """Shipping discipline assessment (GStack).

    Attributes:
        velocity_score: Assessment of shipping velocity.
        discipline_indicators: Signs of good/bad shipping discipline.
        blockers: Identified shipping blockers.
        recommendations: How to improve shipping velocity.
    """

    velocity_score: str = Field(..., description="Velocity assessment")
    discipline_indicators: list[str] = Field(default_factory=list, description="Discipline signals")
    blockers: list[str] = Field(default_factory=list, description="Shipping blockers")
    recommendations: list[str] = Field(default_factory=list, description="Improvement recommendations")


class GrowthLoop(BaseModel):
    """Growth loop identification (GStack).

    Attributes:
        loop_type: Type of growth loop (viral, paid, UGC, SEO, etc.).
        description: How this loop works in the product.
        strength: Assessment of loop strength.
        activation_points: Where to optimize the loop.
    """

    loop_type: str = Field(..., description="Growth loop type")
    description: str = Field(..., description="Loop mechanism description")
    strength: str = Field(..., description="Loop strength assessment")
    activation_points: list[str] = Field(default_factory=list, description="Optimization points")


class FrameworkInsight(BaseModel):
    """Output from a single framework analyzer.

    Attributes:
        framework_type: Which framework produced this insight.
        insights: List of insight strings.
        confidence: Overall confidence score (0.0-1.0).
        priority: Priority level of this insight.
        raw_analysis: Complete framework-specific analysis.
        processing_time_ms: Time taken to generate this insight.
    """

    framework_type: FrameworkType = Field(..., description="Source framework")
    insights: list[str] = Field(default_factory=list, description="Key insights")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Overall confidence")
    priority: InsightPriority = Field(default=InsightPriority.MEDIUM, description="Priority level")
    raw_analysis: dict[str, Any] = Field(default_factory=dict, description="Full analysis data")
    processing_time_ms: Optional[int] = Field(None, description="Processing time in milliseconds")


class SynthesizedInsight(BaseModel):
    """Merged insight from multiple frameworks.

    Attributes:
        title: Short, compelling title for the insight.
        description: Detailed explanation.
        supporting_frameworks: Which frameworks support this insight.
        confidence: Cross-framework confidence score (0.0-1.0).
        priority: Priority level.
        evidence: Supporting evidence from source content.
        contradictions: Any conflicting perspectives.
        action_items: Recommended actions based on this insight.
    """

    title: str = Field(..., min_length=1, description="Insight title")
    description: str = Field(..., min_length=1, description="Detailed description")
    supporting_frameworks: list[FrameworkType] = Field(..., description="Supporting frameworks")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Synthesized confidence")
    priority: InsightPriority = Field(default=InsightPriority.MEDIUM, description="Priority")
    evidence: list[str] = Field(default_factory=list, description="Supporting evidence")
    contradictions: list[str] = Field(default_factory=list, description="Conflicting views")
    action_items: list[str] = Field(default_factory=list, description="Recommended actions")


class ThinkingResult(BaseModel):
    """Final result from the thinking pipeline.

    Attributes:
        request_id: Unique identifier for this analysis.
        payload: The original input payload.
        framework_insights: Individual framework outputs.
        synthesized_insights: Merged insights from synthesis.
        top_insights: Highest priority insights (filtered and ranked).
        confidence_score: Overall confidence for the entire analysis.
        processing_metadata: Timing and resource information.
        formatted_output: Optional pre-formatted output strings.
    """

    request_id: str = Field(..., description="Unique request identifier")
    payload: KnowledgePayload = Field(..., description="Original input")
    framework_insights: list[FrameworkInsight] = Field(
        default_factory=list, description="Individual framework outputs"
    )
    synthesized_insights: list[SynthesizedInsight] = Field(
        default_factory=list, description="Synthesized insights"
    )
    top_insights: list[SynthesizedInsight] = Field(
        default_factory=list, description="Top ranked insights"
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Overall confidence")
    processing_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Processing metadata"
    )
    formatted_output: Optional[dict[str, str]] = Field(
        None, description="Pre-formatted outputs (obsidian, feishu)"
    )