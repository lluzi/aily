"""Base skill interface for DIKIWI.

Skills are modular capabilities that can be loaded on-demand.
Each skill has a specific purpose and can be versioned independently.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aily.llm.client import LLMClient
    from aily.graph.db import GraphDB

logger = logging.getLogger(__name__)


@dataclass
class SkillContext:
    """Context passed to skills during execution.

    Provides access to:
    - LLM for reasoning
    - GraphDB for storage
    - Content being processed
    - Stage information
    """

    llm_client: LLMClient | None = None
    graph_db: GraphDB | None = None

    # Content context
    content_id: str = ""
    content: str = ""
    source: str = ""

    # Stage context
    stage: str = ""  # Current DIKIWI stage
    correlation_id: str = ""

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """Result of skill execution.

    All skills return a standardized result for orchestrator handling.
    """

    success: bool
    skill_name: str
    output: Any = None
    processing_time_ms: float = 0.0
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # For skills that produce content
    output_content: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def success_result(
        cls,
        skill_name: str,
        output: Any,
        processing_time_ms: float,
        **kwargs,
    ) -> SkillResult:
        """Create a successful result."""
        return cls(
            success=True,
            skill_name=skill_name,
            output=output,
            processing_time_ms=processing_time_ms,
            **kwargs,
        )

    @classmethod
    def error_result(
        cls,
        skill_name: str,
        error_message: str,
        processing_time_ms: float = 0.0,
    ) -> SkillResult:
        """Create an error result."""
        return cls(
            success=False,
            skill_name=skill_name,
            error_message=error_message,
            processing_time_ms=processing_time_ms,
        )


class Skill(ABC):
    """Base class for DIKIWI skills.

    Skills are atomic capabilities that can be:
    - Loaded on-demand based on stage and content type
    - Versioned independently
    - Tested in isolation
    - Combined for complex operations

    Example skills:
    - TagExtractionSkill: Extract domain/topic tags from content
    - PatternDetectionSkill: Find patterns in knowledge network
    - SynthesisSkill: Combine insights into wisdom principles
    """

    # Skill metadata (override in subclasses)
    name: str = "base_skill"
    description: str = "Base skill class"
    version: str = "1.0.0"

    # What stage(s) this skill is designed for
    target_stages: list[str] = []

    # What content types this skill handles
    content_types: list[str] = ["*"]  # * = all types

    # Resource requirements
    requires_llm: bool = True
    requires_graph_db: bool = False

    def __init__(self) -> None:
        self._execution_count = 0
        self._total_execution_time_ms = 0.0

    @abstractmethod
    async def execute(self, context: SkillContext) -> SkillResult:
        """Execute the skill.

        Args:
            context: Execution context with LLM, GraphDB, content

        Returns:
            SkillResult with output or error
        """
        pass

    async def run(self, context: SkillContext) -> SkillResult:
        """Run skill with timing and error handling."""
        import time

        start = time.time()
        self._execution_count += 1

        try:
            # Validate requirements
            if self.requires_llm and not context.llm_client:
                return SkillResult.error_result(
                    self.name,
                    "LLM client required but not provided",
                )

            if self.requires_graph_db and not context.graph_db:
                return SkillResult.error_result(
                    self.name,
                    "GraphDB required but not provided",
                )

            # Execute
            result = await self.execute(context)

            # Update timing
            elapsed_ms = (time.time() - start) * 1000
            result.processing_time_ms = elapsed_ms
            self._total_execution_time_ms += elapsed_ms

            return result

        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            logger.exception("Skill %s failed: %s", self.name, e)
            return SkillResult.error_result(self.name, str(e), elapsed_ms)

    def can_handle(self, stage: str, content_type: str = "*") -> bool:
        """Check if this skill can handle given stage and content type."""
        stage_match = not self.target_stages or stage in self.target_stages
        type_match = "*" in self.content_types or content_type in self.content_types
        return stage_match and type_match

    def get_metrics(self) -> dict[str, Any]:
        """Get skill execution metrics."""
        return {
            "name": self.name,
            "version": self.version,
            "execution_count": self._execution_count,
            "total_execution_time_ms": self._total_execution_time_ms,
            "avg_execution_time_ms": (
                self._total_execution_time_ms / max(self._execution_count, 1)
            ),
        }

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"