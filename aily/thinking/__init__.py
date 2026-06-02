"""ARMY OF TOP MINDS - Multi-agent thinking system for Aily.

This module provides parallel analysis using TRIZ, McKinsey, and GStack frameworks
to transform raw knowledge into compelling, insight-rich outputs.
"""

from aily.thinking.models import (
    FrameworkType,
    InsightPriority,
    KnowledgePayload,
    FrameworkInsight,
    SynthesizedInsight,
    ThinkingResult,
    Contradiction,
    PrincipleRecommendation,
    EvolutionAnalysis,
    MeceStructure,
    HypothesisTree,
    FrameworkApplication,
    PMFAnalysis,
    ShippingAssessment,
    GrowthLoop,
)
from aily.thinking.config import ThinkingConfig
from aily.thinking.integration import ThinkingLLMClient

__all__ = [
    "FrameworkType",
    "InsightPriority",
    "KnowledgePayload",
    "FrameworkInsight",
    "SynthesizedInsight",
    "ThinkingResult",
    "Contradiction",
    "PrincipleRecommendation",
    "EvolutionAnalysis",
    "MeceStructure",
    "HypothesisTree",
    "FrameworkApplication",
    "PMFAnalysis",
    "ShippingAssessment",
    "GrowthLoop",
    "ThinkingConfig",
    "ThinkingLLMClient",
]