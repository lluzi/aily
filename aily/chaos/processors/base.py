"""Base processor interface for content extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aily.chaos.config import ChaosConfig
    from aily.chaos.types import ExtractedContentMultimodal
    from aily.llm.llm_router import LLMInterface


class ContentProcessor(ABC):
    """Base class for content processors."""

    def __init__(self, config: "ChaosConfig", llm_client: "LLMInterface | None" = None) -> None:
        self.config = config
        self.llm_client = llm_client

    @abstractmethod
    async def process(self, file_path: Path) -> "ExtractedContentMultimodal | None":
        """Process a file and extract content.

        Args:
            file_path: Path to the file to process

        Returns:
            Extracted content or None if processing failed
        """
        ...

    @abstractmethod
    def can_process(self, file_path: Path) -> bool:
        """Check if this processor can handle the file.

        Args:
            file_path: Path to check

        Returns:
            True if this processor can handle the file
        """
        ...
