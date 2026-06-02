"""Synthesis engine for merging framework insights.

This module provides the synthesis engine that combines outputs from
multiple framework analyzers into unified, coherent insights.
"""

from aily.thinking.synthesis.engine import (
    Conflict,
    Pattern,
    SynthesisEngine,
    calculate_cross_framework_confidence,
    detect_conflicts,
    find_reinforcing_patterns,
    resolve_conflicts,
)

__all__ = [
    "SynthesisEngine",
    "Conflict",
    "Pattern",
    "detect_conflicts",
    "resolve_conflicts",
    "find_reinforcing_patterns",
    "calculate_cross_framework_confidence",
]