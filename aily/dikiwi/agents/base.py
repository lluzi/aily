"""Base agent class for DIKIWI stage agents."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aily.dikiwi.agents.context import AgentContext
    from aily.sessions.dikiwi_mind import StageResult

logger = logging.getLogger(__name__)


class DikiwiAgent(ABC):
    """Abstract base class for all DIKIWI stage agents."""

    @abstractmethod
    async def execute(self, ctx: AgentContext) -> StageResult:
        """Execute this agent's stage logic.

        Args:
            ctx: Pipeline-scoped context with memory, budget, and artifacts.

        Returns:
            StageResult describing success/failure and output data.
        """
        raise NotImplementedError
