"""Synthesis engine for merging insights from multiple frameworks.

The synthesis engine takes outputs from TRIZ, McKinsey, and GStack analyzers
and creates cross-framework synthesized insights that are more powerful than
any single framework alone.

Adaptive behavior based on number of frameworks:
- 1 framework: Pass-through with normalization
- 2 frameworks: Cross-validation and conflict resolution
- 3 frameworks: Full synthesis with pattern matching
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aily.thinking.models import (
        FrameworkInsight,
        FrameworkType,
        KnowledgePayload,
        SynthesizedInsight,
    )

from aily.thinking.models import FrameworkType, InsightPriority, SynthesizedInsight


@dataclass
class Conflict:
    """Represents a conflict between framework insights.

    Attributes:
        insight_a: First insight in conflict.
        insight_b: Second insight in conflict.
        conflict_type: Type of conflict (contradiction, tension, etc.).
        description: Human-readable description of the conflict.
        severity: How severe the conflict is (0.0-1.0).
    """

    insight_a: str
    insight_b: str
    conflict_type: str
    description: str
    severity: float = 0.5


@dataclass
class Pattern:
    """Represents a reinforcing pattern across frameworks.

    Attributes:
        pattern_type: Type of pattern (convergent, complementary, tension).
        description: Description of the pattern.
        frameworks: Frameworks that exhibit this pattern.
        confidence_boost: Additional confidence from cross-framework agreement.
        related_insights: Insights that form this pattern.
    """

    pattern_type: str
    description: str
    frameworks: list[FrameworkType] = field(default_factory=list)
    confidence_boost: float = 0.0
    related_insights: list[str] = field(default_factory=list)


def detect_conflicts(insights: list[SynthesizedInsight]) -> list[Conflict]:
    """Detect conflicts between synthesized insights.

    Analyzes insights to find contradictions, tensions, or incompatible
    recommendations between different frameworks.

    Args:
        insights: List of synthesized insights to analyze.

    Returns:
        List of detected conflicts.
    """
    conflicts: list[Conflict] = []

    if len(insights) < 2:
        return conflicts

    # Keywords that indicate potential conflicts
    contradiction_indicators = [
        ("increase", "decrease"),
        ("more", "less"),
        ("faster", "slower"),
        ("expand", "contract"),
        ("centralize", "decentralize"),
        ("focus", "diversify"),
        ("build", "sunset"),
        ("invest", "divest"),
        ("hire", "fire"),
        ("accelerate", "slow"),
        ("start", "stop"),
        ("add", "remove"),
    ]

    for i, insight_a in enumerate(insights):
        for insight_b in insights[i + 1 :]:
            # Check for framework-level conflicts (different frameworks saying opposite things)
            if _frameworks_overlap(
                insight_a.supporting_frameworks, insight_b.supporting_frameworks
            ):
                continue  # Same framework insights - handled elsewhere

            # Check for semantic contradictions in descriptions
            conflict = _check_semantic_conflict(insight_a, insight_b, contradiction_indicators)
            if conflict:
                conflicts.append(conflict)

            # Check for action item conflicts
            action_conflict = _check_action_conflict(insight_a, insight_b)
            if action_conflict:
                conflicts.append(action_conflict)

    return conflicts


def _frameworks_overlap(
    frameworks_a: list[FrameworkType], frameworks_b: list[FrameworkType]
) -> bool:
    """Check if two lists of frameworks have any overlap."""
    return bool(set(frameworks_a) & set(frameworks_b))


def _check_semantic_conflict(
    insight_a: SynthesizedInsight,
    insight_b: SynthesizedInsight,
    indicators: list[tuple[str, str]],
) -> Conflict | None:
    """Check for semantic contradictions between two insights."""
    text_a = f"{insight_a.title} {insight_a.description}".lower()
    text_b = f"{insight_b.title} {insight_b.description}".lower()

    for word_a, word_b in indicators:
        # Check if insight A has word_a and insight B has word_b (or vice versa)
        a_has_first = word_a in text_a and word_b in text_b
        b_has_first = word_a in text_b and word_b in text_a

        if a_has_first or b_has_first:
            return Conflict(
                insight_a=insight_a.title,
                insight_b=insight_b.title,
                conflict_type="semantic_contradiction",
                description=f"'{insight_a.title}' suggests '{word_a}' while '{insight_b.title}' suggests '{word_b}'",
                severity=0.6,
            )

    return None


def _check_action_conflict(
    insight_a: SynthesizedInsight, insight_b: SynthesizedInsight
) -> Conflict | None:
    """Check for conflicting action items between insights."""
    if not insight_a.action_items or not insight_b.action_items:
        return None

    # Opposite action verbs that indicate conflicts
    opposite_actions = [
        ("build", "kill"),
        ("launch", "sunset"),
        ("expand", "reduce"),
        ("hire", "fire"),
        ("invest", "cut"),
        ("accelerate", "slow"),
        ("increase", "decrease"),
        ("add", "remove"),
        ("start", "stop"),
        ("buy", "sell"),
        ("merge", "split"),
    ]

    for action_a in insight_a.action_items:
        action_a_lower = action_a.lower()
        for action_b in insight_b.action_items:
            action_b_lower = action_b.lower()

            for word_a, word_b in opposite_actions:
                if word_a in action_a_lower and word_b in action_b_lower:
                    return Conflict(
                        insight_a=insight_a.title,
                        insight_b=insight_b.title,
                        conflict_type="action_conflict",
                        description=f"Conflicting actions: '{action_a}' vs '{action_b}'",
                        severity=0.7,
                    )

    return None


def resolve_conflicts(
    insights: list[SynthesizedInsight],
    conflicts: list[Conflict],
    strategy: str = "auto",
) -> list[SynthesizedInsight]:
    """Resolve conflicts between insights using specified strategy.

    Args:
        insights: List of insights to resolve conflicts for.
        conflicts: List of detected conflicts.
        strategy: Resolution strategy - "auto", "higher_confidence", "synthesize",
                 or "flag_for_review".

    Returns:
        List of insights with conflicts resolved.
    """
    if not conflicts:
        return insights

    resolved_insights = list(insights)

    for conflict in conflicts:
        # Find the insights involved in this conflict
        insight_a_idx = next(
            (i for i, ins in enumerate(resolved_insights) if ins.title == conflict.insight_a),
            None,
        )
        insight_b_idx = next(
            (i for i, ins in enumerate(resolved_insights) if ins.title == conflict.insight_b),
            None,
        )

        if insight_a_idx is None or insight_b_idx is None:
            continue

        insight_a = resolved_insights[insight_a_idx]
        insight_b = resolved_insights[insight_b_idx]

        if strategy == "auto":
            # Auto-select based on confidence difference and severity
            confidence_diff = abs(insight_a.confidence - insight_b.confidence)
            if confidence_diff > 0.2:
                strategy = "higher_confidence"
            elif conflict.severity > 0.8:
                strategy = "flag_for_review"
            else:
                strategy = "synthesize"

        if strategy == "higher_confidence":
            resolved_insights = _resolve_by_confidence(
                resolved_insights, insight_a_idx, insight_b_idx
            )
        elif strategy == "synthesize":
            resolved_insights = _resolve_by_synthesis(
                resolved_insights, insight_a_idx, insight_b_idx, conflict
            )
        elif strategy == "flag_for_review":
            resolved_insights = _resolve_by_flagging(
                resolved_insights, insight_a_idx, insight_b_idx, conflict
            )

    return resolved_insights


def _resolve_by_confidence(
    insights: list[SynthesizedInsight], idx_a: int, idx_b: int
) -> list[SynthesizedInsight]:
    """Resolve conflict by keeping the higher confidence insight."""
    insight_a = insights[idx_a]
    insight_b = insights[idx_b]

    if insight_a.confidence >= insight_b.confidence:
        # Keep A, mark B as superseded
        new_insight = SynthesizedInsight(
            title=insight_a.title,
            description=insight_a.description,
            supporting_frameworks=insight_a.supporting_frameworks,
            confidence=insight_a.confidence,
            priority=insight_a.priority,
            evidence=insight_a.evidence,
            contradictions=insight_a.contradictions + [f"Superseded: {insight_b.title}"],
            action_items=insight_a.action_items,
        )
        insights[idx_a] = new_insight
        # Reduce confidence of lower confidence insight
        insights[idx_b] = SynthesizedInsight(
            title=insight_b.title,
            description=insight_b.description,
            supporting_frameworks=insight_b.supporting_frameworks,
            confidence=insight_b.confidence * 0.5,
            priority=InsightPriority.LOW,
            evidence=insight_b.evidence,
            contradictions=insight_b.contradictions + [f"Lower confidence than: {insight_a.title}"],
            action_items=insight_b.action_items,
        )
    else:
        # Keep B, mark A as superseded
        new_insight = SynthesizedInsight(
            title=insight_b.title,
            description=insight_b.description,
            supporting_frameworks=insight_b.supporting_frameworks,
            confidence=insight_b.confidence,
            priority=insight_b.priority,
            evidence=insight_b.evidence,
            contradictions=insight_b.contradictions + [f"Superseded: {insight_a.title}"],
            action_items=insight_b.action_items,
        )
        insights[idx_b] = new_insight
        insights[idx_a] = SynthesizedInsight(
            title=insight_a.title,
            description=insight_a.description,
            supporting_frameworks=insight_a.supporting_frameworks,
            confidence=insight_a.confidence * 0.5,
            priority=InsightPriority.LOW,
            evidence=insight_a.evidence,
            contradictions=insight_a.contradictions + [f"Lower confidence than: {insight_b.title}"],
            action_items=insight_a.action_items,
        )

    return insights


def _resolve_by_synthesis(
    insights: list[SynthesizedInsight],
    idx_a: int,
    idx_b: int,
    conflict: Conflict,
) -> list[SynthesizedInsight]:
    """Resolve conflict by synthesizing both perspectives."""
    insight_a = insights[idx_a]
    insight_b = insights[idx_b]

    # Create a synthesized insight that incorporates both
    merged_title = f"{insight_a.title} / {insight_b.title}"
    merged_description = (
        f"Dual perspective: {insight_a.description}\n\n"
        f"Alternative view: {insight_b.description}\n\n"
        f"Resolution note: Both perspectives have merit depending on context."
    )

    merged_frameworks = list(
        set(insight_a.supporting_frameworks + insight_b.supporting_frameworks)
    )
    merged_confidence = (insight_a.confidence + insight_b.confidence) / 2
    merged_evidence = insight_a.evidence + insight_b.evidence
    merged_actions = insight_a.action_items + insight_b.action_items

    # Use the higher priority
    merged_priority = (
        insight_a.priority if insight_a.priority.value > insight_b.priority.value else insight_b.priority
    )

    new_insight = SynthesizedInsight(
        title=merged_title[:200],  # Cap length
        description=merged_description,
        supporting_frameworks=merged_frameworks,
        confidence=merged_confidence,
        priority=merged_priority,
        evidence=merged_evidence,
        contradictions=[conflict.description],
        action_items=merged_actions,
    )

    # Replace the first insight with the merged one, remove the second
    insights[idx_a] = new_insight
    insights.pop(idx_b)

    return insights


def _resolve_by_flagging(
    insights: list[SynthesizedInsight],
    idx_a: int,
    idx_b: int,
    conflict: Conflict,
) -> list[SynthesizedInsight]:
    """Resolve conflict by flagging for human review."""
    insight_a = insights[idx_a]
    insight_b = insights[idx_b]

    # Add flag to both insights
    flag_note = f"[FLAGGED FOR REVIEW] {conflict.description}"

    insights[idx_a] = SynthesizedInsight(
        title=f"[REVIEW] {insight_a.title}",
        description=insight_a.description,
        supporting_frameworks=insight_a.supporting_frameworks,
        confidence=insight_a.confidence,
        priority=InsightPriority.CRITICAL,  # Elevate to critical for visibility
        evidence=insight_a.evidence,
        contradictions=insight_a.contradictions + [flag_note],
        action_items=insight_a.action_items + ["Review conflicting recommendation"],
    )

    insights[idx_b] = SynthesizedInsight(
        title=insight_b.title,
        description=insight_b.description,
        supporting_frameworks=insight_b.supporting_frameworks,
        confidence=insight_b.confidence,
        priority=insight_b.priority,
        evidence=insight_b.evidence,
        contradictions=insight_b.contradictions + [flag_note],
        action_items=insight_b.action_items,
    )

    return insights


def find_reinforcing_patterns(insights: list[SynthesizedInsight]) -> list[Pattern]:
    """Find reinforcing patterns across framework insights.

    Identifies when multiple frameworks support the same insight or
    provide complementary perspectives.

    Pattern types:
    - Convergent: All frameworks agree on the same insight
    - Complementary: Different aspects of the same problem
    - Tension: Conflicting but valid perspectives

    Args:
        insights: List of synthesized insights to analyze.

    Returns:
        List of identified patterns.
    """
    patterns: list[Pattern] = []

    if len(insights) < 2:
        return patterns

    # Check for convergent patterns (multiple frameworks agree)
    multi_framework_insights = [
        ins for ins in insights if len(ins.supporting_frameworks) >= 2
    ]

    for insight in multi_framework_insights:
        pattern = Pattern(
            pattern_type="convergent",
            description=f"Multiple frameworks converge on: {insight.title}",
            frameworks=insight.supporting_frameworks,
            confidence_boost=_calculate_confidence_boost(len(insight.supporting_frameworks)),
            related_insights=[insight.title],
        )
        patterns.append(pattern)

    # Check for complementary patterns (different aspects of same problem)
    complementary_keywords = [
        (["strategy", "strategic"], ["tactical", "execution", "ship"]),
        (["product", "feature"], ["market", "growth", "user"]),
        (["technical", "technology"], ["business", "commercial"]),
        (["short", "immediate"], ["long", "strategic"]),
        (["problem", "challenge"], ["solution", "opportunity"]),
        (["risk", "threat"], ["opportunity", "advantage"]),
    ]

    for i, insight_a in enumerate(insights):
        for insight_b in insights[i + 1 :]:
            complementary = _check_complementary(
                insight_a, insight_b, complementary_keywords
            )
            if complementary:
                patterns.append(complementary)

    # Check for tension patterns (conflicting but valid)
    tension_keywords = ["but", "however", "although", "while", "yet", "whereas"]
    for insight in insights:
        text = f"{insight.title} {insight.description}".lower()
        if any(kw in text for kw in tension_keywords) and len(insight.supporting_frameworks) >= 2:
            pattern = Pattern(
                pattern_type="tension",
                description=f"Tension pattern in: {insight.title}",
                frameworks=insight.supporting_frameworks,
                confidence_boost=0.05,  # Small boost for acknowledging complexity
                related_insights=[insight.title],
            )
            patterns.append(pattern)

    return patterns


def _check_complementary(
    insight_a: SynthesizedInsight,
    insight_b: SynthesizedInsight,
    keyword_pairs: list[tuple[list[str], list[str]]],
) -> Pattern | None:
    """Check if two insights are complementary (different aspects of same problem)."""
    text_a = f"{insight_a.title} {insight_a.description}".lower()
    text_b = f"{insight_b.title} {insight_b.description}".lower()

    for set_a, set_b in keyword_pairs:
        a_has_set_a = any(kw in text_a for kw in set_a)
        a_has_set_b = any(kw in text_a for kw in set_b)
        b_has_set_a = any(kw in text_b for kw in set_a)
        b_has_set_b = any(kw in text_b for kw in set_b)

        # One insight has set_a keywords, other has set_b keywords
        if (a_has_set_a and b_has_set_b) or (a_has_set_b and b_has_set_a):
            frameworks = list(
                set(insight_a.supporting_frameworks + insight_b.supporting_frameworks)
            )
            return Pattern(
                pattern_type="complementary",
                description=f"'{insight_a.title}' and '{insight_b.title}' address complementary aspects",
                frameworks=frameworks,
                confidence_boost=0.1,
                related_insights=[insight_a.title, insight_b.title],
            )

    return None


def _calculate_confidence_boost(num_frameworks: int) -> float:
    """Calculate confidence boost based on number of supporting frameworks."""
    # Diminishing returns for more frameworks
    boosts = {1: 0.0, 2: 0.15, 3: 0.25}
    return boosts.get(num_frameworks, 0.25)


def calculate_cross_framework_confidence(
    framework_insights: list[FrameworkInsight],
    synthesized_insight: SynthesizedInsight,
) -> float:
    """Calculate confidence score based on cross-framework agreement.

    Args:
        framework_insights: Original framework insights.
        synthesized_insight: The synthesized insight to score.

    Returns:
        Confidence score (0.0-1.0).
    """
    base_confidence = synthesized_insight.confidence

    # Boost based on number of supporting frameworks
    num_frameworks = len(synthesized_insight.supporting_frameworks)
    framework_boost = _calculate_confidence_boost(num_frameworks)

    # Adjust based on average confidence of source frameworks
    source_confidences = []
    for fw in synthesized_insight.supporting_frameworks:
        for fi in framework_insights:
            if fi.framework_type == fw:
                source_confidences.append(fi.confidence)

    avg_source_confidence = (
        sum(source_confidences) / len(source_confidences) if source_confidences else 0.5
    )

    # Combine scores with weighted average
    final_confidence = (base_confidence * 0.5) + (framework_boost * 0.3) + (avg_source_confidence * 0.2)

    return min(final_confidence, 1.0)


class SynthesisEngine:
    """Engine for synthesizing insights from multiple frameworks.

    The synthesis engine merges outputs from 1-3 framework analyzers with
    adaptive behavior based on the number of frameworks:

    - 1 framework: Pass-through with normalization
    - 2 frameworks: Cross-validation and conflict resolution
    - 3 frameworks: Full synthesis with pattern matching

    Attributes:
        llm_client: Optional LLM client for advanced synthesis.
        min_confidence: Minimum confidence threshold for insights.
        max_insights: Maximum number of insights to return.
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the synthesis engine.

        Args:
            llm_client: Optional LLM client for advanced synthesis operations.
            config: Optional configuration dictionary.
        """
        self.llm_client = llm_client
        self.config = config or {}
        self.min_confidence = self.config.get("min_confidence", 0.6)
        self.max_insights = self.config.get("max_insights", 10)

    async def synthesize(
        self,
        payload: KnowledgePayload,
        framework_insights: list[FrameworkInsight],
    ) -> list[SynthesizedInsight]:
        """Synthesize framework insights into unified recommendations.

        Adaptive behavior based on number of frameworks:
        - 1 framework: Pass-through with normalization
        - 2 frameworks: Cross-validation and conflict resolution
        - 3 frameworks: Full synthesis with pattern matching

        Args:
            payload: The original knowledge payload for context.
            framework_insights: List of insights from framework analyzers.

        Returns:
            List of synthesized insights, ranked by confidence and priority.
        """
        if not framework_insights:
            return []

        num_frameworks = len(framework_insights)

        if num_frameworks == 1:
            # Single framework: pass-through with normalization
            synthesized = self._normalize_single_framework(framework_insights[0])
        elif num_frameworks == 2:
            # Two frameworks: cross-validation with conflict resolution
            synthesized = await self._cross_validate_two(framework_insights, payload)
        else:
            # Three or more frameworks: full synthesis with pattern matching
            synthesized = await self._full_synthesis(framework_insights, payload)

        # Apply confidence threshold filtering
        synthesized = [
            ins for ins in synthesized if ins.confidence >= self.min_confidence
        ]

        # Rank by priority and confidence
        synthesized = self._rank_insights(synthesized)

        # Limit to max insights
        synthesized = synthesized[: self.max_insights]

        return synthesized

    def _normalize_single_framework(
        self, framework_insight: FrameworkInsight
    ) -> list[SynthesizedInsight]:
        """Normalize a single framework insight to synthesized format.

        Args:
            framework_insight: The single framework insight to normalize.

        Returns:
            List of synthesized insights.
        """
        synthesized_insights: list[SynthesizedInsight] = []

        for i, insight_text in enumerate(framework_insight.insights):
            synthesized = SynthesizedInsight(
                title=insight_text[:100] if len(insight_text) > 100 else insight_text,
                description=insight_text,
                supporting_frameworks=[framework_insight.framework_type],
                confidence=framework_insight.confidence,
                priority=framework_insight.priority,
                evidence=[insight_text],
                contradictions=[],
                action_items=[],
            )
            synthesized_insights.append(synthesized)

        return synthesized_insights

    async def _cross_validate_two(
        self,
        framework_insights: list[FrameworkInsight],
        payload: KnowledgePayload,
    ) -> list[SynthesizedInsight]:
        """Cross-validate insights from two frameworks.

        Args:
            framework_insights: List of exactly 2 framework insights.
            payload: Original knowledge payload for context.

        Returns:
            List of synthesized insights with conflicts resolved.
        """
        if len(framework_insights) != 2:
            return await self._full_synthesis(framework_insights, payload)

        fw_a, fw_b = framework_insights[0], framework_insights[1]

        synthesized_insights: list[SynthesizedInsight] = []
        processed_pairs: set[tuple[int, int]] = set()

        # Find overlapping themes using similarity matching
        for i, insight_a in enumerate(fw_a.insights):
            best_match_idx = -1
            best_similarity = 0.0

            for j, insight_b in enumerate(fw_b.insights):
                similarity = _calculate_similarity(insight_a, insight_b)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match_idx = j

            if best_match_idx >= 0 and best_similarity > 0.3:
                insight_b = fw_b.insights[best_match_idx]
                processed_pairs.add((i, best_match_idx))

                # High similarity - synthesize into single insight
                synthesized = SynthesizedInsight(
                    title=_generate_title(insight_a, insight_b),
                    description=f"{fw_a.framework_type.value}: {insight_a}\n\n{fw_b.framework_type.value}: {insight_b}",
                    supporting_frameworks=[fw_a.framework_type, fw_b.framework_type],
                    confidence=min(fw_a.confidence, fw_b.confidence) + 0.1,
                    priority=max(fw_a.priority, fw_b.priority, key=lambda p: p.value),
                    evidence=[insight_a, insight_b],
                    contradictions=[],
                    action_items=[],
                )
                synthesized_insights.append(synthesized)

        # Add unmatched insights from framework A
        for i, insight_a in enumerate(fw_a.insights):
            if not any(pair[0] == i for pair in processed_pairs):
                synthesized_insights.append(
                    SynthesizedInsight(
                        title=insight_a[:100] if len(insight_a) > 100 else insight_a,
                        description=insight_a,
                        supporting_frameworks=[fw_a.framework_type],
                        confidence=fw_a.confidence * 0.9,
                        priority=fw_a.priority,
                        evidence=[insight_a],
                        contradictions=[],
                        action_items=[],
                    )
                )

        # Add unmatched insights from framework B
        for j, insight_b in enumerate(fw_b.insights):
            if not any(pair[1] == j for pair in processed_pairs):
                synthesized_insights.append(
                    SynthesizedInsight(
                        title=insight_b[:100] if len(insight_b) > 100 else insight_b,
                        description=insight_b,
                        supporting_frameworks=[fw_b.framework_type],
                        confidence=fw_b.confidence * 0.9,
                        priority=fw_b.priority,
                        evidence=[insight_b],
                        contradictions=[],
                        action_items=[],
                    )
                )

        # Detect and resolve conflicts
        conflicts = detect_conflicts(synthesized_insights)
        if conflicts:
            synthesized_insights = resolve_conflicts(synthesized_insights, conflicts, "auto")

        return synthesized_insights

    async def _full_synthesis(
        self,
        framework_insights: list[FrameworkInsight],
        payload: KnowledgePayload,
    ) -> list[SynthesizedInsight]:
        """Perform full synthesis with 3+ frameworks.

        Args:
            framework_insights: List of 3+ framework insights.
            payload: Original knowledge payload for context.

        Returns:
            List of synthesized insights with pattern matching applied.
        """
        # Collect all insights with their framework sources
        all_insights: list[tuple[str, FrameworkType, float, InsightPriority]] = []
        for fw in framework_insights:
            for insight in fw.insights:
                all_insights.append((insight, fw.framework_type, fw.confidence, fw.priority))

        # Group similar insights using clustering
        insight_groups = self._cluster_insights(all_insights)

        # Synthesize each group
        synthesized_insights: list[SynthesizedInsight] = []
        for group in insight_groups:
            if len(group) == 1:
                # Single framework insight
                insight, fw_type, confidence, priority = group[0]
                synthesized = SynthesizedInsight(
                    title=insight[:100] if len(insight) > 100 else insight,
                    description=insight,
                    supporting_frameworks=[fw_type],
                    confidence=confidence * 0.9,
                    priority=priority,
                    evidence=[insight],
                    contradictions=[],
                    action_items=[],
                )
            else:
                # Multi-framework insight
                frameworks = list(set(item[1] for item in group))
                descriptions = [item[0] for item in group]
                avg_confidence = sum(item[2] for item in group) / len(group)
                max_priority = max(group, key=lambda x: x[3].value)[3]

                synthesized = SynthesizedInsight(
                    title=_generate_multi_framework_title(descriptions),
                    description="\n\n".join(
                        f"{item[1].value}: {item[0]}" for item in group
                    ),
                    supporting_frameworks=frameworks,
                    confidence=min(avg_confidence + 0.15, 1.0),
                    priority=max_priority,
                    evidence=descriptions,
                    contradictions=[],
                    action_items=[],
                )

            synthesized_insights.append(synthesized)

        # Detect and resolve conflicts
        conflicts = detect_conflicts(synthesized_insights)
        if conflicts:
            synthesized_insights = resolve_conflicts(synthesized_insights, conflicts, "auto")

        # Find reinforcing patterns
        patterns = find_reinforcing_patterns(synthesized_insights)

        # Apply pattern-based confidence boosts
        for pattern in patterns:
            for i, insight in enumerate(synthesized_insights):
                if insight.title in pattern.related_insights:
                    synthesized_insights[i] = SynthesizedInsight(
                        title=insight.title,
                        description=insight.description,
                        supporting_frameworks=insight.supporting_frameworks,
                        confidence=min(insight.confidence + pattern.confidence_boost, 1.0),
                        priority=insight.priority,
                        evidence=insight.evidence,
                        contradictions=insight.contradictions,
                        action_items=insight.action_items,
                    )

        return synthesized_insights

    def _cluster_insights(
        self,
        all_insights: list[tuple[str, FrameworkType, float, InsightPriority]],
    ) -> list[list[tuple[str, FrameworkType, float, InsightPriority]]]:
        """Cluster similar insights together.

        Args:
            all_insights: List of all insights with metadata.

        Returns:
            List of insight clusters.
        """
        clusters: list[list[tuple[str, FrameworkType, float, InsightPriority]]] = []
        threshold = 0.4  # Similarity threshold for clustering

        for insight, fw_type, confidence, priority in all_insights:
            added = False
            for cluster in clusters:
                # Check if this insight is similar to any in the cluster
                if any(
                    _calculate_similarity(insight, existing[0]) > threshold
                    for existing in cluster
                ):
                    cluster.append((insight, fw_type, confidence, priority))
                    added = True
                    break

            if not added:
                clusters.append([(insight, fw_type, confidence, priority)])

        return clusters

    def _rank_insights(
        self, insights: list[SynthesizedInsight]
    ) -> list[SynthesizedInsight]:
        """Rank insights by priority and confidence.

        Args:
            insights: List of insights to rank.

        Returns:
            Sorted list of insights.
        """

        def sort_key(insight: SynthesizedInsight) -> tuple:
            # Sort by: priority (desc), confidence (desc), number of frameworks (desc)
            return (
                -insight.priority.value,  # Higher priority first
                -insight.confidence,  # Higher confidence first
                -len(insight.supporting_frameworks),  # More frameworks first
            )

        return sorted(insights, key=sort_key)

    def get_top_insights(
        self,
        synthesized: list[SynthesizedInsight],
        count: int = 5,
        min_priority: InsightPriority = InsightPriority.MEDIUM,
    ) -> list[SynthesizedInsight]:
        """Get the top N insights meeting priority threshold.

        Args:
            synthesized: List of synthesized insights.
            count: Number of insights to return.
            min_priority: Minimum priority level to include.

        Returns:
            Filtered and ranked top insights.
        """
        filtered = [s for s in synthesized if s.priority.value >= min_priority.value]
        return filtered[:count]


def _calculate_similarity(text_a: str, text_b: str) -> float:
    """Calculate simple text similarity between two strings.

    Uses Jaccard similarity on word sets.

    Args:
        text_a: First text.
        text_b: Second text.

    Returns:
        Similarity score (0.0-1.0).
    """
    # Normalize and tokenize
    words_a = set(_tokenize(text_a.lower()))
    words_b = set(_tokenize(text_b.lower()))

    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b

    return len(intersection) / len(union)


def _tokenize(text: str) -> list[str]:
    """Tokenize text into words, filtering out common stop words.

    Args:
        text: Text to tokenize.

    Returns:
        List of tokens.
    """
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "must", "shall",
        "can", "need", "dare", "ought", "used", "to", "of", "in",
        "for", "on", "with", "at", "by", "from", "as", "into",
        "through", "during", "before", "after", "above", "below",
        "between", "under", "and", "but", "or", "yet", "so", "if",
        "because", "although", "though", "while", "where", "when",
        "that", "which", "who", "whom", "whose", "what", "this",
        "these", "those", "i", "you", "he", "she", "it", "we", "they",
    }

    # Simple word extraction
    import re
    words = re.findall(r'\b[a-z]+\b', text.lower())
    return [w for w in words if w not in stop_words and len(w) > 2]


def _generate_title(insight_a: str, insight_b: str) -> str:
    """Generate a title from two related insights.

    Args:
        insight_a: First insight text.
        insight_b: Second insight text.

    Returns:
        Generated title.
    """
    # Use the shorter insight as the title, capped at 100 chars
    shorter = insight_a if len(insight_a) < len(insight_b) else insight_b
    return shorter[:100] if len(shorter) > 100 else shorter


def _generate_multi_framework_title(descriptions: list[str]) -> str:
    """Generate a title from multiple framework descriptions.

    Args:
        descriptions: List of insight descriptions.

    Returns:
        Generated title.
    """
    if not descriptions:
        return "Synthesized Insight"

    # Use the shortest description as the base
    shortest = min(descriptions, key=len)

    # Extract key phrase (first sentence or first 100 chars)
    first_sentence = shortest.split(".")[0]
    title = first_sentence[:100] if len(first_sentence) > 100 else first_sentence

    return title
