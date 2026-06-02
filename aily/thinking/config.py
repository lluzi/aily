"""Configuration for the ARMY OF TOP MINDS thinking system.

This module provides ThinkingConfig - a Pydantic settings class for
configuring the thinking system including LLM settings, timeouts,
and feature flags.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class ThinkingConfig(BaseSettings):
    """Configuration for the thinking system.

    All settings can be overridden via environment variables with
    the AILY_THINKING_ prefix.

    Attributes:
        # Framework enablement
        triz_enabled: Whether TRIZ analyzer is enabled.
        mckinsey_enabled: Whether McKinsey analyzer is enabled.
        gstack_enabled: Whether GStack analyzer is enabled.

        # Analysis settings
        min_confidence_threshold: Minimum confidence to include an insight.
        max_insights_per_analysis: Maximum insights to return.
        parallel_analysis: Whether to run frameworks in parallel.

        # LLM settings (override base LLM for thinking)
        llm_model: Model to use for thinking analysis.
        temperature: Temperature for LLM calls (lower for consistency).
        max_tokens: Maximum tokens per LLM response.
        timeout_seconds: Timeout for LLM calls.
        max_retries: Number of retries on failure.

        # Output settings
        obsidian_folder: Folder in Obsidian for thinking outputs.
        feishu_max_length: Maximum length for Feishu summaries.
        include_framework_details: Whether to include detailed framework output.

        # Storage settings
        store_insights: Whether to persist insights to GraphDB.
        insight_retention_days: How long to retain insights.
    """

    # Framework enablement
    triz_enabled: bool = True
    mckinsey_enabled: bool = True
    gstack_enabled: bool = True

    # Analysis settings
    min_confidence_threshold: float = 0.6
    max_insights_per_analysis: int = 10
    parallel_analysis: bool = True

    # LLM settings (higher quality for thinking)
    llm_model: str = "gpt-4o"
    temperature: float = 0.3
    max_tokens: int = 4000
    timeout_seconds: float = 30.0
    max_retries: int = 3

    # Output settings
    obsidian_folder: str = "Aily Drafts/Thinking"
    feishu_max_length: int = 2000
    include_framework_details: bool = True

    # Storage settings
    store_insights: bool = True
    insight_retention_days: int = 90

    model_config = {"env_prefix": "AILY_THINKING_", "extra": "ignore"}

    @property
    def enabled_frameworks(self) -> list[str]:
        """Return list of enabled framework names."""
        frameworks = []
        if self.triz_enabled:
            frameworks.append("triz")
        if self.mckinsey_enabled:
            frameworks.append("mckinsey")
        if self.gstack_enabled:
            frameworks.append("gstack")
        return frameworks

    @property
    def framework_timeout_seconds(self) -> float:
        """Timeout per framework analyzer.

        When running in parallel, each framework gets this timeout.
        When running sequentially, this is the per-framework timeout.
        """
        return self.timeout_seconds