"""Output formatter for hypnosis-driven, persuasive output.

The formatter transforms synthesized insights into compelling outputs that:
1. Follow logical structure (premise -> evidence -> conclusion)
2. Create narrative arc (problem -> struggle -> insight -> resolution)
3. Weave evidence seamlessly
4. End with clear action catalyst

Hypnosis-driven formatting techniques:
- Pattern interrupt opening (unexpected statement/question)
- Narrative arc (problem -> struggle -> insight -> resolution)
- Evidence weaving (data, examples, authority)
- Action catalyst (clear next steps)
- Use "because" to trigger automatic agreement
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from aily.thinking.models import (
    FrameworkInsight,
    FrameworkType,
    InsightPriority,
    KnowledgePayload,
    SynthesizedInsight,
    ThinkingResult,
)


class PersuasiveOutputFormatter:
    """Formats insights using hypnosis-driven persuasive techniques.

    Key principles:
    - Open with a pattern interrupt (unexpected statement/question)
    - Use "because" to trigger automatic agreement
    - Create open loops that demand closure
    - Embed commands in the text
    - Bridge from known to unknown

    Narrative arc structure:
    - Problem: Establish the challenge
    - Struggle: Describe the tension/complexity
    - Insight: Reveal the breakthrough
    - Resolution: Provide clear path forward
    """

    # Pattern interrupt templates for opening
    PATTERN_INTERRUPTS = [
        "What if everything you assumed about {topic} was incomplete?",
        "The counterintuitive truth about {topic}:",
        "Stop. Before you continue, consider this:",
        "Most people miss this about {topic}:",
        "Here's what the data actually reveals:",
        "The question nobody's asking about {topic}:",
    ]

    # Narrative transition phrases
    NARRATIVE_TRANSITIONS = {
        "problem": [
            "The challenge is clear:",
            "Here's the tension:",
            "The core problem:",
        ],
        "struggle": [
            "But there's a deeper complexity:",
            "The struggle emerges because",
            "What makes this difficult is",
        ],
        "insight": [
            "The breakthrough insight:",
            "Here's what changes everything:",
            "The key realization is",
        ],
        "resolution": [
            "The path forward is clear:",
            "Because of this, you can",
            "Here's how to move forward:",
        ],
    }

    def __init__(self, llm_client: Any | None = None, config: dict[str, Any] | None = None) -> None:
        """Initialize the formatter.

        Args:
            llm_client: LLM client for formatting operations (optional).
            config: Optional configuration for formatting.
        """
        self.llm_client = llm_client
        self.config = config or {}

    async def format_obsidian(
        self,
        result: ThinkingResult,
        payload: KnowledgePayload,
    ) -> str:
        """Format thinking result as Obsidian markdown note with YAML frontmatter.

        Output structure:
        - YAML frontmatter with metadata
        - Executive summary section
        - Key insights with confidence scores
        - Framework perspectives (TRIZ, McKinsey, GStack sections)
        - Synthesis & recommendations
        - Action items checklist

        Args:
            result: The complete thinking result.
            payload: Original knowledge payload.

        Returns:
            Markdown formatted for Obsidian with frontmatter.
        """
        lines: list[str] = []

        # YAML Frontmatter with metadata
        lines.extend(self._generate_frontmatter(result, payload))
        lines.append("")

        # Pattern Interrupt Opening
        lines.append(self._generate_pattern_interrupt(payload))
        lines.append("")

        # Executive Summary with narrative arc
        lines.append("# Executive Summary")
        lines.append("")
        lines.append(await self._generate_summary(result, payload))
        lines.append("")

        # Key Insights with confidence scores
        lines.append("# Key Insights")
        lines.append("")
        lines.append(f"*Overall Confidence: {result.confidence_score:.0%}*")
        lines.append("")

        if result.top_insights:
            for i, insight in enumerate(result.top_insights[:5], 1):
                lines.append(self._format_insight_for_obsidian(insight, i))
                lines.append("")
        else:
            lines.append("_No insights generated for this analysis._")
            lines.append("")

        # Framework Perspectives
        if result.framework_insights:
            lines.append("# Framework Perspectives")
            lines.append("")
            for fi in result.framework_insights:
                lines.append(self._format_framework_section(fi))
                lines.append("")

        # Synthesis & Recommendations
        if result.synthesized_insights:
            lines.append("# Synthesis & Recommendations")
            lines.append("")
            lines.append(
                "These insights emerged from combining multiple thinking frameworks."
            )
            lines.append("")
            for insight in result.synthesized_insights[:3]:
                lines.append(self._format_synthesis_insight(insight))
                lines.append("")

        # Action Items Checklist
        lines.append("# Action Items")
        lines.append("")
        lines.extend(self._generate_action_checklist(result))
        lines.append("")

        # Footer
        lines.append("---")
        lines.append("")
        lines.append("*Generated by Aily ARMY OF TOP MINDS*")
        if result.processing_metadata.get('total_time_ms'):
            lines.append(f"*Processing time: {result.processing_metadata['total_time_ms']}ms*")

        return "\n".join(lines)

    async def format_feishu(
        self,
        result: ThinkingResult,
        payload: KnowledgePayload,
        max_length: int = 2000,
    ) -> str:
        """Format thinking result as concise Feishu message.

        Format includes:
        - Concise summary (< 2000 characters by default)
        - Emoji indicators for priority
        - Top 3 insights only
        - Link to full Obsidian note

        Args:
            result: The complete thinking result.
            payload: Original knowledge payload.
            max_length: Maximum message length (default 2000).

        Returns:
            Formatted message for Feishu, truncated if needed.
        """
        lines: list[str] = []

        # Header with hook
        title = payload.source_title or "ARMY Analysis"
        lines.append(f"🧠 **ARMY Analysis: {title}**")
        lines.append("")

        # Pattern interrupt opening
        if result.top_insights:
            top_insight = result.top_insights[0]
            desc = self._truncate_text(top_insight.description, 120)
            lines.append(f"💡 **Key Finding:** {desc}")
            lines.append("")

        # Confidence indicator with emoji
        confidence_emoji = "🟢" if result.confidence_score > 0.8 else "🟡" if result.confidence_score > 0.6 else "🔴"
        lines.append(f"{confidence_emoji} **Confidence:** {result.confidence_score:.0%}")
        lines.append("")

        # Frameworks used
        frameworks = self._get_frameworks(result)
        if frameworks:
            framework_names = ", ".join(f.value.upper() for f in frameworks)
            lines.append(f"📊 **Frameworks:** {framework_names}")
            lines.append("")

        # Framework-specific findings
        lines.append("**Framework Analysis:**")
        lines.append("")
        for fi in result.framework_insights:
            fw_emoji = {"triz": "⚙️", "mckinsey": "📊", "gstack": "🚀"}.get(fi.framework_type.value, "🔍")
            lines.append(f"{fw_emoji} **{fi.framework_type.value.upper()}** (confidence: {fi.confidence:.0%})")
            # Show actual insights from this framework
            for insight in fi.insights[:2]:  # Top 2 from each framework
                lines.append(f"  • {self._truncate_text(insight, 80)}")
            lines.append("")

        # Top 3 insights with priority emojis
        lines.append("**Synthesized Insights:**")
        lines.append("")
        for i, insight in enumerate(result.top_insights[:3], 1):
            priority_emoji = self._priority_to_emoji(insight.priority)
            lines.append(f"{i}. {priority_emoji} {insight.title}")
            desc = self._truncate_text(insight.description, 100)
            lines.append(f"   {desc}")
            lines.append("")

        # Top action item
        all_actions: list[str] = []
        for insight in result.top_insights[:3]:
            all_actions.extend(insight.action_items[:1])

        if all_actions:
            lines.append("**🎯 Recommended Action:**")
            lines.append(f"• {all_actions[0]}")
            lines.append("")

        # Link to full analysis
        obsidian_path = self._generate_obsidian_path(payload)
        lines.append(f"📄 *Full analysis in Obsidian: `{obsidian_path}`*")

        content = "\n".join(lines)

        # Truncate if exceeds max_length
        if len(content) > max_length:
            content = self._safe_truncate(content, max_length)

        return content

    def _format_insight_for_obsidian(
        self,
        insight: SynthesizedInsight,
        index: int,
    ) -> str:
        """Format a single insight for Obsidian.

        Args:
            insight: The synthesized insight.
            index: Insight number.

        Returns:
            Markdown formatted insight.
        """
        lines: list[str] = []

        # Priority indicator
        priority_emoji = {
            InsightPriority.CRITICAL: "🔴",
            InsightPriority.HIGH: "🟠",
            InsightPriority.MEDIUM: "🟡",
            InsightPriority.LOW: "🟢",
        }.get(insight.priority, "⚪")

        lines.append(f"## {priority_emoji} {index}. {insight.title}")
        lines.append("")

        # Description with hypnotic framing
        lines.append(insight.description)
        lines.append("")

        # Supporting frameworks
        if insight.supporting_frameworks:
            lines.append("**Frameworks:**")
            for fw in insight.supporting_frameworks:
                lines.append(f"- {fw.value.upper()}")
            lines.append("")

        # Evidence
        if insight.evidence:
            lines.append("**Evidence:**")
            for ev in insight.evidence[:3]:
                lines.append(f"- {ev}")
            lines.append("")

        # Action items
        if insight.action_items:
            lines.append("**Actions:**")
            for action in insight.action_items:
                lines.append(f"- [ ] {action}")
            lines.append("")

        # Confidence
        lines.append(f"*Confidence: {insight.confidence:.0%}*")

        return "\n".join(lines)

    def _format_framework_insight(
        self,
        fi: Any,
    ) -> str:
        """Format a framework insight.

        Args:
            fi: FrameworkInsight object.

        Returns:
            Markdown formatted framework insight.
        """
        lines: list[str] = []

        framework_name = fi.framework_type.value.upper()
        confidence_pct = int(fi.confidence * 100)

        lines.append(f"## {framework_name} Analysis")
        lines.append("")

        # Key insights from this framework
        if fi.insights:
            for insight in fi.insights:
                lines.append(f"- {insight}")
            lines.append("")

        lines.append(f"*Confidence: {confidence_pct}%*")

        return "\n".join(lines)

    def _get_frameworks(
        self,
        result: ThinkingResult,
    ) -> list[FrameworkType]:
        """Get unique frameworks from result.

        Args:
            result: ThinkingResult.

        Returns:
            List of unique FrameworkTypes.
        """
        frameworks: set[FrameworkType] = set()
        for fi in result.framework_insights:
            frameworks.add(fi.framework_type)
        return sorted(frameworks, key=lambda f: f.value)

    async def _generate_summary(self, result: ThinkingResult) -> str:
        """Generate executive summary using LLM.

        Args:
            result: The thinking result.

        Returns:
            Summary text.
        """
        if not result.top_insights:
            return "No significant insights generated from this analysis."

        # Build context from top insights
        insights_text = "\n".join(
            f"{i+1}. {ins.title}: {ins.description}"
            for i, ins in enumerate(result.top_insights[:3])
        )

        prompt = f"""Write a compelling 2-3 sentence executive summary.

Key insights:
{insights_text}

The summary should:
1. Open with a pattern interrupt (unexpected angle)
2. Bridge to the core insight
3. End with implied action

Use the word "because" to trigger agreement. Create mild curiosity."""

        try:
            response = await self.llm_client.chat(
                messages=[
                    {"role": "system", "content": "You are an expert at writing persuasive executive summaries. Use hypnotic language patterns."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
            )
            return response.strip()
        except Exception:
            # Fallback summary
            if result.top_insights:
                return f"Analysis reveals {len(result.top_insights)} key insights with {result.confidence_score:.0%} confidence. Top insight: {result.top_insights[0].title}"
            return "Analysis complete."

    # ==========================================================================
    # Helper Methods - Frontmatter & Metadata
    # ==========================================================================

    def _generate_frontmatter(
        self,
        result: ThinkingResult,
        payload: KnowledgePayload,
    ) -> list[str]:
        """Generate YAML frontmatter with metadata.

        Args:
            result: The thinking result.
            payload: Original payload.

        Returns:
            List of frontmatter lines.
        """
        lines: list[str] = []
        lines.append("---")

        # Title with escaping
        title = self._escape_yaml(payload.source_title or "ARMY Analysis")
        lines.append(f"title: {title}")

        # Source URL
        source = payload.source_url or "unknown"
        lines.append(f"source: {self._escape_yaml(source)}")

        # Date
        if isinstance(payload.timestamp, datetime):
            lines.append(f"date: {payload.timestamp.isoformat()}")
        else:
            lines.append(f"date: {payload.timestamp}")

        # Confidence score
        lines.append(f"confidence: {result.confidence_score:.2f}")

        # Frameworks used
        frameworks = self._get_frameworks(result)
        if frameworks:
            lines.append("frameworks:")
            for fw in frameworks:
                lines.append(f"  - {fw.value}")

        # Tags
        lines.append("tags:")
        lines.append("  - army-analysis")
        lines.append("  - synthesized")
        for fw in frameworks:
            lines.append(f"  - {fw.value}")

        # Insight count
        lines.append(f"insights_count: {len(result.top_insights)}")

        lines.append("---")
        return lines

    # ==========================================================================
    # Helper Methods - Hypnosis & Narrative
    # ==========================================================================

    def _generate_pattern_interrupt(self, payload: KnowledgePayload) -> str:
        """Generate a pattern interrupt opening.

        Args:
            payload: Original payload.

        Returns:
            Pattern interrupt text.
        """
        import random

        topic = payload.source_title or "this topic"
        template = random.choice(self.PATTERN_INTERRUPTS)
        return template.format(topic=topic)

    def _get_narrative_transition(self, section: str) -> str:
        """Get a random narrative transition phrase.

        Args:
            section: Section type (problem, struggle, insight, resolution).

        Returns:
            Transition phrase.
        """
        import random

        transitions = self.NARRATIVE_TRANSITIONS.get(section, [""])
        return random.choice(transitions)

    # ==========================================================================
    # Helper Methods - Formatting & Safety
    # ==========================================================================

    def _escape_yaml(self, text: str) -> str:
        """Escape special characters for YAML frontmatter.

        Args:
            text: Input text.

        Returns:
            Escaped text safe for YAML.
        """
        if not text:
            return '""'

        # Check if escaping needed
        needs_quotes = any(c in text for c in [':', '#', '{', '}', '[', ']', ',', '&', '*', '?', '|', '-', '<', '>', '=', '!', '%', '@', '`', '"', "'"])

        if '\n' in text:
            # Multi-line string using literal block
            return '|-\n    ' + text.replace('\n', '\n    ')

        if needs_quotes or text.startswith((' ', '\t')) or text.endswith((' ', '\t')):
            # Escape double quotes and wrap in quotes
            escaped = text.replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'

        return text

    def _escape_markdown(self, text: str) -> str:
        """Escape special markdown characters for Obsidian.

        Args:
            text: Input text.

        Returns:
            Escaped text safe for markdown.
        """
        if not text:
            return ""

        # Escape characters that have special meaning in markdown
        # but preserve intentional formatting
        chars_to_escape = ['*', '_', '`', '[', ']', '<', '>']
        result = text
        for char in chars_to_escape:
            result = result.replace(char, f'\\{char}')
        return result

    def _truncate_text(self, text: str, max_length: int, suffix: str = "...") -> str:
        """Truncate text to max_length with suffix.

        Args:
            text: Input text.
            max_length: Maximum length.
            suffix: Suffix to add if truncated.

        Returns:
            Truncated text.
        """
        if len(text) <= max_length:
            return text
        return text[:max_length - len(suffix)] + suffix

    def _safe_truncate(self, content: str, max_length: int) -> str:
        """Safely truncate content, preserving structure.

        Args:
            content: Content to truncate.
            max_length: Maximum length.

        Returns:
            Truncated content with notice.
        """
        if len(content) <= max_length:
            return content

        # Find a good break point (end of line)
        truncate_at = max_length - 50  # Leave room for notice
        last_newline = content.rfind('\n', 0, truncate_at)

        if last_newline > max_length * 0.7:  # If we can find a good break
            return content[:last_newline] + "\n\n... *(truncated - see full analysis in Obsidian)*"

        # Otherwise truncate mid-line
        return content[:truncate_at] + "... *(truncated - see full analysis in Obsidian)*"

    def _priority_to_emoji(self, priority: InsightPriority) -> str:
        """Convert priority to emoji indicator.

        Args:
            priority: Insight priority level.

        Returns:
            Emoji string.
        """
        return {
            InsightPriority.CRITICAL: "🔴",
            InsightPriority.HIGH: "🟠",
            InsightPriority.MEDIUM: "🟡",
            InsightPriority.LOW: "🟢",
        }.get(priority, "⚪")

    def _generate_obsidian_path(self, payload: KnowledgePayload) -> str:
        """Generate Obsidian path for linking.

        Args:
            payload: Original payload.

        Returns:
            Path string for Obsidian reference.
        """
        folder = "Aily Drafts/Thinking"
        title = payload.source_title or "ARMY Analysis"
        safe_title = re.sub(r'[^\w\s-]', '', title).strip()[:50]
        return f"{folder}/{safe_title}"

    # ==========================================================================
    # Helper Methods - Section Formatting
    # ==========================================================================

    def _format_framework_section(
        self,
        fi: FrameworkInsight,
    ) -> str:
        """Format a framework insight as a section.

        Args:
            fi: FrameworkInsight object.

        Returns:
            Markdown formatted framework section.
        """
        lines: list[str] = []

        framework_name = fi.framework_type.value.upper()
        confidence_pct = int(fi.confidence * 100)

        lines.append(f"## {framework_name} Perspective")
        lines.append("")

        # Key insights from this framework
        if fi.insights:
            lines.append("**Key Findings:**")
            for insight in fi.insights:
                escaped = self._escape_markdown(insight)
                lines.append(f"- {escaped}")
            lines.append("")

        # Raw analysis details if available
        if fi.raw_analysis:
            lines.append(self._format_raw_analysis(fi.framework_type, fi.raw_analysis))

        lines.append(f"*Confidence: {confidence_pct}%*")

        return "\n".join(lines)

    def _format_raw_analysis(
        self,
        framework_type: FrameworkType,
        raw_analysis: dict[str, Any],
    ) -> str:
        """Format raw analysis data based on framework type.

        Args:
            framework_type: Type of framework.
            raw_analysis: Raw analysis dictionary.

        Returns:
            Formatted markdown.
        """
        lines: list[str] = []

        if framework_type == FrameworkType.TRIZ:
            if "contradictions" in raw_analysis:
                lines.append("**Contradictions:**")
                for c in raw_analysis["contradictions"]:
                    desc = c.description if hasattr(c, 'description') else c.get('description', 'Unknown')
                    lines.append(f"- {desc}")
                lines.append("")

            if "principles" in raw_analysis:
                lines.append("**Recommended Principles:**")
                for p in raw_analysis["principles"]:
                    num = p.principle_number if hasattr(p, 'principle_number') else p.get('number', '?')
                    name = p.principle_name if hasattr(p, 'principle_name') else p.get('name', 'Unknown')
                    lines.append(f"- Principle {num}: {name}")
                lines.append("")

        elif framework_type == FrameworkType.MCKINSEY:
            if "mece_structure" in raw_analysis:
                structure = raw_analysis["mece_structure"]
                lines.append("**MECE Structure:**")
                lines.append(f"- Problem: {structure.get('problem_statement', 'N/A')}")
                lines.append("")

            if "hypotheses" in raw_analysis:
                lines.append("**Hypotheses:**")
                for h in raw_analysis["hypotheses"]:
                    lines.append(f"- {h}")
                lines.append("")

        elif framework_type == FrameworkType.GSTACK:
            if "pmf_score" in raw_analysis:
                lines.append(f"**PMF Score:** {raw_analysis['pmf_score']}/100")
                lines.append("")

            if "growth_loops" in raw_analysis:
                lines.append("**Growth Loops:**")
                for gl in raw_analysis["growth_loops"]:
                    loop_type = gl.loop_type if hasattr(gl, 'loop_type') else gl.get('type', 'Unknown')
                    desc = gl.description if hasattr(gl, 'description') else gl.get('description', '')
                    lines.append(f"- {loop_type}: {desc[:80]}")
                lines.append("")

        return "\n".join(lines)

    def _format_synthesis_insight(
        self,
        insight: SynthesizedInsight,
    ) -> str:
        """Format a synthesized insight for the synthesis section.

        Args:
            insight: SynthesizedInsight object.

        Returns:
            Formatted markdown.
        """
        lines: list[str] = []

        lines.append(f"## {insight.title}")
        lines.append("")
        lines.append(insight.description)
        lines.append("")

        # Supporting frameworks
        if insight.supporting_frameworks:
            frameworks_str = ", ".join(
                f.value.upper() for f in insight.supporting_frameworks
            )
            lines.append(f"**Supported by:** {frameworks_str}")
            lines.append("")

        # Evidence weaving
        if insight.evidence:
            lines.append("**Evidence:**")
            for ev in insight.evidence[:3]:
                escaped = self._escape_markdown(ev)
                lines.append(f"- {escaped}")
            lines.append("")

        # Action items
        if insight.action_items:
            lines.append("**Recommended Actions:**")
            for action in insight.action_items:
                lines.append(f"- [ ] {action}")
            lines.append("")

        # Confidence
        lines.append(f"*Confidence: {insight.confidence:.0%}*")

        return "\n".join(lines)

    def _generate_action_checklist(self, result: ThinkingResult) -> list[str]:
        """Generate action items checklist from all insights.

        Args:
            result: ThinkingResult.

        Returns:
            List of markdown lines.
        """
        lines: list[str] = []
        all_actions: list[str] = []

        # Collect actions from top insights
        for insight in result.top_insights:
            all_actions.extend(insight.action_items)

        # Add actions from synthesized insights
        for insight in result.synthesized_insights:
            all_actions.extend(insight.action_items)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_actions: list[str] = []
        for action in all_actions:
            if action and action not in seen:
                seen.add(action)
                unique_actions.append(action)

        if unique_actions:
            # Categorize by urgency
            immediate = unique_actions[:3]
            short_term = unique_actions[3:6]
            strategic = unique_actions[6:10]

            if immediate:
                lines.append("**Immediate (This Week):**")
                for action in immediate:
                    lines.append(f"- [ ] {action}")
                lines.append("")

            if short_term:
                lines.append("**Short-term (This Month):**")
                for action in short_term:
                    lines.append(f"- [ ] {action}")
                lines.append("")

            if strategic:
                lines.append("**Strategic:**")
                for action in strategic:
                    lines.append(f"- [ ] {action}")
        else:
            lines.append("_No specific action items generated. Review the insights above to identify next steps._")

        return lines

    async def _generate_summary(
        self,
        result: ThinkingResult,
        payload: KnowledgePayload,
    ) -> str:
        """Generate executive summary with narrative arc.

        Args:
            result: The thinking result.
            payload: Original payload.

        Returns:
            Summary text with narrative arc.
        """
        if not result.top_insights:
            return "No significant insights generated from this analysis."

        # Try LLM-based summary if available
        if self.llm_client:
            try:
                return await self._generate_llm_summary(result)
            except Exception:
                pass  # Fall through to template

        # Template-based summary with narrative arc
        lines: list[str] = []

        # Problem statement
        lines.append(f"{self._get_narrative_transition('problem')} Analyzing '{payload.source_title or 'the provided content'}' reveals critical patterns that demand attention.")
        lines.append("")

        # Struggle/Complexity
        if len(result.top_insights) > 1:
            lines.append(f"{self._get_narrative_transition('struggle')} multiple frameworks reveal both opportunities and tensions that must be navigated carefully.")
            lines.append("")

        # Insight
        top_insight = result.top_insights[0]
        lines.append(f"{self._get_narrative_transition('insight')} {top_insight.title} - {self._truncate_text(top_insight.description, 150)}")
        lines.append("")

        # Resolution with "because" trigger
        lines.append(f"{self._get_narrative_transition('resolution')} act on these insights because the cross-framework confidence of {result.confidence_score:.0%} indicates high reliability.")

        return "\n".join(lines)

    async def _generate_llm_summary(self, result: ThinkingResult) -> str:
        """Generate summary using LLM.

        Args:
            result: The thinking result.

        Returns:
            LLM-generated summary.
        """
        if not self.llm_client:
            raise RuntimeError("LLM client not available")

        # Build context from top insights
        insights_text = "\n".join(
            f"{i+1}. {ins.title}: {ins.description}"
            for i, ins in enumerate(result.top_insights[:3])
        )

        prompt = f"""Write a compelling 2-3 sentence executive summary.

Key insights:
{insights_text}

The summary should:
1. Open with a pattern interrupt (unexpected angle)
2. Bridge to the core insight
3. End with implied action

Use the word "because" to trigger agreement. Create mild curiosity."""

        response = await self.llm_client.chat(
            messages=[
                {"role": "system", "content": "You are an expert at writing persuasive executive summaries. Use hypnotic language patterns."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        return response.strip()


class OutputFormatter:
    """Main output formatter that delegates to specialized formatters."""

    def __init__(self, llm_client: Any | None = None, config: dict[str, Any] | None = None) -> None:
        """Initialize the formatter.

        Args:
            llm_client: LLM client for formatting (optional).
            config: Optional configuration.
        """
        self.llm_client = llm_client
        self.config = config or {}
        self.formatter = PersuasiveOutputFormatter(llm_client, config)

    async def format(
        self,
        result: ThinkingResult,
        payload: KnowledgePayload,
        format_type: str,
    ) -> str:
        """Format result for specified output type.

        Args:
            result: ThinkingResult to format.
            payload: Original knowledge payload.
            format_type: Target format ("obsidian", "feishu", etc.).

        Returns:
            Formatted string.
        """
        if format_type.lower() == "obsidian":
            return await self.formatter.format_obsidian(result, payload)
        elif format_type.lower() == "feishu":
            return await self.formatter.format_feishu(result, payload)
        else:
            # Default to Obsidian format
            return await self.formatter.format_obsidian(result, payload)
