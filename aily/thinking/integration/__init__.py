"""Integration layer for the ARMY OF TOP MINDS thinking system.

This module provides integration with Aily infrastructure:
- LLM integration with structured output validation (Instructor)
- GraphDB client for insight storage
- Queue integration for job processing
- Output delivery
"""

from aily.thinking.integration.graphdb_client import ThinkingGraphClient
from aily.thinking.integration.llm_integration import ThinkingLLMClient
from aily.thinking.integration.output_integration import (
    DeliveryResult,
    ThinkingOutputHandler,
)
from aily.thinking.integration.queue_integration import (
    ThinkingJobHandler,
    create_thinking_job,
)

__all__ = [
    "ThinkingLLMClient",
    "ThinkingGraphClient",
    "ThinkingJobHandler",
    "ThinkingOutputHandler",
    "DeliveryResult",
    "create_thinking_job",
]
