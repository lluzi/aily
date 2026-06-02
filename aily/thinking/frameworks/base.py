"""Base class for framework analyzers.

All framework analyzers (TRIZ, McKinsey, GStack) must inherit from
FrameworkAnalyzer and implement the abstract methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from aily.thinking.models import FrameworkInsight, FrameworkType, KnowledgePayload


class FrameworkAnalyzer(ABC):
    """Abstract base class for framework analyzers.

    Subclasses must define:
    - framework_type: The FrameworkType enum value
    - analyze(): Main analysis method
    - get_system_prompt(): System prompt for LLM calls

    Example:
        class TrizAnalyzer(FrameworkAnalyzer):
            framework_type = FrameworkType.TRIZ

            async def analyze(self, payload: KnowledgePayload) -> FrameworkInsight:
                # Implementation
                pass

            def get_system_prompt(self) -> str:
                return "You are a TRIZ expert..."
    """

    framework_type: "FrameworkType"

    def __init__(
        self,
        llm_client: Any,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        """Initialize the framework analyzer.

        Args:
            llm_client: The LLM client for making API calls.
            config: Optional configuration dictionary for framework-specific settings.
        """
        self.llm_client = llm_client
        self.config = config or {}

    @abstractmethod
    async def analyze(self, payload: "KnowledgePayload") -> "FrameworkInsight":
        """Analyze the knowledge payload using this framework.

        Args:
            payload: The knowledge payload containing content to analyze.

        Returns:
            FrameworkInsight containing the analysis results.

        Raises:
            LLMError: If the LLM call fails after retries.
            ValidationError: If the LLM output cannot be validated.
        """
        raise NotImplementedError

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this framework.

        Returns:
            System prompt string for LLM calls.
        """
        raise NotImplementedError

    def get_framework_name(self) -> str:
        """Return the human-readable framework name.

        Returns:
            Framework name string.
        """
        return self.framework_type.value.upper()